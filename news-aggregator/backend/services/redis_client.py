"""Async Redis client singleton for cross-worker shared state.

Provides a lazy-initialized Redis connection that returns None when
Redis is unavailable, allowing callers to fall back to PostgreSQL.
"""

import logging

import redis.asyncio as aioredis

from config import settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None
_available: bool | None = None  # None = not yet tested


async def get_redis() -> aioredis.Redis | None:
    """Get the shared Redis client, or None if unavailable.

    Lazy-initializes on first call. Returns None (without retrying)
    if the initial connection fails — call init_redis() to retry.
    """
    global _redis, _available

    if _available is False:
        return None

    if _redis is not None:
        return _redis

    # First call — try to connect
    await init_redis()
    return _redis


async def init_redis() -> None:
    """Initialize (or re-initialize) the Redis connection."""
    global _redis, _available

    try:
        client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await client.ping()
        _redis = client
        _available = True
        logger.info("Redis connected: %s", settings.redis_url)
    except Exception as e:
        _redis = None
        _available = False
        logger.warning("Redis unavailable, using PostgreSQL fallback: %s", e)


def is_redis_available() -> bool:
    """Check whether Redis was successfully initialized."""
    return _available is True


async def close_redis() -> None:
    """Close the Redis connection (call during shutdown)."""
    global _redis, _available
    if _redis is not None:
        await _redis.aclose()
        _redis = None
    _available = None
