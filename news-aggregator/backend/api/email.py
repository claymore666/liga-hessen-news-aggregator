"""Email API endpoints."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import Item, Priority
from services.email import BriefingEmail, EmailConfig

router = APIRouter(prefix="/email", tags=["email"])


class SendBriefingRequest(BaseModel):
    """Request to send a briefing email."""
    recipients: list[EmailStr]
    min_priority: Priority = Priority.NONE
    hours_back: int = 24
    include_read: bool = False


class SendBriefingResponse(BaseModel):
    """Response from sending a briefing email."""
    success: bool
    message: str
    items_count: int


class PreviewBriefingRequest(BaseModel):
    """Request to preview a briefing."""
    min_priority: Priority = Priority.NONE
    hours_back: int = 24
    include_read: bool = False


class PreviewBriefingResponse(BaseModel):
    """Response with briefing preview."""
    subject: str
    text_body: str
    html_body: str
    items_count: int
    items_by_priority: dict[str, int]


@router.post("/send-briefing", response_model=SendBriefingResponse)
async def send_briefing(
    request: SendBriefingRequest,
    db: AsyncSession = Depends(get_db),
):
    """Send a briefing email with recent items."""
    # Calculate time range
    since = datetime.utcnow() - timedelta(hours=request.hours_back)

    # Query items
    query = (
        select(Item)
        .options(selectinload(Item.source))
        .where(Item.fetched_at >= since)
    )

    if not request.include_read:
        query = query.where(Item.is_read == False)

    # Filter by minimum priority
    priority_values = {
        Priority.HIGH: 3,
        Priority.MEDIUM: 2,
        Priority.LOW: 1,
        Priority.NONE: 0,
    }
    min_priority_value = priority_values[request.min_priority]
    valid_priorities = [p for p, v in priority_values.items() if v >= min_priority_value]
    query = query.where(Item.priority.in_(valid_priorities))

    query = query.order_by(Item.priority_score.desc(), Item.fetched_at.desc())

    result = await db.execute(query)
    items = result.scalars().all()

    if not items:
        return SendBriefingResponse(
            success=True,
            message="Keine Meldungen im angegebenen Zeitraum",
            items_count=0,
        )

    # Send email
    config = EmailConfig(
        recipients=request.recipients,
        min_priority=request.min_priority,
    )
    briefing = BriefingEmail(config)
    success, message = briefing.send(items)

    return SendBriefingResponse(
        success=success,
        message=message,
        items_count=len(items),
    )


@router.post("/preview-briefing", response_model=PreviewBriefingResponse)
async def preview_briefing(
    request: PreviewBriefingRequest,
    db: AsyncSession = Depends(get_db),
):
    """Preview a briefing without sending."""
    # Calculate time range
    since = datetime.utcnow() - timedelta(hours=request.hours_back)

    # Query items
    query = (
        select(Item)
        .options(selectinload(Item.source))
        .where(Item.fetched_at >= since)
    )

    if not request.include_read:
        query = query.where(Item.is_read == False)

    # Filter by minimum priority
    priority_values = {
        Priority.HIGH: 3,
        Priority.MEDIUM: 2,
        Priority.LOW: 1,
        Priority.NONE: 0,
    }
    min_priority_value = priority_values[request.min_priority]
    valid_priorities = [p for p, v in priority_values.items() if v >= min_priority_value]
    query = query.where(Item.priority.in_(valid_priorities))

    query = query.order_by(Item.priority_score.desc(), Item.fetched_at.desc())

    result = await db.execute(query)
    items = result.scalars().all()

    # Generate preview
    config = EmailConfig(
        recipients=["preview@example.com"],
        min_priority=request.min_priority,
    )
    briefing = BriefingEmail(config)
    date = datetime.now()

    # Count by priority
    items_by_priority = {p.value: 0 for p in Priority}
    for item in items:
        items_by_priority[item.priority.value] += 1

    return PreviewBriefingResponse(
        subject=f"{config.subject_prefix} Briefing {date.strftime('%d.%m.%Y')}",
        text_body=briefing.generate_text_body(items, date),
        html_body=briefing.generate_html_body(items, date),
        items_count=len(items),
        items_by_priority=items_by_priority,
    )


@router.post("/test")
async def test_email(recipient: EmailStr):
    """Send a test email to verify configuration."""
    from email.mime.text import MIMEText
    import subprocess

    msg = MIMEText("Dies ist eine Test-E-Mail vom Liga News Aggregator.", "plain", "utf-8")
    msg["Subject"] = "[Liga News] Test E-Mail"
    msg["From"] = "Liga News <noreply@liga-hessen.de>"
    msg["To"] = recipient

    try:
        process = subprocess.run(
            ["/usr/sbin/sendmail", "-t"],
            input=msg.as_string(),
            capture_output=True,
            text=True,
            timeout=30,
        )

        if process.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Sendmail Fehler: {process.stderr}")

        return {"success": True, "message": f"Test-E-Mail an {recipient} gesendet"}

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Sendmail Timeout")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Sendmail nicht gefunden - läuft in Docker?")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
