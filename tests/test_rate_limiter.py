"""Regression tests for rate_limiter.

Each test here corresponds to a bug that shipped. They exist so those bugs cannot
come back silently — the old implementation passed zero of them.
"""

import asyncio

import pytest
import fakeredis.aioredis

import rate_limiter as rl


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def limiter(redis, monkeypatch):
    """A limiter with small, fast windows: 3/hour, 5/day, 6/month.

    `init()` writes module globals that monkeypatch can't know about, so register
    them first — otherwise a live client and script leak into every later test,
    including the ones that assert on an *uninitialised* limiter.
    """
    monkeypatch.setattr(rl, "DISABLED", False)
    monkeypatch.setattr(rl, "FAIL_OPEN", False)
    monkeypatch.setattr(rl, "WINDOWS", (("hourly", 3600, 3), ("daily", 86400, 5), ("monthly", 2592000, 6)))
    monkeypatch.setattr(rl, "_WIDEST", 2592000)
    monkeypatch.setattr(rl, "redis_client", None)
    monkeypatch.setattr(rl, "_consume_script", None)
    rl.init(client=redis)
    return rl


async def _drain(limiter, user_id, n):
    """Consume n times, expecting all to be admitted."""
    return [await limiter.consume(user_id) for _ in range(n)]


@pytest.mark.asyncio
async def test_admits_up_to_cap_then_rejects(limiter):
    await _drain(limiter, 1, 3)
    with pytest.raises(rl.RateLimitExceeded) as exc:
        await limiter.consume(1)
    assert exc.value.scope == "hourly"
    assert exc.value.cap == 3
    assert exc.value.retry_after > 0


@pytest.mark.asyncio
async def test_rejection_is_not_swallowed_as_an_error(limiter):
    """Bug 1: `except Exception` around the check caught RateLimitExceeded itself,
    logged it as an infrastructure failure, and admitted the request anyway."""
    await _drain(limiter, 1, 3)
    with pytest.raises(rl.RateLimitExceeded):
        await limiter.consume(1)
    # And it must not be reported as the limiter being broken.
    with pytest.raises(rl.RateLimitExceeded):
        await limiter.consume(1)


@pytest.mark.asyncio
async def test_daily_window_fires_when_hourly_has_room(limiter, monkeypatch):
    """Bug 2: pruning to the hourly horizon made daily/monthly count an hour-old
    set, so they could never reach their caps."""
    # Backdate 5 requests to 2h ago: outside the hourly window, inside the daily one.
    import time

    now = time.time()
    key = rl._key(7)
    await limiter.redis_client.zadd(key, {f"old-{i}": now - 7200 - i for i in range(5)})

    # Hourly window is empty, so hourly must not be the blocker...
    with pytest.raises(rl.RateLimitExceeded) as exc:
        await limiter.consume(7)
    assert exc.value.scope == "daily"  # ...the daily cap of 5 is.


@pytest.mark.asyncio
async def test_monthly_window_survives_the_prune(limiter):
    import time

    now = time.time()
    key = rl._key(8)
    # 6 requests, 3 days old: outside hourly and daily, inside monthly (cap 6).
    await limiter.redis_client.zadd(key, {f"old-{i}": now - 259200 - i for i in range(6)})

    with pytest.raises(rl.RateLimitExceeded) as exc:
        await limiter.consume(8)
    assert exc.value.scope == "monthly"


@pytest.mark.asyncio
async def test_prune_discards_members_past_the_widest_window(limiter):
    import time

    now = time.time()
    key = rl._key(9)
    await limiter.redis_client.zadd(key, {f"ancient-{i}": now - 2592000 - 100 - i for i in range(6)})

    await limiter.consume(9)  # admitted: the ancient entries fall outside every window
    assert await limiter.redis_client.zcard(key) == 1


@pytest.mark.asyncio
async def test_concurrent_consumes_cannot_overshoot_the_cap(limiter):
    """Bug 3: check and record were separate round-trips straddling a multi-second
    LLM call, so N concurrent requests could all observe used == cap - 1."""
    results = await asyncio.gather(
        *(limiter.consume(2) for _ in range(10)), return_exceptions=True
    )
    admitted = [r for r in results if not isinstance(r, Exception)]
    rejected = [r for r in results if isinstance(r, rl.RateLimitExceeded)]
    assert len(admitted) == 3
    assert len(rejected) == 7
    assert await limiter.redis_client.zcard(rl._key(2)) == 3


@pytest.mark.asyncio
async def test_identical_timestamps_both_count(limiter, monkeypatch):
    """Bug 4: the member was str(now), so two requests sharing a float timestamp
    collapsed into one ZSET member — ZADD updates the score, it does not append."""
    monkeypatch.setattr(rl.time, "time", lambda: 1_700_000_000.0)
    await limiter.consume(3)
    await limiter.consume(3)
    assert await limiter.redis_client.zcard(rl._key(3)) == 2


@pytest.mark.asyncio
async def test_refund_returns_quota(limiter):
    receipts = await _drain(limiter, 4, 3)
    with pytest.raises(rl.RateLimitExceeded):
        await limiter.consume(4)

    await limiter.refund(4, receipts[-1])
    assert await limiter.consume(4) is not None  # room again


@pytest.mark.asyncio
async def test_refund_of_unknown_receipt_is_a_noop(limiter):
    await limiter.refund(5, "never-issued")
    await limiter.refund(5, None)
    assert await limiter.redis_client.zcard(rl._key(5)) == 0


@pytest.mark.asyncio
async def test_broken_redis_fails_closed(limiter, monkeypatch):
    """Bug 5/6: an unreachable Redis fell through to an always-empty in-memory
    store, which admitted everyone."""
    async def boom(*a, **k):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(rl, "_consume_script", boom)
    with pytest.raises(rl.RateLimiterUnavailable):
        await limiter.consume(6)


@pytest.mark.asyncio
async def test_broken_redis_fails_open_when_explicitly_configured(limiter, monkeypatch):
    async def boom(*a, **k):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(rl, "_consume_script", boom)
    monkeypatch.setattr(rl, "FAIL_OPEN", True)
    assert await limiter.consume(6) is None  # admitted, loudly


@pytest.mark.asyncio
async def test_stats_reports_each_window_independently(limiter):
    import time

    now = time.time()
    key = rl._key(10)
    await limiter.redis_client.zadd(key, {"a": now - 10, "b": now - 7200, "c": now - 172800})

    stats = await limiter.get_user_stats(10)
    assert stats["hourly"]["used"] == 1     # a
    assert stats["daily"]["used"] == 2      # a, b
    assert stats["monthly"]["used"] == 3    # a, b, c


def test_missing_redis_url_refuses_to_start(monkeypatch):
    """A missing env var must be a startup error, not a silent bypass."""
    monkeypatch.setattr(rl, "DISABLED", False)
    monkeypatch.setattr(rl, "REDIS_URL", "")
    with pytest.raises(RuntimeError, match="RATE_LIMIT_DISABLED"):
        rl.init()
