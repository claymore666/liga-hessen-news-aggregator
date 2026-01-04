"""Telegram public channel connector.

Scrapes public Telegram channels via t.me/s/ web preview.
No authentication required for public channels.
"""

import logging
import re
from datetime import datetime, UTC
from hashlib import md5

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, field_validator

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class TelegramConfig(BaseModel):
    """Configuration for Telegram channel connector."""

    channel: str = Field(..., description="Telegram channel username (without @)")
    max_posts: int = Field(default=20, description="Maximum posts to fetch", ge=1, le=50)
    include_forwards: bool = Field(default=True, description="Include forwarded messages")

    @field_validator("channel")
    @classmethod
    def normalize_channel(cls, v: str) -> str:
        """Remove @ prefix and t.me URL parts if present."""
        v = v.strip()
        # Handle full URLs like https://t.me/channelname
        if "t.me/" in v:
            v = v.split("t.me/")[-1].split("/")[0]
        # Remove @ prefix
        return v.lstrip("@").lower()


@ConnectorRegistry.register
class TelegramConnector(BaseConnector):
    """Telegram public channel connector.

    Scrapes public Telegram channels via the t.me/s/ web preview.
    This works without authentication for public channels.

    Features:
    - Extracts message text, media, timestamps
    - Supports forwarded messages (optional)
    - Links back to original Telegram posts

    Limitations:
    - Only works for PUBLIC channels
    - Limited to ~20 most recent posts per request
    - No access to private channels or groups
    """

    connector_type = "telegram"
    display_name = "Telegram"
    description = "Monitor public Telegram channels"
    config_schema = TelegramConfig

    # User agent for requests
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    async def fetch(self, config: TelegramConfig) -> list[RawItem]:
        """Fetch posts from a public Telegram channel.

        Args:
            config: Telegram configuration

        Returns:
            List of RawItem objects from the channel
        """
        url = f"https://t.me/s/{config.channel}"
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        }

        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()

            # Check if channel exists
            if "tgme_page_icon_error" in response.text:
                logger.warning(f"Telegram channel not found: {config.channel}")
                return []

            soup = BeautifulSoup(response.text, "html.parser")
            items = []

            # Find all message widgets
            messages = soup.select(".tgme_widget_message_wrap")

            for msg in messages[:config.max_posts]:
                try:
                    item = self._parse_message(msg, config)
                    if item:
                        items.append(item)
                except Exception as e:
                    logger.warning(f"Error parsing Telegram message: {e}")
                    continue

            logger.info(f"Fetched {len(items)} posts from Telegram @{config.channel}")
            return items

    def _parse_message(self, msg_wrap, config: TelegramConfig) -> RawItem | None:
        """Parse a single Telegram message widget.

        Args:
            msg_wrap: BeautifulSoup element for message wrapper
            config: Telegram configuration

        Returns:
            RawItem or None if message should be skipped
        """
        msg = msg_wrap.select_one(".tgme_widget_message")
        if not msg:
            return None

        # Check if forwarded
        forwarded_from = msg.select_one(".tgme_widget_message_forwarded_from")
        is_forwarded = forwarded_from is not None
        if is_forwarded and not config.include_forwards:
            return None

        # Get message ID from data attribute or link
        msg_id = msg.get("data-post", "")
        if "/" in msg_id:
            msg_id = msg_id.split("/")[-1]

        # Get message link
        link_elem = msg.select_one(".tgme_widget_message_date")
        msg_url = ""
        if link_elem:
            msg_url = link_elem.get("href", "")
        if not msg_url:
            msg_url = f"https://t.me/{config.channel}/{msg_id}" if msg_id else f"https://t.me/{config.channel}"

        # Get text content
        text_elem = msg.select_one(".tgme_widget_message_text")
        text = ""
        if text_elem:
            # Get text while preserving line breaks
            text = text_elem.get_text(separator="\n", strip=True)

        # Get media info
        media_types = []
        photo = msg.select_one(".tgme_widget_message_photo")
        video = msg.select_one(".tgme_widget_message_video")
        document = msg.select_one(".tgme_widget_message_document")
        voice = msg.select_one(".tgme_widget_message_voice")

        if photo:
            media_types.append("photo")
        if video:
            media_types.append("video")
        if document:
            media_types.append("document")
        if voice:
            media_types.append("voice")

        # Get timestamp
        time_elem = msg.select_one(".tgme_widget_message_date time")
        published_at = datetime.now(UTC)
        if time_elem:
            datetime_str = time_elem.get("datetime", "")
            if datetime_str:
                try:
                    # Telegram uses ISO format with timezone
                    published_at = datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
                except ValueError:
                    pass

        # Get author info
        author_elem = msg.select_one(".tgme_widget_message_owner_name")
        author = config.channel
        if author_elem:
            author = author_elem.get_text(strip=True)

        # Get forwarded from info
        forward_author = None
        if forwarded_from:
            forward_link = forwarded_from.select_one("a")
            if forward_link:
                forward_author = forward_link.get_text(strip=True)

        # Get views count
        views_elem = msg.select_one(".tgme_widget_message_views")
        views = views_elem.get_text(strip=True) if views_elem else None

        # Skip empty messages (unless they have media)
        if not text and not media_types:
            return None

        # Generate external ID
        external_id = msg_id if msg_id else md5(
            f"{config.channel}:{text[:100]}:{published_at.isoformat()}".encode()
        ).hexdigest()

        # Create title from text
        title = text[:100] + "..." if len(text) > 100 else text
        if not title and media_types:
            title = f"[{', '.join(media_types)}] from @{config.channel}"

        return RawItem(
            external_id=external_id,
            title=title,
            content=text,
            url=msg_url,
            author=f"@{author}",
            published_at=published_at,
            metadata={
                "platform": "telegram",
                "channel": config.channel,
                "message_id": msg_id,
                "media_types": media_types,
                "is_forwarded": is_forwarded,
                "forwarded_from": forward_author,
                "views": views,
            },
        )

    async def validate(self, config: TelegramConfig) -> tuple[bool, str]:
        """Validate Telegram channel exists and is public.

        Args:
            config: Configuration to validate

        Returns:
            Tuple of (success, message)
        """
        url = f"https://t.me/s/{config.channel}"
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
                response = await client.get(url, follow_redirects=True)

                if response.status_code == 404:
                    return False, f"Channel not found: @{config.channel}"

                if response.status_code != 200:
                    return False, f"HTTP error {response.status_code}"

                # Check for error page
                if "tgme_page_icon_error" in response.text:
                    return False, f"Channel not found or private: @{config.channel}"

                # Parse to get channel info
                soup = BeautifulSoup(response.text, "html.parser")

                # Get channel title
                title_elem = soup.select_one(".tgme_channel_info_header_title")
                channel_title = title_elem.get_text(strip=True) if title_elem else config.channel

                # Get subscriber count
                counter_elem = soup.select_one(".tgme_channel_info_counter .counter_value")
                subscribers = counter_elem.get_text(strip=True) if counter_elem else "unknown"

                # Count visible messages
                messages = soup.select(".tgme_widget_message_wrap")

                return True, f"Channel '{channel_title}' ({subscribers} subscribers), {len(messages)} recent posts visible"

        except httpx.TimeoutException:
            return False, "Connection timeout"
        except Exception as e:
            return False, f"Error: {str(e)}"
