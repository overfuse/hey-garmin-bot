import os
import time
from typing import Optional
from collections import defaultdict
from datetime import datetime

# Configurable limits
HOURLY_LIMIT = int(os.getenv("RATE_LIMIT_HOURLY", "10"))
DAILY_LIMIT = int(os.getenv("RATE_LIMIT_DAILY", "50"))
MONTHLY_LIMIT = int(os.getenv("RATE_LIMIT_MONTHLY", "200"))

# Redis configuration (optional)
REDIS_URL = os.getenv("REDIS_URL", "")  # e.g., "redis://localhost:6379/0"

# Global state
redis_client = None
redis_available = False
in_memory_store = defaultdict(list)  # Fallback: {user_id: [timestamps]}


class RateLimitExceeded(Exception):
    """Raised when user exceeds rate limit"""
    pass


def _init_redis():
    """Initialize Redis client if available"""
    global redis_client, redis_available
    
    if not REDIS_URL:
        print("⚠️  REDIS_URL not configured - rate limiting disabled for local dev")
        return
    
    try:
        import redis.asyncio as redis
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_available = True
        print("✓ Redis connected for rate limiting")
    except ImportError:
        print("⚠️  redis package not installed - rate limiting disabled")
    except Exception as e:
        print(f"⚠️  Redis connection failed: {e} - falling back to in-memory")
        redis_available = False


async def _check_redis_health() -> bool:
    """Check if Redis is still available"""
    global redis_available
    if not redis_client:
        return False
    try:
        await redis_client.ping()
        return True
    except Exception:
        redis_available = False
        print("⚠️  Redis connection lost - falling back to in-memory")
        return False


async def check_rate_limit(user_id: int) -> None:
    """
    Check if user has exceeded rate limits.
    Uses Redis sliding window if available, otherwise in-memory or disabled.
    """
    if not REDIS_URL:
        # Rate limiting disabled for local dev
        return
    
    if redis_available and redis_client:
        try:
            await _check_rate_limit_redis(user_id)
            return
        except Exception as e:
            print(f"Redis rate limit check failed: {e}, falling back to in-memory")
            # Fall through to in-memory
    
    # Fallback to in-memory
    await _check_rate_limit_memory(user_id)


async def _check_rate_limit_redis(user_id: int) -> None:
    """
    Redis-based sliding window rate limiting.
    Uses sorted sets (ZSET) with timestamps as scores.
    """
    now = time.time()
    hour_ago = now - 3600
    day_ago = now - 86400
    month_ago = now - 2592000  # 30 days
    
    key = f"rate_limit:{user_id}"
    
    # Remove old entries and count remaining
    pipe = redis_client.pipeline()
    
    # Hourly window
    pipe.zremrangebyscore(key, 0, hour_ago)
    pipe.zcount(key, hour_ago, now)
    
    # Daily count
    pipe.zcount(key, day_ago, now)
    
    # Monthly count
    pipe.zcount(key, month_ago, now)
    
    results = await pipe.execute()
    hourly_count = results[1]
    daily_count = results[2]
    monthly_count = results[3]
    
    # Check limits
    if hourly_count >= HOURLY_LIMIT:
        # Calculate time until oldest entry expires
        oldest = await redis_client.zrange(key, 0, 0, withscores=True)
        if oldest:
            oldest_time = oldest[0][1]
            minutes_remaining = int((oldest_time + 3600 - now) / 60) + 1
            raise RateLimitExceeded(
                f"Hourly limit exceeded ({HOURLY_LIMIT} requests/hour). "
                f"Try again in {minutes_remaining} minute(s)."
            )
    
    if daily_count >= DAILY_LIMIT:
        raise RateLimitExceeded(
            f"Daily limit exceeded ({DAILY_LIMIT} requests/day). "
            f"Try again tomorrow."
        )
    
    if monthly_count >= MONTHLY_LIMIT:
        raise RateLimitExceeded(
            f"Monthly limit exceeded ({MONTHLY_LIMIT} requests/month). "
            f"Contact admin to increase your limit."
        )


