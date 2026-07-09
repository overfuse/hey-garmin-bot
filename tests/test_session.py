"""Login-handshake session storage: Redis when available, TTLCache fallback."""

import fakeredis.aioredis
import pytest
from cachetools import TTLCache

import redis_conn
import session


@pytest.fixture
def redis(monkeypatch):
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_conn, "client", client)
    return client


@pytest.mark.asyncio
async def test_redis_roundtrip(redis):
    await session.set_username(1, "runner@example.com")
    assert await session.get_username(1) == "runner@example.com"
    await session.clear(1)
    assert await session.get_username(1) is None


@pytest.mark.asyncio
async def test_redis_entry_has_ttl(redis):
    await session.set_username(1, "runner@example.com")
    ttl = await redis.ttl("login:1")
    assert 0 < ttl <= session.TTL_S


@pytest.mark.asyncio
async def test_fallback_without_redis(monkeypatch):
    monkeypatch.setattr(redis_conn, "client", None)
    monkeypatch.setattr(session, "_fallback", TTLCache(maxsize=10, ttl=300))
    await session.set_username(2, "local@example.com")
    assert await session.get_username(2) == "local@example.com"
    await session.clear(2)
    assert await session.get_username(2) is None
