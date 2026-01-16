"""Mastodon/Fediverse connector."""

import logging
import re
from datetime import datetime
from time import mktime

import feedparser
import httpx
from pydantic import BaseModel, Field, field_validator

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry

logger = logging.getLogger(__name__)


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
    follow_links: bool = Field(
        default=True, description="Follow links to fetch full article content"
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
        # Import article extractor if link following is enabled
        article_extractor = None
        if config.follow_links:
            try:
                from services.article_extractor import ArticleExtractor
                article_extractor = ArticleExtractor()
            except ImportError:
                logger.warning("ArticleExtractor not available, disabling link following")

        if config.use_api and config.api_token:
            return await self._fetch_via_api(config, article_extractor)
        return await self._fetch_via_rss(config, article_extractor)

    async def _fetch_via_rss(self, config: MastodonConfig, article_extractor=None) -> list[RawItem]:
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

            # Try to fetch full article content if link following is enabled
            final_content = plain_content
            article_fetched = False
            if article_extractor:
                urls = article_extractor.extract_urls_from_text(content)
                # Filter out internal Mastodon links (same instance, known Mastodon domains)
                external_urls = [
                    url for url in urls
                    if not any(domain in url.lower() for domain in [
                        config.instance,
                        "mastodon.social", "mastodon.online", "mstdn.social",
                        "social.hessen.de", "social.bund.de", "hessen.social",
                    ])
                ]
                for url in external_urls[:1]:  # Only follow first external link
                    try:
                        article = await article_extractor.fetch_article(url)
                        if article and article.content:
                            final_content = f"Toot: {plain_content}\n\n--- Verlinkter Artikel von {article.source_domain} ---\n\n{article.content}"
                            article_fetched = True
                            logger.debug(f"Fetched article from {url}: {len(article.content)} chars")
                            break
                    except Exception as e:
                        logger.warning(f"Failed to fetch article from {url}: {e}")

            items.append(
                RawItem(
                    external_id=entry.get("id", entry.link),
                    title=title,
                    content=final_content,
                    url=entry.link,
                    author=f"@{config.handle}",
                    published_at=published,
                    metadata={
                        "platform": "mastodon",
                        "instance": config.instance,
                        "handle": config.handle,
                        "source_type": "rss",
                        "article_extracted": article_fetched,
                    },
                )
            )

        return items

    async def _fetch_via_api(self, config: MastodonConfig, article_extractor=None) -> list[RawItem]:
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

            html_content = content_status.get("content", "")
            plain_content = self._strip_html(html_content)
            title = plain_content[:100] + "..." if len(plain_content) > 100 else plain_content

            # Parse date
            published = None
            if status.get("created_at"):
                try:
                    published = datetime.fromisoformat(
                        status["created_at"].replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            # Try to fetch full article content if link following is enabled
            final_content = plain_content
            article_fetched = False
            if article_extractor:
                urls = article_extractor.extract_urls_from_text(html_content)
                # Filter out internal Mastodon links (same instance, known Mastodon domains)
                external_urls = [
                    url for url in urls
                    if not any(domain in url.lower() for domain in [
                        config.instance,
                        "mastodon.social", "mastodon.online", "mstdn.social",
                        "social.hessen.de", "social.bund.de", "hessen.social",
                    ])
                ]
                for url in external_urls[:1]:  # Only follow first external link
                    try:
                        article = await article_extractor.fetch_article(url)
                        if article and article.content:
                            final_content = f"Toot: {plain_content}\n\n--- Verlinkter Artikel von {article.source_domain} ---\n\n{article.content}"
                            article_fetched = True
                            logger.debug(f"Fetched article from {url}: {len(article.content)} chars")
                            break
                    except Exception as e:
                        logger.warning(f"Failed to fetch article from {url}: {e}")

            items.append(
                RawItem(
                    external_id=status["id"],
                    title=title,
                    content=final_content,
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
                        "article_extracted": article_fetched,
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
