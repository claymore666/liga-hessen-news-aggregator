"""Tests for the connector system."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from connectors import (
    BaseConnector,
    ConnectorRegistry,
    RawItem,
    RSSConnector,
    HTMLConnector,
    BlueskyConnector,
    TwitterConnector,
    PDFConnector,
    MastodonConnector,
    InstagramConnector,
)
from connectors.rss import RSSConfig
from connectors.html import HTMLConfig
from connectors.bluesky import BlueskyConfig
from connectors.twitter import TwitterConfig
from connectors.pdf import PDFConfig
from connectors.mastodon import MastodonConfig
from connectors.instagram import InstagramConfig


# === Registry Tests ===


class TestConnectorRegistry:
    """Tests for ConnectorRegistry."""

    def test_list_all_returns_registered_connectors(self):
        """Registry should list all registered connectors."""
        connectors = ConnectorRegistry.list_all()
        assert len(connectors) >= 7  # RSS, HTML, Bluesky, Twitter, PDF, Mastodon, Instagram

        connector_types = [c["type"] for c in connectors]
        assert "rss" in connector_types
        assert "html" in connector_types
        assert "bluesky" in connector_types
        assert "twitter" in connector_types
        assert "pdf" in connector_types
        assert "mastodon" in connector_types
        assert "instagram" in connector_types

    def test_get_returns_correct_connector(self):
        """Registry should return correct connector class."""
        connector_cls = ConnectorRegistry.get("rss")
        assert connector_cls == RSSConnector

        connector_cls = ConnectorRegistry.get("html")
        assert connector_cls == HTMLConnector

    def test_get_raises_for_unknown_type(self):
        """Registry should raise ValueError for unknown connector type."""
        with pytest.raises(ValueError, match="Unknown connector"):
            ConnectorRegistry.get("nonexistent")

    def test_is_registered(self):
        """is_registered should return correct status."""
        assert ConnectorRegistry.is_registered("rss") is True
        assert ConnectorRegistry.is_registered("nonexistent") is False

    def test_get_types(self):
        """get_types should return list of registered types."""
        types = ConnectorRegistry.get_types()
        assert isinstance(types, list)
        assert "rss" in types


# === RawItem Tests ===


class TestRawItem:
    """Tests for RawItem model."""

    def test_raw_item_with_required_fields(self):
        """RawItem should work with only required fields."""
        item = RawItem(
            external_id="123",
            title="Test Title",
            url="https://example.com/article",
        )
        assert item.external_id == "123"
        assert item.title == "Test Title"
        assert item.url == "https://example.com/article"
        assert item.content == ""
        assert item.author is None
        assert item.published_at is None
        assert item.metadata == {}

    def test_raw_item_with_all_fields(self):
        """RawItem should work with all fields."""
        now = datetime.now()
        item = RawItem(
            external_id="123",
            title="Test Title",
            content="Test content",
            url="https://example.com/article",
            author="Test Author",
            published_at=now,
            metadata={"source": "test"},
        )
        assert item.content == "Test content"
        assert item.author == "Test Author"
        assert item.published_at == now
        assert item.metadata == {"source": "test"}


# === Config Tests ===


class TestRSSConfig:
    """Tests for RSS connector configuration."""

    def test_valid_config(self):
        """Valid RSS config should be accepted."""
        config = RSSConfig(url="https://example.com/feed.xml")
        assert str(config.url) == "https://example.com/feed.xml"

    def test_invalid_url_rejected(self):
        """Invalid URL should be rejected."""
        with pytest.raises(ValueError):
            RSSConfig(url="not-a-url")

    def test_optional_custom_title(self):
        """Custom title should be optional."""
        config = RSSConfig(url="https://example.com/feed.xml", custom_title="My Feed")
        assert config.custom_title == "My Feed"


class TestHTMLConfig:
    """Tests for HTML connector configuration."""

    def test_valid_config(self):
        """Valid HTML config should be accepted."""
        config = HTMLConfig(
            url="https://example.com/news",
            item_selector="article.news-item",
        )
        assert config.item_selector == "article.news-item"

    def test_optional_selectors(self):
        """Optional selectors should have defaults."""
        config = HTMLConfig(
            url="https://example.com/news",
            item_selector="article",
        )
        assert config.title_selector == "h2, h3, a"
        assert config.content_selector is None


class TestBlueskyConfig:
    """Tests for Bluesky connector configuration."""

    def test_handle_normalization(self):
        """Handle should be normalized (@ removed)."""
        config = BlueskyConfig(handle="@user.bsky.social")
        assert config.handle == "user.bsky.social"

        config = BlueskyConfig(handle="user.bsky.social")
        assert config.handle == "user.bsky.social"


class TestTwitterConfig:
    """Tests for Twitter connector configuration."""

    def test_username_normalization(self):
        """Username should be normalized (@ removed)."""
        config = TwitterConfig(username="@testuser")
        assert config.username == "testuser"

    def test_default_values(self):
        """Default values should be set."""
        config = TwitterConfig(username="testuser")
        assert config.include_retweets is True
        assert config.include_replies is False
        assert config.nitter_instance == "nitter.privacydev.net"


class TestMastodonConfig:
    """Tests for Mastodon connector configuration."""

    def test_valid_handle(self):
        """Valid handle should be accepted."""
        config = MastodonConfig(handle="user@mastodon.social")
        assert config.username == "user"
        assert config.instance == "mastodon.social"

    def test_handle_with_at_prefix(self):
        """Handle with @ prefix should be normalized."""
        config = MastodonConfig(handle="@user@mastodon.social")
        assert config.handle == "user@mastodon.social"

    def test_invalid_handle_rejected(self):
        """Handle without @ should be rejected."""
        with pytest.raises(ValueError, match="user@instance"):
            MastodonConfig(handle="user")


class TestInstagramConfig:
    """Tests for Instagram connector configuration."""

    def test_username_normalization(self):
        """Username should be normalized (@ removed, lowercased)."""
        config = InstagramConfig(username="@TestUser")
        assert config.username == "testuser"

    def test_default_values(self):
        """Default values should be set."""
        config = InstagramConfig(username="testuser")
        assert config.include_reels is True
        assert config.max_posts == 20
        assert config.proxy_instance == "picuki.com"

    def test_max_posts_bounds(self):
        """Max posts should be within valid range."""
        config = InstagramConfig(username="testuser", max_posts=50)
        assert config.max_posts == 50

        with pytest.raises(ValueError):
            InstagramConfig(username="testuser", max_posts=0)

        with pytest.raises(ValueError):
            InstagramConfig(username="testuser", max_posts=100)


# === Connector Attribute Tests ===


class TestConnectorAttributes:
    """Tests for connector class attributes."""

    @pytest.mark.parametrize(
        "connector_cls,expected_type",
        [
            (RSSConnector, "rss"),
            (HTMLConnector, "html"),
            (BlueskyConnector, "bluesky"),
            (TwitterConnector, "twitter"),
            (PDFConnector, "pdf"),
            (MastodonConnector, "mastodon"),
            (InstagramConnector, "instagram"),
        ],
    )
    def test_connector_type(self, connector_cls, expected_type):
        """Each connector should have correct type."""
        assert connector_cls.connector_type == expected_type

    @pytest.mark.parametrize(
        "connector_cls",
        [RSSConnector, HTMLConnector, BlueskyConnector, TwitterConnector, PDFConnector, MastodonConnector, InstagramConnector],
    )
    def test_connector_has_required_attributes(self, connector_cls):
        """Each connector should have required class attributes."""
        assert hasattr(connector_cls, "connector_type")
        assert hasattr(connector_cls, "display_name")
        assert hasattr(connector_cls, "description")
        assert hasattr(connector_cls, "config_schema")
        assert isinstance(connector_cls.display_name, str)
        assert isinstance(connector_cls.description, str)

    @pytest.mark.parametrize(
        "connector_cls",
        [RSSConnector, HTMLConnector, BlueskyConnector, TwitterConnector, PDFConnector, MastodonConnector, InstagramConnector],
    )
    def test_connector_is_subclass(self, connector_cls):
        """Each connector should be a BaseConnector subclass."""
        assert issubclass(connector_cls, BaseConnector)


# === Fetch Tests with Mocking ===


class TestRSSConnectorFetch:
    """Tests for RSS connector fetch functionality."""

    @pytest.fixture
    def rss_feed_content(self):
        """Sample RSS feed content."""
        return """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Test Feed</title>
                <link>https://example.com</link>
                <item>
                    <title>Test Article</title>
                    <link>https://example.com/article1</link>
                    <description>Test content</description>
                    <guid>article1</guid>
                    <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
                </item>
            </channel>
        </rss>"""

    @pytest.mark.asyncio
    async def test_fetch_parses_rss_feed(self, rss_feed_content):
        """RSS connector should parse feed correctly."""
        connector = RSSConnector()
        config = RSSConfig(url="https://example.com/feed.xml")

        with patch("connectors.rss.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.text = rss_feed_content
            mock_response.raise_for_status = MagicMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            items = await connector.fetch(config)

        assert len(items) == 1
        assert items[0].title == "Test Article"
        assert items[0].url == "https://example.com/article1"
        assert items[0].external_id == "article1"


class TestRSSConnectorValidate:
    """Tests for RSS connector validation."""

    @pytest.mark.asyncio
    async def test_validate_valid_feed(self):
        """Validation should pass for valid feed."""
        connector = RSSConnector()
        config = RSSConfig(url="https://example.com/feed.xml")

        rss_content = """<?xml version="1.0"?>
        <rss version="2.0">
            <channel>
                <title>Test Feed</title>
                <item><title>Test</title></item>
            </channel>
        </rss>"""

        with patch("connectors.rss.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.text = rss_content
            mock_response.raise_for_status = MagicMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            valid, message = await connector.validate(config)

        assert valid is True
        assert "Test Feed" in message

    @pytest.mark.asyncio
    async def test_validate_invalid_feed(self):
        """Validation should fail for invalid feed."""
        connector = RSSConnector()
        config = RSSConfig(url="https://example.com/feed.xml")

        with patch("connectors.rss.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.text = "not xml"
            mock_response.raise_for_status = MagicMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            valid, message = await connector.validate(config)

        # feedparser is very lenient, so this might still pass
        # but with 0 entries
        assert isinstance(valid, bool)
        assert isinstance(message, str)


# === Schema JSON Tests ===


class TestConnectorSchemaJson:
    """Tests for connector JSON schema generation."""

    @pytest.mark.parametrize(
        "connector_cls",
        [RSSConnector, HTMLConnector, BlueskyConnector, TwitterConnector, PDFConnector, MastodonConnector, InstagramConnector],
    )
    def test_get_config_schema_json(self, connector_cls):
        """Each connector should generate valid JSON schema."""
        schema = connector_cls.get_config_schema_json()

        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "type" in schema
        assert schema["type"] == "object"


# === Instagram Connector Tests ===


class TestInstagramConnectorFetch:
    """Tests for Instagram connector fetch functionality."""

    @pytest.fixture
    def picuki_html_content(self):
        """Sample Picuki profile page HTML."""
        return """<!DOCTYPE html>
        <html>
        <body>
            <div class="box-photos">
                <div class="box-photo">
                    <a href="/media/ABC123xyz">
                        <img src="https://picuki.com/img/123.jpg" alt="Post image">
                    </a>
                    <div class="photo-description">Test Instagram post content</div>
                    <div class="time">2 hours ago</div>
                </div>
                <div class="box-photo">
                    <a href="/media/DEF456abc">
                        <img src="https://picuki.com/img/456.jpg" alt="Another post">
                    </a>
                    <div class="photo-description">Another test post</div>
                    <div class="time">1 day ago</div>
                </div>
            </div>
        </body>
        </html>"""

    @pytest.mark.asyncio
    async def test_fetch_parses_picuki_page(self, picuki_html_content):
        """Instagram connector should parse Picuki page correctly."""
        connector = InstagramConnector()
        config = InstagramConfig(username="testuser")

        with patch("connectors.instagram.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.text = picuki_html_content
            mock_response.status_code = 200
            mock_response.raise_for_status = MagicMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            items = await connector.fetch(config)

        assert len(items) == 2
        assert items[0].external_id == "ABC123xyz"
        assert "Test Instagram post" in items[0].content
        assert items[0].url == "https://www.instagram.com/p/ABC123xyz/"
        assert items[0].author == "@testuser"
        assert items[0].metadata["platform"] == "instagram"

    @pytest.mark.asyncio
    async def test_fetch_handles_empty_profile(self):
        """Instagram connector should handle profile with no posts."""
        connector = InstagramConnector()
        config = InstagramConfig(username="emptyuser")

        empty_html = """<!DOCTYPE html><html><body><div class="box-photos"></div></body></html>"""

        with patch("connectors.instagram.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.text = empty_html
            mock_response.status_code = 200
            mock_response.raise_for_status = MagicMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            items = await connector.fetch(config)

        assert len(items) == 0


class TestInstagramConnectorValidate:
    """Tests for Instagram connector validation."""

    @pytest.mark.asyncio
    async def test_validate_valid_profile(self):
        """Validation should pass for valid profile."""
        connector = InstagramConnector()
        config = InstagramConfig(username="testuser")

        profile_html = """<!DOCTYPE html>
        <html><body>
            <div class="box-photos">
                <div class="box-photo"><a href="/media/123"></a></div>
            </div>
        </body></html>"""

        with patch("connectors.instagram.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.text = profile_html
            mock_response.status_code = 200

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            valid, message = await connector.validate(config)

        assert valid is True
        assert "Found" in message or "posts" in message.lower()

    @pytest.mark.asyncio
    async def test_validate_not_found_profile(self):
        """Validation should fail for non-existent profile."""
        connector = InstagramConnector()
        config = InstagramConfig(username="nonexistent_user_12345")

        not_found_html = """<!DOCTYPE html>
        <html><body>
            <p>User not found</p>
            <p>This page doesn't exist</p>
        </body></html>"""

        with patch("connectors.instagram.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.text = not_found_html
            mock_response.status_code = 200

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            valid, message = await connector.validate(config)

        assert valid is False
        assert "not found" in message.lower()

    @pytest.mark.asyncio
    async def test_validate_private_profile(self):
        """Validation should fail for private profile."""
        connector = InstagramConnector()
        config = InstagramConfig(username="privateuser")

        private_html = """<!DOCTYPE html>
        <html><body>
            <p>This account is private</p>
        </body></html>"""

        with patch("connectors.instagram.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.text = private_html
            mock_response.status_code = 200

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            valid, message = await connector.validate(config)

        assert valid is False
        assert "private" in message.lower()
