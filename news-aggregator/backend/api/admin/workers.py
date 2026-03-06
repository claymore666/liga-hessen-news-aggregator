"""Admin endpoints for worker control."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.worker_status import read_state, write_command

logger = logging.getLogger(__name__)
router = APIRouter()


# =============================================================================
# Classifier Bypass
# =============================================================================


async def _get_classifier_bypass() -> bool:
    """Read classifier_bypass setting from DB."""
    from database import async_session_maker
    from models import Setting
    from sqlalchemy import select

    async with async_session_maker() as db:
        setting = await db.scalar(
            select(Setting.value).where(Setting.key == "classifier_bypass")
        )
        return bool(setting) if setting is not None else False


async def _set_classifier_bypass(enabled: bool) -> None:
    """Write classifier_bypass setting to DB."""
    from database import async_session_maker
    from models import Setting
    from sqlalchemy import select

    async with async_session_maker() as db:
        existing = await db.scalar(
            select(Setting).where(Setting.key == "classifier_bypass")
        )
        if existing:
            existing.value = enabled
        else:
            db.add(Setting(
                key="classifier_bypass",
                value=enabled,
                description="Bypass classifier pre-filter, semantic dedup, and vector indexing",
            ))
        await db.commit()


@router.get("/admin/classifier-bypass")
async def get_classifier_bypass():
    """Get current classifier bypass state."""
    bypassed = await _get_classifier_bypass()
    return {"bypassed": bypassed}


@router.post("/admin/classifier-bypass")
async def set_classifier_bypass(request: dict):
    """Toggle classifier bypass on/off.

    When bypassed, all items go directly to LLM without pre-filtering,
    semantic duplicate detection, or vector indexing (all require Ollama embeddings).
    URL-based duplicate detection still works.
    """
    bypassed = request.get("bypassed", False)
    await _set_classifier_bypass(bypassed)
    logger.info(f"Classifier bypass {'enabled' if bypassed else 'disabled'}")
    return {"bypassed": bypassed, "message": f"Classifier bypass {'enabled' if bypassed else 'disabled'}"}


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
    """Start the LLM worker."""
    from services.llm_worker import get_worker, start_worker

    worker = get_worker()
    if worker and worker._running:
        return {"status": "already_running", "message": "LLM worker is already running"}

    state = await read_state("llm")
    if state.get("running"):
        return {"status": "already_running", "message": "LLM worker is already running"}

    await start_worker()
    return {"status": "started", "message": "LLM worker started"}


@router.post("/admin/llm-worker/stop")
async def stop_llm_worker_endpoint():
    """Stop the LLM worker."""
    from services.llm_worker import get_worker, stop_worker

    worker = get_worker()
    if worker and worker._running:
        await stop_worker()
        return {"status": "stopped", "message": "LLM worker stopped"}

    # Worker not on this process - queue command for leader
    state = await read_state("llm")
    if not state.get("running"):
        return {"status": "already_stopped", "message": "LLM worker is not running"}

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

    await start_classifier_worker()
    return {"status": "started", "message": "Classifier worker started"}


@router.post("/admin/classifier-worker/stop")
async def stop_classifier_worker_endpoint():
    """Stop the classifier worker."""
    from services.classifier_worker import get_classifier_worker, stop_classifier_worker

    worker = get_classifier_worker()
    if worker and worker._running:
        await stop_classifier_worker()
        return {"status": "stopped", "message": "Classifier worker stopped"}

    state = await read_state("classifier")
    if not state.get("running"):
        return {"status": "already_stopped", "message": "Classifier worker is not running"}

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


# =============================================================================
# Dedup Worker Control
# =============================================================================


@router.post("/admin/dedup-worker/start")
async def start_dedup_worker_endpoint():
    """Start the dedup worker."""
    from services.dedup_worker import get_dedup_worker, start_dedup_worker

    worker = get_dedup_worker()
    if worker and worker._running:
        return {"status": "already_running", "message": "Dedup worker is already running"}

    state = await read_state("dedup")
    if state.get("running"):
        return {"status": "already_running", "message": "Dedup worker is already running"}

    await start_dedup_worker()
    return {"status": "started", "message": "Dedup worker started"}


@router.post("/admin/dedup-worker/stop")
async def stop_dedup_worker_endpoint():
    """Stop the dedup worker."""
    from services.dedup_worker import get_dedup_worker, stop_dedup_worker

    worker = get_dedup_worker()
    if worker and worker._running:
        await stop_dedup_worker()
        return {"status": "stopped", "message": "Dedup worker stopped"}

    state = await read_state("dedup")
    if not state.get("running"):
        return {"status": "already_stopped", "message": "Dedup worker is not running"}

    await write_command("dedup", "stop")
    return {"status": "command_queued", "message": "Stop command queued for leader process"}


@router.post("/admin/dedup-worker/pause")
async def pause_dedup_worker_endpoint():
    """Pause the dedup worker."""
    state = await read_state("dedup")
    if not state.get("running"):
        raise HTTPException(status_code=503, detail="Dedup worker not running")
    if state.get("paused"):
        return {"status": "already_paused", "message": "Dedup worker is already paused"}

    from services.dedup_worker import get_dedup_worker
    worker = get_dedup_worker()
    if worker and worker._running:
        await worker.pause()
        return {"status": "paused", "message": "Dedup worker paused"}

    await write_command("dedup", "pause")
    return {"status": "command_queued", "message": "Pause command queued for leader process"}


@router.post("/admin/dedup-worker/resume")
async def resume_dedup_worker_endpoint():
    """Resume the dedup worker."""
    state = await read_state("dedup")
    if not state.get("running"):
        raise HTTPException(status_code=503, detail="Dedup worker not running")
    if not state.get("paused"):
        return {"status": "already_running", "message": "Dedup worker is not paused"}

    from services.dedup_worker import get_dedup_worker
    worker = get_dedup_worker()
    if worker and worker._running:
        await worker.resume()
        return {"status": "resumed", "message": "Dedup worker resumed"}

    await write_command("dedup", "resume")
    return {"status": "command_queued", "message": "Resume command queued for leader process"}
