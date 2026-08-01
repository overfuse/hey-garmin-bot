import os

from anthropic import AnthropicError, AsyncAnthropic, BadRequestError

from ..config import LLM_TIMEOUT_S
from ..errors import LLMQuotaExhausted, WorkoutAIConfigError
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


async def plan(system_prompt: str, description: str, model: str) -> Workout:
    # A missing key must surface as our misconfiguration BEFORE any request is
    # issued, so the caller can refund the quota unit. The SDK only raises a
    # TypeError at request-build time, so check explicitly instead.
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise WorkoutAIConfigError("ANTHROPIC_API_KEY is not set")
    try:
        client = AsyncAnthropic(api_key=api_key, timeout=LLM_TIMEOUT_S)
    except AnthropicError as e:
        raise WorkoutAIConfigError(f"Anthropic client init failed: {e}") from e
    try:
        message = await client.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET},
            system=system_prompt,
            messages=[{"role": "user", "content": description}],
            output_format=Workout,
        )
    except BadRequestError as e:
        # Anthropic has no dedicated error code for an empty balance — it comes
        # back as a 400 invalid_request_error whose message says "credit balance
        # is too low". Retag it so the caller doesn't blame the user's input.
        if "credit balance" in str(e).lower():
            raise LLMQuotaExhausted("Anthropic account out of credits") from e
        raise
    if message.parsed_output is None:  # refusal or truncation
        raise ValueError(f"Model did not return a structured workout: {message.stop_reason}")
    return message.parsed_output
