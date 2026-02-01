"""DB-backed worker status for multi-worker (gunicorn) support.

Stores worker state, stats, and commands in the PostgreSQL settings table
so all gunicorn workers can read correct status. Only the leader process
runs background tasks; all processes serve API requests.

Keys: worker:{name}:state, worker:{name}:stats, worker:{name}:command
"""

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, text

from database import async_session_maker
from models import Setting

logger = logging.getLogger(__name__)

# Command timeout: discard commands older than this
COMMAND_TIMEOUT_SECONDS = 60


def _key(name: str, suffix: str) -> str:
    return f"worker:{name}:{suffix}"


async def get_poll_interval() -> int:
    """Get the poll interval, checking DB override first, then env default.

    The DB setting 'worker_status_poll_interval' overrides the env var.
    This allows changing the interval on the fly via the settings API.
    """
    try:
        async with async_session_maker() as db:
            result = await db.scalar(
                select(Setting.value).where(Setting.key == "worker_status_poll_interval")
            )
            if result is not None:
                val = result if isinstance(result, int) else int(result)
                if 1 <= val <= 300:
                    return val
    except Exception:
        pass

    from config import settings
    return settings.worker_status_poll_interval


async def write_state(name: str, *, running: bool, paused: bool = False,
                      stopped_due_to_errors: bool = False) -> None:
    """Write worker state to DB. Called by leader on state changes."""
    value = {
        "running": running,
        "paused": paused,
        "stopped_due_to_errors": stopped_due_to_errors,
        "updated_at": datetime.utcnow().isoformat(),
    }
    await _upsert(_key(name, "state"), value, f"Worker state for {name}")


async def read_state(name: str) -> dict:
    """Read worker state from DB. Returns defaults if not found."""
    value = await _read(_key(name, "state"))
    if value is None:
        return {"running": False, "paused": False, "stopped_due_to_errors": False}
    return value


async def write_stats(name: str, stats: dict) -> None:
    """Write worker stats to DB. Called by leader periodically."""
    value = {**stats, "synced_at": datetime.utcnow().isoformat()}
    await _upsert(_key(name, "stats"), value, f"Worker stats for {name}")


async def read_stats(name: str) -> dict:
    """Read worker stats from DB."""
    value = await _read(_key(name, "stats"))
    return value or {}


async def write_command(name: str, action: str) -> None:
    """Write a command for the worker. Called by API endpoints."""
    value = {
        "action": action,
        "issued_at": datetime.utcnow().isoformat(),
    }
    await _upsert(_key(name, "command"), value, f"Pending command for {name}")


async def read_and_clear_command(name: str) -> str | None:
    """Read and clear pending command. Called by leader poll loop.

    Returns the action string, or None if no command or command is stale.
    """
    key = _key(name, "command")
    value = await _read(key)
    if value is None:
        return None

    # Clear the command
    await _delete(key)

    # Check staleness
    issued_at = value.get("issued_at")
    if issued_at:
        try:
            issued = datetime.fromisoformat(issued_at)
            if datetime.utcnow() - issued > timedelta(seconds=COMMAND_TIMEOUT_SECONDS):
                logger.warning(f"Discarding stale command for {name}: {value.get('action')}")
                return None
        except (ValueError, TypeError):
            pass

    return value.get("action")


async def _upsert(key: str, value: dict, description: str = "") -> None:
    """Insert or update a setting."""
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
        logger.warning(f"Failed to write setting {key}: {e}")


async def _read(key: str) -> dict | None:
    """Read a setting value."""
    try:
        async with async_session_maker() as db:
            result = await db.scalar(
                select(Setting.value).where(Setting.key == key)
            )
            if result is not None:
                return result if isinstance(result, dict) else dict(result)
            return None
    except Exception as e:
        logger.warning(f"Failed to read setting {key}: {e}")
        return None


async def _delete(key: str) -> None:
    """Delete a setting."""
    try:
        async with async_session_maker() as db:
            existing = await db.scalar(
                select(Setting).where(Setting.key == key)
            )
            if existing:
                await db.delete(existing)
                await db.commit()
    except Exception as e:
        logger.warning(f"Failed to delete setting {key}: {e}")
