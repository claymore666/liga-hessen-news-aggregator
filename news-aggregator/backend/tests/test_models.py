"""Tests for database models."""

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Channel,
    ConnectorType,
    Item,
    ItemRuleMatch,
    Priority,
    Rule,
    RuleType,
    Setting,
    Source,
)


class TestSourceModel:
    """Tests for Source model (organization-level)."""

    @pytest.mark.asyncio
    async def test_create_source(self, db_session: AsyncSession):
        """Test creating a source (organization)."""
        source = Source(
            name="Test Organization",
            description="A test organization",
            is_stakeholder=True,
            enabled=True,
        )
        db_session.add(source)
        await db_session.flush()

        assert source.id is not None
        assert source.name == "Test Organization"
        assert source.description == "A test organization"
        assert source.is_stakeholder is True
        assert source.enabled is True
        assert source.created_at is not None

    @pytest.mark.asyncio
    async def test_source_channels_relationship(self, db_session: AsyncSession):
        """Test source-channels relationship."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel1 = Channel(
            source_id=source.id,
            name="RSS Feed",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
        )
        channel2 = Channel(
            source_id=source.id,
            name="X Account",
            connector_type=ConnectorType.X_SCRAPER,
            config={"handle": "@example"},
        )
        db_session.add_all([channel1, channel2])
        await db_session.flush()
        await db_session.refresh(source, ["channels"])

        assert len(source.channels) == 2
        assert source.channels[0].source_id == source.id

    @pytest.mark.asyncio
    async def test_source_cascade_delete(self, db_session: AsyncSession):
        """Test that deleting source cascades to channels and items."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
        )
        db_session.add(channel)
        await db_session.flush()

        item = Item(
            channel_id=channel.id,
            external_id="ext-1",
            title="Test",
            content="Content",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="hash1",
        )
        db_session.add(item)
        await db_session.flush()

        # Delete source
        await db_session.delete(source)
        await db_session.flush()

        # Verify channel and item are deleted
        channel_result = await db_session.execute(select(Channel))
        assert len(channel_result.scalars().all()) == 0

        item_result = await db_session.execute(select(Item))
        assert len(item_result.scalars().all()) == 0


