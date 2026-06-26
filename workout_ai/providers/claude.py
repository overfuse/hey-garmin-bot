import os

from anthropic import AsyncAnthropic

from ..models import Workout

NAME = "claude"
DEFAULT_MODEL = "claude-haiku-4-5"

# Haiku uses extended thinking so it reliably handles arithmetic-heavy budgeting
# (e.g. "1 km in 200/200 mode" -> exactly five 200 m segments); without it Haiku
# gets that right only ~1/3 of the time. budget_tokens must be < max_tokens, and
# max_tokens has to leave room for both the thinking and the JSON output.
THINKING_BUDGET = 2000
MAX_TOKENS = 4000


async def plan(system_prompt: str, description: str, model: str) -> Workout:
    client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
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
