"""Eval cases drawn from the workouts we stress-tested this session.

Each case carries a list of (label, check) pairs. A check takes the parsed result
dict and returns True/False for one specific property — the properties are exactly
the failure modes we saw models trip on (dropped paces, mis-budgeted distances,
flaky rep counts, rest misplaced outside the repeat). Partial credit = fraction of
checks passed, so a model that gets structure right but a pace wrong still scores.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_PROMPTS = Path(__file__).resolve().parent.parent / "examples" / "prompts"


def _prompt(filename: str) -> str:
    """Load a real user prompt straight from examples/prompts/."""
    return (_PROMPTS / filename).read_text(encoding="utf-8").strip()


@dataclass
class Case:
    name: str
    prompt: str
    expected: str  # human-readable, shown in the report header
    checks: list[tuple[str, Callable[[dict], bool]]]


# --- helpers to read the result dict defensively ----------------------------

def _intervals(r):
    return r.get("intervals", [])


def _warmup(r):
    return (r.get("warmup") or {}).get("distance")


def _cooldown(r):
    return (r.get("cooldown") or {}).get("distance")


def _repeats(r):
    return [e for e in _intervals(r) if e.get("type") == "repeat"]


def _flat_steps(r):
    """Every step, flattening repeat groups one level (workouts never nest)."""
    out = []
    for e in _intervals(r):
        if e.get("type") == "repeat":
            out.extend(e.get("steps", []))
        else:
            out.append(e)
    return out


# --- Case 1: layered slashes + pace-annotation lines -------------------------
# "4000/500/2000/500/4000" is the skeleton; the following lines annotate paces.
# Correct = 5 flat run segments, no repeat, no duplication.
C1 = Case(
    name="layered-slashes",
    prompt=(
        "2 км разминка\n"
        "4000/500/2000/500/4000\n"
        "4 км в темпе 4:20\n"
        "500м ближе к 5:20\n"
        "2 км в темпе 4:00\n"
        "2 км заминка"
    ),
    expected="wu 2000; 5 runs [4000@4:20,500@5:20,2000@4:00,500@5:20,4000@4:20]; cd 2000",
    checks=[
        ("warm/cool", lambda r: _warmup(r) == 2000 and _cooldown(r) == 2000),
        ("5 runs, no repeat", lambda r: len(_intervals(r)) == 5
            and all(e.get("type") == "run" for e in _intervals(r))),
        ("distances", lambda r: [e.get("distance") for e in _intervals(r)]
            == [4000, 500, 2000, 500, 4000]),
        ("paces", lambda r: [e.get("pace") for e in _intervals(r)]
            == ["04:20", "05:20", "04:00", "05:20", "04:20"]),
    ],
)


# --- Case 2: subdivision inside a repeat + on/off recovery -------------------
C2 = Case(
    name="subdivide+strides",
    prompt=(
        "3 км разминки с прогрессией в темпе\n"
        "6 раз по 1500м через 2 мин постоять/походить\n"
        "1000м начинаешь в темпе 4:00 и заключительные 500м в темпе 3:45\n\n"
        "После 5 раз 100/100 свободно ноги разбегать\n\n"
        "3 км заминка"
    ),
    expected="wu/cd 3000; 6x[1000@4:00,500@3:45,rest120]; 5x[run100,recovery100]",
    checks=[
        ("warm/cool", lambda r: _warmup(r) == 3000 and _cooldown(r) == 3000),
        ("1500 split (1000+500)", lambda r: any(
            g.get("repeat") == 6
            and any(s.get("distance") == 1000 and s.get("pace") == "04:00" for s in g["steps"])
            and any(s.get("distance") == 500 and s.get("pace") == "03:45" for s in g["steps"])
            and not any(s.get("distance") == 1500 for s in g["steps"])
            for g in _repeats(r))),
        ("rest 120 in repeat", lambda r: any(
            g.get("repeat") == 6
            and any(s.get("type") == "rest" and s.get("rest") == 120 for s in g["steps"])
            for g in _repeats(r))),
        ("5x 100/100 recovery", lambda r: any(
            g.get("repeat") == 5 and len(g["steps"]) == 2
            and g["steps"][0].get("distance") == 100
            and g["steps"][1].get("distance") == 100
            and g["steps"][1].get("type") == "recovery"
            for g in _repeats(r))),
    ],
)


# --- Case 3: distance-budgeted alternation + fast/slow paces (two blocks) ----
def _seg200(r):
    return [s for s in _flat_steps(r) if s.get("type") == "run" and s.get("distance") == 200]


C3 = Case(
    name="200/200-budget",
    prompt=(
        "2 км разминка\n"
        "3 км в темпе 4:00\n"
        "1 км в режиме 200/200\n"
        "Быстрые по 3:30\n"
        "Медленные по 5:00\n"
        "3 км в темпе 4:00\n"
        "1 км в режиме 200/200\n"
        "Быстрые по. 3:30\n"
        "Медленные по 5:00\n"
        "2 км заминка"
    ),
    expected="wu/cd 2000; two 3000@4:00; two 1km blocks = 5x200 (3 fast@3:30 + 2 slow@5:00)",
    checks=[
        ("warm/cool", lambda r: _warmup(r) == 2000 and _cooldown(r) == 2000),
        ("two 3000@4:00", lambda r: sum(
            1 for e in _intervals(r)
            if e.get("type") == "run" and e.get("distance") == 3000 and e.get("pace") == "04:00") == 2),
        ("budget: 10x200 (=1000m/block)", lambda r: len(_seg200(r)) == 10),
        ("slow pace kept (3:30/5:00)", lambda r: len(_seg200(r)) > 0
            and all(s.get("pace") in ("03:30", "05:00") for s in _seg200(r))),
    ],
)


# --- Case 4: explicit rep count + rest between sets (examples/hey-track-07-22) -
# Tests reliability on a large EXPLICIT count ("Repeat 10 times") — the failure
# mode where reasoning models occasionally returned 9 instead of 10.
C4 = Case(
    name="explicit-10x+rest",
    prompt=_prompt("hey-track-07-22.txt"),
    expected="wu 3000, cd 2000; 10x[300@3:30, recovery100, 200@3:20, rest90]",
    checks=[
        ("warm/cool", lambda r: _warmup(r) == 3000 and _cooldown(r) == 2000),
        ("repeat == 10", lambda r: any(g.get("repeat") == 10 for g in _repeats(r))),
        ("4 steps, rest inside", lambda r: any(
            g.get("repeat") == 10 and len(g["steps"]) == 4
            and g["steps"][0].get("distance") == 300 and g["steps"][0].get("pace") == "03:30"
            and g["steps"][1].get("distance") == 100 and g["steps"][1].get("type") == "recovery"
            and g["steps"][2].get("distance") == 200 and g["steps"][2].get("pace") == "03:20"
            and g["steps"][3].get("type") == "rest" and g["steps"][3].get("rest") == 90
            for g in _repeats(r))),
    ],
)


# --- Case 5: real set with time-based recovery + ambiguous count (09-30) -----
# From examples/hey-track-09-30.txt. New dimensions vs the others: an active jog
# recovery given by time ("200m active jog 60-70 sec"), an ambiguous rep count
# ("do 4-5 of them"), and a time rest "between sets". Distinct from C4 (which has
# distance-based recovery and a single EXACT count).
C5 = Case(
    name="real-set-09-30",
    prompt=_prompt("hey-track-09-30.txt"),
    expected="wu 3000, cd 1000; 4-5x[1600@4:00, recovery 200, 400@3:30, rest 120]",
    checks=[
        ("warm/cool", lambda r: _warmup(r) == 3000 and _cooldown(r) == 1000),
        ("repeat 4 or 5", lambda r: any(g.get("repeat") in (4, 5) for g in _repeats(r))),
        ("paced runs 1600@4:00 & 400@3:30", lambda r: any(
            g.get("repeat") in (4, 5)
            and any(s.get("distance") == 1600 and s.get("pace") == "04:00" for s in g["steps"])
            and any(s.get("distance") == 400 and s.get("pace") == "03:30" for s in g["steps"])
            for g in _repeats(r))),
        ("jog recovery 200 + rest 120 in set", lambda r: any(
            g.get("repeat") in (4, 5)
            and any(s.get("type") == "recovery" and s.get("distance") == 200 for s in g["steps"])
            and any(s.get("type") == "rest" and s.get("rest") == 120 for s in g["steps"])
            for g in _repeats(r))),
    ],
)


CASES = [C1, C2, C3, C4, C5]
