"""prefs.resolve merging and the apply() post-parse transform.

apply() is the entire enforcement mechanism (there is no prompt-side
component — see prefs.py docstring for why), so these tests pin the full
behavior: sections added, distance/pace stripped, plan-text values
overridden, and nothing else in the workout touched.
"""

from prefs import DEFAULTS, KEYS, apply, resolve


def _workout(**extra):
    return {
        "name": "10x400 @ 3:45",
        "intervals": [
            {"type": "repeat", "repeat": 10, "steps": [
                {"type": "run", "distance": 400, "pace": "03:45"},
                {"type": "recovery", "distance": 200},
            ]},
        ],
        **extra,
    }


# --- resolve -----------------------------------------------------------------

def test_resolve_none_returns_defaults():
    assert resolve(None) == DEFAULTS
    assert resolve({}) == DEFAULTS


def test_resolve_returns_a_copy():
    prefs = resolve(None)
    prefs["add_warmup"] = True
    assert DEFAULTS["add_warmup"] is False


def test_resolve_merges_stored_over_defaults():
    prefs = resolve({"add_warmup": True, "wu_cd_lap_press": False})
    assert prefs["add_warmup"] is True
    assert prefs["wu_cd_lap_press"] is False
    assert prefs["add_cooldown"] is False
    assert prefs["wu_cd_skip_pace"] is True


def test_resolve_drops_unknown_keys_and_coerces_bool():
    prefs = resolve({"nonsense": True, "add_warmup": 1})
    assert "nonsense" not in prefs
    assert prefs["add_warmup"] is True
    assert set(prefs) == set(KEYS)


# --- apply -------------------------------------------------------------------

def test_all_off_is_identity():
    workout = _workout(warmup={"distance": 2000, "pace": "05:30"})
    assert apply(workout, dict.fromkeys(KEYS, False)) == workout


def test_input_is_never_mutated():
    workout = _workout(warmup={"distance": 2000})
    apply(workout, dict.fromkeys(KEYS, True))
    assert workout["warmup"] == {"distance": 2000}


def test_add_warmup_and_cooldown_as_lap_press_sections():
    out = apply(_workout(), resolve({"add_warmup": True, "add_cooldown": True}))
    # empty section == lap-button-ended step downstream (garmin_convert)
    assert out["warmup"] == {}
    assert out["cooldown"] == {}


def test_add_toggles_off_add_nothing():
    out = apply(_workout(), resolve(None))
    assert "warmup" not in out
    assert "cooldown" not in out


def test_existing_sections_are_not_duplicated_or_replaced():
    out = apply(
        _workout(warmup={"distance": 2000}),
        resolve({"add_warmup": True, "wu_cd_lap_press": False}),
    )
    assert out["warmup"] == {"distance": 2000}


def test_default_prefs_strip_distance_and_pace_from_plan_text():
    # the plan explicitly said "2km warmup @ 5:30" — the toggles exist
    # precisely to override that, so both fields must go
    out = apply(
        _workout(warmup={"distance": 2000, "pace": "05:30"}, cooldown={"distance": 1000}),
        resolve(None),
    )
    assert out["warmup"] == {}
    assert out["cooldown"] == {}


def test_strip_toggles_are_independent():
    prefs = resolve({"wu_cd_lap_press": False})  # skip_pace stays default-on
    out = apply(_workout(warmup={"distance": 2000, "pace": "05:30"}), prefs)
    assert out["warmup"] == {"distance": 2000}


def test_intervals_are_untouched():
    workout = _workout(warmup={"distance": 2000})
    out = apply(workout, dict.fromkeys(KEYS, True))
    assert out["intervals"] == workout["intervals"]
    assert out["name"] == workout["name"]
