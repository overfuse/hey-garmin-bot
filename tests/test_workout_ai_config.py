"""WorkoutAIConfigError: our misconfiguration must be typed, raised before any
provider request, and distinguishable from bad user input (bot.py refunds it)."""

import pytest

import workout_ai
from workout_ai import WorkoutAIConfigError
from workout_ai import config as workout_ai_config


@pytest.mark.asyncio
async def test_unknown_provider_is_a_config_error(monkeypatch):
    monkeypatch.setattr(workout_ai_config, "PROVIDER", "grok")
    with pytest.raises(WorkoutAIConfigError, match="WORKOUT_AI_PROVIDER"):
        await workout_ai.plan_to_json_async("easy 5k")


@pytest.mark.asyncio
async def test_missing_openai_key_is_a_config_error(monkeypatch):
    from workout_ai.providers import openai as openai_provider

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(WorkoutAIConfigError):
        await openai_provider.plan("sys", "easy 5k", openai_provider.DEFAULT_MODEL)


@pytest.mark.asyncio
async def test_missing_anthropic_key_is_a_config_error(monkeypatch):
    from workout_ai.providers import claude as claude_provider

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(WorkoutAIConfigError):
        await claude_provider.plan("sys", "easy 5k", claude_provider.DEFAULT_MODEL)