class TestChannelModel:
    """Tests for Channel model."""

    @pytest.mark.asyncio
    async def test_create_channel(self, db_session: AsyncSession):
        """Test creating a channel linked to a source."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Main Feed",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
            source_identifier="https://example.com/feed.xml",
            enabled=True,
            fetch_interval_minutes=30,
        )
        db_session.add(channel)
        await db_session.flush()

        assert channel.id is not None
        assert channel.source_id == source.id
        assert channel.connector_type == ConnectorType.RSS
        assert channel.enabled is True
        assert channel.created_at is not None

    @pytest.mark.asyncio
    async def test_multiple_channels_same_type(self, db_session: AsyncSession):
        """Test creating multiple RSS channels for one source (FAZ case)."""
        source = Source(name="FAZ", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel1 = Channel(
            source_id=source.id,
            name="Aktuell",
            connector_type=ConnectorType.RSS,
            config={"url": "https://faz.net/aktuell.rss"},
            source_identifier="https://faz.net/aktuell.rss",
        )
        channel2 = Channel(
            source_id=source.id,
            name="Gesellschaft",
            connector_type=ConnectorType.RSS,
            config={"url": "https://faz.net/gesellschaft.rss"},
            source_identifier="https://faz.net/gesellschaft.rss",
        )
        channel3 = Channel(
            source_id=source.id,
            name="Rhein-Main",
            connector_type=ConnectorType.RSS,
            config={"url": "https://faz.net/rhein-main.rss"},
            source_identifier="https://faz.net/rhein-main.rss",
        )
        db_session.add_all([channel1, channel2, channel3])
        await db_session.flush()

        result = await db_session.execute(
            select(Channel).where(Channel.source_id == source.id)
        )
        channels = result.scalars().all()

        assert len(channels) == 3
        assert all(c.connector_type == ConnectorType.RSS for c in channels)

    @pytest.mark.asyncio
    async def test_channel_connector_types(self, db_session: AsyncSession):
        """Test all connector types can be stored."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        for ct in ConnectorType:
            channel = Channel(
                source_id=source.id,
                connector_type=ct,
                config={},
                source_identifier=f"test-{ct.value}",
            )
            db_session.add(channel)

        await db_session.flush()

        result = await db_session.execute(select(Channel))
        channels = result.scalars().all()

        assert len(channels) == len(ConnectorType)

    @pytest.mark.asyncio
    async def test_channel_source_relationship(self, db_session: AsyncSession):
        """Test channel-source relationship."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
        )
        db_session.add(channel)
        await db_session.flush()
        await db_session.refresh(channel, ["source"])

        assert channel.source.name == "Test Org"

    @pytest.mark.asyncio
    async def test_channel_cascade_delete(self, db_session: AsyncSession):
        """Test that deleting channel cascades to items."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
        )
        db_session.add(channel)
        await db_session.flush()

        item = Item(
            channel_id=channel.id,
            external_id="ext-1",
            title="Test",
            content="Content",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="hash1",
        )
        db_session.add(item)
        await db_session.flush()

        # Delete channel
        await db_session.delete(channel)
        await db_session.flush()

        # Verify item is deleted
        item_result = await db_session.execute(select(Item))
        assert len(item_result.scalars().all()) == 0

    @pytest.mark.asyncio
    async def test_channel_config_json(self, db_session: AsyncSession):
        """Test that channel config is stored as JSON."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        config = {
            "url": "https://example.com/feed.xml",
            "custom_title": "My Feed",
            "nested": {"key": "value"},
        }
        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config=config,
        )
        db_session.add(channel)
        await db_session.flush()

        # Query back
        result = await db_session.execute(
            select(Channel).where(Channel.id == channel.id)
        )
        loaded = result.scalar_one()

        assert loaded.config == config
        assert loaded.config["nested"]["key"] == "value"


class TestItemModel:
    """Tests for Item model."""

    @pytest.mark.asyncio
    async def test_create_item(self, db_session: AsyncSession):
        """Test creating an item linked to a channel."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
        )
        db_session.add(channel)
        await db_session.flush()

        item = Item(
            channel_id=channel.id,
            external_id="ext-123",
            title="Test Article",
            content="This is the content of the test article.",
            url="https://example.com/article",
            author="Test Author",
            published_at=datetime.utcnow(),
            content_hash="abc123hash",
            priority=Priority.HIGH,
            priority_score=75,
        )
        db_session.add(item)
        await db_session.flush()

        assert item.id is not None
        assert item.channel_id == channel.id
        assert item.is_read is False
        assert item.is_starred is False

    @pytest.mark.asyncio
    async def test_item_channel_relationship(self, db_session: AsyncSession):
        """Test item-channel relationship."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            name="Main Feed",
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(channel)
        await db_session.flush()

        item = Item(
            channel_id=channel.id,
            external_id="ext-456",
            title="Test",
            content="Content",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="hash456",
        )
        db_session.add(item)
        await db_session.flush()
        await db_session.refresh(item, ["channel"])

        assert item.channel.name == "Main Feed"

    @pytest.mark.asyncio
    async def test_item_source_property(self, db_session: AsyncSession):
        """Test item.source property access through channel."""
        source = Source(name="Test Org", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(channel)
        await db_session.flush()

        item = Item(
            channel_id=channel.id,
            external_id="ext-789",
            title="Test",
            content="Content",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="hash789",
        )
        db_session.add(item)
        await db_session.flush()
        await db_session.refresh(item, ["channel"])
        await db_session.refresh(item.channel, ["source"])

        # Access source through channel
        assert item.channel.source.name == "Test Org"

    @pytest.mark.asyncio
    async def test_item_priority_levels(self, db_session: AsyncSession):
        """Test all priority levels."""
        source = Source(name="Test", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(channel)
        await db_session.flush()

        for i, priority in enumerate(Priority):
            item = Item(
                channel_id=channel.id,
                external_id=f"ext-{i}",
                title=f"Priority {priority.value}",
                content="Content",
                url=f"https://example.com/{i}",
                published_at=datetime.utcnow(),
                content_hash=f"hash{i}",
                priority=priority,
            )
            db_session.add(item)

        await db_session.flush()

        result = await db_session.execute(select(Item))
        items = result.scalars().all()

        assert len(items) == len(Priority)


class TestRuleModel:
    """Tests for Rule model."""

    @pytest.mark.asyncio
    async def test_create_keyword_rule(self, db_session: AsyncSession):
        """Test creating a keyword rule."""
        rule = Rule(
            name="Urgent Keywords",
            description="Matches urgent keywords",
            rule_type=RuleType.KEYWORD,
            pattern="dringend, eilig, sofort",
            priority_boost=30,
            target_priority=Priority.HIGH,
            enabled=True,
            order=1,
        )
        db_session.add(rule)
        await db_session.flush()

        assert rule.id is not None
        assert rule.rule_type == RuleType.KEYWORD

    @pytest.mark.asyncio
    async def test_create_regex_rule(self, db_session: AsyncSession):
        """Test creating a regex rule."""
        rule = Rule(
            name="Budget Pattern",
            rule_type=RuleType.REGEX,
            pattern=r"kürzung.*\d+.*euro",
            priority_boost=25,
        )
        db_session.add(rule)
        await db_session.flush()

        assert rule.rule_type == RuleType.REGEX

    @pytest.mark.asyncio
    async def test_rule_ordering(self, db_session: AsyncSession):
        """Test rule ordering."""
        for i in range(3):
            rule = Rule(
                name=f"Rule {i}",
                rule_type=RuleType.KEYWORD,
                pattern="test",
                order=2 - i,  # Reverse order
            )
            db_session.add(rule)

        await db_session.flush()

        result = await db_session.execute(select(Rule).order_by(Rule.order))
        rules = result.scalars().all()

        assert rules[0].name == "Rule 2"
        assert rules[1].name == "Rule 1"
        assert rules[2].name == "Rule 0"


class TestItemRuleMatchModel:
    """Tests for ItemRuleMatch junction table."""

    @pytest.mark.asyncio
    async def test_item_rule_match(self, db_session: AsyncSession):
        """Test creating an item-rule match."""
        source = Source(name="Test", enabled=True)
        db_session.add(source)
        await db_session.flush()

        channel = Channel(
            source_id=source.id,
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(channel)
        await db_session.flush()

        item = Item(
            channel_id=channel.id,
            external_id="ext-1",
            title="Test Article",
            content="Content with kürzung keyword",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="hash1",
        )
        db_session.add(item)

        rule = Rule(
            name="Budget Rule",
            rule_type=RuleType.KEYWORD,
            pattern="kürzung",
        )
        db_session.add(rule)
        await db_session.flush()

        match = ItemRuleMatch(
            item_id=item.id,
            rule_id=rule.id,
            match_details={"matched_keyword": "kürzung"},
        )
        db_session.add(match)
        await db_session.flush()

        assert match.id is not None
        assert match.match_details["matched_keyword"] == "kürzung"


class TestSettingModel:
    """Tests for Setting model."""

    @pytest.mark.asyncio
    async def test_create_setting(self, db_session: AsyncSession):
        """Test creating a setting."""
        setting = Setting(
            key="fetch_interval",
            value=30,
            description="Fetch interval in minutes",
        )
        db_session.add(setting)
        await db_session.flush()

        result = await db_session.execute(
            select(Setting).where(Setting.key == "fetch_interval")
        )
        loaded = result.scalar_one()

        assert loaded.value == 30

    @pytest.mark.asyncio
    async def test_setting_json_value(self, db_session: AsyncSession):
        """Test storing complex JSON in settings."""
        setting = Setting(
            key="email_config",
            value={
                "smtp_host": "mail.example.com",
                "recipients": ["a@example.com", "b@example.com"],
                "enabled": True,
            },
        )
        db_session.add(setting)
        await db_session.flush()

        result = await db_session.execute(
            select(Setting).where(Setting.key == "email_config")
        )
        loaded = result.scalar_one()

        assert loaded.value["smtp_host"] == "mail.example.com"
        assert len(loaded.value["recipients"]) == 2
