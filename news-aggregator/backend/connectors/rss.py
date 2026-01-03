"""RSS/Atom feed connector."""

from datetime import datetime
from time import mktime

import feedparser
import httpx
from pydantic import BaseModel, Field, HttpUrl

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry


class RSSConfig(BaseModel):
    """Configuration for RSS connector."""

    url: HttpUrl = Field(..., description="Feed URL")
    custom_title: str | None = Field(
        default=None, description="Custom name for the feed (optional)"
    )


@ConnectorRegistry.register
class RSSConnector(BaseConnector):
    """RSS/Atom feed connector.

    Fetches items from any standard RSS or Atom feed.
    """

    connector_type = "rss"
    display_name = "RSS Feed"
    description = "Subscribe to any RSS or Atom feed"
    config_schema = RSSConfig

    async def fetch(self, config: RSSConfig) -> list[RawItem]:
        """Fetch items from RSS feed.

        Args:
            config: RSS configuration with feed URL

        Returns:
            List of RawItem objects from the feed
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                str(config.url),
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
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                try:
                    published = datetime.fromtimestamp(mktime(entry.updated_parsed))
                except (ValueError, OverflowError):
                    pass

            # Get content (prefer full content over summary)
            content = ""
            if hasattr(entry, "content") and entry.content:
                content = entry.content[0].get("value", "")
            elif hasattr(entry, "summary"):
                content = entry.summary
            elif hasattr(entry, "description"):
                content = entry.description

            # Get link
            link = entry.get("link", "")
            if not link and hasattr(entry, "links") and entry.links:
                link = entry.links[0].get("href", "")

            # Get author
            author = entry.get("author")
            if not author and hasattr(entry, "authors") and entry.authors:
                author = entry.authors[0].get("name")

            # Extract tags
            tags = []
            if hasattr(entry, "tags"):
                tags = [t.term for t in entry.tags if hasattr(t, "term")]

            items.append(
                RawItem(
                    external_id=entry.get("id", link),
                    title=entry.get("title", "Untitled"),
                    content=content,
                    url=link,
                    author=author,
                    published_at=published,
                    metadata={
                        "feed_title": config.custom_title
                        or feed.feed.get("title", "Unknown Feed"),
                        "feed_url": str(config.url),
                        "tags": tags,
                    },
                )
            )

        return items

    async def validate(self, config: RSSConfig) -> tuple[bool, str]:
        """Validate RSS feed URL.

        Args:
            config: RSS configuration to validate

        Returns:
            Tuple of (success, message)
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    str(config.url),
                    headers={"User-Agent": "NewsAggregator/1.0"},
                    follow_redirects=True,
                )
                response.raise_for_status()

            feed = feedparser.parse(response.text)

            # Check if it's a valid feed
            if feed.bozo and not feed.entries:
                error_msg = str(feed.bozo_exception) if feed.bozo_exception else "Unknown error"
                return False, f"Invalid feed: {error_msg}"

            feed_title = feed.feed.get("title", "Unknown")
            entry_count = len(feed.entries)
            return True, f"Valid feed: {feed_title} ({entry_count} entries)"

        except httpx.TimeoutException:
            return False, "Connection timeout"
        except httpx.HTTPStatusError as e:
            return False, f"HTTP error: {e.response.status_code}"
        except Exception as e:
            return False, f"Error: {str(e)}"
