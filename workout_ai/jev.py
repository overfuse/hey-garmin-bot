"""Not-a-workout pre-gate: ask TypeSafe Jev whether a message is a workout.

Jev is a decision model on OpenRouter's Decisions API: it returns a calibrated
probability for a yes/no question instead of generated text, at ~$0.00002 and
~0.2s per call. Asking it BEFORE the LLM stops chatter ("thanks", injury talk,
pace questions) from being forced through structured output into an empty or
invented workout — and from costing an LLM call.

The gate is OFF when OPENROUTER_API_KEY is unset (config.JEV_ENABLED) — a
deploy without the key behaves exactly as before the gate existed. When on,
it is an optimisation, never a dependency: it FAILS OPEN. An HTTP error, a
timeout or a malformed answer all return None, and the caller parses the
message exactly as it would without the gate. Wrongly rejecting a
real workout is worse than letting chatter through, which is also why the
threshold sits low (config.JEV_REJECT_BELOW).

The circuit breaker keeps a sick endpoint from adding JEV_TIMEOUT_S to every
request: after JEV_BREAKER_FAILURES consecutive failures (or one auth/billing
rejection, which won't fix itself) calls are skipped for JEV_BREAKER_COOLDOWN_S,
then a single probe is let through to test recovery.
"""

import time
from typing import Callable

import aiohttp

from .config import (
    JEV_BREAKER_COOLDOWN_S,
    JEV_BREAKER_FAILURES,
    JEV_ENABLED,
    JEV_MODEL,
    JEV_TIMEOUT_S,
    OPENROUTER_API_KEY,
)

URL = "https://openrouter.ai/api/alpha/decisions"

# Wording validated by the PoC: terse and Russian plans score high, pace
# questions and past-run reports score low.
QUESTIONS = {
    "is_workout": {
        "type": "noul",
        "instructions": (
            "Does the message describe a running workout the user wants created "
            "(distances, durations, repetitions, paces, warmup/cooldown)?"
        ),
        "criteria": {
            "true": "A workout plan, even terse or in Russian, e.g. '10x400/200', "
                    "'2 км разминка, 5x1км по 4:00', 'easy 45 min'.",
            "false": "Chatter, greetings, questions, advice requests, injury talk, "
                     "reports of a past run, or anything that is not a plan to build.",
        },
    }
}

# Statuses that mean "our key or account is broken" — retrying in a few
# seconds will fail the same way, so trip the breaker on the first one.
_FATAL_STATUSES = frozenset({401, 402, 403})


class JevError(Exception):
    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


class CircuitBreaker:
    """Consecutive-failure breaker with a single half-open probe.

    Not locked: the event loop is single-threaded and allow()/record_*() never
    await, so their read-modify-write cannot interleave.
    """

    def __init__(
        self,
        failures: int,
        cooldown_s: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.threshold = failures
        self.cooldown_s = cooldown_s
        self.clock = clock
        self.failures = 0
        self.open_until = 0.0

    @property
    def is_open(self) -> bool:
        return self.failures >= self.threshold

    def allow(self) -> bool:
        if not self.is_open:
            return True
        now = self.clock()
        if now < self.open_until:
            return False
        # Half-open: let this one caller probe and hold everyone else off for
        # another cooldown; record_success() closes the breaker early.
        self.open_until = now + self.cooldown_s
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def record_failure(self, fatal: bool = False) -> None:
        was_open = self.is_open
        self.failures = self.threshold if fatal else self.failures + 1
        if self.is_open:
            self.open_until = self.clock() + self.cooldown_s
            if not was_open:
                print(f"[jev] breaker OPEN for {self.cooldown_s:.0f}s", flush=True)


breaker = CircuitBreaker(JEV_BREAKER_FAILURES, JEV_BREAKER_COOLDOWN_S)

if not JEV_ENABLED:
    print("[jev] OPENROUTER_API_KEY not set; not-a-workout gate OFF", flush=True)


async def _decide(text: str, api_key: str) -> float:
    """One Decisions API call; returns P(is a workout). Raises JevError."""
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=JEV_TIMEOUT_S)
        ) as session:
            async with session.post(
                URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": JEV_MODEL, "state": {"message": text}, "questions": QUESTIONS},
            ) as resp:
                if resp.status != 200:
                    raise JevError(f"HTTP {resp.status}", fatal=resp.status in _FATAL_STATUSES)
                body = await resp.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        raise JevError(f"{type(e).__name__}: {e}") from e
    try:
        p = float(body["answers"]["is_workout"]["noul"])
    except (KeyError, TypeError, ValueError) as e:
        raise JevError(f"malformed answer: {body!r:.200}") from e
    if not 0.0 <= p <= 1.0:
        raise JevError(f"probability out of range: {p}")
    return p


async def workout_probability(text: str) -> float | None:
    """P(text is a workout plan), or None when the gate can't answer.

    None means "don't know" — the caller must proceed as if there were no
    gate. Callers should check JEV_ENABLED first; when the gate is off this
    returns None without a call. Never raises.
    """
    if not JEV_ENABLED or not breaker.allow():
        return None
    try:
        p = await _decide(text, OPENROUTER_API_KEY)
    except JevError as e:
        print(f"[jev] gate skipped: {e}", flush=True)
        breaker.record_failure(fatal=e.fatal)
        return None
    breaker.record_success()
    return p
