"""Workout AI package: LLM providers, provider dispatch, and the concurrency gate.

Env configuration lives in config.py; provider dispatch in planner.py; the
global concurrency gate in gate.py. bot.py should call parse_plan (gated);
plan_to_json / plan_to_json_async are the ungated primitives for CLI/eval use.
"""

from .errors import LLMBusy, WorkoutAIConfigError
from .gate import parse_plan
from .planner import plan_to_json, plan_to_json_async

__all__ = [
    "LLMBusy",
    "WorkoutAIConfigError",
    "parse_plan",
    "plan_to_json",
    "plan_to_json_async",
]