async def _check_rate_limit_memory(user_id: int) -> None:
    """
    In-memory fallback rate limiting.
    Uses simple list of timestamps.
    """
    now = time.time()
    hour_ago = now - 3600
    day_ago = now - 86400
    month_ago = now - 2592000
    
    timestamps = in_memory_store[user_id]
    
    # Clean up old entries
    timestamps[:] = [ts for ts in timestamps if ts > month_ago]
    
    hourly_count = sum(1 for ts in timestamps if ts > hour_ago)
    daily_count = sum(1 for ts in timestamps if ts > day_ago)
    monthly_count = len(timestamps)
    
    if hourly_count >= HOURLY_LIMIT:
        oldest_in_hour = min([ts for ts in timestamps if ts > hour_ago])
        minutes_remaining = int((oldest_in_hour + 3600 - now) / 60) + 1
        raise RateLimitExceeded(
            f"Hourly limit exceeded ({HOURLY_LIMIT} requests/hour). "
            f"Try again in {minutes_remaining} minute(s)."
        )
    
    if daily_count >= DAILY_LIMIT:
        raise RateLimitExceeded(
            f"Daily limit exceeded ({DAILY_LIMIT} requests/day). "
            f"Try again tomorrow."
        )
    
    if monthly_count >= MONTHLY_LIMIT:
        raise RateLimitExceeded(
            f"Monthly limit exceeded ({MONTHLY_LIMIT} requests/month). "
            f"Contact admin to increase your limit."
        )


async def record_request(user_id: int) -> None:
    """
    Record a new API request for the user.
    """
    if not REDIS_URL:
        # Rate limiting disabled
        return
    
    now = time.time()
    
    if redis_available and redis_client:
        try:
            key = f"rate_limit:{user_id}"
            # Add current timestamp to sorted set
            await redis_client.zadd(key, {str(now): now})
            # Set expiry to 31 days (slightly more than monthly window)
            await redis_client.expire(key, 2678400)
            return
        except Exception as e:
            print(f"Redis record failed: {e}, using in-memory")
            # Fall through to in-memory
    
    # Fallback to in-memory
    in_memory_store[user_id].append(now)


async def get_user_stats(user_id: int) -> dict:
    """
    Get current usage statistics for a user.
    """
    if not REDIS_URL:
        return {
            "hourly": {"used": 0, "limit": HOURLY_LIMIT},
            "daily": {"used": 0, "limit": DAILY_LIMIT},
            "monthly": {"used": 0, "limit": MONTHLY_LIMIT},
            "note": "Rate limiting disabled (no REDIS_URL configured)"
        }
    
    now = time.time()
    hour_ago = now - 3600
    day_ago = now - 86400
    month_ago = now - 2592000
    
    if redis_available and redis_client:
        try:
            key = f"rate_limit:{user_id}"
            hourly_count = await redis_client.zcount(key, hour_ago, now)
            daily_count = await redis_client.zcount(key, day_ago, now)
            monthly_count = await redis_client.zcount(key, month_ago, now)
            
            return {
                "hourly": {"used": hourly_count, "limit": HOURLY_LIMIT},
                "daily": {"used": daily_count, "limit": DAILY_LIMIT},
                "monthly": {"used": monthly_count, "limit": MONTHLY_LIMIT}
            }
        except Exception:
            # Fall through to in-memory
            pass
    
    # Fallback to in-memory
    timestamps = in_memory_store.get(user_id, [])
    timestamps = [ts for ts in timestamps if ts > month_ago]
    
    hourly_count = sum(1 for ts in timestamps if ts > hour_ago)
    daily_count = sum(1 for ts in timestamps if ts > day_ago)
    monthly_count = len(timestamps)
    
    return {
        "hourly": {"used": hourly_count, "limit": HOURLY_LIMIT},
        "daily": {"used": daily_count, "limit": DAILY_LIMIT},
        "monthly": {"used": monthly_count, "limit": MONTHLY_LIMIT},
        "note": "Using in-memory fallback"
    }


async def reset_user_limits(user_id: int) -> None:
    """
    Admin function: reset all rate limits for a user.
    """
    if redis_available and redis_client:
        key = f"rate_limit:{user_id}"
        await redis_client.delete(key)
    
    # Also clear in-memory
    if user_id in in_memory_store:
        del in_memory_store[user_id]


async def create_indexes() -> None:
    """
    Initialize rate limiting system.
    """
    _init_redis()
    
    if redis_available and redis_client:
        # Test connection
        await _check_redis_health()


async def close_connections() -> None:
    """
    Close Redis connections on shutdown.
    """
    if redis_client:
        await redis_client.close()