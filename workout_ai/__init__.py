import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from .errors import WorkoutAIConfigError
from .providers import REGISTRY

load_dotenv()

# Provider selection via env. Both API keys can live in .env; only the selected
# provider's key is needed at runtime.
#   WORKOUT_AI_PROVIDER  "claude" | "openai"   (default: claude)
#   WORKOUT_AI_MODEL     optional override of the provider's default model
PROVIDER = os.environ.get("WORKOUT_AI_PROVIDER", "openai").lower()
MODEL = os.environ.get("WORKOUT_AI_MODEL")

# One SYSTEM_PROMPT.md is shared by every provider. It lives at the repo root and
# is resolved relative to this package, so it loads regardless of the CWD.
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "SYSTEM_PROMPT.md"

__all__ = ["plan_to_json", "plan_to_json_async", "WorkoutAIConfigError"]


def plan_to_json(description: str) -> dict:
    return asyncio.run(plan_to_json_async(description))


async def plan_to_json_async(description: str) -> dict:
    provider = REGISTRY.get(PROVIDER)
    if provider is None:
        raise WorkoutAIConfigError(
            f"Unknown WORKOUT_AI_PROVIDER={PROVIDER!r}; expected one of {sorted(REGISTRY)}"
        )

    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    workout = await provider.plan(system_prompt, description, MODEL or provider.DEFAULT_MODEL)
    return workout.model_dump(exclude_none=True)
