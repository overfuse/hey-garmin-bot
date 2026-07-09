"""Tests for parse_plan's concurrency gate (garmin.py).

Plan item A's _ConcurrencyGate refactor became obsolete when the per-user
semaphore was replaced by bot.py's single-flight `_active_notice` gate — there
is one global semaphore left and one budget, so the shared-deadline attribution
bug can no longer occur. What remained missing was any test at all for the
machinery: LLMBusy semantics, the timeout, and slot release on every exit path.
"""

import asyncio

import pytest

import garmin


@pytest.fixture
def gate(monkeypatch):
    """A 1-slot gate with fast timeouts so contention is cheap to arrange."""
    sem = asyncio.Semaphore(1)
    monkeypatch.setattr(garmin, "_llm_sem", sem)
    monkeypatch.setattr(garmin, "LLM_QUEUE_WAIT_S", 0.05)
    monkeypatch.setattr(garmin, "LLM_TIMEOUT_S", 0.2)
    return sem


@pytest.mark.asyncio
async def test_provider_called_once_when_free(gate, monkeypatch):
    calls = []

    async def fake_plan(text):
        calls.append(text)
        return {"name": "w"}

    monkeypatch.setattr(garmin, "plan_to_json_async", fake_plan)
    assert await garmin.parse_plan("easy 5k") == {"name": "w"}
    assert calls == ["easy 5k"]


@pytest.mark.asyncio
async def test_llmbusy_when_slots_exhausted_and_nothing_billed(gate, monkeypatch):
    release = asyncio.Event()
    calls = []

    async def slow_plan(text):
        calls.append(text)
        await release.wait()
        return {"name": "w"}

    monkeypatch.setattr(garmin, "plan_to_json_async", slow_plan)
    first = asyncio.create_task(garmin.parse_plan("first"))
    await asyncio.sleep(0.01)  # let `first` claim the only slot

    with pytest.raises(garmin.LLMBusy):
        await garmin.parse_plan("second")
    assert calls == ["first"]  # the shed request never reached the provider

    release.set()
    assert await first == {"name": "w"}


@pytest.mark.asyncio
async def test_llmbusy_is_not_a_timeout(gate, monkeypatch):
    """bot.py bills TimeoutError but refunds LLMBusy; conflating them re-opens
    the 'failures cost nothing' hole in the other direction."""
    assert not issubclass(garmin.LLMBusy, asyncio.TimeoutError)


@pytest.mark.asyncio
async def test_provider_timeout_raises_and_releases_the_slot(gate, monkeypatch):
    async def hang(text):
        await asyncio.sleep(30)

    monkeypatch.setattr(garmin, "plan_to_json_async", hang)
    with pytest.raises(asyncio.TimeoutError):
        await garmin.parse_plan("hangs")

    # The slot must be free again: a healthy call now succeeds instead of LLMBusy.
    async def fast(text):
        return {"name": "w"}

    monkeypatch.setattr(garmin, "plan_to_json_async", fast)
    assert await garmin.parse_plan("after") == {"name": "w"}


@pytest.mark.asyncio
async def test_shed_request_does_not_leak_a_slot(gate, monkeypatch):
    release = asyncio.Event()

    async def slow_plan(text):
        await release.wait()
        return {"name": "w"}

    monkeypatch.setattr(garmin, "plan_to_json_async", slow_plan)
    first = asyncio.create_task(garmin.parse_plan("first"))
    await asyncio.sleep(0.01)
    with pytest.raises(garmin.LLMBusy):
        await garmin.parse_plan("second")
    release.set()
    await first

    assert not gate.locked()  # every exit path released what it acquired
