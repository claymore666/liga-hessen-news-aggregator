"""Tests for database models."""

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
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
    """Tests for Source model."""

    @pytest.mark.asyncio
    async def test_create_source(self, db_session: AsyncSession):
        """Test creating a source."""
        source = Source(
            name="Test Feed",
            connector_type=ConnectorType.RSS,
            config={"url": "https://example.com/feed.xml"},
            enabled=True,
            fetch_interval_minutes=30,
        )
        db_session.add(source)
        await db_session.flush()

        assert source.id is not None
        assert source.name == "Test Feed"
        assert source.connector_type == ConnectorType.RSS
        assert source.enabled is True
        assert source.created_at is not None

    @pytest.mark.asyncio
    async def test_source_config_json(self, db_session: AsyncSession):
        """Test that source config is stored as JSON."""
        config = {
            "url": "https://example.com/feed.xml",
            "custom_title": "My Feed",
            "nested": {"key": "value"},
        }
        source = Source(
            name="JSON Test",
            connector_type=ConnectorType.RSS,
            config=config,
        )
        db_session.add(source)
        await db_session.flush()

        # Query back
        result = await db_session.execute(
            select(Source).where(Source.id == source.id)
        )
        loaded = result.scalar_one()

        assert loaded.config == config
        assert loaded.config["nested"]["key"] == "value"

    @pytest.mark.asyncio
    async def test_source_connector_types(self, db_session: AsyncSession):
        """Test all connector types can be stored."""
        for ct in ConnectorType:
            source = Source(
                name=f"Test {ct.value}",
                connector_type=ct,
                config={},
            )
            db_session.add(source)

        await db_session.flush()

        result = await db_session.execute(select(Source))
        sources = result.scalars().all()

        assert len(sources) == len(ConnectorType)


class TestItemModel:
    """Tests for Item model."""

    @pytest.mark.asyncio
    async def test_create_item(self, db_session: AsyncSession):
        """Test creating an item."""
        # First create a source
        source = Source(
            name="Test Source",
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(source)
        await db_session.flush()

        # Create item
        item = Item(
            source_id=source.id,
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
        assert item.source_id == source.id
        assert item.is_read is False
        assert item.is_starred is False

    @pytest.mark.asyncio
    async def test_item_source_relationship(self, db_session: AsyncSession):
        """Test item-source relationship."""
        source = Source(
            name="Test Source",
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(source)
        await db_session.flush()

        item = Item(
            source_id=source.id,
            external_id="ext-456",
            title="Test",
            content="Content",
            url="https://example.com",
            published_at=datetime.utcnow(),
            content_hash="hash456",
        )
        db_session.add(item)
        await db_session.flush()
        await db_session.refresh(item, ["source"])

        assert item.source.name == "Test Source"

    @pytest.mark.asyncio
    async def test_item_priority_levels(self, db_session: AsyncSession):
        """Test all priority levels."""
        source = Source(
            name="Test",
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(source)
        await db_session.flush()

        for i, priority in enumerate(Priority):
            item = Item(
                source_id=source.id,
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
            target_priority=Priority.CRITICAL,
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

        result = await db_session.execute(
            select(Rule).order_by(Rule.order)
        )
        rules = result.scalars().all()

        assert rules[0].name == "Rule 2"
        assert rules[1].name == "Rule 1"
        assert rules[2].name == "Rule 0"


class TestItemRuleMatchModel:
    """Tests for ItemRuleMatch junction table."""

    @pytest.mark.asyncio
    async def test_item_rule_match(self, db_session: AsyncSession):
        """Test creating an item-rule match."""
        source = Source(
            name="Test",
            connector_type=ConnectorType.RSS,
            config={},
        )
        db_session.add(source)
        await db_session.flush()

        item = Item(
            source_id=source.id,
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
