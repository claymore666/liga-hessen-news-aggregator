"""API endpoints for scheduler control."""

import logging

from apscheduler.triggers.interval import IntervalTrigger
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import settings
from services.scheduler import scheduler, get_job_status, start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)
router = APIRouter()


class SchedulerStatusResponse(BaseModel):
    """Scheduler status information."""

    running: bool
    jobs: list[dict]
    fetch_interval_minutes: int


class IntervalUpdateRequest(BaseModel):
    """Request to update the fetch interval."""

    minutes: int = Field(..., ge=1, le=1440, description="Interval in minutes (1-1440)")


class IntervalUpdateResponse(BaseModel):
    """Response after updating interval."""

    success: bool
    new_interval_minutes: int
    message: str


@router.get("/scheduler/status", response_model=SchedulerStatusResponse)
async def get_scheduler_status() -> SchedulerStatusResponse:
    """Get scheduler running state, jobs, and next run times."""
    return SchedulerStatusResponse(
        running=scheduler.running,
        jobs=get_job_status(),
        fetch_interval_minutes=settings.fetch_interval_minutes,
    )


@router.post("/scheduler/start")
async def start_scheduler_endpoint() -> dict[str, str]:
    """Start the scheduler if not already running."""
    if scheduler.running:
        return {"status": "already_running", "message": "Scheduler is already running"}

    try:
        start_scheduler()
        return {"status": "started", "message": "Scheduler started successfully"}
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start scheduler: {e}")


@router.post("/scheduler/stop")
async def stop_scheduler_endpoint() -> dict[str, str]:
    """Stop the scheduler if running."""
    if not scheduler.running:
        return {"status": "already_stopped", "message": "Scheduler is not running"}

    try:
        stop_scheduler()
        return {"status": "stopped", "message": "Scheduler stopped successfully"}
    except Exception as e:
        logger.error(f"Failed to stop scheduler: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to stop scheduler: {e}")


@router.put("/scheduler/interval", response_model=IntervalUpdateResponse)
async def update_fetch_interval(request: IntervalUpdateRequest) -> IntervalUpdateResponse:
    """Change the fetch interval for the main fetch job.

    Note: This updates the running scheduler but does not persist across restarts.
    To persist, update the FETCH_INTERVAL_MINUTES environment variable.
    """
    job = scheduler.get_job("fetch_all_sources")
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Fetch job not found. Is the scheduler running?",
        )

    try:
        # Reschedule with new interval
        scheduler.reschedule_job(
            "fetch_all_sources",
            trigger=IntervalTrigger(minutes=request.minutes),
        )

        logger.info(f"Updated fetch interval to {request.minutes} minutes")
        return IntervalUpdateResponse(
            success=True,
            new_interval_minutes=request.minutes,
            message=f"Fetch interval updated to {request.minutes} minutes",
        )
    except Exception as e:
        logger.error(f"Failed to update interval: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update interval: {e}")
