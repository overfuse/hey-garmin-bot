"""Per-user workout-structure preferences.

One place owns the preference catalog: the storage whitelist (user.py / the
webapp API validate against KEYS), the defaults, and the enforcement. The
stored document holds the FULL resolved dict, not a delta — what a user saved
is what they keep, even if the defaults change in a later release.

Enforcement is a POST-PARSE TRANSFORM, not a prompt directive, on purpose.
The first version appended "add a warmup section" instructions to the system
prompt and gpt-4.1-mini silently ignored them (the prompt's own "Skip any
optional property if no value is available" rule wins). Every one of these
preferences is a mechanical JSON edit — add an empty section, drop a key —
so the LLM has no business being in the loop: apply() is deterministic,
testable without a provider call, and leaves SYSTEM_PROMPT.md and the eval
baselines completely untouched.
"""

import copy

# Defaults are product policy, not neutrality: warmup/cooldown sections that
# end on the lap button and don't beep about pace match how these sections
# are actually run, so they start enabled. Auto-adding sections the athlete
# didn't ask for is more opinionated, so those start disabled.
DEFAULTS: dict[str, bool] = {
    "add_warmup": False,      # add a warmup even when the plan has none
    "add_cooldown": False,    # add a cooldown even when the plan has none
    "wu_cd_lap_press": True,  # drop warmup/cooldown distance; end on lap press
    "wu_cd_skip_pace": True,  # drop warmup/cooldown pace target
}

KEYS = frozenset(DEFAULTS)


def resolve(stored: dict | None) -> dict[str, bool]:
    """Merge a stored prefs dict over the defaults, dropping unknown keys."""
    prefs = dict(DEFAULTS)
    for key, value in (stored or {}).items():
        if key in KEYS:
            prefs[key] = bool(value)
    return prefs


def apply(workout_json: dict, prefs: dict[str, bool]) -> dict:
    """Enforce preferences on a parsed workout dict; returns a new dict.

    An empty `{}` section is meaningful downstream: garmin_convert turns a
    warmup/cooldown without distance into a lap-button-ended step, which is
    exactly what "always include warmup" should produce.

    add_* is literal: the section is added even when the description said to
    skip it — "always" means always, and deterministic beats clever. A user
    who wants a warmup-less day toggles it off (or deletes the step on the
    watch). The wu_cd_* strips likewise override an explicit "2km warmup @
    5:00" in the plan text — dropping a provided value is the only case where
    the toggle matters at all, since absent values are already absent.
    """
    out = copy.deepcopy(workout_json)
    for section in ("warmup", "cooldown"):
        if prefs.get(f"add_{section}") and section not in out:
            out[section] = {}
        body = out.get(section)
        if isinstance(body, dict):
            if prefs.get("wu_cd_lap_press"):
                body.pop("distance", None)
            if prefs.get("wu_cd_skip_pace"):
                body.pop("pace", None)
    return out
