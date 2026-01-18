"""Bluesky connector using native RSS feeds."""

import logging
from datetime import datetime
from time import mktime

import feedparser
import httpx
from pydantic import BaseModel, Field, field_validator

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class BlueskyConfig(BaseModel):
    """Configuration for Bluesky connector."""

    handle: str = Field(
        ..., description="Bluesky handle (e.g., user.bsky.social or @user.bsky.social)"
    )
    follow_links: bool = Field(
        default=True, description="Follow links to fetch full article content"
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
        # Import article extractor if link following is enabled
        article_extractor = None
        if config.follow_links:
            try:
                from services.article_extractor import ArticleExtractor
                article_extractor = ArticleExtractor()
            except ImportError:
                logger.warning("ArticleExtractor not available, disabling link following")

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

            # Try to fetch full article content if link following is enabled
            final_content = content
            article_fetched = False
            if article_extractor:
                urls = article_extractor.extract_urls_from_text(content)
                # Filter out internal Bluesky links
                external_urls = [
                    url for url in urls
                    if not any(domain in url.lower() for domain in [
                        "bsky.app", "bsky.social", "blueskyweb.xyz",
                    ])
                ]
                for url in external_urls[:1]:  # Only follow first external link
                    try:
                        article = await article_extractor.fetch_article(url)
                        if article and article.content:
                            final_content = f"Post: {content}\n\n--- Verlinkter Artikel von {article.source_domain} ---\n\n{article.content}"
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
                        "platform": "bluesky",
                        "handle": config.handle,
                        "article_extracted": article_fetched,
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
