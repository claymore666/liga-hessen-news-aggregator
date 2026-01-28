"""Message of the Day API endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import MOTD

router = APIRouter(prefix="/motd", tags=["motd"])


class MOTDResponse(BaseModel):
    """Response with current MOTD."""

    id: int | None = None
    message: str | None = None
    active: bool = False
    updated_at: str | None = None
    expires_at: str | None = None


class MOTDCreate(BaseModel):
    """Request to create/update MOTD."""

    message: str
    active: bool = True
    expires_at: datetime | None = None


class MOTDUpdateResponse(BaseModel):
    """Response after updating MOTD."""

    success: bool
    message: str
    motd: MOTDResponse | None = None


@router.get("", response_model=MOTDResponse)
async def get_motd(db: AsyncSession = Depends(get_db)) -> MOTDResponse:
    """Get current active MOTD.

    Returns the most recent active MOTD that hasn't expired.
    Used by frontend to display message of the day.
    """
    now = datetime.utcnow()

    # Get most recent active MOTD that hasn't expired
    result = await db.execute(
        select(MOTD)
        .where(MOTD.active == True)  # noqa: E712
        .where((MOTD.expires_at == None) | (MOTD.expires_at > now))  # noqa: E711
        .order_by(MOTD.updated_at.desc())
        .limit(1)
    )
    motd = result.scalar_one_or_none()

    if not motd:
        return MOTDResponse(active=False)

    return MOTDResponse(
        id=motd.id,
        message=motd.message,
        active=motd.active,
        updated_at=motd.updated_at.isoformat() if motd.updated_at else None,
        expires_at=motd.expires_at.isoformat() if motd.expires_at else None,
    )


@router.post("/admin", response_model=MOTDUpdateResponse)
async def set_motd(
    data: MOTDCreate,
    db: AsyncSession = Depends(get_db),
) -> MOTDUpdateResponse:
    """Set or update the MOTD (admin only).

    Creates a new MOTD entry. Previous MOTDs are deactivated.
    """
    # Deactivate all existing MOTDs
    result = await db.execute(select(MOTD).where(MOTD.active == True))  # noqa: E712
    for old_motd in result.scalars().all():
        old_motd.active = False

    # Create new MOTD
    motd = MOTD(
        message=data.message,
        active=data.active,
        expires_at=data.expires_at,
    )
    db.add(motd)
    await db.commit()
    await db.refresh(motd)

    return MOTDUpdateResponse(
        success=True,
        message="MOTD updated successfully",
        motd=MOTDResponse(
            id=motd.id,
            message=motd.message,
            active=motd.active,
            updated_at=motd.updated_at.isoformat() if motd.updated_at else None,
            expires_at=motd.expires_at.isoformat() if motd.expires_at else None,
        ),
    )


@router.delete("/admin", response_model=MOTDUpdateResponse)
async def clear_motd(db: AsyncSession = Depends(get_db)) -> MOTDUpdateResponse:
    """Clear/deactivate the current MOTD (admin only)."""
    result = await db.execute(select(MOTD).where(MOTD.active == True))  # noqa: E712
    deactivated = 0
    for motd in result.scalars().all():
        motd.active = False
        deactivated += 1

    await db.commit()

    return MOTDUpdateResponse(
        success=True,
        message=f"Deactivated {deactivated} MOTD(s)",
    )


@router.get("/history", response_model=list[MOTDResponse])
async def get_motd_history(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
) -> list[MOTDResponse]:
    """Get MOTD history (admin only)."""
    result = await db.execute(
        select(MOTD).order_by(MOTD.created_at.desc()).limit(limit)
    )
    motds = result.scalars().all()

    return [
        MOTDResponse(
            id=m.id,
            message=m.message,
            active=m.active,
            updated_at=m.updated_at.isoformat() if m.updated_at else None,
            expires_at=m.expires_at.isoformat() if m.expires_at else None,
        )
        for m in motds
    ]
