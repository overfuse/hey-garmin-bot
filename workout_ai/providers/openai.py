import os

from openai import AsyncOpenAI

from ..models import Workout

NAME = "openai"
DEFAULT_MODEL = "gpt-4.1-mini"

# gpt-4.1-mini is a chat model: temperature=0 + a fixed seed give near-deterministic
# output. (Reasoning models like o3/gpt-5 would need different params — they reject
# temperature/seed — so they are not handled by this chat-model path.)


async def plan(system_prompt: str, description: str, model: str) -> Workout:
    client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    completion = await client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": description},
        ],
        max_tokens=1200,
        seed=42,
        temperature=0,
        response_format=Workout,
    )
    message = completion.choices[0].message
    if message.parsed is None:  # refusal or truncation
        raise ValueError(f"Model did not return a structured workout: {message.refusal}")
    return message.parsed
