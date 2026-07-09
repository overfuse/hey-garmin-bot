import os

from openai import AsyncOpenAI, OpenAIError

from ..config import LLM_TIMEOUT_S
from ..errors import WorkoutAIConfigError
from ..models import Workout

NAME = "openai"
DEFAULT_MODEL = "gpt-4.1-mini"

MAX_TOKENS = 2000  # must match evals/models.py — truncation is a production bug

# gpt-4.1-mini is a chat model: temperature=0 + a fixed seed give near-deterministic
# output. (Reasoning models like o3/gpt-5 would need different params — they reject
# temperature/seed — so they are not handled by this chat-model path.)


async def plan(system_prompt: str, description: str, model: str) -> Workout:
    # Construction raises on a missing key — before any request is issued, which
    # is what lets the caller refund the quota unit for our misconfiguration.
    try:
        client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"), timeout=LLM_TIMEOUT_S)
    except OpenAIError as e:
        raise WorkoutAIConfigError(f"OpenAI client init failed: {e}") from e
    completion = await client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": description},
        ],
        max_tokens=MAX_TOKENS,
        seed=42,
        temperature=0,
        response_format=Workout,
    )
    message = completion.choices[0].message
    if message.parsed is None:  # refusal or truncation
        raise ValueError(f"Model did not return a structured workout: {message.refusal}")
    return message.parsed
