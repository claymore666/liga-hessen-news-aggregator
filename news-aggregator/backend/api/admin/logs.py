"""Admin endpoints for log viewing with in-memory buffer."""

import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory log buffer (circular buffer of last 1000 entries)
MAX_LOG_ENTRIES = 1000
_log_buffer: deque[dict] = deque(maxlen=MAX_LOG_ENTRIES)


class MemoryLogHandler(logging.Handler):
    """Custom log handler that stores entries in memory."""

    def emit(self, record: logging.LogRecord):
        try:
            entry = {
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
            _log_buffer.append(entry)
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

    # Add to root logger
    root_logger = logging.getLogger()
    # Avoid duplicate handlers
    for h in root_logger.handlers:
        if isinstance(h, MemoryLogHandler):
            return
    root_logger.addHandler(handler)


class LogEntry(BaseModel):
    """A single log entry."""

    timestamp: str
    level: str
    logger: str
    message: str


class LogsResponse(BaseModel):
    """Response for logs endpoint with pagination."""

    entries: list[LogEntry]
    total: int
    page: int
    page_size: int
    total_pages: int
    source: str


@router.get("/admin/logs", response_model=LogsResponse)
async def get_application_logs(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(100, ge=10, le=200, description="Entries per page"),
    level: Optional[str] = Query(None, description="Filter by log level"),
    logger_filter: Optional[str] = Query(None, alias="logger", description="Filter by logger name"),
    search: Optional[str] = Query(None, description="Search in message"),
) -> LogsResponse:
    """View recent application logs with pagination.

    Returns logs from in-memory buffer with filtering options.
    Logs are stored in a circular buffer of the last 1000 entries.
    """
    # Ensure memory logging is set up
    setup_memory_logging()

    # Filter logs
    filtered_entries = list(_log_buffer)

    if level:
        level_upper = level.upper()
        filtered_entries = [e for e in filtered_entries if e["level"] == level_upper]

    if logger_filter:
        filtered_entries = [e for e in filtered_entries if logger_filter in e["logger"]]

    if search:
        search_lower = search.lower()
        filtered_entries = [e for e in filtered_entries if search_lower in e["message"].lower()]

    # Reverse to show newest first
    filtered_entries = list(reversed(filtered_entries))

    total = len(filtered_entries)
    total_pages = max(1, (total + page_size - 1) // page_size)

    # Paginate
    start = (page - 1) * page_size
    end = start + page_size
    page_entries = filtered_entries[start:end]

    return LogsResponse(
        entries=[LogEntry(**e) for e in page_entries],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        source="memory",
    )


@router.get("/admin/logs/stats")
async def get_log_stats() -> dict:
    """Get log statistics."""
    setup_memory_logging()

    level_counts: dict[str, int] = {}
    logger_counts: dict[str, int] = {}

    for entry in _log_buffer:
        level = entry["level"]
        level_counts[level] = level_counts.get(level, 0) + 1

        # Only count top-level logger
        logger_name = entry["logger"].split(".")[0]
        logger_counts[logger_name] = logger_counts.get(logger_name, 0) + 1

    return {
        "total": len(_log_buffer),
        "max_entries": MAX_LOG_ENTRIES,
        "by_level": level_counts,
        "by_logger": dict(sorted(logger_counts.items(), key=lambda x: -x[1])[:10]),
    }
