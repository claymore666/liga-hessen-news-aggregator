"""Admin endpoints for log viewing with Redis-backed cross-worker buffer.

Logs are written to Redis LIST `logs:entries` so all gunicorn workers
share the same log stream. A sync MemoryLogHandler pushes records into
an asyncio.Queue; a background task drains the queue to Redis.

Falls back to a per-worker in-memory deque when Redis is unavailable.
"""

import asyncio
import json
import logging
from collections import deque
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_LOG_ENTRIES = 1000
REDIS_LOG_KEY = "logs:entries"

# In-memory fallback buffer
_log_buffer: deque[dict] = deque(maxlen=MAX_LOG_ENTRIES)

# Async queue bridging sync emit() → async Redis writer
_log_queue: asyncio.Queue | None = None
_writer_task: asyncio.Task | None = None


class MemoryLogHandler(logging.Handler):
    """Log handler that pushes entries to asyncio queue (→ Redis) or in-memory deque."""

    def emit(self, record: logging.LogRecord):
        try:
            entry = {
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
            # Always write to local deque (fallback + immediate availability)
            _log_buffer.append(entry)

            # Try to enqueue for Redis (non-blocking, drop if full)
            if _log_queue is not None:
                try:
                    _log_queue.put_nowait(entry)
                except asyncio.QueueFull:
                    pass
        except Exception:
            self.handleError(record)


def setup_memory_logging():
    """Set up the in-memory log handler on the root logger."""
    handler = MemoryLogHandler()
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    for h in root_logger.handlers:
        if isinstance(h, MemoryLogHandler):
            return
    root_logger.addHandler(handler)


async def start_log_writer():
    """Start the background task that drains the log queue to Redis."""
    global _log_queue, _writer_task

    _log_queue = asyncio.Queue(maxsize=500)
    _writer_task = asyncio.create_task(_redis_log_writer())


async def stop_log_writer():
    """Stop the background log writer task."""
    global _writer_task
    if _writer_task and not _writer_task.done():
        _writer_task.cancel()
        try:
            await _writer_task
        except asyncio.CancelledError:
            pass
    _writer_task = None


async def _redis_log_writer():
    """Background task: drain queue → LPUSH + LTRIM to Redis."""
    from services.redis_client import get_redis

    while True:
        try:
            # Collect a batch (wait for at least one, then grab more)
            entry = await _log_queue.get()
            batch = [json.dumps(entry)]
            for _ in range(min(49, _log_queue.qsize())):
                try:
                    batch.append(json.dumps(_log_queue.get_nowait()))
                except asyncio.QueueEmpty:
                    break

            r = await get_redis()
            if r is not None:
                pipe = r.pipeline(transaction=False)
                pipe.lpush(REDIS_LOG_KEY, *batch)
                pipe.ltrim(REDIS_LOG_KEY, 0, MAX_LOG_ENTRIES - 1)
                await pipe.execute()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Redis down — entries are still in local deque
            logger.debug("Redis log writer error: %s", e)
            await asyncio.sleep(2)


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

class LogEntry(BaseModel):
    timestamp: str
    level: str
    logger: str
    message: str


class LogsResponse(BaseModel):
    entries: list[LogEntry]
    total: int
    page: int
    page_size: int
    total_pages: int
    source: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/admin/logs", response_model=LogsResponse)
async def get_application_logs(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(100, ge=10, le=200, description="Entries per page"),
    level: str | None = Query(None, description="Filter by log level"),
    logger_filter: str | None = Query(None, alias="logger", description="Filter by logger name"),
    search: str | None = Query(None, description="Search in message"),
) -> LogsResponse:
    """View recent application logs with pagination.

    Reads from Redis (shared across workers) when available,
    falls back to per-worker in-memory buffer.
    """
    setup_memory_logging()

    entries, source = await _read_log_entries()

    # Filter
    if level:
        level_upper = level.upper()
        entries = [e for e in entries if e["level"] == level_upper]

    if logger_filter:
        entries = [e for e in entries if logger_filter in e["logger"]]

    if search:
        search_lower = search.lower()
        entries = [e for e in entries if search_lower in e["message"].lower()]

    # Already newest-first from Redis (LPUSH); reverse local deque
    if source == "memory":
        entries = list(reversed(entries))

    total = len(entries)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    page_entries = entries[start:start + page_size]

    return LogsResponse(
        entries=[LogEntry(**e) for e in page_entries],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        source=source,
    )


@router.get("/admin/logs/stats")
async def get_log_stats() -> dict:
    """Get log statistics."""
    setup_memory_logging()

    entries, source = await _read_log_entries()

    level_counts: dict[str, int] = {}
    logger_counts: dict[str, int] = {}

    for entry in entries:
        lv = entry["level"]
        level_counts[lv] = level_counts.get(lv, 0) + 1
        logger_name = entry["logger"].split(".")[0]
        logger_counts[logger_name] = logger_counts.get(logger_name, 0) + 1

    return {
        "total": len(entries),
        "max_entries": MAX_LOG_ENTRIES,
        "source": source,
        "by_level": level_counts,
        "by_logger": dict(sorted(logger_counts.items(), key=lambda x: -x[1])[:10]),
    }


async def _read_log_entries() -> tuple[list[dict], str]:
    """Read log entries from Redis or fall back to local deque.

    Returns (entries, source_label).
    """
    from services.redis_client import get_redis

    r = await get_redis()
    if r is not None:
        try:
            raw_entries = await r.lrange(REDIS_LOG_KEY, 0, MAX_LOG_ENTRIES - 1)
            entries = [json.loads(e) for e in raw_entries]
            return entries, "redis"
        except Exception as e:
            logger.debug("Failed to read logs from Redis: %s", e)

    return list(_log_buffer), "memory"
