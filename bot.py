import asyncio
import os
import time
import traceback
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv
from garmin import (
    GarminAuthExpired,
    LLMBusy,
    login_to_garmin,
    parse_plan,
    upload_parsed_workout,
    refresh_token_async,
    workout_url,
)
from garmin_curl_login import GarminInvalidCredentials
from workout_ai import WorkoutAIConfigError
from user import (
    get_user,
    save_user,
    delete_user,
    get_garmin_token,
    has_garmin_auth,
    create_indexes as create_user_indexes,
)
import session
import token_crypto
from audit import log_auth_event, create_indexes as create_audit_indexes
from rate_limiter import (
    close_connections,
    consume,
    refund,
    get_user_stats,
    RateLimitExceeded,
    RateLimiterUnavailable,
    init as init_rate_limiter,
)
from workout_log import log_workout_request, create_indexes as create_workout_indexes

# Load environment variables
load_dotenv()
API_ID = int(os.getenv("TELEGRAM_API_ID", 0))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# States
AWAIT_USERNAME = "await_username"
AWAIT_PASSWORD = "await_password"
AUTHORIZED = "authorized"

# One workout at a time per user. Maps a user_id to the live "Uploading..." notice
# while their workout is in flight (None during the brief window after the slot is
# claimed but before the notice is sent). Presence of the key — not its value — is
# the lock; a user already present has further messages ignored, not queued. In
# process only, which is all we need: a long-polling bot is a single Telegram
# consumer, so one process holds all of a user's traffic.
_active_notice: dict[int, "Message | None"] = {}

_PROCESSING_TEXT = "Uploading your workout to Garmin Connect..."

# Appended to the processing notice when we ignore a message sent mid-flight.
_BUSY_SUFFIX = (
    "\n\n⚠️ I can only process one workout at a time. "
    "I'm ignoring new messages until this one finishes — resend them after."
)

