import asyncio
import os
import sys

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pathlib import Path

from workout_models import Workout

load_dotenv()
api_key = os.environ.get("OPENAI_API_KEY")

MODEL = "gpt-4.1-mini"


def plan_to_json(description: str) -> dict:
    return asyncio.run(_plan_to_json_impl(description))


async def plan_to_json_async(description: str) -> dict:
    return await _plan_to_json_impl(description)


async def _plan_to_json_impl(description: str) -> dict:
    client = AsyncOpenAI(api_key=api_key)
    system_prompt = Path("SYSTEM_PROMPT.md").read_text(encoding="utf-8")

    completion = await client.beta.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": description},
        ],
        max_tokens=1200,
        seed=42,
        temperature=0,
        response_format=Workout,
    )

    workout = completion.choices[0].message.parsed
    if workout is None:  # refusal or truncation
        refusal = completion.choices[0].message.refusal
        raise ValueError(f"Model did not return a structured workout: {refusal}")

    return workout.model_dump(exclude_none=True)


def read_stdin():
    print("Paste/type your workout. Press Ctrl-D (Unix) or Ctrl-Z (Windows) then Enter to finish:")
    return sys.stdin.read()


if __name__ == "__main__":
    content = sys.stdin.read()
    workout_json = plan_to_json(content)
    print(workout_json)
