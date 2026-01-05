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
        """Extract posts from Instagram profile page, including full captions."""
        items = []

        # Find all post links (Instagram uses /p/SHORTCODE/ format)
        post_links = await page.query_selector_all("article a[href*='/p/']")

        seen_shortcodes = set()
        shortcodes_to_fetch = []

        # First pass: collect unique shortcodes
        for link in post_links[:config.max_posts * 2]:
            try:
                href = await link.get_attribute("href")
                if not href:
                    continue

                match = re.search(r"/p/([A-Za-z0-9_-]+)", href)
                if not match:
                    continue

                shortcode = match.group(1)
                if shortcode not in seen_shortcodes:
                    seen_shortcodes.add(shortcode)
                    shortcodes_to_fetch.append(shortcode)

                if len(shortcodes_to_fetch) >= config.max_posts:
                    break
            except Exception:
                continue

        # Second pass: visit each post to get full caption
        for shortcode in shortcodes_to_fetch:
            try:
                post_url = f"https://www.instagram.com/p/{shortcode}/"
                logger.debug(f"Fetching post details: {post_url}")

                await page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)

                # Extract full caption from post page
                caption = await self._extract_caption(page)

                # Get image URL
                image_url = ""
                img = await page.query_selector("article img[src*='instagram']")
                if img:
                    image_url = await img.get_attribute("src") or ""

                # Get alt text (contains image description)
                alt_text = ""
                if img:
                    alt_text = await img.get_attribute("alt") or ""

                # Check if video/reel
                is_video = bool(await page.query_selector("article video"))

                # Try to get timestamp
                published_at = datetime.now(UTC)
                time_elem = await page.query_selector("time[datetime]")
                if time_elem:
                    datetime_str = await time_elem.get_attribute("datetime")
                    if datetime_str:
                        try:
                            published_at = datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
                        except ValueError:
                            pass

                # Combine alt text (image description) with caption
                full_content = caption
                if alt_text and not alt_text.startswith("Photo"):
                    # Alt text might have OCR'd text from image
                    full_content = f"{alt_text}\n\n{caption}" if caption else alt_text

                # Create title from caption (first line or truncated)
                title = caption.split("\n")[0][:100] if caption else f"Post by @{config.username}"
                if len(caption.split("\n")[0]) > 100:
                    title += "..."

                items.append(
                    RawItem(
                        external_id=shortcode,
                        title=title,
                        content=full_content,
                        url=post_url,
                        author=f"@{config.username}",
                        published_at=published_at,
                        metadata={
                            "platform": "instagram",
                            "username": config.username,
                            "shortcode": shortcode,
                            "image_url": image_url,
                            "alt_text": alt_text,
                            "is_video": is_video,
                        },
                    )
                )

            except Exception as e:
                logger.warning(f"Error extracting post {shortcode}: {e}")
                continue

        return items

    async def _extract_caption(self, page) -> str:
        """Extract full caption from Instagram post page."""
        caption = ""

        # Try multiple selectors for caption
        selectors = [
            # Main caption container
            "article div[role='button'] span:not([class])",
            "article h1 + div span",
            # Expanded caption
            "article span[dir='auto']",
            # Caption in meta
            "meta[property='og:description']",
        ]

        for selector in selectors:
            try:
                if selector.startswith("meta"):
                    elem = await page.query_selector(selector)
                    if elem:
                        caption = await elem.get_attribute("content") or ""
                else:
                    # Get all matching elements and find the longest text
                    elements = await page.query_selector_all(selector)
                    for elem in elements:
                        text = await elem.inner_text()
                        if text and len(text) > len(caption):
                            # Skip if it's just metadata like "likes" or "comments"
                            if not re.match(r"^\d+[,.]?\d*\s*(likes?|comments?|views?)", text.lower()):
                                caption = text

                if len(caption) > 50:  # Found substantial caption
                    break
            except Exception:
                continue

        return caption.strip()

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
