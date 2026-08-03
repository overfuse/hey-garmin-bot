"""Models under test and how to call each family.

Providers differ in call shape, so there is one runner per family:
  - openai chat      (gpt-4.1-mini):      temperature/seed, max_tokens
  - openai reasoning (o3/gpt-5/gpt-5.6):  reasoning_effort, max_completion_tokens, no temp/seed
  - anthropic        (haiku/sonnet): extended thinking, max_tokens
  - gemini (openai-compatible):      OpenAI SDK pointed at Google's endpoint

Each runner returns the parsed result as a dict (exclude_none), or raises on a
refusal/truncation. A model is only run if its api_key_env var is set, so adding
a provider you don't have a key for is harmless (it's skipped).
"""

import os
from dataclasses import dataclass
from typing import Awaitable, Callable

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from workout_ai.models import Workout
from workout_ai.providers import claude as _claude_provider
from workout_ai.providers import openai as _openai_provider

# Import the production caps rather than restating them. These runners already
# drifted once — production capped chat completions at 1200 tokens while the eval
# used 2000, so truncation between the two was a live bug the harness could not
# see. Anything the eval hardcodes is something the eval stops measuring.
CHAT_MAX_TOKENS = _openai_provider.MAX_TOKENS
REASONING_MAX_TOKENS = _openai_provider.REASONING_MAX_TOKENS
REASONING_EFFORT = _openai_provider.REASONING_EFFORT
ANTHROPIC_MAX_TOKENS = _claude_provider.MAX_TOKENS
ANTHROPIC_THINKING_BUDGET = _claude_provider.THINKING_BUDGET

Runner = Callable[[str, str, str], Awaitable[dict]]


def _messages(system_prompt: str, description: str):
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": description},
    ]


def _openai_dump(completion) -> dict:
    message = completion.choices[0].message
    if message.parsed is None:
        raise ValueError(f"no structured output (refusal={message.refusal})")
    return message.parsed.model_dump(exclude_none=True)


async def run_openai_chat(system_prompt: str, description: str, model: str) -> dict:
    client = AsyncOpenAI()
    completion = await client.chat.completions.parse(
        model=model,
        messages=_messages(system_prompt, description),
        max_tokens=CHAT_MAX_TOKENS,
        seed=42,
        temperature=0,
        response_format=Workout,
    )
    return _openai_dump(completion)


async def run_openai_reasoning(system_prompt: str, description: str, model: str) -> dict:
    client = AsyncOpenAI()
    completion = await client.chat.completions.parse(
        model=model,
        messages=_messages(system_prompt, description),
        max_completion_tokens=REASONING_MAX_TOKENS,
        reasoning_effort=REASONING_EFFORT,
        response_format=Workout,
    )
    return _openai_dump(completion)


async def run_anthropic(system_prompt: str, description: str, model: str) -> dict:
    client = AsyncAnthropic()
    message = await client.messages.parse(
        model=model,
        max_tokens=ANTHROPIC_MAX_TOKENS,
        thinking={"type": "enabled", "budget_tokens": ANTHROPIC_THINKING_BUDGET},
        system=system_prompt,
        messages=[{"role": "user", "content": description}],
        output_format=Workout,
    )
    if message.parsed_output is None:
        raise ValueError(f"no structured output (stop_reason={message.stop_reason})")
    return message.parsed_output.model_dump(exclude_none=True)


async def run_gemini(system_prompt: str, description: str, model: str) -> dict:
    # Google exposes an OpenAI-compatible endpoint, so the OpenAI SDK works with a
    # different base_url + key. Structured output support there is best-effort.
    client = AsyncOpenAI(
        api_key=os.environ.get("GEMINI_API_KEY"),
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )
    completion = await client.chat.completions.parse(
        model=model,
        messages=_messages(system_prompt, description),
        temperature=0,
        response_format=Workout,
    )
    return _openai_dump(completion)


@dataclass
class ModelSpec:
    label: str          # provider/short-name shown in the report
    model: str          # the API model id
    runner: Runner
    api_key_env: str    # only run if this env var is set
    reasoning_prompt: bool = False  # use SYSTEM_PROMPT_REASONING.md, as prod would


def _openai_spec(label: str, model: str, runner: Runner) -> ModelSpec:
    # Prompt choice comes from the production selector, not restated here — the
    # eval must send whatever production sends for this model.
    return ModelSpec(label, model, runner, "OPENAI_API_KEY",
                     reasoning_prompt=_openai_provider.wants_reasoning_prompt(model))


MODELS = [
    _openai_spec("openai/gpt-4.1-mini", "gpt-4.1-mini", run_openai_chat),
    _openai_spec("openai/gpt-5-mini", "gpt-5-mini", run_openai_reasoning),
    _openai_spec("openai/gpt-5.6-luna", "gpt-5.6-luna", run_openai_reasoning),
    _openai_spec("openai/o3-mini", "o3-mini", run_openai_reasoning),
    # The claude provider has no wants_reasoning_prompt yet, so prod sends the
    # full prompt to haiku/sonnet — the eval does the same.
    ModelSpec("anthropic/haiku-4.5", "claude-haiku-4-5", run_anthropic, "ANTHROPIC_API_KEY"),
    ModelSpec("anthropic/sonnet-4.6", "claude-sonnet-4-6", run_anthropic, "ANTHROPIC_API_KEY"),
    # "other provider" example — skipped unless GEMINI_API_KEY is set.
    ModelSpec("google/gemini-2.5-flash", "gemini-2.5-flash", run_gemini, "GEMINI_API_KEY"),
]
