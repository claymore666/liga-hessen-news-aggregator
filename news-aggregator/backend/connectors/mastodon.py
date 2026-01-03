"""Mastodon/Fediverse connector."""

import re
from datetime import datetime
from time import mktime

import feedparser
import httpx
from pydantic import BaseModel, Field, field_validator

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry


class MastodonConfig(BaseModel):
    """Configuration for Mastodon connector."""

    handle: str = Field(
        ..., description="Mastodon handle (e.g., @user@mastodon.social or user@instance)"
    )
    use_api: bool = Field(
        default=False, description="Use API instead of RSS (requires token)"
    )
    api_token: str | None = Field(
        default=None, description="API token for private accounts"
    )

    @field_validator("handle")
    @classmethod
    def parse_handle(cls, v: str) -> str:
        """Normalize handle: remove leading @, validate format."""
        v = v.lstrip("@")
        if "@" not in v:
            raise ValueError("Handle must be in format user@instance")
        return v

    @property
    def username(self) -> str:
        """Get username part of handle."""
        return self.handle.split("@")[0]

    @property
    def instance(self) -> str:
        """Get instance part of handle."""
        return self.handle.split("@")[1]


@ConnectorRegistry.register
class MastodonConnector(BaseConnector):
    """Mastodon/Fediverse connector.

    Supports both RSS feeds and API access for Mastodon accounts.
    RSS is used by default and works without authentication.
    API access requires a token and provides more metadata.
    """

    connector_type = "mastodon"
    display_name = "Mastodon"
    description = "Follow Mastodon/Fediverse accounts"
    config_schema = MastodonConfig

    def _strip_html(self, html: str) -> str:
        """Remove HTML tags from string."""
        clean = re.sub(r"<[^>]+>", "", html)
        return clean.strip()

    def _get_rss_url(self, config: MastodonConfig) -> str:
        """Get RSS feed URL for Mastodon account."""
        return f"https://{config.instance}/@{config.username}.rss"

    async def fetch(self, config: MastodonConfig) -> list[RawItem]:
        """Fetch posts from Mastodon account.

        Args:
            config: Mastodon configuration

        Returns:
            List of RawItem objects from the account
        """
        if config.use_api and config.api_token:
            return await self._fetch_via_api(config)
        return await self._fetch_via_rss(config)

    async def _fetch_via_rss(self, config: MastodonConfig) -> list[RawItem]:
        """Fetch posts via RSS feed."""
        rss_url = self._get_rss_url(config)

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

            content = entry.get("summary", "")
            plain_content = self._strip_html(content)
            title = plain_content[:100] + "..." if len(plain_content) > 100 else plain_content

            items.append(
                RawItem(
                    external_id=entry.get("id", entry.link),
                    title=title,
                    content=plain_content,
                    url=entry.link,
                    author=f"@{config.handle}",
                    published_at=published,
                    metadata={
                        "platform": "mastodon",
                        "instance": config.instance,
                        "handle": config.handle,
                        "source_type": "rss",
                    },
                )
            )

        return items

    async def _fetch_via_api(self, config: MastodonConfig) -> list[RawItem]:
        """Fetch posts via Mastodon API."""
        api_base = f"https://{config.instance}/api/v1"
        headers = {}
        if config.api_token:
            headers["Authorization"] = f"Bearer {config.api_token}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            # First, look up account ID
            lookup_url = f"{api_base}/accounts/lookup"
            response = await client.get(
                lookup_url,
                params={"acct": config.username},
                headers=headers,
            )
            response.raise_for_status()
            account = response.json()
            account_id = account["id"]

            # Fetch statuses
            statuses_url = f"{api_base}/accounts/{account_id}/statuses"
            response = await client.get(
                statuses_url,
                params={"limit": 40, "exclude_replies": True},
                headers=headers,
            )
            response.raise_for_status()
            statuses = response.json()

        items = []
        for status in statuses:
            # Handle boosts (reblogs)
            content_status = status.get("reblog") or status

            content = self._strip_html(content_status.get("content", ""))
            title = content[:100] + "..." if len(content) > 100 else content

            # Parse date
            published = None
            if status.get("created_at"):
                try:
                    published = datetime.fromisoformat(
                        status["created_at"].replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            items.append(
                RawItem(
                    external_id=status["id"],
                    title=title,
                    content=content,
                    url=status["url"],
                    author=f"@{content_status['account']['acct']}",
                    published_at=published,
                    metadata={
                        "platform": "mastodon",
                        "instance": config.instance,
                        "handle": config.handle,
                        "is_reblog": status.get("reblog") is not None,
                        "favorites": status.get("favourites_count", 0),
                        "reblogs": status.get("reblogs_count", 0),
                        "replies": status.get("replies_count", 0),
                        "source_type": "api",
                    },
                )
            )

        return items

    async def validate(self, config: MastodonConfig) -> tuple[bool, str]:
        """Validate Mastodon account.

        Args:
            config: Configuration to validate

        Returns:
            Tuple of (success, message)
        """
        try:
            rss_url = self._get_rss_url(config)

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
            return True, f"Valid Mastodon account @{config.handle} ({len(feed.entries)} posts)"

        except httpx.TimeoutException:
            return False, "Connection timeout"
        except httpx.HTTPStatusError as e:
            return False, f"HTTP error: {e.response.status_code}"
        except Exception as e:
            return False, f"Error: {str(e)}"
