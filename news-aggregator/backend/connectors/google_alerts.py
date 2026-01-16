"""Google Alerts connector.

Fetches news from Google Alerts RSS feeds.
"""

import logging

from pydantic import BaseModel, Field, HttpUrl, field_validator

from .registry import ConnectorRegistry
from .rss import RSSConnector

logger = logging.getLogger(__name__)


class GoogleAlertsConfig(BaseModel):
    """Configuration for Google Alerts connector."""

    url: HttpUrl = Field(
        ...,
        description="Google Alerts RSS-Feed URL (von google.com/alerts)"
    )
    custom_title: str | None = Field(
        default=None, description="Custom name for the feed (optional)"
    )
    follow_links: bool = Field(
        default=True, description="Follow links to fetch full article content"
    )

    @field_validator("url")
    @classmethod
    def validate_google_alerts_url(cls, v: HttpUrl) -> HttpUrl:
        """Ensure URL is a valid Google Alerts feed."""
        url_str = str(v)
        if "google.com/alerts" not in url_str and "google.de/alerts" not in url_str:
            raise ValueError(
                "Bitte eine gültige Google Alerts Feed-URL eingeben "
                "(beginnt mit https://www.google.com/alerts/feeds/...)"
            )
        return v


@ConnectorRegistry.register
class GoogleAlertsConnector(RSSConnector):
    """Google Alerts RSS connector.

    Fetches items from Google Alerts RSS feeds. Uses the same
    parsing logic as the RSS connector since Google Alerts
    delivers standard Atom feeds.

    Setup:
    1. Go to google.com/alerts
    2. Create alert with "RSS Feed" delivery
    3. Copy the feed URL
    """

    connector_type = "google_alerts"
    display_name = "Google Alerts"
    description = "Google Alerts RSS-Feeds (google.com/alerts → RSS-Zustellung wählen)"
    config_schema = GoogleAlertsConfig
