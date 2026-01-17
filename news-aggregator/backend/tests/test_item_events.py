"""Tests for item event recording service."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.item_events import (
    record_event,
    EVENT_CREATED,
    EVENT_CLASSIFIER_PROCESSED,
    EVENT_LLM_PROCESSED,
    EVENT_LLM_REPROCESSED,
    EVENT_USER_MODIFIED,
    EVENT_PRIORITY_CHANGED,
    EVENT_AK_CHANGED,
    EVENT_READ,
    EVENT_ARCHIVED,
    EVENT_STARRED,
    EVENT_DUPLICATE_DETECTED,
)


class TestEventConstants:
    """Tests for event type constants."""

    def test_event_constants_exist(self):
        """All event constants should be defined."""
        assert EVENT_CREATED == "created"
        assert EVENT_CLASSIFIER_PROCESSED == "classifier_processed"
        assert EVENT_LLM_PROCESSED == "llm_processed"
        assert EVENT_LLM_REPROCESSED == "llm_reprocessed"
        assert EVENT_USER_MODIFIED == "user_modified"
        assert EVENT_PRIORITY_CHANGED == "priority_changed"
        assert EVENT_AK_CHANGED == "ak_changed"
        assert EVENT_READ == "read"
        assert EVENT_ARCHIVED == "archived"
        assert EVENT_STARRED == "starred"
        assert EVENT_DUPLICATE_DETECTED == "duplicate_detected"

    def test_event_constants_unique(self):
        """All event constants should be unique."""
        events = [
            EVENT_CREATED,
            EVENT_CLASSIFIER_PROCESSED,
            EVENT_LLM_PROCESSED,
            EVENT_LLM_REPROCESSED,
            EVENT_USER_MODIFIED,
            EVENT_PRIORITY_CHANGED,
            EVENT_AK_CHANGED,
            EVENT_READ,
            EVENT_ARCHIVED,
            EVENT_STARRED,
            EVENT_DUPLICATE_DETECTED,
        ]
        assert len(events) == len(set(events))


class TestRecordEvent:
    """Tests for record_event function."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database session."""
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_record_event_basic(self, mock_db):
        """Should create and add event to session."""
        event = await record_event(
            db=mock_db,
            item_id=123,
            event_type=EVENT_CREATED,
        )

        assert event.item_id == 123
        assert event.event_type == EVENT_CREATED
        assert event.data is None
        mock_db.add.assert_called_once_with(event)
        # Note: flush is NOT called - caller manages transaction

    @pytest.mark.asyncio
    async def test_record_event_with_data(self, mock_db):
        """Should include data in event."""
        data = {"old_priority": "low", "new_priority": "high"}
        event = await record_event(
            db=mock_db,
            item_id=123,
            event_type=EVENT_PRIORITY_CHANGED,
            data=data,
        )

        assert event.data == data

    @pytest.mark.asyncio
    async def test_record_event_with_ip(self, mock_db):
        """Should include IP address in event."""
        event = await record_event(
            db=mock_db,
            item_id=123,
            event_type=EVENT_USER_MODIFIED,
            ip_address="192.168.1.1",
        )

        assert event.ip_address == "192.168.1.1"

    @pytest.mark.asyncio
    async def test_record_event_with_session(self, mock_db):
        """Should include session ID in event."""
        event = await record_event(
            db=mock_db,
            item_id=123,
            event_type=EVENT_READ,
            session_id="abc123",
        )

        assert event.session_id == "abc123"

    @pytest.mark.asyncio
    async def test_record_event_all_params(self, mock_db):
        """Should handle all parameters together."""
        event = await record_event(
            db=mock_db,
            item_id=456,
            event_type=EVENT_LLM_PROCESSED,
            data={"score": 0.85},
            ip_address="10.0.0.1",
            session_id="session456",
        )

        assert event.item_id == 456
        assert event.event_type == EVENT_LLM_PROCESSED
        assert event.data == {"score": 0.85}
        assert event.ip_address == "10.0.0.1"
        assert event.session_id == "session456"

    @pytest.mark.asyncio
    async def test_record_event_classifier(self, mock_db):
        """Should record classifier events."""
        event = await record_event(
            db=mock_db,
            item_id=789,
            event_type=EVENT_CLASSIFIER_PROCESSED,
            data={
                "confidence": 0.75,
                "priority": "medium",
                "ak_suggestion": "Altenhilfe",
            },
        )

        assert event.event_type == EVENT_CLASSIFIER_PROCESSED
        assert event.data["confidence"] == 0.75

    @pytest.mark.asyncio
    async def test_record_event_duplicate(self, mock_db):
        """Should record duplicate detection events."""
        event = await record_event(
            db=mock_db,
            item_id=101,
            event_type=EVENT_DUPLICATE_DETECTED,
            data={
                "similar_to_id": 50,
                "similarity_score": 0.95,
            },
        )

        assert event.event_type == EVENT_DUPLICATE_DETECTED
        assert event.data["similar_to_id"] == 50


class TestEventTypes:
    """Tests for different event types usage."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database session."""
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_created_event(self, mock_db):
        """Should record item creation."""
        event = await record_event(
            db=mock_db,
            item_id=1,
            event_type=EVENT_CREATED,
            data={"source": "rss", "channel_id": 5},
        )
        assert event.event_type == "created"

    @pytest.mark.asyncio
    async def test_starred_event(self, mock_db):
        """Should record starring action."""
        event = await record_event(
            db=mock_db,
            item_id=1,
            event_type=EVENT_STARRED,
            data={"starred": True},
        )
        assert event.event_type == "starred"

    @pytest.mark.asyncio
    async def test_ak_changed_event(self, mock_db):
        """Should record AK assignment changes."""
        event = await record_event(
            db=mock_db,
            item_id=1,
            event_type=EVENT_AK_CHANGED,
            data={
                "old_aks": ["Altenhilfe"],
                "new_aks": ["Altenhilfe", "Pflege"],
            },
        )
        assert event.event_type == "ak_changed"
        assert event.data["new_aks"] == ["Altenhilfe", "Pflege"]
