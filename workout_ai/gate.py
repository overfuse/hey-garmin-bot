"""Global concurrency gate in front of the LLM providers.

Bounds how many LLM calls are in flight at once. This is a *concurrency* bound,
not a spend bound — spend is bounded per-user in rate_limiter.py, which is the
only place that can actually count requests. A previous version used a single
global asyncio.Lock and called it cost control; it wasn't (a user can serialize
a thousand requests through a mutex), and with no timeout on the provider call
one hung request stalled every user behind it for the SDK's 600s default.

The two bounds are orthogonal: the limiter is per-user and cannot see a spike of
N distinct users each firing their first, fully-in-quota request at once. That
spike is what the semaphore is for — it keeps us under the provider's org-wide
RPM/TPM ceiling.

Bound the *wait* as well as the concurrency. An unbounded queue turns a provider
slowdown into a silent pile-up: at concurrency 4 and a 45s timeout, the 500th
queued request waits ~90 minutes before its own timeout clock even starts, long
after Telegram (and the user) gave up. Failing fast with "busy" is worse latency
on paper and much better behaviour in practice.

This bound is cross-user only. Keeping a single user to one workout at a time is
the bot's job, not this module's — bot.py holds a per-user single-flight gate
across the whole parse+upload flow and ignores further messages while one is in
progress, so a per-user bound here would be redundant.
"""

import asyncio

from .config import LLM_CONCURRENCY, LLM_QUEUE_WAIT_S, LLM_TIMEOUT_S
from .errors import LLMBusy
from .planner import plan_to_json_async

_llm_sem = asyncio.Semaphore(LLM_CONCURRENCY)


async def parse_plan(workout_plan: str) -> dict:
    """Turn free text into a validated workout dict. The only billable step.

    Two bounds. LLM_QUEUE_WAIT_S caps how long we queue for a global slot; then
    LLM_TIMEOUT_S covers the provider call, its clock starting only once the slot
    is held — a request must never burn its provider budget queueing.

    Raises:
        LLMBusy:               every global slot was busy. Nothing was billed.
        asyncio.TimeoutError:  the provider call itself exceeded LLM_TIMEOUT_S.
    """
    try:
        async with asyncio.timeout(LLM_QUEUE_WAIT_S):
            await _llm_sem.acquire()
    except TimeoutError as e:
        raise LLMBusy(f"no LLM slot within {LLM_QUEUE_WAIT_S}s") from e

    try:
        return await asyncio.wait_for(
            plan_to_json_async(workout_plan), timeout=LLM_TIMEOUT_S
        )
    finally:
        _llm_sem.release()
