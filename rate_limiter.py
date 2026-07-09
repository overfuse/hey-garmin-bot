"""Per-user sliding-window rate limiting, backed by Redis.

Design
------
One sorted set per user, `rate_limit:{user_id}`. Each admitted request adds one
member scored with its Unix timestamp. A window's usage is the number of members
scored within it, so hour/day/month all read off the same set.

Two properties this module exists to guarantee, both of which the previous
implementation silently violated:

1. **Check-and-increment is atomic.** Counting and recording happen inside one
   Lua script, so concurrent requests cannot both observe `used == cap - 1` and
   both be admitted. The caller consumes quota *before* doing the billable work,
   and explicitly refunds it if that work fails.

2. **A rejection is a result, not an error.** `RateLimitExceeded` is raised on
   the happy path when a user is over quota; it must never be conflated with
   `RateLimiterUnavailable`, which means the limiter itself is broken. Catching
   the two together is what turned this limiter into a no-op.

Failure policy is fail-closed by default: if Redis is unreachable we refuse the
request rather than silently admitting everyone. Set RATE_LIMIT_FAIL_OPEN=1 to
invert that. Running with no Redis at all requires RATE_LIMIT_DISABLED=1, so a
missing REDIS_URL in a deploy is a startup error rather than a silent bypass.
"""

import math
import os
import time
import uuid

# Configurable limits. (label, window_seconds, cap) — ordered narrowest first so
# the most specific limit is the one reported to the user.
WINDOWS = (
    ("hourly", 3600, int(os.getenv("RATE_LIMIT_HOURLY", "10"))),
    ("daily", 86400, int(os.getenv("RATE_LIMIT_DAILY", "50"))),
    ("monthly", 2592000, int(os.getenv("RATE_LIMIT_MONTHLY", "200"))),
)

# Keep the key alive slightly longer than the widest window we count over.
_WIDEST = max(w for _, w, _ in WINDOWS)
KEY_TTL = _WIDEST + 86400

REDIS_URL = os.getenv("REDIS_URL", "")
DISABLED = os.getenv("RATE_LIMIT_DISABLED", "") == "1"
FAIL_OPEN = os.getenv("RATE_LIMIT_FAIL_OPEN", "") == "1"

redis_client = None
_consume_script = None


class RateLimitExceeded(Exception):
    """The user is over quota. A normal outcome — never catch this as an error."""

    def __init__(self, scope: str, cap: int, retry_after: int):
        self.scope = scope
        self.cap = cap
        self.retry_after = retry_after
        super().__init__(self._message())

    def _message(self) -> str:
        if self.scope == "hourly":
            mins = max(1, math.ceil(self.retry_after / 60))
            return f"Hourly limit reached ({self.cap} per hour). Try again in {mins} minute(s)."
        if self.scope == "daily":
            hours = max(1, math.ceil(self.retry_after / 3600))
            return f"Daily limit reached ({self.cap} per day). Try again in {hours} hour(s)."
        days = max(1, math.ceil(self.retry_after / 86400))
        return f"Monthly limit reached ({self.cap} per month). Try again in {days} day(s)."


class RateLimiterUnavailable(Exception):
    """The limiter itself is broken (Redis down). Distinct from a rejection."""


# ---------------------------------------------------------------------------
# The atomic check-and-increment.
#
# KEYS[1]              the user's sorted set
# ARGV[1]              now (float seconds)
# ARGV[2]              member to add if admitted (unique)
# ARGV[3]              key TTL in seconds
# ARGV[4]              widest window in seconds (prune horizon)
# ARGV[5..]            (label, window_seconds, cap) triples
#
# Returns {1, "", 0} on admit, or {0, label, retry_after_seconds} on reject.
#
# Redis executes a script atomically, so no other client can observe or mutate
# the set between the ZCOUNTs and the ZADD.
# ---------------------------------------------------------------------------
_CONSUME_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local member = ARGV[2]
local ttl    = tonumber(ARGV[3])
local widest = tonumber(ARGV[4])

-- Prune to the WIDEST window we count over, not the narrowest. Pruning to the
-- hourly horizon here would make the daily and monthly ZCOUNTs below read an
-- hour-old set, so those limits could never fire.
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - widest)

for i = 5, #ARGV, 3 do
  local label  = ARGV[i]
  local window = tonumber(ARGV[i + 1])
  local cap    = tonumber(ARGV[i + 2])
  local since  = now - window
  local used   = redis.call('ZCOUNT', key, since, now)

  if used >= cap then
    -- Quota frees up when the oldest member inside this window ages out of it.
    local oldest = redis.call('ZRANGEBYSCORE', key, since, now,
                              'WITHSCORES', 'LIMIT', 0, 1)
    local retry = 1
    if oldest[2] then
      retry = math.ceil(tonumber(oldest[2]) + window - now)
      if retry < 1 then retry = 1 end
    end
    return {0, label, retry}
  end
