import os

from openai import AsyncOpenAI, OpenAIError, RateLimitError

from ..config import LLM_TIMEOUT_S
from ..errors import LLMQuotaExhausted, WorkoutAIConfigError
from ..models import Workout

NAME = "openai"
DEFAULT_MODEL = "gpt-5.6-luna"

# Chat models (gpt-4 family): temperature=0 + a fixed seed give near-deterministic
# output. Reasoning models (gpt-5*/o*, incl. gpt-5.6-luna) reject temperature/seed
# and take reasoning_effort + max_completion_tokens instead; the cap covers hidden
# reasoning tokens plus the visible JSON, so it needs generous headroom (billing is
# by actual use, not the cap). evals/models.py imports all three constants —
# truncation drift between eval and production is a production bug.
MAX_TOKENS = 2000
REASONING_MAX_TOKENS = 8000
REASONING_EFFORT = "medium"

_CHAT_PREFIXES = ("gpt-4",)


def _is_chat_model(model: str) -> bool:
    return model.startswith(_CHAT_PREFIXES)


def wants_reasoning_prompt(model: str) -> bool:
    """Reasoning models get the condensed SYSTEM_PROMPT_REASONING.md (the full
    prompt's repetition and emphasis are band-aids for chat-model arithmetic)."""
    return not _is_chat_model(model)


async def plan(system_prompt: str, description: str, model: str) -> Workout:
    # Construction raises on a missing key — before any request is issued, which
    # is what lets the caller refund the quota unit for our misconfiguration.
    try:
        client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"), timeout=LLM_TIMEOUT_S)
    except OpenAIError as e:
        raise WorkoutAIConfigError(f"OpenAI client init failed: {e}") from e
    if _is_chat_model(model):
        params = dict(max_tokens=MAX_TOKENS, seed=42, temperature=0)
    else:
        params = dict(
            max_completion_tokens=REASONING_MAX_TOKENS, reasoning_effort=REASONING_EFFORT
        )
    try:
        completion = await client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": description},
            ],
            response_format=Workout,
            **params,
        )
    except RateLimitError as e:
        # OpenAI reuses 429 for two very different things: a transient RPM/TPM
        # limit (retryable, stays a RateLimitError) and an exhausted account
        # balance (`insufficient_quota` / `credit_balance_exhausted`), which no
        # retry will ever fix and which must not be blamed on the user's input.
        markers = f"{e.code or ''} {e.type or ''}"
        if any(m in markers for m in ("insufficient_quota", "credit", "billing")):
            raise LLMQuotaExhausted(f"OpenAI account out of credits: {e.code}") from e
        raise
    message = completion.choices[0].message
    if message.parsed is None:  # refusal or truncation
        raise ValueError(f"Model did not return a structured workout: {message.refusal}")
    return message.parsed
