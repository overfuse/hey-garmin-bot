import os

from anthropic import AsyncAnthropic

from ..models import Workout

NAME = "claude"
DEFAULT_MODEL = "claude-haiku-4-5"

# Haiku uses extended thinking so it reliably handles arithmetic-heavy budgeting
# (e.g. "1 km in 200/200 mode" -> exactly five 200 m segments); without it Haiku
# gets that right only ~1/3 of the time. max_tokens caps thinking + visible output
# combined, so it needs generous headroom above budget_tokens or a long thinking
# pass truncates the JSON (stop_reason "max_tokens"). Billing is by actual tokens
# used, not the cap, so the headroom is free insurance.
THINKING_BUDGET = 2000
MAX_TOKENS = 8000
TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "45"))


async def plan(system_prompt: str, description: str, model: str) -> Workout:
    client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), timeout=TIMEOUT_S)
    message = await client.messages.parse(
        model=model,
        max_tokens=MAX_TOKENS,
        thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET},
        system=system_prompt,
        messages=[{"role": "user", "content": description}],
        output_format=Workout,
    )
    if message.parsed_output is None:  # refusal or truncation
        raise ValueError(f"Model did not return a structured workout: {message.stop_reason}")
    return message.parsed_output
