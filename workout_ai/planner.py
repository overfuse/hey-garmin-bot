"""Provider dispatch: free text in, validated workout dict out."""

import asyncio
from pathlib import Path

from . import config
from .errors import WorkoutAIConfigError
from .providers import REGISTRY

# Two prompt variants live at the repo root, resolved relative to this package so
# they load regardless of the CWD. SYSTEM_PROMPT.md is the full version tuned for
# chat models; SYSTEM_PROMPT_REASONING.md is the condensed one for reasoning
# models. A provider opts its model into the short one by exposing
# wants_reasoning_prompt(model); absent that, the full prompt is used.
_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_PATH = _ROOT / "SYSTEM_PROMPT.md"
_REASONING_PROMPT_PATH = _ROOT / "SYSTEM_PROMPT_REASONING.md"


def load_system_prompt(provider, model: str) -> str:
    wants = getattr(provider, "wants_reasoning_prompt", None)
    path = _REASONING_PROMPT_PATH if wants and wants(model) else _PROMPT_PATH
    return path.read_text(encoding="utf-8")


def plan_to_json(description: str) -> dict:
    return asyncio.run(plan_to_json_async(description))


async def plan_to_json_async(description: str) -> dict:
    provider = REGISTRY.get(config.PROVIDER)
    if provider is None:
        raise WorkoutAIConfigError(
            f"Unknown WORKOUT_AI_PROVIDER={config.PROVIDER!r}; expected one of {sorted(REGISTRY)}"
        )

    model = config.MODEL or provider.DEFAULT_MODEL
    workout = await provider.plan(load_system_prompt(provider, model), description, model)
    return workout.model_dump(exclude_none=True)