# Initialize Pyrogram Client
app = Client("garmin_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# /start command: begin login flow
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    user_data = await get_user(user_id)
    if user_data and user_data.get("state") == AUTHORIZED and has_garmin_auth(user_data):
        return await message.reply(
            "You are already logged in! Send me a workout plan to import.\n"
            "Use /logout first if you want to switch accounts."
        )

    # Initialize persistent state
    await save_user(user_id, {"state": AWAIT_USERNAME})
    # Drop any stale half-finished login handshake
    await session.clear(user_id)
    await message.reply("Welcome! To get started, please enter your Garmin Connect username.")

# /logout command: remove authorization
@app.on_message(filters.command("logout") & filters.private)
async def logout_handler(client: Client, message: Message):
    user_id = message.from_user.id
    user_data = await get_user(user_id)
    if user_data and user_data.get("state") == AUTHORIZED:
        await delete_user(user_id)
        await log_auth_event(user_id, "logout")
        # Honest copy: there is no revocation endpoint reachable from this flow
        # (scraped SSO session, not a registered OAuth app), so deleting our copy
        # is all that actually happens. Don't imply Garmin-side access was
        # withdrawn when it wasn't.
        await message.reply(
            "You have been logged out — I deleted your stored Garmin credentials.\n\n"
            "Note: this doesn't invalidate the session on Garmin's side. If you "
            "want to be certain it can never be used again, change your Garmin "
            "password."
        )
    else:
        await message.reply("You are not logged in. Use /start to log in.")

# /stats command: show current rate limit usage
@app.on_message(filters.command("stats") & filters.private)
async def stats_handler(client: Client, message: Message):
    user_id = message.from_user.id
    try:
        stats = await get_user_stats(user_id)
    except RateLimiterUnavailable:
        return await message.reply("Usage stats are unavailable right now. Try again shortly.")

    response = (
        "📊 **Your API Usage:**\n\n"
        f"⏱ **Hourly:** {stats['hourly']['used']}/{stats['hourly']['limit']}\n"
        f"📅 **Daily:** {stats['daily']['used']}/{stats['daily']['limit']}\n"
        f"📆 **Monthly:** {stats['monthly']['used']}/{stats['monthly']['limit']}\n"
    )
    await message.reply(response)


# Text handler for login and workout messages
@app.on_message(filters.text & filters.private)
async def text_handler(client: Client, message: Message):
    user_id = message.from_user.id
    user_data = await get_user(user_id)

    # NEVER log message.text here. This handler also receives the Garmin password
    # (state == AWAIT_PASSWORD), and stdout goes straight to the deploy logs —
    # which would defeat the message.delete() scrub below.
    print(f"[msg] user={user_id} state={(user_data or {}).get('state')} len={len(message.text)}")

    # Ensure a login session has been started
    if not user_data:
        return await message.reply("Please use /start to log in first.")

    if message.text.lower() == "ping":
        return await message.reply("pong")

    state = user_data.get("state")

    # Handle username entry
    if state == AWAIT_USERNAME:
        # The handshake entry has a 5-minute TTL while `state` lives in Mongo, so
        # it can expire between /start and the username arriving. (Re)create it.
        await session.set_username(user_id, message.text.strip())
        # Update persistent state to await password
        user_data["state"] = AWAIT_PASSWORD
        await save_user(user_id, user_data)
        await message.reply("Great! Now please enter your Garmin Connect password.")
        return

    # Handle password entry and attempt login
    if state == AWAIT_PASSWORD:
        username = await session.get_username(user_id)
        if not username:
            await delete_user(user_id)
            return await message.reply("Session expired or invalid. Please use /start to log in again.")

        password = message.text.strip()
        await message.reply("Logging in to Garmin Connect...")
        try:
            token = await login_to_garmin(username, password)
            # Clean up raw credentials
            await session.clear(user_id)
            # Scrub the password from the chat history now that login succeeded.
            try:
                await message.delete()
            except Exception:
                pass
            # Store only token and authorized state
            user_data.update({"garmin_auth": token, "state": AUTHORIZED})
            await save_user(user_id, user_data)
            await log_auth_event(user_id, "login_success")
            return await message.reply(
                "Successfully logged in! Send me any workout plan (text) to import into your Garmin Connect account."
            )
        except Exception as e:
            await session.clear(user_id)
            await delete_user(user_id)
            # Class name only — never the exception body, which can echo credentials.
            await log_auth_event(user_id, "login_failure", outcome="fail", detail=type(e).__name__)
            print(
                f"[login] user={user_id} method={os.getenv('GARMIN_LOGIN_METHOD', 'garth')} "
                f"err={type(e).__name__}: {e}",
                flush=True,
            )
            traceback.print_exc()
            if isinstance(e, GarminInvalidCredentials):
                return await message.reply(
                    "❌ Incorrect Garmin email or password.\n"
                    "Please use /start and try again."
                )
            if "429" in str(e):
                return await message.reply(
                    "Garmin is temporarily rate limiting logins. Please wait a few minutes and try /start again."
                )
            return await message.reply(f"Login failed: {type(e).__name__}: {e}. Use /start to try again.")

    # Handle workout import for authorized users
    if state == AUTHORIZED:
        workout_data = message.text  # For file uploads, use filters.document and download

        # One workout at a time per user. Claim the slot synchronously — there is no
        # await between the `in` check and the assignment — so two messages racing on
        # separate dispatcher workers cannot both pass. A user already in flight has
        # this message IGNORED (not queued); we just annotate their live notice.
        if user_id in _active_notice:
            notice = _active_notice[user_id]
            if notice is not None:
                try:
                    await notice.edit_text(_PROCESSING_TEXT + _BUSY_SUFFIX)
                except Exception:
                    pass  # a repeat edit is "message not modified" — nothing to do
            return
        _active_notice[user_id] = None  # slot claimed; real notice stored below

        try:
            # Consume quota BEFORE the billable work. We are limiting attempts, not
            # successes — an LLM call that later fails at Garmin still costs money.
            # The receipt lets us hand the quota back if the attempt was our fault.
            try:
                receipt = await consume(user_id)
            except RateLimitExceeded as e:
                return await message.reply(f"⚠️ {e}")
            except RateLimiterUnavailable:
                # Fail closed: without a working limiter we cannot bound spend.
                return await message.reply(
                    "Can't process workouts right now — usage tracking is unavailable. "
                    "Please try again in a few minutes."
                )

            _active_notice[user_id] = await message.reply(_PROCESSING_TEXT)
            start = time.monotonic()

            try:
                # Parse once. The retry below reuses this result rather than paying
                # for a second LLM call.
                workout_json = await parse_plan(workout_data)
            except LLMBusy:
                # Load shed, not a failure of this request — nothing was billed. Logged
                # so the rate of shedding is visible; it's the signal to raise
                # LLM_CONCURRENCY (or that the provider is degraded).
                await refund(user_id, receipt)
                await log_workout_request(user_id=user_id, prompt=workout_data, error="LLM busy")
                return await message.reply(
                    "I'm handling a lot of workouts right now. Send that again in a moment."
                )
            except WorkoutAIConfigError as e:
                # Our misconfiguration (unknown provider, missing API key), raised
                # strictly before any provider request — nothing was billed, and
                # blaming the user's input for our env var would be a lie.
                await refund(user_id, receipt)
                print(f"[config] user={user_id} err={e}", flush=True)
                await log_workout_request(user_id=user_id, prompt=workout_data, error=f"config: {e}")
                return await message.reply(
                    "Something's broken on my side. Please try again later."
                )
            # The invariant the handlers above and below draw: REFUND IFF NO PROVIDER
            # REQUEST WAS ISSUED. Past this point a slot was held and the call went
            # out, so the request was billed. Do NOT refund: quota tracks spend, and
            # refunding here would make malformed input free to retry in a loop — the
            # exact "failures cost nothing" hole that consuming up-front closes.
            except asyncio.TimeoutError:
                await log_workout_request(user_id=user_id, prompt=workout_data, error="LLM timeout")
                return await message.reply("Parsing timed out. Please try again.")
            except Exception as e:
                print(f"[parse] user={user_id} err={type(e).__name__}: {e}", flush=True)
                await log_workout_request(
                    user_id=user_id, prompt=workout_data, error=f"{type(e).__name__}: {e}"
                )
                return await message.reply(
                    "I couldn't turn that into a workout. Try describing the intervals "
                    "with distances and paces, e.g. '2km warmup, 10x400m @ 3:45, 2km cooldown'."
                )

            try:
                token = await get_garmin_token(user_data)
            except Exception as e:
                # InvalidTag (tampered/swapped ciphertext) or a key mismatch after a
                # bad rotation. The stored credential is unusable; re-login is the fix.
                print(f"[token] user={user_id} decrypt failed: {type(e).__name__}: {e}", flush=True)
                await log_workout_request(user_id=user_id, prompt=workout_data, error="token decrypt failed")
                return await message.reply(
                    "Your stored Garmin session is unreadable. Use /logout then /start to log in again."
                )
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
                    await message.reply("Session refreshed, retrying upload...")
                    workout_id, refreshed = await upload_parsed_workout(new_token, workout_json)
            except GarminAuthExpired:
                await log_workout_request(user_id=user_id, prompt=workout_data, error="auth refresh failed")
                return await message.reply(
                    "Session expired and refresh failed. Use /logout then /start to re-login."
                )
            except Exception as e:
                # Garmin rejected the upload. The LLM call was still billed, so the
                # quota stays consumed — that spend was real.
                print(f"[upload] user={user_id} err={type(e).__name__}: {e}", flush=True)
                await log_workout_request(
                    user_id=user_id,
                    prompt=workout_data,
                    workout_json=workout_json,
                    error=f"{type(e).__name__}: {e}",
                )
                return await message.reply("Failed to import workout into Garmin. Please try again.")

            if refreshed:
                # garth refreshed OAuth2 inside the upload. Persist it or every
                # subsequent upload re-pays this refresh round-trip forever.
                user_data["garmin_auth"] = refreshed
                await save_user(user_id, user_data)
                await log_auth_event(user_id, "token_refresh", detail="garth-internal")

            processing_time = (time.monotonic() - start) * 1000
            await log_workout_request(
                user_id=user_id,
                prompt=workout_data,
                workout_json=workout_json,
                garmin_workout_id=workout_id,
                processing_time_ms=processing_time,
            )
            return await message.reply(
                f"Workout successfully imported! 🎉\n"
                f"{workout_url(workout_id)}\n\n"
                f"⚡ Processed in {processing_time:.0f}ms"
            )
        finally:
            # Release the slot no matter how we leave — success, handled reply, or a
            # crash. Without this a single unexpected exception would wedge the user
            # into a permanent "busy" state with no workout ever processing.
            _active_notice.pop(user_id, None)

async def startup():
    """Initialize indexes and other startup tasks.

    init_rate_limiter() raises if REDIS_URL is absent and the bypass was not made
    explicit — a missing env var must crash the deploy, not silently disable the
    only thing bounding our LLM spend. token_crypto.init() applies the same
    policy to TOKEN_ENC_KEY: no key and no explicit TOKEN_ENC_DISABLED=1 means
    no deploy, not silent plaintext writes.
    """
    await init_rate_limiter()
    token_crypto.init()
    await create_user_indexes()
    await create_workout_indexes()
    await create_audit_indexes()
    print("✓ Mongo indexes created (users, workout_logs, auth_events)")


async def shutdown():
    """Release what startup() acquired. Mirrors it in reverse."""
    if await close_connections():
        print("✓ Redis connections closed")

if __name__ == "__main__":
    from pyrogram import idle

    async def main():
        await startup()
        print("Starting Pyrogram...")
        await app.start()
        print(f"Bot started as @{app.me.username}")
        # finally, not a trailing statement: idle() returns on SIGTERM, which is how
        # Railway stops us. An exception escaping it must not skip the teardown.
        try:
            await idle()
        finally:
            await app.stop()
            await shutdown()

    # Must use app.run() — it reuses the event loop that Pyrogram's
    # Dispatcher captured at import time.  asyncio.run() creates a
    # new loop, so handlers registered via decorators would be lost.
    app.run(main())
