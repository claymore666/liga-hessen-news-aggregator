"""Async Redis client singleton for cross-worker shared state.

Provides a lazy-initialized Redis connection that returns None when
Redis is unavailable, allowing callers to fall back to PostgreSQL.
"""

import logging
import time

import redis.asyncio as aioredis

from config import settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None
_available: bool | None = None  # None = not yet tested
_last_retry_at: float = 0.0  # monotonic timestamp of last failed attempt
_RETRY_INTERVAL: float = 60.0  # seconds between retry attempts


async def get_redis() -> aioredis.Redis | None:
    """Get the shared Redis client, or None if unavailable.

    Lazy-initializes on first call. If the connection failed previously,
    retries every 60 seconds so that Redis can recover without a restart.
    """
    global _redis, _available

    if _available is False:
        # Periodically retry instead of giving up forever
        if time.monotonic() - _last_retry_at >= _RETRY_INTERVAL:
            await init_redis()
        return _redis

    if _redis is not None:
        return _redis

    # First call — try to connect
    await init_redis()
    return _redis


async def init_redis() -> None:
    """Initialize (or re-initialize) the Redis connection."""
    global _redis, _available, _last_retry_at

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
        _last_retry_at = time.monotonic()
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
