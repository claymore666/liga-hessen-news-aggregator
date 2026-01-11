"""Pytest configuration and fixtures."""

import asyncio
from collections.abc import AsyncGenerator, Generator
from datetime import datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base, get_db
from main import app
from models import Channel, ConnectorType, Item, Priority, Rule, RuleType, Setting, Source

# Use in-memory SQLite for tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_engine():
    """Create a test database engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    async_session_maker = async_sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create a test HTTP client with database override."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# === Source fixtures ===


@pytest.fixture
def sample_source_data() -> dict[str, Any]:
    """Sample source (organization) data for testing."""
    return {
        "name": "Test Organization",
        "description": "A test organization",
        "is_stakeholder": True,
        "enabled": True,
        "channels": [],
    }


@pytest.fixture
def sample_source_with_channels() -> dict[str, Any]:
    """Sample source with channels for testing."""
    return {
        "name": "Multi-Channel Source",
        "description": "Source with multiple channels",
        "is_stakeholder": False,
        "enabled": True,
        "channels": [
            {
                "name": "RSS Feed",
                "connector_type": "rss",
                "config": {"url": "https://example.com/feed.xml"},
                "enabled": True,
                "fetch_interval_minutes": 30,
            },
            {
                "name": "X Account",
                "connector_type": "x_scraper",
                "config": {"handle": "@example"},
                "enabled": True,
                "fetch_interval_minutes": 60,
            },
        ],
    }


# === Channel fixtures ===


@pytest.fixture
def sample_channel_data() -> dict[str, Any]:
    """Sample channel data for testing."""
    return {
        "name": "Main Feed",
        "connector_type": "rss",
        "config": {"url": "https://example.com/feed.xml"},
        "enabled": True,
        "fetch_interval_minutes": 30,
    }


# === Rule fixtures ===


@pytest.fixture
def sample_rule_data() -> dict[str, Any]:
    """Sample rule data for testing."""
    return {
        "name": "Test Keyword Rule",
        "description": "Matches test keywords",
        "rule_type": "keyword",
        "pattern": "wichtig, dringend, kürzung",
        "priority_boost": 20,
        "enabled": True,
        "order": 0,
    }


@pytest.fixture
def sample_regex_rule_data() -> dict[str, Any]:
    """Sample regex rule data for testing."""
    return {
        "name": "Test Regex Rule",
        "description": "Matches regex patterns",
        "rule_type": "regex",
        "pattern": r"\bpflege\w*\b",
        "priority_boost": 15,
        "target_priority": "high",
        "enabled": True,
        "order": 1,
    }


# === Database fixtures (pre-populated) ===


@pytest_asyncio.fixture
async def source_in_db(db_session: AsyncSession) -> Source:
    """Create a source directly in the database."""
    source = Source(
        name="DB Test Source",
        description="Created directly in DB",
        is_stakeholder=True,
        enabled=True,
    )
    db_session.add(source)
    await db_session.flush()
    return source


@pytest_asyncio.fixture
async def channel_in_db(db_session: AsyncSession, source_in_db: Source) -> Channel:
    """Create a channel directly in the database."""
    channel = Channel(
        source_id=source_in_db.id,
        name="DB Test Channel",
        connector_type=ConnectorType.RSS,
        config={"url": "https://test.com/feed.xml"},
        source_identifier="https://test.com/feed.xml",
        enabled=True,
        fetch_interval_minutes=30,
    )
    db_session.add(channel)
    await db_session.flush()
    return channel


@pytest_asyncio.fixture
async def item_in_db(db_session: AsyncSession, channel_in_db: Channel) -> Item:
    """Create an item directly in the database."""
    item = Item(
        channel_id=channel_in_db.id,
        external_id="test-ext-1",
        title="Test Article",
        content="This is test content for the article.",
        url="https://test.com/article/1",
        author="Test Author",
        published_at=datetime.utcnow(),
        content_hash="hash123",
        priority=Priority.MEDIUM,
        priority_score=50,
    )
    db_session.add(item)
    await db_session.flush()
    return item


@pytest_asyncio.fixture
async def multiple_items_in_db(
    db_session: AsyncSession, channel_in_db: Channel
) -> list[Item]:
    """Create multiple items with different priorities in the database."""
    items = []
    priorities = [Priority.CRITICAL, Priority.HIGH, Priority.MEDIUM, Priority.LOW]

    for i, priority in enumerate(priorities):
        item = Item(
            channel_id=channel_in_db.id,
            external_id=f"test-ext-{i}",
            title=f"Test Article {i} - {priority.value}",
            content=f"Content for article {i}",
            url=f"https://test.com/article/{i}",
            published_at=datetime.utcnow() - timedelta(hours=i),
            content_hash=f"hash{i}",
            priority=priority,
            priority_score=100 - (i * 25),
            is_read=i % 2 == 0,
            is_starred=i == 0,
        )
        items.append(item)
        db_session.add(item)

    await db_session.flush()
    return items


@pytest_asyncio.fixture
async def rule_in_db(db_session: AsyncSession) -> Rule:
    """Create a rule directly in the database."""
    rule = Rule(
        name="DB Test Rule",
        description="Rule created in DB",
        rule_type=RuleType.KEYWORD,
        pattern="test, example",
        priority_boost=10,
        enabled=True,
        order=0,
    )
    db_session.add(rule)
    await db_session.flush()
    return rule


@pytest_asyncio.fixture
async def setting_in_db(db_session: AsyncSession) -> Setting:
    """Create a setting directly in the database."""
    setting = Setting(
        key="test_setting",
        value="test_value",
        description="A test setting",
    )
    db_session.add(setting)
    await db_session.flush()
    return setting


# === Config export fixtures ===


@pytest.fixture
def sample_config_export() -> dict[str, Any]:
    """Sample config export data for testing import."""
    return {
        "version": "1.0",
        "instance_identifier": "test-instance",
        "exported_at": datetime.utcnow().isoformat(),
        "sources": [
            {
                "name": "Import Test Source",
                "description": "Imported source",
                "is_stakeholder": True,
                "enabled": True,
                "channels": [
                    {
                        "name": "Imported RSS",
                        "connector_type": "rss",
                        "config": {"url": "https://imported.com/feed.xml"},
                        "enabled": True,
                        "fetch_interval_minutes": 30,
                    }
                ],
            }
        ],
        "rules": [
            {
                "name": "Imported Rule",
                "description": "An imported rule",
                "rule_type": "keyword",
                "pattern": "imported, test",
                "priority_boost": 5,
                "target_priority": None,
                "enabled": True,
                "order": 0,
            }
        ],
        "settings": [
            {
                "key": "imported_setting",
                "value": "imported_value",
                "description": "An imported setting",
            }
        ],
    }
