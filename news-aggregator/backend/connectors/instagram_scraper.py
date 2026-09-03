"""Instagram scraper connector using Playwright.

Direct scraping of public instagram.com profiles with Playwright.
Works for public profiles without authentication.
"""

import asyncio
import logging
import random
import re
import time
from datetime import datetime, UTC

from playwright.async_api import TimeoutError as PlaywrightTimeout
from pydantic import BaseModel, Field, field_validator

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry
from services.browser_pool import browser_pool

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
    # Grid links on a profile page (post or reel). Instagram dropped the
    # <article> wrapper in 2026, so scope to <main>.
    POST_LINK_SELECTOR = "main a[href*='/p/'], main a[href*='/reel/']"

    # Anonymous Instagram access is rate limited per IP (observed 2026-09-02:
    # ~30 profile fetches with ~10 post pages each inside two hours got every
    # further request redirected to the login page for hours). Stay well
    # below that: one profile at a time, spaced out, slow post pages, and a
    # hard back-off once a login wall is seen. Keep the total per channel
    # under the scheduler's 300 s timeout for this connector.
    MIN_PROFILE_SPACING = 45.0      # seconds between profile loads (process-wide)
    POST_PAGE_DELAY_MS = 5000       # pause between post detail pages
    LOGIN_WALL_BACKOFF = 6 * 3600   # seconds to skip all Instagram fetches after a login wall
    _gate: asyncio.Lock | None = None
    _last_profile_fetch = 0.0
    _blocked_until = 0.0

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
        cls = InstagramScraperConnector
        remaining = cls._blocked_until - time.monotonic()
        if remaining > 0:
            logger.warning(
                f"Skipping @{config.username}: Instagram login-wall back-off active "
                f"for another {remaining / 60:.0f} min"
            )
            return []

        if cls._gate is None:
            cls._gate = asyncio.Lock()
        async with cls._gate:
            wait = cls._last_profile_fetch + cls.MIN_PROFILE_SPACING - time.monotonic()
            if wait > 0:
                logger.info(f"Pacing Instagram: waiting {wait:.0f}s before @{config.username}")
                await asyncio.sleep(wait)
            cls._last_profile_fetch = time.monotonic()
            return await self._fetch_paced(config)

    async def _fetch_paced(self, config: InstagramScraperConfig) -> list[RawItem]:
        """Fetch one profile (called with the pacing gate held)."""
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

        # Use shared browser pool instead of creating new Playwright instance
        async with browser_pool.get_browser() as browser:
            context = None
            try:
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

                # NOTE: playwright_stealth is deliberately not applied — its
                # navigator patches make Instagram render a blank page (0 posts).
                # Plain Playwright loads public profiles fine (verified 2026-09-02).

                # Navigate to profile
                url = f"https://www.instagram.com/{config.username}/"
                logger.info(f"Fetching Instagram profile: {url}")

                await page.goto(url, wait_until="domcontentloaded", timeout=45000)

                # Wait for page to load and check for errors
                await page.wait_for_timeout(3000)

                # Anonymous access is rate limited per IP: after a few dozen
                # profile loads Instagram redirects every request to the login
                # page. Say so explicitly instead of "No posts found".
                if "/accounts/login" in page.url:
                    InstagramScraperConnector._blocked_until = (
                        time.monotonic() + self.LOGIN_WALL_BACKOFF
                    )
                    logger.warning(
                        f"Instagram login wall for @{config.username} — anonymous "
                        "access from this IP is rate limited; fetches will fail until "
                        f"the block expires. Skipping Instagram for {self.LOGIN_WALL_BACKOFF // 3600} h."
                    )
                    return []

                # Check if profile exists
                page_content = await page.content()
                if "Sorry, this page isn't available" in page_content:
                    logger.warning(f"Instagram profile not found: @{config.username}")
                    return []

                if "This Account is Private" in page_content:
                    logger.warning(f"Instagram profile is private: @{config.username}")
                    return []

                # Wait for posts to load. Instagram dropped the <article> wrapper
                # in 2026; grid links now look like /<username>/p/<code>/ or
                # /<username>/reel/<code>/ and anonymous views also mix in
                # suggested posts from other accounts.
                try:
                    await page.wait_for_selector(self.POST_LINK_SELECTOR, timeout=15000)
                except PlaywrightTimeout:
                    logger.warning(f"No posts found for @{config.username}")
                    return []

                # Extract posts
                items = await self._extract_posts(page, config)

            except PlaywrightTimeout as e:
                logger.error(f"Timeout scraping @{config.username}: {e}")
                raise
            except Exception as e:
                logger.error(f"Error scraping @{config.username}: {e}")
                raise
            finally:
                # Close context (browser is closed by pool)
                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass

        logger.info(f"Extracted {len(items)} posts from @{config.username}")
        return items

    async def _extract_posts(self, page, config: InstagramScraperConfig) -> list[RawItem]:
        """Extract posts from Instagram profile page, including full captions."""
        items = []

        # Find this account's own post links (skip suggested posts by others)
        post_links = await page.query_selector_all(self.POST_LINK_SELECTOR)
        own_prefixes = (f"/{config.username.lower()}/", "/p/", "/reel/")

        seen_shortcodes = set()
        shortcodes_to_fetch = []

        # First pass: collect unique shortcodes
        # Iterate the whole grid: the first links are often suggested posts
        # from other accounts, which are skipped below.
        for link in post_links:
            try:
                href = await link.get_attribute("href")
                if not href or not href.lower().startswith(own_prefixes):
                    continue

                match = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)", href)
                if not match:
                    continue

                shortcode = match.group(1)
                if shortcode not in seen_shortcodes:
                    seen_shortcodes.add(shortcode)
                    shortcodes_to_fetch.append(shortcode)

                if len(shortcodes_to_fetch) >= config.max_posts:
                    break
            except Exception:
                # Some links may fail to extract, continue to next
                continue

        # Second pass: visit each post to get full caption
        for shortcode in shortcodes_to_fetch:
            try:
                post_url = f"https://www.instagram.com/p/{shortcode}/"
                logger.debug(f"Fetching post details: {post_url}")

                await page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(self.POST_PAGE_DELAY_MS)

                # Extract full caption from post page
                caption = await self._extract_caption(page)

                # Image / video: og: meta tags are stable and present anonymously
                image_url = await self._meta_content(page, "og:image")

                # Alt text of the post image (Instagram's image description)
                alt_text = ""
                img = await page.query_selector("main img[alt^='Photo by'], main img[alt^='Foto von']")
                if img:
                    alt_text = await img.get_attribute("alt") or ""

                # Check if video/reel
                is_video = bool(
                    await page.query_selector("meta[property='og:video']")
                    or await page.query_selector("main video")
                )

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

    @staticmethod
    async def _meta_content(page, prop: str) -> str:
        """Return content of <meta property=...> or empty string."""
        elem = await page.query_selector(f"meta[property='{prop}']")
        if not elem:
            return ""
        return await elem.get_attribute("content") or ""

    async def _extract_caption(self, page) -> str:
        """Extract full caption from Instagram post page."""
        caption = ""

        # og:description carries the full caption and is served to anonymous
        # visitors: '41 likes, 0 comments - user am August 26, 2026: "caption"'
        og = await self._meta_content(page, "og:description")
        m = re.search(r':\s*[\u201c"](.*)[\u201d"]\.?\s*$', og, re.DOTALL)
        if m and len(m.group(1).strip()) > 0:
            return m.group(1).strip()

        # Fallback: visible caption text (DOM changes often; Instagram dropped
        # the <article> wrapper in 2026, so scope to <main>)
        selectors = [
            "main h1",
            "main span[dir='auto']",
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
                # Selector may not match current page structure, try next
                continue

        return caption.strip()

    async def validate(self, config: InstagramScraperConfig) -> tuple[bool, str]:
        """Validate configuration by checking if profile exists."""
        try:
            async with browser_pool.get_browser() as browser:
                context = await browser.new_context(
                    user_agent=random.choice(self.USER_AGENTS),
                )
                try:
                    page = await context.new_page()


                    url = f"https://www.instagram.com/{config.username}/"
                    await page.goto(url, timeout=20000)
                    await page.wait_for_timeout(2000)

                    content = await page.content()

                    if "Sorry, this page isn't available" in content:
                        return False, f"Profile @{config.username} not found"

                    if "This Account is Private" in content:
                        return False, f"Profile @{config.username} is private"

                    # Check for posts
                    if "/p/" in content:
                        return True, f"Profile @{config.username} found with posts"

                    return True, f"Profile @{config.username} found (may have no posts)"
                finally:
                    await context.close()

        except PlaywrightTimeout:
            return False, "Connection timeout - Instagram may be blocking"
        except Exception as e:
            return False, f"Validation error: {str(e)}"
