"""The core workout use case: free text in, Garmin workout out.

Owns everything between "an authorized user sent a plan" and "there is an
outcome to report": quota accounting, LLM parsing, stored-token decryption,
upload with a one-shot reactive token refresh, token persistence, and request
logging. Telegram stays in bot.py — this module never sees a Message; it
returns a typed Outcome and bot.py owns the copy.

The invariant the quota handling draws: REFUND IFF NO PROVIDER REQUEST WAS
ISSUED. LLMBusy and WorkoutAIConfigError are raised strictly before any
provider call, so those refund. Past parse_plan a slot was held and the call
went out, so the request was billed and the quota stays consumed — refunding
would make malformed input free to retry in a loop, the exact "failures cost
nothing" hole that consuming up-front closes.
"""

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

import prefs
from audit import log_auth_event
from garmin import GarminAuthExpired, refresh_token_async, upload_parsed_workout
from rate_limiter import RateLimiterUnavailable, RateLimitExceeded, consume, refund
from user import get_garmin_token, save_user
from workout_ai import LLMBusy, WorkoutAIConfigError, parse_plan
from workout_log import log_workout_request


class FailureCode(Enum):
    RATE_LIMITED = "rate_limited"        # over quota; detail is the human-readable limit message
    LIMITER_DOWN = "limiter_down"        # Redis unreachable; fail closed, nothing processed
    LLM_BUSY = "llm_busy"                # load shed before any provider call; quota refunded
    CONFIG_ERROR = "config_error"        # our env/misconfig, pre-request; quota refunded
    PARSE_TIMEOUT = "parse_timeout"      # provider call exceeded its budget; billed
    PARSE_FAILED = "parse_failed"        # provider couldn't produce a workout; billed
    TOKEN_UNREADABLE = "token_unreadable"  # stored credential undecryptable; re-login required
    AUTH_EXPIRED = "auth_expired"        # 401 and the refresh also failed; re-login required
    UPLOAD_FAILED = "upload_failed"      # Garmin rejected the upload; billed


@dataclass
class Success:
    workout_id: str
    processing_ms: float


@dataclass
class Failure:
    code: FailureCode
    detail: str = ""


Outcome = Success | Failure

# Mid-flow progress hook (e.g. "refreshed, retrying"). Async so the bot can
# surface it as a chat message without this module importing Telegram.
Notify = Callable[[str], Awaitable[None]]

# Fired once the request is admitted (quota consumed) and real work is about to
# start — the point where the bot shows its "Uploading..." notice. Kept as a
# hook so a rate-limited request never flashes a processing message.
OnAccepted = Callable[[], Awaitable[None]]


async def _noop_notify(_: str) -> None:
    return None


async def _noop_accepted() -> None:
    return None


