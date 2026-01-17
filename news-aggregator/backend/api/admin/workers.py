"""Admin endpoints for worker control."""

import logging

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()


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

    await start_worker()
    return {"status": "started", "message": "LLM worker started"}


@router.post("/admin/llm-worker/stop")
async def stop_llm_worker_endpoint():
    """Stop the LLM worker."""
    from services.llm_worker import get_worker, stop_worker

    worker = get_worker()
    if not worker or not worker._running:
        return {"status": "already_stopped", "message": "LLM worker is not running"}

    await stop_worker()
    return {"status": "stopped", "message": "LLM worker stopped"}


@router.post("/admin/llm-worker/pause")
async def pause_llm_worker_endpoint():
    """Pause the LLM worker."""
    from services.llm_worker import get_worker

    worker = get_worker()
    if not worker:
        raise HTTPException(status_code=503, detail="LLM worker not running")

    if worker._paused:
        return {"status": "already_paused", "message": "LLM worker is already paused"}

    worker.pause()
    return {"status": "paused", "message": "LLM worker paused"}


@router.post("/admin/llm-worker/resume")
async def resume_llm_worker_endpoint():
    """Resume the LLM worker."""
    from services.llm_worker import get_worker

    worker = get_worker()
    if not worker:
        raise HTTPException(status_code=503, detail="LLM worker not running")

    if not worker._paused:
        return {"status": "already_running", "message": "LLM worker is not paused"}

    worker.resume()
    return {"status": "resumed", "message": "LLM worker resumed"}


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

    await start_classifier_worker()
    return {"status": "started", "message": "Classifier worker started"}


@router.post("/admin/classifier-worker/stop")
async def stop_classifier_worker_endpoint():
    """Stop the classifier worker."""
    from services.classifier_worker import get_classifier_worker, stop_classifier_worker

    worker = get_classifier_worker()
    if not worker or not worker._running:
        return {"status": "already_stopped", "message": "Classifier worker is not running"}

    await stop_classifier_worker()
    return {"status": "stopped", "message": "Classifier worker stopped"}


@router.post("/admin/classifier-worker/pause")
async def pause_classifier_worker_endpoint():
    """Pause the classifier worker."""
    from services.classifier_worker import get_classifier_worker

    worker = get_classifier_worker()
    if not worker:
        raise HTTPException(status_code=503, detail="Classifier worker not running")

    if worker._paused:
        return {"status": "already_paused", "message": "Classifier worker is already paused"}

    worker.pause()
    return {"status": "paused", "message": "Classifier worker paused"}


@router.post("/admin/classifier-worker/resume")
async def resume_classifier_worker_endpoint():
    """Resume the classifier worker."""
    from services.classifier_worker import get_classifier_worker

    worker = get_classifier_worker()
    if not worker:
        raise HTTPException(status_code=503, detail="Classifier worker not running")

    if not worker._paused:
        return {"status": "already_running", "message": "Classifier worker is not paused"}

    worker.resume()
    return {"status": "resumed", "message": "Classifier worker resumed"}
