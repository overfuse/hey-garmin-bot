"""Unit tests for the Garmin payload validator.

Pure functions: no network, no LLM. The interesting property is not any single
rule but the AGREEMENT between three pieces that used to be wired independently
— prefs.apply decides whether a warm-up keeps its distance, garmin_convert turns
that into an end condition, and the validator judges the result. When the
validator hardcoded lap.button, the shipped default produced payloads it
rejected, and every "2 km warmup" plan failed the CLI.
"""

import prefs
from garmin_convert import convert
from validate_garmin import validate_garmin_workout

PLAN = {
    "name": "25×400",
    "warmup": {"distance": 2000},
    "intervals": [
        {
            "type": "repeat",
            "repeat": 25,
            "steps": [
                {"type": "run", "distance": 400, "pace": "03:55"},
                {"type": "rest", "rest": 45},
            ],
        }
    ],
    "cooldown": {"distance": 2000},
}


def _warmups(payload):
    return [
        s
        for s in payload["workoutSegments"][0]["workoutSteps"]
        if s["stepType"]["stepTypeKey"] in ("warmup", "cooldown")
    ]


# --- the invariant that matters ---------------------------------------------

def test_pipeline_agrees_with_itself_in_both_arms():
    """apply -> convert -> validate must be clean whichever way the toggle sits."""
    for lap_press in (True, False):
        resolved = prefs.resolve({"wu_cd_lap_press": lap_press})
        payload = convert(prefs.apply(PLAN, resolved))
        errors, _ = validate_garmin_workout(payload, wu_cd_lap_press=lap_press)
        assert errors == [], f"lap_press={lap_press}: {errors}"


def test_toggle_on_strips_the_distance_and_the_validator_expects_that():
    payload = convert(prefs.apply(PLAN, prefs.resolve({"wu_cd_lap_press": True})))
    assert [s["endCondition"]["conditionTypeKey"] for s in _warmups(payload)] == [
        "lap.button",
        "lap.button",
    ]


def test_toggle_off_keeps_the_distance_and_the_validator_accepts_it():
    payload = convert(prefs.apply(PLAN, prefs.resolve({"wu_cd_lap_press": False})))
    assert [s["endConditionValue"] for s in _warmups(payload)] == [2000.0, 2000.0]
    errors, _ = validate_garmin_workout(payload, wu_cd_lap_press=False)
    assert errors == []


# --- the check still bites when the pairing is genuinely wrong ---------------

def test_distance_warmup_is_rejected_when_lap_press_is_on():
    """A payload that kept its distance under a lap-press user is a real defect:
    prefs.apply was skipped somewhere upstream."""
    payload = convert(prefs.apply(PLAN, prefs.resolve({"wu_cd_lap_press": False})))
    errors, _ = validate_garmin_workout(payload, wu_cd_lap_press=True)
    assert len(errors) == 2
    assert all("must end by lap.button" in e for e in errors)


def test_lap_button_warmup_stays_valid_when_lap_press_is_off():
    """Toggle off means "keep the distance the plan gave" — not "invent one".
    A plan that never stated a distance still ends on the lap press."""
    plan = {k: v for k, v in PLAN.items() if k not in ("warmup", "cooldown")}
    plan["warmup"] = {}
    payload = convert(prefs.apply(plan, prefs.resolve({"wu_cd_lap_press": False})))
    assert _warmups(payload)[0]["endCondition"]["conditionTypeKey"] == "lap.button"
    errors, _ = validate_garmin_workout(payload, wu_cd_lap_press=False)
    assert errors == []


def test_default_is_the_shipped_preference_default():
    """Callers that pass nothing get the same behaviour as a default user."""
    payload = convert(prefs.apply(PLAN, prefs.resolve(None)))
    assert validate_garmin_workout(payload)[0] == []
    assert prefs.DEFAULTS["wu_cd_lap_press"] is True
