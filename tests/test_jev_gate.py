"""Not-a-workout pre-gate: Jev's verdict, the fail-open contract, the circuit
breaker, and the per-user toggle. No network — jev._decide is faked."""

import pytest

import workout_service
from workout_ai import jev
from workout_ai.config import JEV_REJECT_BELOW
from workout_ai.jev import CircuitBreaker, JevError
from workout_service import Failure, FailureCode


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(jev, "breaker", CircuitBreaker(3, 300, clock=clock))
    monkeypatch.setattr(jev, "JEV_ENABLED", True)
    monkeypatch.setattr(jev, "OPENROUTER_API_KEY", "sk-or-test")
    return clock


def _fake_decide(monkeypatch, *results):
    """Each call pops the next result: a float is returned, an exception raised."""
    calls = []
    queue = list(results)

    async def decide(text, api_key):
        calls.append(text)
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(jev, "_decide", decide)
    return calls


# --- workout_probability: fail-open + breaker ------------------------------


@pytest.mark.asyncio
async def test_returns_probability(clock, monkeypatch):
    _fake_decide(monkeypatch, 0.02)
    assert await jev.workout_probability("Привет!") == 0.02


@pytest.mark.asyncio
async def test_disabled_gate_never_calls(clock, monkeypatch):
    monkeypatch.setattr(jev, "JEV_ENABLED", False)
    calls = _fake_decide(monkeypatch, 0.02)
    assert await jev.workout_probability("hello") is None
    assert calls == []


@pytest.mark.asyncio
async def test_transient_errors_open_breaker_after_threshold(clock, monkeypatch):
    calls = _fake_decide(monkeypatch, *[JevError("HTTP 503")] * 3, 0.9)
    for _ in range(3):
        assert await jev.workout_probability("x") is None
    # Open: skipped without a call until the cooldown passes.
    assert await jev.workout_probability("x") is None
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_fatal_error_opens_breaker_immediately(clock, monkeypatch):
    calls = _fake_decide(monkeypatch, JevError("HTTP 401", fatal=True), 0.9)
    assert await jev.workout_probability("x") is None
    assert await jev.workout_probability("x") is None
    assert len(calls) == 1


def test_half_open_admits_a_single_probe(clock):
    jev.breaker.record_failure(fatal=True)
    clock.now += 301
    assert jev.breaker.allow() is True   # the probe...
    assert jev.breaker.allow() is False  # ...while concurrent callers still skip


@pytest.mark.asyncio
async def test_successful_probe_closes_breaker(clock, monkeypatch):
    calls = _fake_decide(monkeypatch, JevError("HTTP 401", fatal=True), 0.9, 0.8)
    await jev.workout_probability("x")
    clock.now += 301
    assert await jev.workout_probability("x") == 0.9
    assert await jev.workout_probability("x") == 0.8  # closed: no cooldown wait
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_failed_probe_reopens(clock, monkeypatch):
    calls = _fake_decide(monkeypatch, JevError("HTTP 401", fatal=True), JevError("timeout"), 0.9)
    await jev.workout_probability("x")
    clock.now += 301
    assert await jev.workout_probability("x") is None   # probe fails
    clock.now += 10
    assert await jev.workout_probability("x") is None   # back to open, no call
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_success_resets_failure_count(clock, monkeypatch):
    calls = _fake_decide(monkeypatch, JevError("t"), JevError("t"), 0.9, JevError("t"), JevError("t"), 0.9)
    for _ in range(6):
        await jev.workout_probability("x")
    assert len(calls) == 6  # never 3 in a row, never opened


# --- process_workout integration -------------------------------------------


@pytest.fixture
def service(monkeypatch):
    state = {"refunds": [], "parsed": [], "logs": [], "accepted": 0, "jev": []}

    async def fake_consume(user_id):
        return "receipt-1"

    async def fake_refund(user_id, receipt):
        state["refunds"].append(receipt)

    async def fake_parse_plan(text):
        state["parsed"].append(text)
        raise RuntimeError("stop after parse")  # upload path is not under test

    async def fake_log(**kwargs):
        state["logs"].append(kwargs)

    async def on_accepted():
        state["accepted"] += 1

    def set_probability(p):
        async def fake_probability(text):
            state["jev"].append(text)
            return p
        monkeypatch.setattr(workout_service, "workout_probability", fake_probability)

    monkeypatch.setattr(workout_service, "JEV_ENABLED", True)
    monkeypatch.setattr(workout_service, "consume", fake_consume)
    monkeypatch.setattr(workout_service, "refund", fake_refund)
    monkeypatch.setattr(workout_service, "parse_plan", fake_parse_plan)
    monkeypatch.setattr(workout_service, "log_workout_request", fake_log)
    state["set_probability"] = set_probability
    state["on_accepted"] = on_accepted
    return state


@pytest.mark.asyncio
async def test_chatter_is_rejected_refunded_and_never_parsed(service):
    service["set_probability"](0.02)
    outcome = await workout_service.process_workout(
        1, {}, "Спасибо!", on_accepted=service["on_accepted"]
    )
    assert outcome == Failure(FailureCode.NOT_A_WORKOUT)
    assert service["refunds"] == ["receipt-1"]
    assert service["parsed"] == []
    assert service["accepted"] == 0  # no "Uploading..." flash
    assert "not a workout" in service["logs"][0]["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("p", [JEV_REJECT_BELOW, 0.6, None])
async def test_workouts_and_unknown_verdicts_go_to_the_llm(service, p):
    service["set_probability"](p)
    outcome = await workout_service.process_workout(1, {}, "10x400/200")
    assert outcome == Failure(FailureCode.PARSE_FAILED)  # i.e. it reached parse_plan
    assert service["parsed"] == ["10x400/200"]
    assert service["refunds"] == []


@pytest.mark.asyncio
async def test_gate_toggle_off_skips_jev(service):
    service["set_probability"](0.02)
    user = {"prefs": {"reject_non_workouts": False}}
    outcome = await workout_service.process_workout(1, user, "Спасибо!")
    assert outcome == Failure(FailureCode.PARSE_FAILED)
    assert service["jev"] == []
    assert service["parsed"] == ["Спасибо!"]


@pytest.mark.asyncio
async def test_no_api_key_turns_gate_off_regardless_of_pref(service, monkeypatch):
    monkeypatch.setattr(workout_service, "JEV_ENABLED", False)
    service["set_probability"](0.02)
    outcome = await workout_service.process_workout(1, {}, "Спасибо!")
    assert outcome == Failure(FailureCode.PARSE_FAILED)
    assert service["jev"] == []
    assert service["parsed"] == ["Спасибо!"]


@pytest.mark.asyncio
async def test_gate_on_by_default_for_existing_stored_prefs(service):
    # Prefs saved before the toggle existed lack the key; the default applies.
    service["set_probability"](0.02)
    user = {"prefs": {"add_warmup": True, "add_cooldown": False,
                      "wu_cd_lap_press": True, "wu_cd_skip_pace": True}}
    outcome = await workout_service.process_workout(1, user, "hello")
    assert outcome == Failure(FailureCode.NOT_A_WORKOUT)
