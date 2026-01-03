"""Bluesky connector using native RSS feeds."""

from datetime import datetime
from time import mktime

import feedparser
import httpx
from pydantic import BaseModel, Field, field_validator

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry


class BlueskyConfig(BaseModel):
    """Configuration for Bluesky connector."""

    handle: str = Field(
        ..., description="Bluesky handle (e.g., user.bsky.social or @user.bsky.social)"
    )

    @field_validator("handle")
    @classmethod
    def normalize_handle(cls, v: str) -> str:
        """Remove @ prefix if present."""
        return v.lstrip("@")


@ConnectorRegistry.register
class BlueskyConnector(BaseConnector):
    """Bluesky connector using native RSS feeds.

    Bluesky offers native RSS feeds at https://bsky.app/profile/{handle}/rss
    """

    connector_type = "bluesky"
    display_name = "Bluesky"
    description = "Follow Bluesky accounts via RSS"
    config_schema = BlueskyConfig

    def _get_rss_url(self, handle: str) -> str:
        """Get RSS feed URL for a Bluesky handle."""
        return f"https://bsky.app/profile/{handle}/rss"

    async def fetch(self, config: BlueskyConfig) -> list[RawItem]:
        """Fetch posts from Bluesky account.

        Args:
            config: Bluesky configuration with handle

        Returns:
            List of RawItem objects from the account
        """
        rss_url = self._get_rss_url(config.handle)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                rss_url,
                headers={"User-Agent": "NewsAggregator/1.0"},
                follow_redirects=True,
            )
            response.raise_for_status()

        feed = feedparser.parse(response.text)
        items = []

        for entry in feed.entries:
            # Parse publication date
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    published = datetime.fromtimestamp(mktime(entry.published_parsed))
                except (ValueError, OverflowError):
                    pass

            # Bluesky posts don't have titles - use first 100 chars of content
            content = entry.get("summary", entry.get("description", ""))
            title = content[:100] + "..." if len(content) > 100 else content

            items.append(
                RawItem(
                    external_id=entry.get("id", entry.link),
                    title=title,
                    content=content,
                    url=entry.link,
                    author=f"@{config.handle}",
                    published_at=published,
                    metadata={
                        "platform": "bluesky",
                        "handle": config.handle,
                    },
                )
            )

        return items

    async def validate(self, config: BlueskyConfig) -> tuple[bool, str]:
        """Validate Bluesky account.

        Args:
            config: Configuration to validate

        Returns:
            Tuple of (success, message)
        """
        try:
            rss_url = self._get_rss_url(config.handle)

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    rss_url,
                    headers={"User-Agent": "NewsAggregator/1.0"},
                    follow_redirects=True,
                )

                if response.status_code == 404:
                    return False, f"Account not found: @{config.handle}"

                response.raise_for_status()

            feed = feedparser.parse(response.text)
            return True, f"Found {len(feed.entries)} posts from @{config.handle}"

        except httpx.TimeoutException:
            return False, "Connection timeout"
        except httpx.HTTPStatusError as e:
            return False, f"HTTP error: {e.response.status_code}"
        except Exception as e:
            return False, f"Error: {str(e)}"