end

redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, ttl)
return {1, '', 0}
"""


def _key(user_id: int) -> str:
    return f"rate_limit:{user_id}"


def _caps() -> dict:
    return {label: cap for label, _, cap in WINDOWS}


async def init(client=None) -> None:
    """Wire up Redis and verify it answers. Raises at startup, not on first use.

    redis.from_url is lazy and register_script is local object construction —
    neither touches the network. Without the ping a typo'd REDIS_URL sails
    through startup and, because this module is fail-closed, converts into a
    total outage on the first workout instead of a crashed deploy.

    Pass `client` to inject a fake in tests.
    """
    global redis_client, _consume_script

    if DISABLED:
        print("⚠️  RATE_LIMIT_DISABLED=1 — rate limiting is OFF", flush=True)
        return

    if client is None:
        if not REDIS_URL:
            raise RuntimeError(
                "REDIS_URL is not set and RATE_LIMIT_DISABLED is not 1. Refusing to "
                "start without rate limiting — set one or the other explicitly."
            )
        import redis.asyncio as redis

        client = redis.from_url(REDIS_URL, decode_responses=True)

    # Prove the connection before publishing any state or claiming success.
    await client.ping()

    redis_client = client
    _consume_script = client.register_script(_CONSUME_LUA)
    print(f"✓ Rate limiting active ({', '.join(f'{c}/{l}' for l, _, c in WINDOWS)})", flush=True)


async def consume(user_id: int) -> str | None:
    """Atomically check every window and record the request if all have room.

    Call this BEFORE the billable work, not after it succeeds — you are limiting
    attempts, not successes. Returns a receipt to pass to `refund` if the work
    fails, or None when limiting is disabled.

    Raises:
        RateLimitExceeded:      the user is over quota.
        RateLimiterUnavailable: Redis is unreachable and FAIL_OPEN is not set.
    """
    if DISABLED:
        return None
    if _consume_script is None:
        raise RateLimiterUnavailable("rate limiter not initialised; call init()")

    now = time.time()
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"  # unique: ZADD updates, not appends, on a repeat member

    args = [now, member, KEY_TTL, _WIDEST]
    for label, window, cap in WINDOWS:
        args.extend([label, window, cap])

    try:
        admitted, scope, retry_after = await _consume_script(keys=[_key(user_id)], args=args)
    except Exception as e:  # connection refused, timeout, NOSCRIPT reload failure...
        if FAIL_OPEN:
            print(f"⚠️  Rate limiter unavailable ({e}) — FAIL_OPEN, admitting request", flush=True)
            return None
        raise RateLimiterUnavailable(str(e)) from e

    if not int(admitted):
        scope = scope.decode() if isinstance(scope, bytes) else scope
        raise RateLimitExceeded(scope, _caps()[scope], int(retry_after))

    return member


async def refund(user_id: int, receipt: str | None) -> None:
    """Return quota consumed by work that failed. Best-effort: never raises."""
    if not receipt or redis_client is None:
        return
    try:
        await redis_client.zrem(_key(user_id), receipt)
    except Exception as e:
        print(f"⚠️  Rate limit refund failed for {user_id}: {e}", flush=True)


async def get_user_stats(user_id: int) -> dict:
    """Current usage per window. Read-only — never prunes."""
    if DISABLED:
        return {
            label: {"used": 0, "limit": cap} for label, _, cap in WINDOWS
        } | {"note": "Rate limiting disabled (RATE_LIMIT_DISABLED=1)"}

    if redis_client is None:
        raise RateLimiterUnavailable("rate limiter not initialised; call init()")

    now = time.time()
    key = _key(user_id)
    try:
        pipe = redis_client.pipeline()
        for _, window, _cap in WINDOWS:
            pipe.zcount(key, now - window, now)
        counts = await pipe.execute()
    except Exception as e:
        raise RateLimiterUnavailable(str(e)) from e

    return {
        label: {"used": int(used), "limit": cap}
        for (label, _, cap), used in zip(WINDOWS, counts)
    }


async def close_connections() -> bool:
    """Release the Redis pool on shutdown. Best-effort: never blocks the exit.

    Returns True if a live pool was actually closed, False when there was
    nothing to close (e.g. RATE_LIMIT_DISABLED=1 — init() never opened one).
    """
    if redis_client is None:
        return False
    await redis_client.aclose()
    return True