async def process_workout(
    user_id: int,
    user_data: dict,
    plan_text: str,
    notify: Notify = _noop_notify,
    on_accepted: OnAccepted = _noop_accepted,
) -> Outcome:
    """Run one workout request end to end. Never raises on expected failures.

    May mutate and persist `user_data` (refreshed Garmin token). The caller is
    responsible for per-user single-flighting; this function assumes it is the
    only in-flight request for `user_id`.
    """
    # Consume quota BEFORE the billable work. We are limiting attempts, not
    # successes — an LLM call that later fails at Garmin still costs money.
    # The receipt lets us hand the quota back if the attempt was our fault.
    try:
        receipt = await consume(user_id)
    except RateLimitExceeded as e:
        return Failure(FailureCode.RATE_LIMITED, str(e))
    except RateLimiterUnavailable:
        # Fail closed: without a working limiter we cannot bound spend.
        return Failure(FailureCode.LIMITER_DOWN)

    await on_accepted()
    start = time.monotonic()

    try:
        # Parse once. The refresh retry below reuses this result rather than
        # paying for a second LLM call.
        workout_json = await parse_plan(plan_text)
    except LLMBusy:
        # Load shed, not a failure of this request — nothing was billed. Logged
        # so the rate of shedding is visible; it's the signal to raise
        # LLM_CONCURRENCY (or that the provider is degraded).
        await refund(user_id, receipt)
        await log_workout_request(user_id=user_id, prompt=plan_text, error="LLM busy")
        return Failure(FailureCode.LLM_BUSY)
    except WorkoutAIConfigError as e:
        # Our misconfiguration (unknown provider, missing API key), raised
        # strictly before any provider request — nothing was billed, and
        # blaming the user's input for our env var would be a lie.
        await refund(user_id, receipt)
        print(f"[config] user={user_id} err={e}", flush=True)
        await log_workout_request(user_id=user_id, prompt=plan_text, error=f"config: {e}")
        return Failure(FailureCode.CONFIG_ERROR)
    except asyncio.TimeoutError:
        await log_workout_request(user_id=user_id, prompt=plan_text, error="LLM timeout")
        return Failure(FailureCode.PARSE_TIMEOUT)
    except Exception as e:
        print(f"[parse] user={user_id} err={type(e).__name__}: {e}", flush=True)
        await log_workout_request(
            user_id=user_id, prompt=plan_text, error=f"{type(e).__name__}: {e}"
        )
        return Failure(FailureCode.PARSE_FAILED)

    # Enforce the user's structure preferences on the parsed workout. Done
    # here — after the LLM, before upload and logging — so the logged
    # workout_json is exactly what went to Garmin. Pure dict surgery, cannot
    # fail, costs nothing when the prefs change nothing.
    workout_json = prefs.apply(workout_json, prefs.resolve(user_data.get("prefs")))

    try:
        token = await get_garmin_token(user_data)
    except Exception as e:
        # InvalidTag (tampered/swapped ciphertext) or a key mismatch after a
        # bad rotation. The stored credential is unusable; re-login is the fix.
        print(f"[token] user={user_id} decrypt failed: {type(e).__name__}: {e}", flush=True)
        await log_workout_request(user_id=user_id, prompt=plan_text, error="token decrypt failed")
        return Failure(FailureCode.TOKEN_UNREADABLE)

    try:
        try:
            workout_id, refreshed = await upload_parsed_workout(token, workout_json)
        except GarminAuthExpired:
            # Token expired — refresh via OAuth1 (no SSO hit) and re-upload the
            # ALREADY-PARSED workout. Re-running the plan through the LLM here
            # would double the token spend for a single recorded request.
            #
            # refresh_token_async raises whatever curl_cffi/json/consumer lookup
            # throws, never GarminAuthExpired. Left unwrapped, a failed refresh
            # lands in the generic `except Exception` below and the user is told
            # to "try again" against a token that will never work. Retag it so
            # the auth handler owns the whole auth story.
            try:
                new_token = await refresh_token_async(token)
            except Exception as e:
                await log_auth_event(user_id, "token_refresh", outcome="fail", detail=type(e).__name__)
                raise GarminAuthExpired(f"refresh failed: {e}") from e
            user_data["garmin_auth"] = new_token
            await save_user(user_id, user_data)
            await log_auth_event(user_id, "token_refresh", detail="reactive-401")
            await notify("Session refreshed, retrying upload...")
            workout_id, refreshed = await upload_parsed_workout(new_token, workout_json)
    except GarminAuthExpired:
        await log_workout_request(user_id=user_id, prompt=plan_text, error="auth refresh failed")
        return Failure(FailureCode.AUTH_EXPIRED)
    except Exception as e:
        # Garmin rejected the upload. The LLM call was still billed, so the
        # quota stays consumed — that spend was real.
        print(f"[upload] user={user_id} err={type(e).__name__}: {e}", flush=True)
        await log_workout_request(
            user_id=user_id,
            prompt=plan_text,
            workout_json=workout_json,
            error=f"{type(e).__name__}: {e}",
        )
        return Failure(FailureCode.UPLOAD_FAILED)

    if refreshed:
        # garth refreshed OAuth2 inside the upload. Persist it or every
        # subsequent upload re-pays this refresh round-trip forever.
        user_data["garmin_auth"] = refreshed
        await save_user(user_id, user_data)
        await log_auth_event(user_id, "token_refresh", detail="garth-internal")

    processing_ms = (time.monotonic() - start) * 1000
    await log_workout_request(
        user_id=user_id,
        prompt=plan_text,
        workout_json=workout_json,
        garmin_workout_id=workout_id,
        processing_time_ms=processing_ms,
    )
    return Success(workout_id=workout_id, processing_ms=processing_ms)
