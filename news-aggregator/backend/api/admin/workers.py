"""Admin endpoints for worker control."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.worker_status import read_state, write_command

logger = logging.getLogger(__name__)
router = APIRouter()


# =============================================================================
# Worker Status Poll Interval
# =============================================================================


class PollIntervalRequest(BaseModel):
    interval: int = Field(..., ge=1, le=300, description="Poll interval in seconds (1-300)")


@router.get("/admin/worker-poll-interval")
async def get_poll_interval():
    """Get current worker status poll interval."""
    from services.worker_status import get_poll_interval
    interval = await get_poll_interval()
    return {"interval": interval}


@router.put("/admin/worker-poll-interval")
async def set_poll_interval(request: PollIntervalRequest):
    """Set worker status poll interval on the fly (persisted in DB).

    Workers will pick up the new interval on their next poll cycle.
    """
    from database import async_session_maker
    from models import Setting
    from sqlalchemy import select

    async with async_session_maker() as db:
        existing = await db.scalar(
            select(Setting).where(Setting.key == "worker_status_poll_interval")
        )
        if existing:
            existing.value = request.interval
        else:
            db.add(Setting(
                key="worker_status_poll_interval",
                value=request.interval,
                description="Worker status DB sync/command poll interval in seconds",
            ))
        await db.commit()

    return {"interval": request.interval, "message": f"Poll interval set to {request.interval}s"}


# =============================================================================
# Scheduler Control
# =============================================================================


@router.post("/admin/scheduler/start")
async def start_scheduler_endpoint():
    """Start the background scheduler."""
    from services.scheduler import scheduler, start_scheduler

    if scheduler.running:
        return {"status": "already_running", "message": "Scheduler is already running"}

    start_scheduler()
    return {"status": "started", "message": "Scheduler started"}


@router.post("/admin/scheduler/stop")
async def stop_scheduler_endpoint():
    """Stop the background scheduler."""
    from services.scheduler import scheduler, stop_scheduler

    if not scheduler.running:
        return {"status": "already_stopped", "message": "Scheduler is not running"}

    stop_scheduler()
    return {"status": "stopped", "message": "Scheduler stopped"}


# =============================================================================
# LLM Worker Control
# =============================================================================


@router.post("/admin/llm-worker/start")
async def start_llm_worker_endpoint():
    """Start the LLM worker (only works on leader process)."""
    from services.llm_worker import get_worker, start_worker

    worker = get_worker()
    if worker and worker._running:
        return {"status": "already_running", "message": "LLM worker is already running"}

    # Check DB state - if no local worker, send command
    state = await read_state("llm")
    if state.get("running"):
        return {"status": "already_running", "message": "LLM worker is already running"}

    # Try local start first (works if we're on leader)
    if worker is not None or get_worker() is not None:
        await start_worker()
        return {"status": "started", "message": "LLM worker started"}

    # Not on leader - queue command
    await write_command("llm", "start")
    return {"status": "command_queued", "message": "Start command queued for leader process"}


@router.post("/admin/llm-worker/stop")
async def stop_llm_worker_endpoint():
    """Stop the LLM worker."""
    state = await read_state("llm")
    if not state.get("running"):
        return {"status": "already_stopped", "message": "LLM worker is not running"}

    # Try local stop first
    from services.llm_worker import get_worker, stop_worker
    worker = get_worker()
    if worker and worker._running:
        await stop_worker()
        return {"status": "stopped", "message": "LLM worker stopped"}

    await write_command("llm", "stop")
    return {"status": "command_queued", "message": "Stop command queued for leader process"}


@router.post("/admin/llm-worker/pause")
async def pause_llm_worker_endpoint():
    """Pause the LLM worker."""
    state = await read_state("llm")
    if not state.get("running"):
        raise HTTPException(status_code=503, detail="LLM worker not running")
    if state.get("paused"):
        return {"status": "already_paused", "message": "LLM worker is already paused"}

    # Try local pause first
    from services.llm_worker import get_worker
    worker = get_worker()
    if worker and worker._running:
        await worker.pause()
        return {"status": "paused", "message": "LLM worker paused"}

    await write_command("llm", "pause")
    return {"status": "command_queued", "message": "Pause command queued for leader process"}


@router.post("/admin/llm-worker/resume")
async def resume_llm_worker_endpoint():
    """Resume the LLM worker."""
    state = await read_state("llm")
    if not state.get("running"):
        raise HTTPException(status_code=503, detail="LLM worker not running")
    if not state.get("paused"):
        return {"status": "already_running", "message": "LLM worker is not paused"}

    # Try local resume first
    from services.llm_worker import get_worker
    worker = get_worker()
    if worker and worker._running:
        await worker.resume()
        return {"status": "resumed", "message": "LLM worker resumed"}

    await write_command("llm", "resume")
    return {"status": "command_queued", "message": "Resume command queued for leader process"}


# =============================================================================
# Classifier Worker Control
# =============================================================================


@router.post("/admin/classifier-worker/start")
async def start_classifier_worker_endpoint():
    """Start the classifier worker."""
    from services.classifier_worker import get_classifier_worker, start_classifier_worker

    worker = get_classifier_worker()
    if worker and worker._running:
        return {"status": "already_running", "message": "Classifier worker is already running"}

    state = await read_state("classifier")
    if state.get("running"):
        return {"status": "already_running", "message": "Classifier worker is already running"}

    if worker is not None or get_classifier_worker() is not None:
        await start_classifier_worker()
        return {"status": "started", "message": "Classifier worker started"}

    await write_command("classifier", "start")
    return {"status": "command_queued", "message": "Start command queued for leader process"}


@router.post("/admin/classifier-worker/stop")
async def stop_classifier_worker_endpoint():
    """Stop the classifier worker."""
    state = await read_state("classifier")
    if not state.get("running"):
        return {"status": "already_stopped", "message": "Classifier worker is not running"}

    from services.classifier_worker import get_classifier_worker, stop_classifier_worker
    worker = get_classifier_worker()
    if worker and worker._running:
        await stop_classifier_worker()
        return {"status": "stopped", "message": "Classifier worker stopped"}

    await write_command("classifier", "stop")
    return {"status": "command_queued", "message": "Stop command queued for leader process"}


@router.post("/admin/classifier-worker/pause")
async def pause_classifier_worker_endpoint():
    """Pause the classifier worker."""
    state = await read_state("classifier")
    if not state.get("running"):
        raise HTTPException(status_code=503, detail="Classifier worker not running")
    if state.get("paused"):
        return {"status": "already_paused", "message": "Classifier worker is already paused"}

    from services.classifier_worker import get_classifier_worker
    worker = get_classifier_worker()
    if worker and worker._running:
        await worker.pause()
        return {"status": "paused", "message": "Classifier worker paused"}

    await write_command("classifier", "pause")
    return {"status": "command_queued", "message": "Pause command queued for leader process"}


@router.post("/admin/classifier-worker/resume")
async def resume_classifier_worker_endpoint():
    """Resume the classifier worker."""
    state = await read_state("classifier")
    if not state.get("running"):
        raise HTTPException(status_code=503, detail="Classifier worker not running")
    if not state.get("paused"):
        return {"status": "already_running", "message": "Classifier worker is not paused"}

    from services.classifier_worker import get_classifier_worker
    worker = get_classifier_worker()
    if worker and worker._running:
        await worker.resume()
        return {"status": "resumed", "message": "Classifier worker resumed"}

    await write_command("classifier", "resume")
    return {"status": "command_queued", "message": "Resume command queued for leader process"}
