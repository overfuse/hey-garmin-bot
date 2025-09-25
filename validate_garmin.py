from __future__ import annotations

from typing import Any, Dict, List, Tuple


ALLOWED_STEP_KEYS = {"warmup", "cooldown", "interval", "recovery", "rest", "repeat"}


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except Exception:
        return False


def _validate_step(step: Dict[str, Any], path: str, errors: List[str]) -> None:
    if step.get("type") not in ("ExecutableStepDTO", "RepeatGroupDTO"):
        errors.append(f"{path}: unknown step 'type'={step.get('type')}")
        return

    step_type = (step.get("stepType") or {}).get("stepTypeKey")
    if step_type not in ALLOWED_STEP_KEYS:
        errors.append(f"{path}: invalid stepType.stepTypeKey={step_type}")

    if step["type"] == "RepeatGroupDTO":
        if step_type != "repeat":
            errors.append(f"{path}: RepeatGroupDTO must have stepTypeKey=repeat")
        if not isinstance(step.get("numberOfIterations"), int) or step["numberOfIterations"] < 1:
            errors.append(f"{path}: numberOfIterations must be int >= 1")
        children = step.get("workoutSteps")
        if not isinstance(children, list) or not children:
            errors.append(f"{path}: workoutSteps must be a non-empty list for RepeatGroupDTO")
            return
        for idx, child in enumerate(children, start=1):
            _validate_step(child, f"{path}.workoutSteps[{idx}]", errors)
        return

    # Executable steps
    end_condition = (step.get("endCondition") or {}).get("conditionTypeKey")
    if step_type in ("warmup", "cooldown"):
        if end_condition != "lap.button":
            errors.append(f"{path}: {step_type} must end by lap.button")
    elif step_type == "interval":
        if end_condition != "distance":
            errors.append(f"{path}: interval must end by distance")
        if not _is_number(step.get("endConditionValue")):
            errors.append(f"{path}: interval must have numeric endConditionValue")
        if (step.get("targetType") or {}).get("workoutTargetTypeKey") != "pace.zone":
            errors.append(f"{path}: interval must have targetType pace.zone")
        if not (_is_number(step.get("targetValueOne")) and _is_number(step.get("targetValueTwo"))):
            errors.append(f"{path}: interval must have numeric targetValueOne/Two")
    elif step_type == "recovery":
        if end_condition != "distance":
            errors.append(f"{path}: recovery must end by distance")
        if not _is_number(step.get("endConditionValue")):
            errors.append(f"{path}: recovery must have numeric endConditionValue")
    elif step_type == "rest":
        if end_condition != "time":
            errors.append(f"{path}: rest must end by time")
        if not _is_number(step.get("endConditionValue")):
            errors.append(f"{path}: rest must have numeric endConditionValue")


def validate_garmin_workout(payload: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings) for a Garmin workout payload."""
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(payload, dict):
        return (["Payload must be a JSON object"], warnings)

    if not payload.get("workoutName"):
        warnings.append("Missing workoutName")

    segments = payload.get("workoutSegments")
    if not isinstance(segments, list) or not segments:
        errors.append("workoutSegments must be a non-empty list")
        return (errors, warnings)

    for i, seg in enumerate(segments, start=1):
        steps = seg.get("workoutSteps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"workoutSegments[{i}].workoutSteps must be a non-empty list")
            continue
        # stepOrder monotonicity
        orders = [s.get("stepOrder") for s in steps]
        if any(not isinstance(o, int) for o in orders):
            warnings.append(f"workoutSegments[{i}]: stepOrder should be integers")
        if orders and any(orders[j] >= orders[j+1] for j in range(len(orders)-1)):
            warnings.append(f"workoutSegments[{i}]: stepOrder not strictly increasing")
        for j, s in enumerate(steps, start=1):
            _validate_step(s, f"workoutSegments[{i}].workoutSteps[{j}]", errors)

    return (errors, warnings)




