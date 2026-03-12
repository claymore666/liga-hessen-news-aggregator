"""Cross-worker state via Redis with PostgreSQL fallback.

Stores worker state, stats, and commands so all gunicorn workers
see consistent status. Redis is the primary store (~0.1 ms ops);
PostgreSQL is the automatic fallback when Redis is unavailable.

Keys: worker:{name}:state, worker:{name}:stats, worker:{name}:command
"""

import json
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select

from database import async_session_maker
from models import Setting

logger = logging.getLogger(__name__)

COMMAND_TIMEOUT_SECONDS = 60

# Cache for get_poll_interval() to avoid querying settings table every 10s
_poll_interval_cache: int | None = None
_poll_interval_cached_at: float = 0
_POLL_INTERVAL_CACHE_TTL = 60  # seconds


def _key(name: str, suffix: str) -> str:
    return f"worker:{name}:{suffix}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_poll_interval() -> int:
    """Get poll interval from DB, cached for 60s to reduce settings table scans."""
    global _poll_interval_cache, _poll_interval_cached_at
    now = time.monotonic()
    if _poll_interval_cache is not None and (now - _poll_interval_cached_at) < _POLL_INTERVAL_CACHE_TTL:
        return _poll_interval_cache

    try:
        async with async_session_maker() as db:
            result = await db.scalar(
                select(Setting.value).where(Setting.key == "worker_status_poll_interval")
            )
            if result is not None:
                val = result if isinstance(result, int) else int(result)
                if 1 <= val <= 300:
                    _poll_interval_cache = val
                    _poll_interval_cached_at = now
                    return val
    except Exception:
        pass

    from config import settings
    val = settings.worker_status_poll_interval
    _poll_interval_cache = val
    _poll_interval_cached_at = now
    return val


async def write_state(name: str, *, running: bool, paused: bool = False,
                      stopped_due_to_errors: bool = False,
                      service_available: bool | None = None) -> None:
    value = {
        "running": running,
        "paused": paused,
        "stopped_due_to_errors": stopped_due_to_errors,
        "updated_at": datetime.utcnow().isoformat(),
    }
    if service_available is not None:
        value["service_available"] = service_available
    await _write(_key(name, "state"), value, f"Worker state for {name}")


async def read_state(name: str) -> dict:
    value = await _read(_key(name, "state"))
    if value is None:
        return {"running": False, "paused": False, "stopped_due_to_errors": False}
    return value


async def write_stats(name: str, stats: dict) -> None:
    value = {**stats, "synced_at": datetime.utcnow().isoformat()}
    await _write(_key(name, "stats"), value, f"Worker stats for {name}")


async def read_stats(name: str) -> dict:
    value = await _read(_key(name, "stats"))
    return value or {}


async def write_command(name: str, action: str) -> None:
    value = {
        "action": action,
        "issued_at": datetime.utcnow().isoformat(),
    }
    # Use Redis SETEX with TTL so stale commands auto-expire
    r = await _get_redis()
    if r is not None:
        try:
            await r.setex(
                _key(name, "command"),
                COMMAND_TIMEOUT_SECONDS,
                json.dumps(value),
            )
            return
        except Exception as e:
            logger.warning("Redis write_command failed, falling back to DB: %s", e)

    await _db_upsert(_key(name, "command"), value, f"Pending command for {name}")


async def read_and_clear_command(name: str) -> str | None:
    key = _key(name, "command")

    r = await _get_redis()
    if r is not None:
        try:
            # GET + DELETE in a pipeline (atomic-ish)
            pipe = r.pipeline(transaction=True)
            pipe.get(key)
            pipe.delete(key)
            raw, _ = await pipe.execute()
            if raw is None:
                return None
            value = json.loads(raw)
            return value.get("action")
        except Exception as e:
            logger.warning("Redis read_and_clear_command failed, falling back to DB: %s", e)

    # DB fallback
    value = await _db_read(key)
    if value is None:
        return None
    await _db_delete(key)

    issued_at = value.get("issued_at")
    if issued_at:
        try:
            issued = datetime.fromisoformat(issued_at)
            if datetime.utcnow() - issued > timedelta(seconds=COMMAND_TIMEOUT_SECONDS):
                logger.warning("Discarding stale command for %s: %s", key, value.get("action"))
                return None
        except (ValueError, TypeError):
            pass

    return value.get("action")


# ---------------------------------------------------------------------------
# Internal: Redis-first, DB-fallback helpers
# ---------------------------------------------------------------------------

async def _get_redis():
    from services.redis_client import get_redis
    return await get_redis()


async def _write(key: str, value: dict, description: str = "") -> None:
    r = await _get_redis()
    if r is not None:
        try:
            if key.endswith(":state"):
                # HSET for state (flat hash)
                mapping = {k: json.dumps(v) if isinstance(v, bool) else str(v) for k, v in value.items()}
                await r.hset(key, mapping=mapping)
            else:
                await r.set(key, json.dumps(value))
            return
        except Exception as e:
            logger.warning("Redis write failed for %s, falling back to DB: %s", key, e)

    await _db_upsert(key, value, description)


async def _read(key: str) -> dict | None:
    r = await _get_redis()
    if r is not None:
        try:
            if key.endswith(":state"):
                data = await r.hgetall(key)
                if not data:
                    return None
                # Deserialize booleans back
                result = {}
                for k, v in data.items():
                    if v in ("true", "false"):
                        result[k] = v == "true"
                    else:
                        result[k] = v
                return result
            else:
                raw = await r.get(key)
                return json.loads(raw) if raw else None
        except Exception as e:
            logger.warning("Redis read failed for %s, falling back to DB: %s", key, e)

    return await _db_read(key)


# ---------------------------------------------------------------------------
# PostgreSQL fallback (original implementation)
# ---------------------------------------------------------------------------

async def _db_upsert(key: str, value: dict, description: str = "") -> None:
    try:
        async with async_session_maker() as db:
            existing = await db.scalar(
                select(Setting).where(Setting.key == key)
            )
            if existing:
                existing.value = value
            else:
                db.add(Setting(key=key, value=value, description=description))
            await db.commit()
    except Exception as e:
        logger.warning("Failed to write setting %s: %s", key, e)


async def _db_read(key: str) -> dict | None:
    try:
        async with async_session_maker() as db:
            result = await db.scalar(
                select(Setting.value).where(Setting.key == key)
            )
            if result is not None:
                return result if isinstance(result, dict) else dict(result)
            return None
    except Exception as e:
        logger.warning("Failed to read setting %s: %s", key, e)
        return None


async def _db_delete(key: str) -> None:
    try:
        async with async_session_maker() as db:
            existing = await db.scalar(
                select(Setting).where(Setting.key == key)
            )
            if existing:
                await db.delete(existing)
                await db.commit()
    except Exception as e:
        logger.warning("Failed to delete setting %s: %s", key, e)
