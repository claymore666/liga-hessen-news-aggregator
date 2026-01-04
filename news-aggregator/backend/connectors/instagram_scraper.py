"""Instagram scraper connector using Playwright.

Direct scraping of instagram.com with stealth mode.
Works for public profiles without authentication.
"""

import logging
import random
import re
from datetime import datetime, UTC

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from pydantic import BaseModel, Field, field_validator

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class InstagramScraperConfig(BaseModel):
    """Configuration for Instagram scraper connector."""

    username: str = Field(..., description="Instagram username (without @)")
    use_proxy: bool = Field(default=False, description="Use proxy rotation")
    max_posts: int = Field(default=12, ge=1, le=30, description="Maximum posts to fetch")

    @field_validator("username")
    @classmethod
    def normalize_username(cls, v: str) -> str:
        """Remove @ prefix and normalize."""
        v = v.strip()
        # Handle full URLs
        if "instagram.com/" in v:
            v = v.split("instagram.com/")[-1].split("/")[0].split("?")[0]
        return v.lstrip("@").lower()


@ConnectorRegistry.register
class InstagramScraperConnector(BaseConnector):
    """Instagram scraper using Playwright.

    Scrapes posts directly from instagram.com profile pages using headless Chromium.
    Supports fingerprint rotation and optional proxy rotation.

    Note: Without login, only ~12 posts are visible on public profiles.
    Private profiles cannot be accessed.
    """

    connector_type = "instagram_scraper"
    display_name = "Instagram Scraper"
    description = "Scrape posts directly from Instagram profiles (public only)"
    config_schema = InstagramScraperConfig

    # User-Agent rotation pool
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]

    # Viewport rotation pool
    VIEWPORTS = [
        {"width": 1920, "height": 1080},
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},
        {"width": 1440, "height": 900},
    ]

    async def fetch(self, config: InstagramScraperConfig) -> list[RawItem]:
        """Fetch posts from Instagram profile.

        Args:
            config: Scraper configuration

        Returns:
            List of RawItem objects containing posts
        """
        # Get proxy if enabled
        proxy_server = None
        if config.use_proxy:
            try:
                from services.proxy_manager import proxy_manager
                proxy = proxy_manager.get_next_proxy()
                if proxy:
                    proxy_server = f"http://{proxy}"
                    logger.info(f"Using proxy: {proxy}")
            except Exception as e:
                logger.warning(f"Failed to get proxy: {e}, continuing without proxy")

        # Try with proxy first, fallback to direct
        try:
            return await self._fetch_with_browser(config, proxy_server)
        except Exception as e:
            if proxy_server:
                logger.warning(f"Proxy failed: {e}. Retrying without proxy...")
                return await self._fetch_with_browser(config, None)
            raise

    async def _fetch_with_browser(
        self, config: InstagramScraperConfig, proxy_server: str | None
    ) -> list[RawItem]:
        """Fetch posts using Playwright browser."""
        user_agent = random.choice(self.USER_AGENTS)
        viewport = random.choice(self.VIEWPORTS)

        items = []

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                    ],
                )

                context_args = {
                    "user_agent": user_agent,
                    "viewport": viewport,
                    "locale": "de-DE",
                    "timezone_id": "Europe/Berlin",
                }

                if proxy_server:
                    context_args["proxy"] = {"server": proxy_server}

                context = await browser.new_context(**context_args)
                page = await context.new_page()

                # Apply stealth mode
                stealth = Stealth()
                await stealth.apply_stealth_async(page)

                # Navigate to profile
                url = f"https://www.instagram.com/{config.username}/"
                logger.info(f"Fetching Instagram profile: {url}")

                await page.goto(url, wait_until="domcontentloaded", timeout=45000)

                # Wait for page to load and check for errors
                await page.wait_for_timeout(3000)

                # Check if profile exists
                page_content = await page.content()
                if "Sorry, this page isn't available" in page_content:
                    logger.warning(f"Instagram profile not found: @{config.username}")
                    await browser.close()
                    return []

                if "This Account is Private" in page_content:
                    logger.warning(f"Instagram profile is private: @{config.username}")
                    await browser.close()
                    return []

                # Wait for posts to load
                try:
                    # Instagram uses article elements for posts
                    await page.wait_for_selector("article a[href*='/p/']", timeout=15000)
                except PlaywrightTimeout:
                    logger.warning(f"No posts found for @{config.username}")
                    await browser.close()
                    return []

                # Extract posts
                items = await self._extract_posts(page, config)

                await browser.close()

        except PlaywrightTimeout as e:
            logger.error(f"Timeout scraping @{config.username}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error scraping @{config.username}: {e}")
            raise

        logger.info(f"Extracted {len(items)} posts from @{config.username}")
        return items

    async def _extract_posts(self, page, config: InstagramScraperConfig) -> list[RawItem]:
        """Extract posts from Instagram profile page."""
        items = []

        # Find all post links (Instagram uses /p/SHORTCODE/ format)
        post_links = await page.query_selector_all("article a[href*='/p/']")

        seen_shortcodes = set()

        for link in post_links[:config.max_posts * 2]:  # Get extra to filter duplicates
            try:
                href = await link.get_attribute("href")
                if not href:
                    continue

                # Extract shortcode from URL
                match = re.search(r"/p/([A-Za-z0-9_-]+)", href)
                if not match:
                    continue

                shortcode = match.group(1)

                # Skip duplicates (Instagram often has multiple links to same post)
                if shortcode in seen_shortcodes:
                    continue
                seen_shortcodes.add(shortcode)

                if len(items) >= config.max_posts:
                    break

                # Get image from the link
                img = await link.query_selector("img")
                image_url = ""
                alt_text = ""
                if img:
                    image_url = await img.get_attribute("src") or ""
                    alt_text = await img.get_attribute("alt") or ""

                # The alt text often contains the caption
                content = alt_text
                if content.startswith("Photo by"):
                    # Extract actual caption after "Photo by X on Month Day, Year."
                    parts = content.split(".", 1)
                    if len(parts) > 1:
                        content = parts[1].strip()

                # Check if it's a video/reel
                is_video = False
                video_indicator = await link.query_selector("svg[aria-label*='Video'], svg[aria-label*='Reel']")
                if video_indicator:
                    is_video = True

                # Create title from content
                title = content[:100] + "..." if len(content) > 100 else content
                if not title:
                    title = f"Post by @{config.username}"

                # Instagram URLs
                post_url = f"https://www.instagram.com/p/{shortcode}/"

                items.append(
                    RawItem(
                        external_id=shortcode,
                        title=title,
                        content=content,
                        url=post_url,
                        author=f"@{config.username}",
                        published_at=datetime.now(UTC),  # Instagram doesn't show dates on grid
                        metadata={
                            "platform": "instagram",
                            "username": config.username,
                            "shortcode": shortcode,
                            "image_url": image_url,
                            "is_video": is_video,
                        },
                    )
                )

            except Exception as e:
                logger.warning(f"Error extracting post: {e}")
                continue

        return items

    async def validate(self, config: InstagramScraperConfig) -> tuple[bool, str]:
        """Validate configuration by checking if profile exists."""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=random.choice(self.USER_AGENTS),
                )
                page = await context.new_page()

                stealth = Stealth()
                await stealth.apply_stealth_async(page)

                url = f"https://www.instagram.com/{config.username}/"
                await page.goto(url, timeout=20000)
                await page.wait_for_timeout(2000)

                content = await page.content()
                await browser.close()

                if "Sorry, this page isn't available" in content:
                    return False, f"Profile @{config.username} not found"

                if "This Account is Private" in content:
                    return False, f"Profile @{config.username} is private"

                # Check for posts
                if "/p/" in content:
                    return True, f"Profile @{config.username} found with posts"

                return True, f"Profile @{config.username} found (may have no posts)"

        except PlaywrightTimeout:
            return False, "Connection timeout - Instagram may be blocking"
        except Exception as e:
            return False, f"Validation error: {str(e)}"
