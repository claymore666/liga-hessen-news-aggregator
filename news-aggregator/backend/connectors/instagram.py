"""Instagram connector using viewer proxy services."""

import re
from datetime import datetime, timedelta
from hashlib import md5

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, field_validator

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry

# Instagram viewer services (similar to Nitter for Twitter)
INSTAGRAM_PROXIES = [
    "picuki.com",
    "picnob.com",
    "imginn.com",
]


class InstagramConfig(BaseModel):
    """Configuration for Instagram connector."""

    username: str = Field(..., description="Instagram username (without @)")
    proxy_instance: str = Field(
        default="picuki.com",
        description="Instagram viewer proxy to use",
    )
    include_reels: bool = Field(default=True, description="Include Reels posts")
    max_posts: int = Field(default=20, description="Maximum posts to fetch", ge=1, le=50)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, v: str) -> str:
        """Remove @ prefix if present and lowercase."""
        return v.lstrip("@").lower()


@ConnectorRegistry.register
class InstagramConnector(BaseConnector):
    """Instagram connector using viewer proxy services.

    Uses Instagram viewer services (like Picuki, Picnob) to fetch public posts
    without authentication.

    WARNING: Instagram aggressively blocks scraping. These proxy services may:
    - Return 403 Forbidden errors
    - Be temporarily or permanently unavailable
    - Require CAPTCHA solving

    This connector works best when proxy services are operational. Consider
    using alternative news sources if Instagram monitoring is critical.
    """

    connector_type = "instagram"
    display_name = "Instagram"
    description = "Follow Instagram accounts via viewer proxy (may be unreliable)"
    config_schema = InstagramConfig

    def _get_profile_url(self, config: InstagramConfig) -> str:
        """Get profile URL for an Instagram user via proxy."""
        if config.proxy_instance == "picuki.com":
            return f"https://www.picuki.com/profile/{config.username}"
        elif config.proxy_instance == "picnob.com":
            return f"https://www.picnob.com/profile/{config.username}/"
        elif config.proxy_instance == "imginn.com":
            return f"https://imginn.com/{config.username}/"
        else:
            return f"https://www.picuki.com/profile/{config.username}"

    def _parse_relative_time(self, time_str: str) -> datetime | None:
        """Parse relative time strings like '2 hours ago', '3 days ago'."""
        if not time_str:
            return None

        time_str = time_str.lower().strip()
        now = datetime.now()

        patterns = [
            (r"(\d+)\s*min", lambda m: now - timedelta(minutes=int(m.group(1)))),
            (r"(\d+)\s*hour", lambda m: now - timedelta(hours=int(m.group(1)))),
            (r"(\d+)\s*day", lambda m: now - timedelta(days=int(m.group(1)))),
            (r"(\d+)\s*week", lambda m: now - timedelta(weeks=int(m.group(1)))),
            (r"(\d+)\s*month", lambda m: now - timedelta(days=int(m.group(1)) * 30)),
            (r"(\d+)\s*year", lambda m: now - timedelta(days=int(m.group(1)) * 365)),
        ]

        for pattern, calc in patterns:
            match = re.search(pattern, time_str)
            if match:
                return calc(match)

        return None

    async def _fetch_from_picuki(
        self, client: httpx.AsyncClient, config: InstagramConfig
    ) -> list[RawItem]:
        """Fetch posts from Picuki."""
        url = f"https://www.picuki.com/profile/{config.username}"
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        items = []

        # Find post containers
        posts = soup.select(".box-photos .box-photo")[:config.max_posts]

        for post in posts:
            try:
                # Get post link
                link_elem = post.select_one("a")
                if not link_elem:
                    continue

                post_path = link_elem.get("href", "")
                if not post_path:
                    continue

                # Construct original Instagram URL
                # Picuki uses /media/SHORTCODE format
                shortcode_match = re.search(r"/media/([^/]+)", post_path)
                if shortcode_match:
                    shortcode = shortcode_match.group(1)
                    instagram_url = f"https://www.instagram.com/p/{shortcode}/"
                else:
                    instagram_url = f"https://www.instagram.com/{config.username}/"

                # Get image
                img_elem = post.select_one("img")
                image_url = img_elem.get("src", "") if img_elem else ""

                # Get caption/content
                caption_elem = post.select_one(".photo-description")
                content = caption_elem.get_text(strip=True) if caption_elem else ""

                # Get timestamp
                time_elem = post.select_one(".time")
                time_str = time_elem.get_text(strip=True) if time_elem else ""
                published = self._parse_relative_time(time_str) or datetime.now()

                # Check if it's a reel/video
                is_reel = bool(post.select_one(".icon-video, .video-icon, .reel-icon"))
                if is_reel and not config.include_reels:
                    continue

                # Generate unique ID
                external_id = shortcode if shortcode_match else md5(
                    f"{config.username}:{content[:100]}".encode()
                ).hexdigest()

                items.append(
                    RawItem(
                        external_id=external_id,
                        title=content[:100] + "..." if len(content) > 100 else content or f"Post by @{config.username}",
                        content=content,
                        url=instagram_url,
                        author=f"@{config.username}",
                        published_at=published,
                        metadata={
                            "platform": "instagram",
                            "username": config.username,
                            "image_url": image_url,
                            "is_reel": is_reel,
                            "proxy_instance": config.proxy_instance,
                        },
                    )
                )
            except Exception:
                # Individual post parsing may fail, continue to next
                continue

        return items

    async def _fetch_from_picnob(
        self, client: httpx.AsyncClient, config: InstagramConfig
    ) -> list[RawItem]:
        """Fetch posts from Picnob."""
        url = f"https://www.picnob.com/profile/{config.username}/"
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        items = []

        # Picnob uses similar structure
        posts = soup.select(".item, .post-item, .photo-item")[:config.max_posts]

        for post in posts:
            try:
                link_elem = post.select_one("a[href*='/p/']")
                if not link_elem:
                    continue

                href = link_elem.get("href", "")
                shortcode_match = re.search(r"/p/([^/]+)", href)
                if shortcode_match:
                    shortcode = shortcode_match.group(1)
                    instagram_url = f"https://www.instagram.com/p/{shortcode}/"
                else:
                    continue

                img_elem = post.select_one("img")
                image_url = img_elem.get("src", "") if img_elem else ""

                caption_elem = post.select_one(".desc, .caption, .description")
                content = caption_elem.get_text(strip=True) if caption_elem else ""

                time_elem = post.select_one(".time, .date")
                time_str = time_elem.get_text(strip=True) if time_elem else ""
                published = self._parse_relative_time(time_str) or datetime.now()

                is_reel = bool(post.select_one(".video, .reel"))
                if is_reel and not config.include_reels:
                    continue

                items.append(
                    RawItem(
                        external_id=shortcode,
                        title=content[:100] + "..." if len(content) > 100 else content or f"Post by @{config.username}",
                        content=content,
                        url=instagram_url,
                        author=f"@{config.username}",
                        published_at=published,
                        metadata={
                            "platform": "instagram",
                            "username": config.username,
                            "image_url": image_url,
                            "is_reel": is_reel,
                            "proxy_instance": config.proxy_instance,
                        },
                    )
                )
            except Exception:
                # Individual post parsing may fail, continue to next
                continue

        return items

    async def _fetch_from_imginn(
        self, client: httpx.AsyncClient, config: InstagramConfig
    ) -> list[RawItem]:
        """Fetch posts from Imginn."""
        url = f"https://imginn.com/{config.username}/"
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        items = []

        # Imginn structure
        posts = soup.select(".item, .swiper-slide")[:config.max_posts]

        for post in posts:
            try:
                link_elem = post.select_one("a[href*='/p/']")
                if not link_elem:
                    continue

                href = link_elem.get("href", "")
                shortcode_match = re.search(r"/p/([^/]+)", href)
                if shortcode_match:
                    shortcode = shortcode_match.group(1)
                    instagram_url = f"https://www.instagram.com/p/{shortcode}/"
                else:
                    continue

                img_elem = post.select_one("img")
                image_url = img_elem.get("src", "") if img_elem else ""

                # Imginn often has caption in title attribute or separate element
                caption = img_elem.get("alt", "") if img_elem else ""
                caption_elem = post.select_one(".caption, .txt")
                if caption_elem:
                    caption = caption_elem.get_text(strip=True)

                time_elem = post.select_one(".time, .date")
                time_str = time_elem.get_text(strip=True) if time_elem else ""
                published = self._parse_relative_time(time_str) or datetime.now()

                is_reel = bool(post.select_one(".video-icon, .reel"))
                if is_reel and not config.include_reels:
                    continue

                items.append(
                    RawItem(
                        external_id=shortcode,
                        title=caption[:100] + "..." if len(caption) > 100 else caption or f"Post by @{config.username}",
                        content=caption,
                        url=instagram_url,
                        author=f"@{config.username}",
                        published_at=published,
                        metadata={
                            "platform": "instagram",
                            "username": config.username,
                            "image_url": image_url,
                            "is_reel": is_reel,
                            "proxy_instance": config.proxy_instance,
                        },
                    )
                )
            except Exception:
                # Individual post parsing may fail, continue to next
                continue

        return items

    async def fetch(self, config: InstagramConfig) -> list[RawItem]:
        """Fetch posts from Instagram account via proxy.

        Args:
            config: Instagram configuration

        Returns:
            List of RawItem objects from the account
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            # Try configured instance first
            if config.proxy_instance == "picuki.com":
                return await self._fetch_from_picuki(client, config)
            elif config.proxy_instance == "picnob.com":
                return await self._fetch_from_picnob(client, config)
            elif config.proxy_instance == "imginn.com":
                return await self._fetch_from_imginn(client, config)
            else:
                # Default to picuki
                return await self._fetch_from_picuki(client, config)

    async def validate(self, config: InstagramConfig) -> tuple[bool, str]:
        """Validate Instagram account via proxy.

        Args:
            config: Configuration to validate

        Returns:
            Tuple of (success, message)
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        # Try configured instance first, then fallback to others
        instances_to_try = [config.proxy_instance] + [
            i for i in INSTAGRAM_PROXIES if i != config.proxy_instance
        ]

        last_error = ""
        for instance in instances_to_try:
            try:
                test_config = config.model_copy(update={"proxy_instance": instance})

                async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
                    url = self._get_profile_url(test_config)
                    response = await client.get(url, follow_redirects=True)

                    if response.status_code == 404:
                        return False, f"Account not found: @{config.username}"

                    if response.status_code == 200:
                        soup = BeautifulSoup(response.text, "html.parser")

                        # Check for "not found" or "private" indicators
                        page_text = soup.get_text().lower()
                        if "not found" in page_text or "doesn't exist" in page_text:
                            return False, f"Account not found: @{config.username}"
                        if "private" in page_text and "account is private" in page_text:
                            return False, f"Account @{config.username} is private"

                        # Check for posts
                        posts = soup.select(".box-photo, .item, .post-item, .photo-item")
                        if posts:
                            msg = f"Found {len(posts)} posts"
                            if instance != config.proxy_instance:
                                msg += f" (using {instance})"
                            return True, msg

                        # Profile exists but no posts visible
                        return True, f"Profile found, but no public posts visible (using {instance})"

            except httpx.HTTPStatusError as e:
                last_error = f"HTTP {e.response.status_code}"
            except httpx.TimeoutException:
                last_error = "Connection timeout"
            except Exception as e:
                last_error = str(e)
            continue

        return False, f"No working proxy instance found (Instagram blocks most scrapers). Last error: {last_error}"
