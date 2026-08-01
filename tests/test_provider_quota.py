"""LLMQuotaExhausted: an empty provider balance must be typed, refunded, and
never blamed on the user's workout text (regression for the real outage where
OpenAI `credit_balance_exhausted` surfaced as PARSE_FAILED — "I couldn't turn
that into a workout" — for a perfectly valid plan)."""

from types import SimpleNamespace

import httpx
import pytest
from anthropic import BadRequestError
from openai import RateLimitError

import workout_service
from workout_ai import LLMQuotaExhausted
from workout_ai.providers import claude as claude_provider
from workout_ai.providers import openai as openai_provider
from workout_service import Failure, FailureCode


def _openai_429(body: dict) -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return RateLimitError(
        "429", response=httpx.Response(429, request=request), body=body
    )


def _anthropic_400(message: str) -> BadRequestError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return BadRequestError(
        message,
        response=httpx.Response(400, request=request),
        body={"type": "invalid_request_error", "message": message},
    )


class _FakeOpenAI:
    exc: Exception  # set per-test on the class; instantiated inside the provider

    def __init__(self, **kwargs):
        async def parse(**_):
            raise type(self).exc

        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=parse))


class _FakeAnthropic:
    exc: Exception

    def __init__(self, **kwargs):
        async def parse(**_):
            raise type(self).exc

        self.messages = SimpleNamespace(parse=parse)


@pytest.mark.asyncio
async def test_openai_insufficient_quota_is_typed(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", _FakeOpenAI)
    _FakeOpenAI.exc = _openai_429(
        {"code": "credit_balance_exhausted", "type": "insufficient_quota"}
    )
    with pytest.raises(LLMQuotaExhausted):
        await openai_provider.plan("sys", "easy 5k", openai_provider.DEFAULT_MODEL)


@pytest.mark.asyncio
async def test_openai_transient_429_stays_a_rate_limit(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", _FakeOpenAI)
    _FakeOpenAI.exc = _openai_429({"code": "rate_limit_exceeded", "type": "requests"})
    with pytest.raises(RateLimitError):
        await openai_provider.plan("sys", "easy 5k", openai_provider.DEFAULT_MODEL)


@pytest.mark.asyncio
async def test_anthropic_low_balance_is_typed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(claude_provider, "AsyncAnthropic", _FakeAnthropic)
    _FakeAnthropic.exc = _anthropic_400(
        "Your credit balance is too low to access the Anthropic API."
    )
    with pytest.raises(LLMQuotaExhausted):
        await claude_provider.plan("sys", "easy 5k", claude_provider.DEFAULT_MODEL)


@pytest.mark.asyncio
async def test_anthropic_other_400_passes_through(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(claude_provider, "AsyncAnthropic", _FakeAnthropic)
    _FakeAnthropic.exc = _anthropic_400("max_tokens is too large")
    with pytest.raises(BadRequestError):
        await claude_provider.plan("sys", "easy 5k", claude_provider.DEFAULT_MODEL)


@pytest.mark.asyncio
async def test_process_workout_refunds_and_reports_provider_quota(monkeypatch):
    refunds = []

    async def fake_consume(user_id):
        return "receipt-1"

    async def fake_refund(user_id, receipt):
        refunds.append((user_id, receipt))

    async def fake_parse_plan(text):
        raise LLMQuotaExhausted("OpenAI account out of credits")

    async def fake_log(**kwargs):
        return None

    monkeypatch.setattr(workout_service, "consume", fake_consume)
    monkeypatch.setattr(workout_service, "refund", fake_refund)
    monkeypatch.setattr(workout_service, "parse_plan", fake_parse_plan)
    monkeypatch.setattr(workout_service, "log_workout_request", fake_log)

    outcome = await workout_service.process_workout(1, {}, "6/2/6/4/6/2")

    assert outcome == Failure(FailureCode.PROVIDER_QUOTA)
    assert refunds == [(1, "receipt-1")]
