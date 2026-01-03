"""Twitter/X connector using Nitter RSS proxy."""

from datetime import datetime
from time import mktime

import feedparser
import httpx
from pydantic import BaseModel, Field, field_validator

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry

# List of public Nitter instances (some may be down)
NITTER_INSTANCES = [
    "nitter.privacydev.net",
    "nitter.poast.org",
    "nitter.1d4.us",
]


class TwitterConfig(BaseModel):
    """Configuration for Twitter/Nitter connector."""

    username: str = Field(..., description="Twitter username (without @)")
    nitter_instance: str = Field(
        default="nitter.privacydev.net",
        description="Nitter instance to use",
    )
    include_retweets: bool = Field(default=True, description="Include retweets")
    include_replies: bool = Field(default=False, description="Include replies")

    @field_validator("username")
    @classmethod
    def normalize_username(cls, v: str) -> str:
        """Remove @ prefix if present."""
        return v.lstrip("@")


@ConnectorRegistry.register
class TwitterConnector(BaseConnector):
    """Twitter/X connector using Nitter RSS proxy.

    Nitter is an alternative Twitter frontend that provides RSS feeds.
    Note: Nitter instances may be unreliable or blocked.
    """

    connector_type = "twitter"
    display_name = "Twitter/X"
    description = "Follow Twitter accounts via Nitter RSS proxy"
    config_schema = TwitterConfig

    def _get_rss_url(self, config: TwitterConfig) -> str:
        """Get RSS feed URL for a Twitter user via Nitter."""
        base_url = f"https://{config.nitter_instance}/{config.username}/rss"
        return base_url

    async def fetch(self, config: TwitterConfig) -> list[RawItem]:
        """Fetch tweets from Twitter account via Nitter.

        Args:
            config: Twitter configuration

        Returns:
            List of RawItem objects from the account
        """
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

            # Check if it's a retweet
            title = entry.get("title", "")
            is_retweet = title.startswith("RT by")
            if is_retweet and not config.include_retweets:
                continue

            # Check if it's a reply
            is_reply = title.startswith("R to")
            if is_reply and not config.include_replies:
                continue

            content = entry.get("description", "")

            # Convert Nitter URLs back to Twitter URLs
            original_link = entry.link.replace(
                config.nitter_instance, "twitter.com"
            ).replace("nitter.", "")

            items.append(
                RawItem(
                    external_id=entry.get("id", entry.link),
                    title=title[:100] if title else content[:100],
                    content=content,
                    url=original_link,
                    author=f"@{config.username}",
                    published_at=published,
                    metadata={
                        "platform": "twitter",
                        "username": config.username,
                        "is_retweet": is_retweet,
                        "is_reply": is_reply,
                        "nitter_instance": config.nitter_instance,
                    },
                )
            )

        return items

    async def validate(self, config: TwitterConfig) -> tuple[bool, str]:
        """Validate Twitter account via Nitter.

        Args:
            config: Configuration to validate

        Returns:
            Tuple of (success, message)
        """
        # Try configured instance first, then fallback to others
        instances_to_try = [config.nitter_instance] + [
            i for i in NITTER_INSTANCES if i != config.nitter_instance
        ]

        last_error = ""
        for instance in instances_to_try:
            try:
                test_config = config.model_copy(update={"nitter_instance": instance})
                rss_url = self._get_rss_url(test_config)

                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        rss_url,
                        headers={"User-Agent": "NewsAggregator/1.0"},
                        follow_redirects=True,
                    )

                    if response.status_code == 404:
                        return False, f"Account not found: @{config.username}"

                    if response.status_code == 200:
                        feed = feedparser.parse(response.text)
                        if feed.entries:
                            msg = f"Found {len(feed.entries)} tweets"
                            if instance != config.nitter_instance:
                                msg += f" (using {instance})"
                            return True, msg

            except Exception as e:
                last_error = str(e)
                continue

        return False, f"No working Nitter instance found. Last error: {last_error}"
