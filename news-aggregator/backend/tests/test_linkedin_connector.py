"""Tests for LinkedIn connector."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from connectors import ConnectorRegistry, LinkedInConnector
from connectors.linkedin import LinkedInConfig


# === Config Tests ===


class TestLinkedInConfig:
    """Tests for LinkedIn connector configuration."""

    def test_valid_company_url(self):
        """Valid company URL should be accepted."""
        config = LinkedInConfig(profile_url="https://linkedin.com/company/microsoft")
        assert "linkedin.com/company/microsoft" in config.profile_url
        assert config.profile_type == "company"
        assert config.profile_id == "microsoft"

    def test_valid_personal_url(self):
        """Valid personal URL should be accepted."""
        config = LinkedInConfig(profile_url="https://linkedin.com/in/satya-nadella")
        assert "linkedin.com/in/satya-nadella" in config.profile_url
        assert config.profile_type == "personal"
        assert config.profile_id == "satya-nadella"

    def test_url_normalization(self):
        """URL should be normalized."""
        # Without https
        config = LinkedInConfig(profile_url="linkedin.com/company/test")
        assert config.profile_url.startswith("https://")

        # With www
        config = LinkedInConfig(profile_url="https://www.linkedin.com/company/test")
        assert "www" not in config.profile_url

        # Trailing slash
        config = LinkedInConfig(profile_url="https://linkedin.com/company/test/")
        assert not config.profile_url.endswith("/")

    def test_invalid_url_rejected(self):
        """Non-LinkedIn URL should be rejected."""
        with pytest.raises(ValueError, match="LinkedIn URL"):
            LinkedInConfig(profile_url="https://twitter.com/test")

    def test_unknown_profile_type(self):
        """Unknown profile type should be detected."""
        config = LinkedInConfig(profile_url="https://linkedin.com/jobs/test")
        assert config.profile_type == "unknown"

    def test_default_values(self):
        """Default values should be set."""
        config = LinkedInConfig(profile_url="https://linkedin.com/company/test")
        assert config.use_proxy is False
        assert config.max_posts == 10

    def test_max_posts_bounds(self):
        """Max posts should be within valid range."""
        config = LinkedInConfig(
            profile_url="https://linkedin.com/company/test", max_posts=50
        )
        assert config.max_posts == 50

        with pytest.raises(ValueError):
            LinkedInConfig(
                profile_url="https://linkedin.com/company/test", max_posts=0
            )

        with pytest.raises(ValueError):
            LinkedInConfig(
                profile_url="https://linkedin.com/company/test", max_posts=100
            )


# === Connector Registration Tests ===


class TestLinkedInConnectorRegistration:
    """Tests for LinkedIn connector registration."""

    def test_connector_is_registered(self):
        """LinkedIn connector should be registered."""
        assert ConnectorRegistry.is_registered("linkedin")

    def test_get_connector_class(self):
        """Registry should return correct connector class."""
        connector_cls = ConnectorRegistry.get("linkedin")
        assert connector_cls == LinkedInConnector

    def test_connector_in_list_all(self):
        """LinkedIn should be in list of all connectors."""
        connectors = ConnectorRegistry.list_all()
        connector_types = [c["type"] for c in connectors]
        assert "linkedin" in connector_types


# === Connector Attributes Tests ===


class TestLinkedInConnectorAttributes:
    """Tests for LinkedIn connector class attributes."""

    def test_connector_type(self):
        """Connector should have correct type."""
        assert LinkedInConnector.connector_type == "linkedin"

    def test_connector_has_required_attributes(self):
        """Connector should have all required attributes."""
        assert hasattr(LinkedInConnector, "connector_type")
        assert hasattr(LinkedInConnector, "display_name")
        assert hasattr(LinkedInConnector, "description")
        assert hasattr(LinkedInConnector, "config_schema")
        assert isinstance(LinkedInConnector.display_name, str)
        assert isinstance(LinkedInConnector.description, str)

    def test_config_schema(self):
        """Config schema should be LinkedInConfig."""
        assert LinkedInConnector.config_schema == LinkedInConfig

    def test_get_config_schema_json(self):
        """Should generate valid JSON schema."""
        schema = LinkedInConnector.get_config_schema_json()
        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "profile_url" in schema["properties"]


# === Cookie Loading Tests ===


class TestLinkedInCookieLoading:
    """Tests for LinkedIn cookie loading."""

    def test_load_cookies_no_file(self):
        """Should return None when no cookie file exists."""
        connector = LinkedInConnector()
        with patch("connectors.linkedin.COOKIE_FILE") as mock_path:
            mock_path.exists.return_value = False
            result = connector._load_cookies()
        assert result is None

    def test_load_cookies_missing_li_at(self):
        """Should return None when li_at cookie is missing."""
        connector = LinkedInConnector()
        cookies = [{"name": "other_cookie", "value": "test"}]

        with patch("connectors.linkedin.COOKIE_FILE") as mock_path:
            mock_path.exists.return_value = True
            with patch("builtins.open", MagicMock()):
                with patch("json.load", return_value=cookies):
                    result = connector._load_cookies()
        assert result is None

    def test_load_cookies_success(self):
        """Should return cookies when file exists and has li_at."""
        connector = LinkedInConnector()
        cookies = [
            {"name": "li_at", "value": "test_session_token"},
            {"name": "JSESSIONID", "value": "test_jsession"},
        ]

        with patch("connectors.linkedin.COOKIE_FILE") as mock_path:
            mock_path.exists.return_value = True
            with patch("builtins.open", MagicMock()):
                with patch("json.load", return_value=cookies):
                    result = connector._load_cookies()

        assert result == cookies


# === Time Parsing Tests ===


class TestLinkedInTimeParsing:
    """Tests for LinkedIn relative time parsing."""

    def test_parse_minutes(self):
        """Should parse minute-based times."""
        result = LinkedInConnector._parse_relative_time("30m")
        assert (datetime.utcnow() - result).total_seconds() < 35 * 60

    def test_parse_hours(self):
        """Should parse hour-based times."""
        result = LinkedInConnector._parse_relative_time("2h")
        assert (datetime.utcnow() - result).total_seconds() < 3 * 60 * 60

    def test_parse_days(self):
        """Should parse day-based times."""
        result = LinkedInConnector._parse_relative_time("3d")
        assert (datetime.utcnow() - result).total_seconds() < 4 * 24 * 60 * 60

    def test_parse_weeks(self):
        """Should parse week-based times."""
        result = LinkedInConnector._parse_relative_time("2w")
        assert (datetime.utcnow() - result).total_seconds() < 15 * 24 * 60 * 60

    def test_parse_unknown_format(self):
        """Should return now for unknown formats."""
        result = LinkedInConnector._parse_relative_time("just now")
        # Should be very close to now
        assert (datetime.utcnow() - result).total_seconds() < 5


# === Fetch Tests (without cookies) ===


class TestLinkedInConnectorFetchNoCookies:
    """Tests for LinkedIn connector fetch without cookies."""

    @pytest.mark.asyncio
    async def test_fetch_returns_empty_without_cookies(self):
        """Fetch should return empty list when no cookies available."""
        connector = LinkedInConnector()
        config = LinkedInConfig(profile_url="https://linkedin.com/company/test")

        with patch.object(connector, "_load_cookies", return_value=None):
            items = await connector.fetch(config)

        assert items == []


# === Validation Tests ===


class TestLinkedInConnectorValidation:
    """Tests for LinkedIn connector validation."""

    @pytest.mark.asyncio
    async def test_validate_unknown_profile_type(self):
        """Validation should fail for unknown profile type."""
        connector = LinkedInConnector()
        config = LinkedInConfig(profile_url="https://linkedin.com/jobs/test")

        valid, message = await connector.validate(config)

        assert valid is False
        assert "Invalid" in message or "/company/" in message or "/in/" in message

    @pytest.mark.asyncio
    async def test_validate_no_cookies(self):
        """Validation should fail when no cookies available."""
        connector = LinkedInConnector()
        config = LinkedInConfig(profile_url="https://linkedin.com/company/test")

        with patch.object(connector, "_load_cookies", return_value=None):
            valid, message = await connector.validate(config)

        assert valid is False
        assert "cookies" in message.lower()


# === Model Integration Tests ===


class TestLinkedInModelIntegration:
    """Tests for LinkedIn integration with Channel model."""

    def test_extract_identifier_company(self):
        """extract_identifier should work for company pages."""
        from models import Channel

        identifier = Channel.extract_identifier(
            "linkedin", {"profile_url": "https://linkedin.com/company/microsoft"}
        )
        assert identifier == "microsoft"

    def test_extract_identifier_personal(self):
        """extract_identifier should work for personal profiles."""
        from models import Channel

        identifier = Channel.extract_identifier(
            "linkedin", {"profile_url": "https://linkedin.com/in/satya-nadella"}
        )
        assert identifier == "satya-nadella"

    def test_extract_identifier_unknown(self):
        """extract_identifier should handle unknown URL formats."""
        from models import Channel

        identifier = Channel.extract_identifier(
            "linkedin", {"profile_url": "https://linkedin.com/jobs/123"}
        )
        # Should return the URL in lowercase
        assert identifier is not None
