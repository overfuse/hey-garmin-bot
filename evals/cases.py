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


def _unrolled(r):
    """Every step in execution order, repeat groups unrolled `repeat` times."""
    out = []
    for e in _intervals(r):
        if e.get("type") == "repeat":
            for _ in range(e.get("repeat") or 0):
                out.extend(e.get("steps", []))
        else:
            out.append(e)
    return out


def _pair_groups(r, n, a, b):
    """Repeat groups of exactly n × [a m run, b m paceless recovery]."""
    return [g for g in _repeats(r) if g.get("repeat") == n and len(g["steps"]) == 2
            and g["steps"][0].get("type") == "run" and g["steps"][0].get("distance") == a
            and g["steps"][1].get("type") == "recovery" and g["steps"][1].get("distance") == b]


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
        ("5x[run100, recovery100]", lambda r: len(_pair_groups(r, 5, 100, 100)) == 1),
    ],
)


# --- Case 3: distance-budgeted pair + fast/slow paces (two blocks) ----------
# "1 км в режиме 200/200" = 1000 m of 200/200 pairs. The slow leg has its own
# pace, so it is a paced run, not a recovery; 1000 is 2.5 pairs →
# repeat 2 × [200 run @ 3:30, 200 run @ 5:00] + one trailing 200 run @ 3:30.
def _paced_pair_groups(r, n, a, b, pa, pb):
    """Repeat groups of exactly n × [a m run @ pa, b m run @ pb]."""
    return [g for g in _repeats(r) if g.get("repeat") == n and len(g["steps"]) == 2
            and (g["steps"][0].get("type"), g["steps"][0].get("distance"), g["steps"][0].get("pace"))
            == ("run", a, pa)
            and (g["steps"][1].get("type"), g["steps"][1].get("distance"), g["steps"][1].get("pace"))
            == ("run", b, pb)]


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
    expected="wu/cd 2000; two 3000@4:00; two 1km blocks = 2x[200@3:30, 200@5:00] + 200@3:30",
    checks=[
        ("two 3000@4:00", lambda r: sum(
            1 for e in _intervals(r)
            if e.get("type") == "run" and e.get("distance") == 3000 and e.get("pace") == "04:00") == 2),
        ("budget: 2×1000 m of 200s", lambda r: sum(
            s.get("distance") or 0 for s in _unrolled(r) if s.get("distance") == 200) == 2000),
        ("pairs: 2x[200@3:30, 200@5:00] ×2", lambda r: len(
            _paced_pair_groups(r, 2, 200, 200, "03:30", "05:00")) == 2),
        ("trailing 200 @3:30", lambda r: sum(
            1 for e in _intervals(r)
            if e.get("type") == "run" and e.get("distance") == 200 and e.get("pace") == "03:30") == 2),
    ],
)


