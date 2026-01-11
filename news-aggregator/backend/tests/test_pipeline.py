"""Tests for the item processing pipeline."""

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from models import Channel, ConnectorType, Item, Priority, Rule, RuleType, Source
from services.pipeline import Pipeline, RawItem


class TestRawItem:
    """Tests for RawItem dataclass."""

    def test_create_raw_item(self):
        """Test creating a RawItem."""
        item = RawItem(
            external_id="123",
            title="Test Title",
            content="Test content",
            url="https://example.com",
        )

        assert item.external_id == "123"
        assert item.title == "Test Title"
        assert item.author is None
        assert item.published_at is not None

    def test_raw_item_with_metadata(self):
        """Test RawItem with metadata."""
        item = RawItem(
            external_id="123",
            title="Test",
            content="Content",
            url="https://example.com",
            metadata={"source": "twitter", "likes": 100},
        )

        assert item.metadata["source"] == "twitter"
        assert item.metadata["likes"] == 100


class TestPipeline:
    """Tests for Pipeline class."""

    @pytest.mark.asyncio
    async def test_normalize_content_strips_html(self, db_session: AsyncSession):
        """Test that HTML is stripped from content."""
        pipeline = Pipeline(db_session)

        raw = RawItem(
            external_id="1",
            title="<b>Bold Title</b>",
            content="<p>Paragraph with <a href='#'>link</a></p>",
            url="https://example.com",
        )

        normalized = pipeline._normalize_content(raw)

        assert "<" not in normalized.title
        assert "<" not in normalized.content
        assert "Bold Title" in normalized.title
        assert "Paragraph with link" in normalized.content

    @pytest.mark.asyncio
    async def test_normalize_content_fixes_entities(self, db_session: AsyncSession):
        """Test that HTML entities are decoded."""
        pipeline = Pipeline(db_session)

        raw = RawItem(
            external_id="1",
            title="Test &amp; Title",
            content="Content with &lt;tags&gt; and &quot;quotes&quot;",
            url="https://example.com",
        )

        normalized = pipeline._normalize_content(raw)

        assert "&" in normalized.title
        assert "<tags>" in normalized.content
        assert '"quotes"' in normalized.content

    @pytest.mark.asyncio
    async def test_normalize_content_normalizes_whitespace(
        self, db_session: AsyncSession
    ):
        """Test that whitespace is normalized."""
        pipeline = Pipeline(db_session)

        raw = RawItem(
            external_id="1",
            title="Title   with   spaces",
            content="Content\n\n\nwith\t\tnewlines",
            url="https://example.com",
        )

        normalized = pipeline._normalize_content(raw)

        assert "  " not in normalized.title
        assert "\n" not in normalized.content
        assert "\t" not in normalized.content

    @pytest.mark.asyncio
    async def test_compute_hash(self, db_session: AsyncSession):
        """Test content hash computation."""
        pipeline = Pipeline(db_session)

        hash1 = pipeline._compute_hash("Hello World")
        hash2 = pipeline._compute_hash("Hello World")
        hash3 = pipeline._compute_hash("Different content")

        assert hash1 == hash2
        assert hash1 != hash3
        assert len(hash1) == 64  # SHA-256 hex

    @pytest.mark.asyncio
    async def test_is_duplicate_by_external_id(self, db_session: AsyncSession):
        """Test duplicate detection by external_id."""
        source = Source(name="Test")
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(channel)
        await db_session.flush()

        # Create existing item
        existing = Item(
            channel_id=channel.id,
            external_id="existing-123",
            title="Existing",
            content="Content",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="somehash",
        )
        db_session.add(existing)
        await db_session.flush()

        pipeline = Pipeline(db_session)

        # Same external_id should be duplicate
        is_dup = await pipeline._is_duplicate(channel.id, "existing-123", "differenthash")
        assert is_dup is True

        # Different external_id should not be duplicate
        is_dup = await pipeline._is_duplicate(channel.id, "new-456", "differenthash")
        assert is_dup is False

    @pytest.mark.asyncio
    async def test_is_duplicate_by_content_hash(self, db_session: AsyncSession):
        """Test duplicate detection by content hash."""
        source = Source(name="Test")
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(channel)
        await db_session.flush()

        content_hash = "abc123def456"
        existing = Item(
            channel_id=channel.id,
            external_id="original",
            title="Original",
            content="Same content",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash=content_hash,
        )
        db_session.add(existing)
        await db_session.flush()

        pipeline = Pipeline(db_session)

        # Same content hash should be duplicate
        is_dup = await pipeline._is_duplicate(channel.id, "different-id", content_hash)
        assert is_dup is True

    @pytest.mark.asyncio
    async def test_match_keyword_rule(self, db_session: AsyncSession):
        """Test keyword rule matching."""
        pipeline = Pipeline(db_session)

        rule = Rule(
            name="Test",
            rule_type=RuleType.KEYWORD,
            pattern="kürzung, streichung, haushalt",
        )

        item_match = Item(
            channel_id=1,
            external_id="1",
            title="Haushaltskürzung angekündigt",
            content="Die Regierung plant Kürzungen",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="hash1",
        )

        item_no_match = Item(
            channel_id=1,
            external_id="2",
            title="Wetter morgen",
            content="Es wird sonnig",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="hash2",
        )

        assert pipeline._match_rule(rule, item_match) is True
        assert pipeline._match_rule(rule, item_no_match) is False

    @pytest.mark.asyncio
    async def test_match_regex_rule(self, db_session: AsyncSession):
        """Test regex rule matching."""
        pipeline = Pipeline(db_session)

        rule = Rule(
            name="Budget Pattern",
            rule_type=RuleType.REGEX,
            pattern=r"\d+\s*(million|mio|mrd).*euro",
        )

        item_match = Item(
            channel_id=1,
            external_id="1",
            title="Budget News",
            content="Das Projekt kostet 50 Millionen Euro",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="hash1",
        )

        item_no_match = Item(
            channel_id=1,
            external_id="2",
            title="Other News",
            content="Keine Zahlen hier",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="hash2",
        )

        assert pipeline._match_rule(rule, item_match) is True
        assert pipeline._match_rule(rule, item_no_match) is False

    @pytest.mark.asyncio
    async def test_score_to_priority(self, db_session: AsyncSession):
        """Test score to priority conversion."""
        pipeline = Pipeline(db_session)

        assert pipeline._score_to_priority(95) == Priority.HIGH
        assert pipeline._score_to_priority(90) == Priority.HIGH
        assert pipeline._score_to_priority(85) == Priority.MEDIUM
        assert pipeline._score_to_priority(70) == Priority.MEDIUM
        assert pipeline._score_to_priority(60) == Priority.LOW
        assert pipeline._score_to_priority(51) == Priority.LOW
        assert pipeline._score_to_priority(50) == Priority.NONE
        assert pipeline._score_to_priority(0) == Priority.NONE

    @pytest.mark.asyncio
    async def test_process_items(self, db_session: AsyncSession):
        """Test full pipeline processing."""
        source = Source(name="Test")
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(channel)

        rule = Rule(
            name="Urgent",
            rule_type=RuleType.KEYWORD,
            pattern="dringend, wichtig",
            priority_boost=30,
            enabled=True,
        )
        db_session.add(rule)
        await db_session.flush()

        raw_items = [
            RawItem(
                external_id="1",
                title="Wichtige Meldung",
                content="Das ist dringend",
                url="https://example.com/1",
            ),
            RawItem(
                external_id="2",
                title="Normale Meldung",
                content="Nichts besonderes",
                url="https://example.com/2",
            ),
        ]

        pipeline = Pipeline(db_session)
        new_items = await pipeline.process(raw_items, channel)

        assert len(new_items) == 2

        # First item should have higher priority due to rule match
        urgent_item = next(i for i in new_items if i.external_id == "1")
        normal_item = next(i for i in new_items if i.external_id == "2")

        assert urgent_item.priority_score > normal_item.priority_score
        assert urgent_item.priority in [Priority.HIGH, Priority.MEDIUM]

    @pytest.mark.asyncio
    async def test_process_skips_duplicates(self, db_session: AsyncSession):
        """Test that duplicates are skipped."""
        source = Source(name="Test")
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(channel)
        await db_session.flush()

        # Create existing item
        existing = Item(
            channel_id=channel.id,
            external_id="existing-1",
            title="Existing",
            content="Content",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="hash1",
        )
        db_session.add(existing)
        await db_session.flush()

        raw_items = [
            RawItem(
                external_id="existing-1",  # Duplicate
                title="Existing",
                content="Content",
                url="https://example.com",
            ),
            RawItem(
                external_id="new-1",  # New
                title="New Article",
                content="New content",
                url="https://example.com/new",
            ),
        ]

        pipeline = Pipeline(db_session)
        new_items = await pipeline.process(raw_items, channel)

        assert len(new_items) == 1
        assert new_items[0].external_id == "new-1"
