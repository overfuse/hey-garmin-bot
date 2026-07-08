"""Unit tests for the internal Workout -> Garmin payload transformer.

These are pure-function tests: no network, no LLM, no Garmin. They cover the gap
that the eval harness structurally cannot see — the evals score the model's output,
and stop there. Everything downstream of `Workout` was untested.
"""

import pytest

from garmin_convert import convert, exec_step, pace_to_sec_per_km, pace_window_mps


def _steps(payload):
    return payload["workoutSegments"][0]["workoutSteps"]


def _by_type(payload, key):
    return [s for s in _steps(payload) if s["stepType"]["stepTypeKey"] == key]


# --- the bug the evals were blind to ----------------------------------------

def test_warmup_distance_reaches_the_payload():
    """Regression: warmup/cooldown were hardcoded to a lap-button end condition,
    so an extracted distance was silently discarded. Every eval case checks that
    the model produces this value; nothing checked that it survived convert()."""
    payload = convert({"warmup": {"distance": 2000}, "intervals": []})
    wu = _by_type(payload, "warmup")[0]

    assert wu["endCondition"]["conditionTypeKey"] == "distance"
    assert wu["endConditionValue"] == 2000.0
    assert wu["durationValue"] == 2000
    assert wu["preferredEndConditionUnit"]["unitKey"] == "meter"


def test_cooldown_distance_reaches_the_payload():
    payload = convert({"intervals": [], "cooldown": {"distance": 1000}})
    cd = _by_type(payload, "cooldown")[0]
    assert cd["endCondition"]["conditionTypeKey"] == "distance"
    assert cd["endConditionValue"] == 1000.0


def test_warmup_pace_reaches_the_payload():
    """Segment.pace existed on the model and was read by nothing."""
    payload = convert({"warmup": {"distance": 2000, "pace": "06:00"}, "intervals": []})
    wu = _by_type(payload, "warmup")[0]
    assert wu["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert wu["targetValueOne"] > wu["targetValueTwo"]  # fast bound > slow bound, in m/s


def test_warmup_without_distance_still_falls_back_to_lap_button():
    """SYSTEM_PROMPT.md promises this: no distance -> lap-button transition."""
    payload = convert({"warmup": {}, "intervals": []})
    wu = _by_type(payload, "warmup")[0]
    assert wu["endCondition"]["conditionTypeKey"] == "lap.button"
    assert wu["endConditionValue"] == 0.0
    assert wu["targetType"]["workoutTargetTypeKey"] == "no.target"


# --- structure ---------------------------------------------------------------

def test_paced_run_becomes_an_interval_with_a_pace_window():
    payload = convert({"intervals": [{"type": "run", "distance": 400, "pace": "03:20"}]})
    step = _steps(payload)[0]
    assert step["stepType"]["stepTypeKey"] == "interval"
    assert step["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert step["endConditionValue"] == 400.0


def test_paceless_run_becomes_a_recovery_with_no_target():
    payload = convert({"intervals": [{"type": "run", "distance": 100}]})
    step = _steps(payload)[0]
    assert step["stepType"]["stepTypeKey"] == "recovery"
    assert step["targetType"]["workoutTargetTypeKey"] == "no.target"


def test_rest_step_ends_on_time_not_distance():
    payload = convert({"intervals": [{"type": "rest", "rest": 90}]})
    step = _steps(payload)[0]
    assert step["endCondition"]["conditionTypeKey"] == "time"
    assert step["endConditionValue"] == 90.0
    assert step["durationType"]["workoutStepDurationTypeKey"] == "time"


def test_repeat_group_wraps_children_and_marks_them():
    payload = convert({
        "intervals": [{
            "type": "repeat",
            "repeat": 10,
            "steps": [
                {"type": "run", "distance": 300, "pace": "03:30"},
                {"type": "recovery", "distance": 100},
                {"type": "rest", "rest": 90},
            ],
        }]
    })
    group = _steps(payload)[0]
    assert group["type"] == "RepeatGroupDTO"
    assert group["numberOfIterations"] == 10
    assert group["endConditionValue"] == 10.0
    assert len(group["workoutSteps"]) == 3
    assert all(s["childStepId"] == 1 for s in group["workoutSteps"])


def test_step_order_is_globally_sequential_across_repeat_children():
    payload = convert({
        "warmup": {"distance": 2000},
        "intervals": [{
            "type": "repeat", "repeat": 2,
            "steps": [{"type": "run", "distance": 400, "pace": "04:00"},
                      {"type": "recovery", "distance": 200}],
        }],
        "cooldown": {"distance": 1000},
    })
    orders = [s["stepOrder"] for s in _steps(payload)]
    child_orders = [s["stepOrder"] for s in _steps(payload)[1]["workoutSteps"]]
    assert orders == [1, 2, 5]           # warmup, repeat group, cooldown
    assert child_orders == [3, 4]        # children numbered inside the group


def test_full_workout_shape():
    payload = convert({
        "name": "10×300/100",
        "warmup": {"distance": 3000},
        "intervals": [{"type": "run", "distance": 3000, "pace": "04:00"}],
        "cooldown": {"distance": 2000},
    })
    assert payload["workoutName"] == "10×300/100"
    assert payload["sportType"]["sportTypeKey"] == "running"
    assert len(_steps(payload)) == 3


def test_unnamed_workout_gets_a_default():
    assert convert({"intervals": []})["workoutName"] == "Converted Workout"


# --- pace maths --------------------------------------------------------------

def test_pace_parsing_and_window():
    assert pace_to_sec_per_km("04:00") == 240
    fast, slow = pace_window_mps("04:00")
    assert fast == pytest.approx(1000 / 235)
    assert slow == pytest.approx(1000 / 245)
    assert fast > slow


def test_absurdly_fast_pace_is_rejected_not_divided_by_zero():
    with pytest.raises(ValueError, match="too fast"):
        pace_window_mps("00:03")


# --- error paths -------------------------------------------------------------

def test_unknown_element_type_is_rejected():
    with pytest.raises(ValueError, match="Unknown element type"):
        convert({"intervals": [{"type": "sprint", "distance": 100}]})


def test_unknown_step_type_is_rejected():
    with pytest.raises(ValueError, match="Unknown meta_key"):
        exec_step(1, "hurdles", distance=100)


def test_distanceless_interval_is_rejected_rather_than_crashing_on_none():
    with pytest.raises(ValueError, match="requires a distance"):
        exec_step(1, "interval")


def test_restless_rest_step_is_rejected():
    with pytest.raises(ValueError, match="requires a duration"):
        exec_step(1, "rest")
