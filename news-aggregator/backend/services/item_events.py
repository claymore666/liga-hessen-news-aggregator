"""Item event recording service for audit trail."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from models import ItemEvent

logger = logging.getLogger(__name__)

# Event type constants
EVENT_CREATED = "created"
EVENT_CLASSIFIER_PROCESSED = "classifier_processed"
EVENT_LLM_PROCESSED = "llm_processed"
EVENT_LLM_REPROCESSED = "llm_reprocessed"
EVENT_USER_MODIFIED = "user_modified"
EVENT_PRIORITY_CHANGED = "priority_changed"
EVENT_AK_CHANGED = "ak_changed"
EVENT_READ = "read"
EVENT_ARCHIVED = "archived"
EVENT_STARRED = "starred"
EVENT_DUPLICATE_DETECTED = "duplicate_detected"


async def record_event(
    db: AsyncSession,
    item_id: int,
    event_type: str,
    data: dict[str, Any] | None = None,
    ip_address: str | None = None,
    session_id: str | None = None,
) -> ItemEvent:
    """
    Record an event for an item.

    Args:
        db: Database session
        item_id: ID of the item
        event_type: Type of event (use EVENT_* constants)
        data: Optional event-specific data
        ip_address: Optional client IP address
        session_id: Optional session identifier

    Returns:
        The created ItemEvent
    """
    event = ItemEvent(
        item_id=item_id,
        event_type=event_type,
        data=data,
        ip_address=ip_address,
        session_id=session_id,
    )
    db.add(event)
    # Note: No flush here - caller manages transaction.
    # Flushing inside a loop causes greenlet_spawn errors.
    logger.debug(f"Recorded event {event_type} for item {item_id}")
    return event


def record_events_batch(
    db: AsyncSession,
    events_data: list[dict[str, Any]],
) -> list[ItemEvent]:
    """
    Record multiple events in a batch (synchronous add_all).

    Args:
        db: Database session
        events_data: List of dicts with keys: item_id, event_type, data (optional),
                     ip_address (optional), session_id (optional)

    Returns:
        List of created ItemEvent objects
    """
    events = []
    for ed in events_data:
        event = ItemEvent(
            item_id=ed["item_id"],
            event_type=ed["event_type"],
            data=ed.get("data"),
            ip_address=ed.get("ip_address"),
            session_id=ed.get("session_id"),
        )
        events.append(event)

    db.add_all(events)
    logger.debug(f"Recorded {len(events)} events in batch")
    return events
