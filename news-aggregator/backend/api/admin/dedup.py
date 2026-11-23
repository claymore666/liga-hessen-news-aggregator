"""Admin endpoints for repairing duplicate groups."""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import dedup_rebuild
from services.worker_status import read_state

logger = logging.getLogger(__name__)
router = APIRouter()


class DedupRebuildRequest(BaseModel):
    """Range of items whose duplicate groups are rebuilt."""

    since: datetime = Field(description="Rebuild items with fetched_at >= since (UTC)")
    until: datetime | None = Field(
        default=None, description="... and fetched_at < until (UTC); null = up to now"
    )
    dry_run: bool = Field(
        default=True, description="Only report what would change; write nothing"
    )


class DedupRebuildProgress(BaseModel):
    processed: int = 0
    total: int = 0


class DedupRebuildStatus(BaseModel):
    """Status of the current or last rebuild run."""

    state: str = Field(description="idle, running, done or failed")
    dry_run: bool | None = None
    since: str | None = None
    until: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    progress: DedupRebuildProgress = Field(default_factory=DedupRebuildProgress)
    result: dict | None = Field(
        default=None, description="Summary of the finished run (see services.dedup_rebuild)"
    )
    error: str | None = None


async def _dedup_worker_active() -> bool:
    """True if the dedup worker is running and not paused (in any process)."""
    from services.dedup_worker import get_dedup_worker

    worker = get_dedup_worker()
    if worker is not None and worker._running:
        return not worker._paused

    state = await read_state("dedup")
    return bool(state.get("running")) and not state.get("paused")


@router.post("/admin/dedup/rebuild", response_model=DedupRebuildStatus, status_code=202)
async def start_dedup_rebuild(request: DedupRebuildRequest) -> DedupRebuildStatus:
    """Rebuild the duplicate groups of the items fetched in [since, until).

    Starts one background run and returns immediately; poll
    GET /admin/dedup/rebuild for progress and the result. ``dry_run`` (default)
    only reports the differences. A real run requires the dedup worker to be
    paused or stopped, so the two do not write links at the same time.
    """
    since = dedup_rebuild._naive_utc(request.since)
    until = dedup_rebuild._naive_utc(request.until)
    if until is not None and until <= since:
        raise HTTPException(status_code=422, detail="until must be later than since")

    status = await dedup_rebuild.get_rebuild_status()
    if status["state"] == "running":
        raise HTTPException(status_code=409, detail="A dedup rebuild is already running")

    if not request.dry_run and await _dedup_worker_active():
        raise HTTPException(
            status_code=409,
            detail=(
                "Dedup worker is running. Pause it first "
                "(POST /api/admin/dedup-worker/pause) or use dry_run."
            ),
        )

    from services.relevance_filter import create_relevance_filter

    classifier = await create_relevance_filter()
    if classifier is None:
        raise HTTPException(status_code=503, detail="Classifier service unavailable")

    try:
        started = await dedup_rebuild.start_rebuild(since, until, request.dry_run, classifier)
    except dedup_rebuild.RebuildAlreadyRunning:
        await classifier.close()
        raise HTTPException(status_code=409, detail="A dedup rebuild is already running")

    logger.info(
        f"Dedup rebuild started: since={since} until={until} dry_run={request.dry_run}"
    )
    return DedupRebuildStatus(**started)


@router.get("/admin/dedup/rebuild", response_model=DedupRebuildStatus)
async def get_dedup_rebuild() -> DedupRebuildStatus:
    """Status, progress and last result of the dedup rebuild."""
    return DedupRebuildStatus(**await dedup_rebuild.get_rebuild_status())