# --- Case 5: real set with time-based recovery + ambiguous count (09-30) -----
# From examples/hey-track-09-30.txt. New dimensions vs the others: an active jog
# recovery given by time ("200m active jog 60-70 sec"), an ambiguous rep count
# ("do 4-5 of them"), and a time rest "between sets" that must stay inside the
# repeat.
C5 = Case(
    name="real-set-09-30",
    prompt=_prompt("hey-track-09-30.txt"),
    expected="wu 3000, cd 1000; 4-5x[1600@4:00, recovery 200, 400@3:30, rest 120]",
    checks=[
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


# --- Case 6: exercise blocks between easy runs + "N раз X/Y" rep count -------
# Three failure modes seen live with this exact workout: (a) the four strength
# lines were dropped wholesale (no schema slot for them pre-BreakStep), (b) the
# interior easy 3 km / 2 km vanished once the first/last easy runs were claimed
# by warmup/cooldown, and (c) "5 раз 200/200" was parsed via the distance-budget
# rule (flat 5 runs / 4 recoveries = 1800 m) instead of as a rep count.
def _is_easy(e, dist):
    """A pace-less easy segment of `dist` (recovery, or run without pace)."""
    return (e.get("distance") == dist and e.get("type") in ("recovery", "run")
            and not e.get("pace"))


def _easy(r, dist):
    return [e for e in _intervals(r) if _is_easy(e, dist)]


def _c6_body(r):
    """Intervals, minus an accepted trailing easy-1km stand-in for cooldown.

    gpt-4.1-mini stably emits the closing "1 км легко" as a trailing pace-less
    run instead of `cooldown`. On the watch the two are the same step minus the
    label, so the checks accept either — but only when `cooldown` is absent, so
    a model that emits BOTH (duplicating the kilometre) still fails.
    """
    seq = list(_intervals(r))
    if seq and not _cooldown(r) and _is_easy(seq[-1], 1000):
        seq = seq[:-1]
    return seq


def _c6_cooldown_ok(r):
    return _cooldown(r) == 1000 or (
        not _cooldown(r) and bool(_intervals(r)) and _is_easy(_intervals(r)[-1], 1000))


def _c6_order(r):
    """break, break, easy 3000, break, break, easy 2000, repeat — exactly."""
    seq = _c6_body(r)
    types = [e.get("type") for e in seq]
    return (len(seq) == 7
            and types[0] == types[1] == types[3] == types[4] == "break"
            and seq[2].get("distance") == 3000
            and seq[5].get("distance") == 2000
            and types[6] == "repeat")


C6 = Case(
    name="breaks-between-runs",
    prompt=(
        "3 км легко\n"
        "30 лягушек вперед\n"
        "30 выпрыгиваний из глубокого приседа вверх\n"
        "3 км легко\n"
        "30 лягушек вперед\n"
        "30 выпрыгиваний из глубокого приседа вверх\n"
        "2 км легко\n"
        "5 раз 200/200 на ритм\n"
        "1 км легко"
    ),
    expected="wu 3000; break×2; easy 3000; break×2; easy 2000; 5x[run200,recovery200]; cd 1000 (or trailing easy 1km)",
    checks=[
        ("warm/cool", lambda r: _warmup(r) == 3000 and _c6_cooldown_ok(r)),
        ("4 break steps", lambda r: sum(
            1 for e in _intervals(r) if e.get("type") == "break") == 4),
        ("interior 3km & 2km kept", lambda r: len(_easy(r, 3000)) == 1
            and len(_easy(r, 2000)) == 1),
        # The work leg must stay 200 — a past flake let it absorb the
        # preceding "2 км" (run 2000 + recovery 200).
        ("5x[run200, recovery200]", lambda r: len(_pair_groups(r, 5, 200, 200)) == 1),
        ("order", _c6_order),
    ],
)


# --- Case 7: unitless km skeleton, fewer annotations than segments -----------
# Real failure (2026-08): "6/2/6/4/6/2" is a 6-segment skeleton in km; the three
# annotation lines cover the three DISTINCT distances. gpt-4.1-mini emitted one
# step per annotation line (6+2+4 = 12 km) instead of one per skeleton segment
# (26 km) — the annotation count won over the skeleton count. Also covers the
# easier explicit-metre skeleton (`4000/500/2000/500/4000`), which needs the
# same segment↔annotation binding without the unit inference.
C7 = Case(
    name="unitless-km-skeleton",
    prompt=(
        "6/2/6/4/6/2\n"
        "6 км в темпе 4:50\n"
        "2 км в темпе 4:00\n"
        "4 км в темпе 4:15"
    ),
    expected="6 runs [6000@4:50,2000@4:00,6000@4:50,4000@4:15,6000@4:50,2000@4:00] = 26 km",
    checks=[
        ("6 runs, no repeat", lambda r: len(_intervals(r)) == 6
            and all(e.get("type") == "run" for e in _intervals(r))),
        ("distances (26 km)", lambda r: [e.get("distance") for e in _intervals(r)]
            == [6000, 2000, 6000, 4000, 6000, 2000]),
        ("paces", lambda r: [e.get("pace") for e in _intervals(r)]
            == ["04:50", "04:00", "04:50", "04:15", "04:50", "04:00"]),
    ],
)


# --- Case 8: pace ceiling + long rep count with standing rest ----------------
# Real workout (2026-08). Three distinct things to get right: "чуть из 4 мин" is
# a CEILING, so the emitted pace must be 03:55 and not 03:59/04:00 (the ±5 s
# window in garmin_convert then closes exactly at 4:00 instead of straddling
# it); the 400 m rep is a paced RUN, not a recovery, even though the only other
# step in the group is rest; and "постоять просто" is passive rest in seconds,
# not a jog. The chatter line exists to be ignored.
def _c8_steps(r):
    for g in _repeats(r):
        if g.get("repeat") == 25:
            return g.get("steps", [])
    return []


C8 = Case(
    name="pace-ceiling-25x400",
    prompt=(
        "2 км разминка\n"
        "25 раз по 400м в темпе чуть из 4 мин\n"
        "Отдых между 40-45 сек постоять просто\n"
        "Тренировка скучная, но она нужна\n"
        "2 км заминка"
    ),
    expected="wu/cd 2000; 25x[400 run @03:55, rest 40-45s]",
    checks=[
        ("repeat == 25", lambda r: any(g.get("repeat") == 25 for g in _repeats(r))),
        ("400 m is a run, not recovery", lambda r: any(
            s.get("type") == "run" and s.get("distance") == 400 for s in _c8_steps(r))),
        ("pace ceiling → 03:55", lambda r: any(
            s.get("distance") == 400 and s.get("pace") == "03:55" for s in _c8_steps(r))),
        # "40-45 сек" is a genuine range and models land anywhere inside it
        # (40, 42 and 45 all seen). Pinning one value would measure the
        # mixture ratio, not correctness.
        ("standing rest 40-45 s inside", lambda r: any(
            s.get("type") == "rest" and isinstance(s.get("rest"), int)
            and 40 <= s["rest"] <= 45 for s in _c8_steps(r))),
    ],
)


# --- Case 9: rep-counted pair w/ single pace + time-based walk/jog recovery --
# Real workout (2026-09). Two flaky failure modes on luna: (a) "3 раза 200/200 в
# темпе 4:00" — the single pace was bound to BOTH legs (run@4:00 + run@4:00)
# instead of the work leg only (run@4:00 + pace-less recovery); (b) "5 мин
# шаг/трусца" — a time-based walk/jog with no pace was read as a time-based run
# and dropped (rule "no pace → skip that step") instead of becoming rest 300.
def _c9_pair_groups(r):
    return [g for g in _repeats(r) if g.get("repeat") == 3
            and len(g["steps"]) == 2
            and g["steps"][0].get("distance") == 200
            and g["steps"][1].get("distance") == 200]


C9 = Case(
    name="rep-pair-single-pace+walk-jog",
    prompt=(
        "3 км разминка с прогрессией\n"
        "3 раза 200/200 в темпе 4:00\n"
        "6 км в темпе 4:30-4:35\n"
        "5 мин шаг/трусца\n"
        "3 раза 200/200 в темпе 4:00\n"
        "1 км заминка"
    ),
    expected="wu 3000, cd 1000; two 3x[200@4:00, recovery200]; 6000@4:30; rest 300 between the two sets",
    checks=[
        ("two 3x200/200 groups", lambda r: len(_c9_pair_groups(r)) == 2),
        ("work leg run@4:00, recovery leg pace-less", lambda r: len(_c9_pair_groups(r)) > 0
            and all(g["steps"][0].get("type") == "run" and g["steps"][0].get("pace") == "04:00"
                    and g["steps"][1].get("type") == "recovery" and not g["steps"][1].get("pace")
                    for g in _c9_pair_groups(r))),
        ("6 km @ 4:30 kept", lambda r: any(
            e.get("type") == "run" and e.get("distance") == 6000
            and e.get("pace") in ("04:30", "04:35") for e in _intervals(r))),
        ("5 min walk/jog kept as rest 300", lambda r: any(
            e.get("type") == "rest" and e.get("rest") == 300 for e in _flat_steps(r))),
    ],
)


# --- Case 10: unitless km skeleton whose annotation is a distance budget -----
# Real workout (2026-09). "2/3/2" is a km skeleton; "2 км в режиме 200/200" is a
# budget annotation that must expand EACH 2 km segment in place into repeat
# 5 × [200 run, 200 recovery] (2000 ÷ 400). Failure modes seen on luna: the
# second 2 left as a single 2000 m step (or dropped with the 3 km), wrong
# segment counts, and the first leg keeping the 2000 m block total.
C10 = Case(
    name="km-skeleton+budget",
    prompt=(
        "3 км разминка/заминка\n\n"
        "2/3/2\n"
        "2 км в режиме 200/200 свободные ускорения\n"
        "3 км в темпе 4:30"
    ),
    expected="wu/cd 3000; 5x[run200, recovery200] → 3000@4:30 → 5x[run200, recovery200]",
    checks=[
        ("warm/cool", lambda r: _warmup(r) == 3000 and _cooldown(r) == 3000),
        ("3000 @ 4:30 kept", lambda r: any(
            e.get("type") == "run" and e.get("distance") == 3000 and e.get("pace") == "04:30"
            for e in _intervals(r))),
        ("two 5x[run200, recovery200]", lambda r: len(_pair_groups(r, 5, 200, 200)) == 2),
        ("order: 5 pairs, 3000, 5 pairs", lambda r: [s.get("distance") for s in _unrolled(r)]
            == [200] * 10 + [3000] + [200] * 10),
    ],
)


# --- Case 12: minutes skeleton + pace range + timed warmup/cooldown ----------
# Real workout (2026-10). The old prompt converted every timed step to a
# distance using the pace (15 мин @ 4:28 → 3360 m), dropped the 10-minute
# warmup/cooldown (no slot for a time) and collapsed "4:25-4:30" to 04:28.
# "15/10/5" is a skeleton in MINUTES; "через 5 мин между" goes only between
# segments and keeps its explicit 5:30 pace.
def _c12_timed(r):
    return [(e.get("type"), e.get("duration"), e.get("pace"), e.get("pace_slow"))
            for e in _intervals(r)]


C12 = Case(
    name="minutes-skeleton+pace-range",
    prompt=(
        "10 мин разминка\n"
        "15/10/5\n"
        "Все через 5 мин между в темпе около 5:30\n"
        "15 мин, 10 мин и 5 мин в темпе около 4:25-4:30\n"
        "10 мин заминка"
    ),
    expected="wu/cd 600 s; run 900/300/600/300/300 s, work @04:25–04:30, between @05:30; no distances",
    checks=[
        ("warm/cool 600 s", lambda r: (r.get("warmup") or {}).get("duration") == 600
            and (r.get("cooldown") or {}).get("duration") == 600),
        ("durations 900/300/600/300/300", lambda r: [e.get("duration") for e in _intervals(r)]
            == [900, 300, 600, 300, 300]),
        ("no derived distances", lambda r: not any(s.get("distance") for s in _flat_steps(r))
            and not _warmup(r) and not _cooldown(r)),
        ("work legs keep the 4:25-4:30 range", lambda r: [t[2:] for t in _c12_timed(r)][0::2]
            == [("04:25", "04:30")] * 3),
        ("between legs @5:30", lambda r: [t[2] for t in _c12_timed(r)][1::2] == ["05:30"] * 2),
    ],
)

# --- Case 13: timed reps inside a repeat, then a distance step ---------------
# C12 covers timed steps only as flat segments. Here the timed work leg and its
# timed recovery live INSIDE a repeat, with a pace range on the work leg, and a
# distance step follows — so time and distance must coexist without either
# leaking into the other. "2 мин трусцой" is a paceless timed jog: the prompt
# says passive rest, the schema also has a timed recovery; both end on time on
# the watch, so either passes.
def _c13_steps(r):
    for g in _repeats(r):
        if g.get("repeat") == 6:
            return g.get("steps", [])
    return []


C13 = Case(
    name="timed-reps+distance",
    prompt=(
        "2 км разминка\n"
        "6 раз по 3 мин в темпе 4:00-4:05 через 2 мин трусцой\n"
        "Потом 2 км в темпе 4:20\n"
        "2 км заминка"
    ),
    expected="wu/cd 2000; 6x[run 180 s @04:00–04:05, 120 s rest/recovery]; 2000@4:20",
    checks=[
        ("repeat == 6, 2 steps", lambda r: len(_c13_steps(r)) == 2),
        ("work leg 180 s @ 4:00-4:05, no distance", lambda r: bool(_c13_steps(r))
            and (_c13_steps(r)[0].get("type"), _c13_steps(r)[0].get("duration"),
                 _c13_steps(r)[0].get("pace"), _c13_steps(r)[0].get("pace_slow"),
                 _c13_steps(r)[0].get("distance"))
            == ("run", 180, "04:00", "04:05", None)),
        ("120 s recovery inside", lambda r: len(_c13_steps(r)) == 2 and (
            _c13_steps(r)[1] == {"type": "rest", "rest": 120}
            or _c13_steps(r)[1] == {"type": "recovery", "duration": 120})),
        ("then 2000 @ 4:20", lambda r: [e.get("type") for e in _intervals(r)] == ["repeat", "run"]
            and _intervals(r)[1].get("distance") == 2000
            and _intervals(r)[1].get("pace") == "04:20"),
    ],
)


# --- Case 14: not a workout --------------------------------------------------
# Structured output forces a Workout for every message, so chatter can't be
# refused — the model either invents steps or returns an empty one. The only
# acceptable outputs are an empty workout or a refusal (which the runner scores
# as ERROR). Anything else is a fabricated workout uploaded to the user's watch.
C14 = Case(
    name="not-a-workout",
    prompt="Привет! Сегодня не бегу, колено побаливает после вчерашних 15 км. Что посоветуешь?",
    expected="no steps: intervals empty, no warmup/cooldown",
    checks=[
        ("no intervals", lambda r: not _intervals(r)),
        ("no warmup/cooldown", lambda r: not r.get("warmup") and not r.get("cooldown")),
    ],
)

# --- Case 15: unitless 3-part skeleton of two budgeted pairs, both legs paced -
# Seen live (2026-10-26): every slow leg came back as a paceless recovery
# because the prompt said to drop a slow leg's pace. The athlete wants both legs
# paced, in the order the description states them (slow first here). A 1/3 flake also expanded "6/1/6" into two 1/1 blocks plus the 500s.
C15 = Case(
    name="skeleton+paced-pairs",
    prompt=(
        "2 км легкая разминка\n"
        "6/1/6\n"
        "6 км в режиме 1/1\n"
        "1 км медленно по 4:40\n"
        "1 км быстро по 4:00\n\n"
        "1 км между шестерками легко\n\n"
        "6 км в режиме 500/500\n"
        "500м медленные по 4:30\n"
        "500м быстрые по 3:50\n\n"
        "2 км заминка"
    ),
    expected="wu/cd 2000; 3x[1000@4:40, 1000@4:00] → easy 1000 → 6x[500@4:30, 500@3:50]",
    checks=[
        ("3x[1000@4:40, 1000@4:00] (stated order)", lambda r: len(
            _paced_pair_groups(r, 3, 1000, 1000, "04:40", "04:00")) == 1),
        ("6x[500@4:30, 500@3:50] (stated order)", lambda r: len(
            _paced_pair_groups(r, 6, 500, 500, "04:30", "03:50")) == 1),
        ("shape: repeat, easy 1000, repeat", lambda r: len(_intervals(r)) == 3
            and _is_easy(_intervals(r)[1], 1000)),
        ("wu/cd 2000", lambda r: _warmup(r) == 2000 and _cooldown(r) == 2000),
    ],
)

CASES = [C2, C3, C5, C6, C7, C8, C9, C10, C12, C13, C14, C15]
