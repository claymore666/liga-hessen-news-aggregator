"""Digest API endpoints for generating, previewing, and sending daily digests."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

logger = logging.getLogger(__name__)
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_admin_key
from database import get_db
from models import Digest, DigestStatus

router = APIRouter(prefix="/digest", tags=["digest"])


class DigestResponse(BaseModel):
    id: int
    date: str
    status: str
    item_count: int
    llm_model: str | None = None
    llm_prompt_version: int | None = None
    generation_time_ms: int | None = None
    recipients: list[str] = []
    error_message: str | None = None
    created_at: str | None = None
    sent_at: str | None = None


class DigestDetailResponse(DigestResponse):
    content: dict | None = None
    item_ids: list[int] = []


def _digest_response(d: Digest, detail: bool = False) -> dict:
    resp = {
        "id": d.id,
        "date": d.date.isoformat() if d.date else None,
        "status": d.status.value if hasattr(d.status, "value") else str(d.status),
        "item_count": len(d.item_ids) if d.item_ids else 0,
        "llm_model": d.llm_model,
        "llm_prompt_version": d.llm_prompt_version,
        "generation_time_ms": d.generation_time_ms,
        "recipients": d.recipients or [],
        "error_message": d.error_message,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "sent_at": d.sent_at.isoformat() if d.sent_at else None,
    }
    if detail:
        resp["content"] = d.content
        resp["item_ids"] = d.item_ids or []
    return resp


@router.post("/generate", dependencies=[Depends(require_admin_key)])
async def generate_digest_endpoint(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Generate a new digest for today. Returns digest ID and preview info."""
    from services.digest import generate_digest

    try:
        digest_id = await generate_digest(db)
    except Exception as e:
        logger.error(f"Failed to generate digest: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Digest generation failed")

    digest = await db.get(Digest, digest_id)
    if not digest:
        raise HTTPException(status_code=500, detail="Digest created but not found")

    return {
        "success": digest.status != DigestStatus.FAILED,
        "digest": _digest_response(digest, detail=True),
    }


@router.post("/send/{digest_id}", dependencies=[Depends(require_admin_key)])
async def send_digest_endpoint(
    digest_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Send a generated digest via email."""
    from services.digest import send_digest

    try:
        success = await send_digest(db, digest_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to send digest {digest_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Digest sending failed")

    digest = await db.get(Digest, digest_id)
    return {
        "success": success,
        "digest": _digest_response(digest) if digest else None,
    }


@router.get("/preview/{digest_id}", response_class=HTMLResponse)
async def preview_digest(
    digest_id: int,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Return the rendered HTML for a digest (for browser preview)."""
    digest = await db.get(Digest, digest_id)
    if not digest:
        raise HTTPException(status_code=404, detail="Digest not found")
    if not digest.html_body:
        raise HTTPException(status_code=404, detail="Digest has no rendered HTML")
    return HTMLResponse(content=digest.html_body)


@router.get("/history")
async def digest_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List past digests (paginated, newest first)."""
    total = await db.scalar(select(func.count(Digest.id)))
    query = (
        select(Digest)
        .order_by(Digest.date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    digests = result.scalars().all()

    return {
        "total": total or 0,
        "page": page,
        "page_size": page_size,
        "digests": [_digest_response(d) for d in digests],
    }


@router.get("/{digest_id}")
async def get_digest(
    digest_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get full digest details including content JSON."""
    digest = await db.get(Digest, digest_id)
    if not digest:
        raise HTTPException(status_code=404, detail="Digest not found")
    return _digest_response(digest, detail=True)
