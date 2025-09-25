#!/usr/bin/env python3
"""
interval_to_garmin_schema.py  (no stepId)
----------------------------------------
Convert an *interval‑workout* JSON into a Garmin Connect workout payload **without
the optional `stepId` field** — Garmin accepts `stepOrder` alone.

Key characteristics (unchanged from prior version unless noted):
* **ExecutableStepDTO** for runs, recoveries, rests, warm‑up, cool‑down.
* **RepeatGroupDTO** for repeat containers.
* `stepType.stepTypeId` / `displayOrder` mapping:
  1 warm‑up, 2 cool‑down, 3 interval, 4 recovery, 5 rest, 6 repeat.
* Paced runs use `pace.zone` with **targetValueOne = fast bound**,
  **targetValueTwo = slow bound** (± 5 s/km converted to m/s).
* Comprehensive `endCondition`, `endConditionValue`, `preferredEndConditionUnit`.
* Every element has **stepOrder** (and **childStepId = 1** when inside a repeat).

Usage
```
python interval_to_garmin_schema.py interval.json garmin.json
```
Omit the second argument to print the converted JSON to *stdout*.
"""
from __future__ import annotations
import json
import sys
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Pace helpers
# ---------------------------------------------------------------------------

def pace_to_sec_per_km(pace: str) -> int:
    m, s = pace.split(":")
    return int(m) * 60 + int(s)

def sec_per_km_to_mps(sec: float) -> float:
    return 1000.0 / sec

def pace_window_mps(pace: str, delta: int = 5) -> Tuple[float, float]:
    base = pace_to_sec_per_km(pace)
    fast_sec, slow_sec = base - delta, base + delta
    if fast_sec <= 0:
        raise ValueError("Pace too fast for ±delta window")
    return sec_per_km_to_mps(fast_sec), sec_per_km_to_mps(slow_sec)

# ---------------------------------------------------------------------------
# Static blocks mirroring Garmin constants
# ---------------------------------------------------------------------------

UNIT_METER = {"unitId": 1, "unitKey": "meter", "factor": 100.0}

TARGET_NO = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
TARGET_PACE = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6}

END_DISTANCE = {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}
END_LAP      = {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}
END_TIME     = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
END_ITER     = {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": True}

STEP_META = {
    "warmup":  {"stepTypeId": 1, "stepTypeKey": "warmup",   "displayOrder": 1},
    "cooldown":{"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "interval":{"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery":{"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
    "rest":    {"stepTypeId": 5, "stepTypeKey": "rest",     "displayOrder": 5},
    "repeat":  {"stepTypeId": 6, "stepTypeKey": "repeat",   "displayOrder": 6}
}

# ---------------------------------------------------------------------------
# Builders (no stepId)
# ---------------------------------------------------------------------------

def exec_step(step_order: int, meta_key: str, *,
              distance: Optional[int] = None,
              pace: Optional[str] = None,
              rest: Optional[int] = None,
              child: bool = False) -> Dict[str, Any]:
    dto: Dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": step_order,
        "stepType": STEP_META[meta_key].copy(),
    }
    if child:
        dto["childStepId"] = 1

    # Target setup
    if meta_key == "interval":
        fast_mps, slow_mps = pace_window_mps(pace)
        dto["targetType"] = TARGET_PACE
        dto["targetValueOne"] = fast_mps   # high (fast)
        dto["targetValueTwo"] = slow_mps   # low (slow)
    else:
        dto["targetType"] = TARGET_NO

    # End conditions
    if meta_key in ("warmup", "cooldown"):
        dto.update({
            "endCondition": END_LAP,
            "endConditionValue": 0.0,
            "preferredEndConditionUnit": None
        })
    elif meta_key in ("interval", "recovery"):
        dto.update({
            "endCondition": END_DISTANCE,
            "endConditionValue": float(distance),
            "preferredEndConditionUnit": UNIT_METER,
            "durationType": {"workoutStepDurationTypeKey": "distance"},
            "durationValue": distance
        })
    elif meta_key == "rest":
        dto.update({
            "endCondition": END_TIME,
            "endConditionValue": float(rest),
            "preferredEndConditionUnit": None,
            "durationType": {"workoutStepDurationTypeKey": "time"},
            "durationValue": rest
        })
    else:
        raise ValueError(f"Unknown meta_key {meta_key}")

    return dto


def repeat_group(order: int, iterations: int, children: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "type": "RepeatGroupDTO",
        "stepOrder": order,
        "stepType": STEP_META["repeat"].copy(),
        "childStepId": 1,
        "numberOfIterations": iterations,
        "endCondition": END_ITER,
        "endConditionValue": float(iterations),
        "preferredEndConditionUnit": None,
        "skipLastRestStep": False,
        "smartRepeat": False,
        "workoutSteps": children
    }

# ---------------------------------------------------------------------------
# Converter core
# ---------------------------------------------------------------------------

def convert(interval_json: Dict[str, Any]) -> Dict[str, Any]:
    order = 0
    steps: List[Dict[str, Any]] = []

    # Warm‑up
    if "warmup" in interval_json:
        wu = interval_json["warmup"]
        order += 1
        steps.append(exec_step(order, "warmup", distance=wu.get("distance"), pace=wu.get("pace")))

    # Recursive element handling
    def make_step(elem: Dict[str, Any], *, nested=False) -> Dict[str, Any]:
        nonlocal order
        etype = elem["type"]
        if etype == "run":
            key = "interval" if "pace" in elem else "recovery"
            order += 1
            return exec_step(order, key, distance=elem["distance"], pace=elem.get("pace"), child=nested)
        if etype == "recovery":
            order += 1
            return exec_step(order, "recovery", distance=elem["distance"], child=nested)
        if etype == "rest":
            order += 1
            return exec_step(order, "rest", rest=elem["rest"], child=nested)
        if etype == "repeat":
            order += 1
            group_order = order
            child_steps: List[Dict[str, Any]] = []
            for c in elem["steps"]:
                child_steps.append(make_step(c, nested=True))
            return repeat_group(group_order, elem["repeat"], child_steps)
        raise ValueError(f"Unknown element type {etype}")

    for elem in interval_json.get("intervals", []):
        steps.append(make_step(elem))

    # Cool‑down
    if "cooldown" in interval_json:
        cd = interval_json["cooldown"]
        order += 1
        steps.append(exec_step(order, "cooldown", distance=cd.get("distance"), pace=cd.get("pace")))

    return {
        "workoutName": interval_json.get("name", "Converted Workout"),
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
                "workoutSteps": steps
            }
        ]
    }

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python garmin_convert.py interval.json [garmin.json]", file=sys.stderr)
        sys.exit(1)
    in_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    with open(in_path, "r", encoding="utf-8") as f:
        interval = json.load(f)
    garmin = convert(interval)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(garmin, f, indent=2)
    else:
        print(json.dumps(garmin, indent=2))

if __name__ == "__main__":
    main()
