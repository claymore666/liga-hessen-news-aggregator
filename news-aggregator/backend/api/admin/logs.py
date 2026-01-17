"""Admin endpoints for log viewing."""

import logging
from collections import deque
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class LogEntry(BaseModel):
    """A single log entry."""
    line: str


class LogsResponse(BaseModel):
    """Response for logs endpoint."""
    lines: list[str]
    source: str
    total_lines: int


@router.get("/admin/logs", response_model=LogsResponse)
async def get_application_logs(
    lines: int = Query(100, ge=1, le=1000, description="Number of lines to return"),
) -> LogsResponse:
    """View recent application logs.

    Returns logs from Docker container or log file.
    """
    log_lines: list[str] = []
    source = "memory"

    # Try reading from a log file first
    log_file = Path("/app/data/app.log")
    if log_file.exists():
        try:
            with open(log_file) as f:
                log_lines = list(deque(f, maxlen=lines))
                source = str(log_file)
        except Exception as e:
            logger.warning(f"Failed to read log file: {e}")

    # If no log file, return a message
    if not log_lines:
        log_lines = [
            "Log viewing requires file-based logging configuration.",
            "Currently logs are sent to stdout (view with 'docker logs').",
            "To enable: configure LOG_FILE=/app/data/app.log in environment.",
        ]
        source = "info"

    return LogsResponse(
        lines=log_lines,
        source=source,
        total_lines=len(log_lines),
    )
