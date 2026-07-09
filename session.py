"""Login-flow scratch state: the username captured between the two prompts.

Backed by Redis (already a hard dependency via rate_limiter) so the handshake
survives a second replica — with a per-process TTLCache, username lands on
replica A, password on replica B, and login becomes a coin flip. Falls back to
the in-process cache only when Redis is absent (RATE_LIMIT_DISABLED local dev).

This holds a *username*, never a password or token — don't let it drift into
holding one, or it needs the token_crypto treatment.

Caveat: this removes the state barrier to multi-replica, not the dispatch one.
Two Pyrogram processes on the same bot token split updates nondeterministically;
scaling out needs one dispatcher feeding a work queue, not N bots.
"""

from cachetools import TTLCache

import redis_conn

TTL_S = 300

_fallback: TTLCache = TTLCache(maxsize=1000, ttl=TTL_S)


def _key(uid: int) -> str:
    return f"login:{uid}"


async def set_username(uid: int, username: str) -> None:
    r = redis_conn.client
    if r is not None:
        await r.setex(_key(uid), TTL_S, username)
    else:
        _fallback[uid] = username


async def get_username(uid: int) -> str | None:
    r = redis_conn.client
    if r is not None:
        return await r.get(_key(uid))
    return _fallback.get(uid)


async def clear(uid: int) -> None:
    r = redis_conn.client
    if r is not None:
        await r.delete(_key(uid))
    else:
        _fallback.pop(uid, None)
