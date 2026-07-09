"""Provider dispatch: free text in, validated workout dict out."""

import asyncio
from pathlib import Path

from . import config
from .errors import WorkoutAIConfigError
from .providers import REGISTRY

# One SYSTEM_PROMPT.md is shared by every provider. It lives at the repo root and
# is resolved relative to this package, so it loads regardless of the CWD.
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "SYSTEM_PROMPT.md"


def plan_to_json(description: str) -> dict:
    return asyncio.run(plan_to_json_async(description))


async def plan_to_json_async(description: str) -> dict:
    provider = REGISTRY.get(config.PROVIDER)
    if provider is None:
        raise WorkoutAIConfigError(
            f"Unknown WORKOUT_AI_PROVIDER={config.PROVIDER!r}; expected one of {sorted(REGISTRY)}"
        )

    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    workout = await provider.plan(
        system_prompt, description, config.MODEL or provider.DEFAULT_MODEL
    )
    return workout.model_dump(exclude_none=True)
