"""Semantic validators on the parsed Workout: timed steps and pace ranges."""

import pytest
from pydantic import ValidationError

from workout_ai.models import TimedRecoveryStep, TimedRunStep, Workout


def _parse(*steps, **extra):
    return Workout.model_validate({"name": "x", "intervals": list(steps), **extra})


def test_timed_steps_and_pace_range_parse():
    w = _parse(
        {"type": "run", "duration": 900, "pace": "4:25", "pace_slow": "4:30"},
        {"type": "recovery", "duration": 300},
        warmup={"duration": 600},
    )
    run, recovery = w.intervals
    assert isinstance(run, TimedRunStep) and isinstance(recovery, TimedRecoveryStep)
    assert (run.duration, run.pace, run.pace_slow) == (900, "04:25", "04:30")
    assert "distance" not in w.model_dump(exclude_none=True)["intervals"][0]
    assert w.warmup.duration == 600


@pytest.mark.parametrize("step", [{"type": "run", "pace": "04:00"}, {"type": "recovery"}])
def test_step_without_distance_or_duration_is_rejected(step):
    with pytest.raises(ValidationError):
        _parse(step)


def test_pace_slow_without_pace_is_rejected():
    with pytest.raises(ValidationError, match="pace_slow requires pace"):
        _parse({"type": "run", "distance": 400, "pace_slow": "04:00"})


def test_inverted_pace_range_is_rejected():
    with pytest.raises(ValidationError, match="faster than pace"):
        _parse({"type": "run", "distance": 400, "pace": "04:30", "pace_slow": "04:25"})
