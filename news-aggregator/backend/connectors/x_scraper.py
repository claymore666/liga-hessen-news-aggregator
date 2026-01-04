"""X.com (Twitter) scraper connector using Playwright."""

import logging
import random
import re
from datetime import datetime

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from pydantic import BaseModel, Field

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class XScraperConfig(BaseModel):
    """Configuration for X.com scraper connector."""

    username: str = Field(..., description="X/Twitter username (without @)")
    use_proxy: bool = Field(default=False, description="Use proxy rotation")
    include_replies: bool = Field(default=False, description="Include replies")
    max_tweets: int = Field(default=20, ge=1, le=100, description="Maximum tweets to fetch")


@ConnectorRegistry.register
class XScraperConnector(BaseConnector):
    """X.com scraper using Playwright.

    Scrapes posts directly from X.com profile pages using headless Chromium.
    Supports fingerprint rotation and optional proxy rotation.
    """

    connector_type = "x_scraper"
    display_name = "X.com Scraper"
    description = "Scrape posts directly from X.com/Twitter profiles"
    config_schema = XScraperConfig

    # User-Agent rotation pool (modern desktop browsers)
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0",
    ]

    # Viewport rotation pool (common desktop resolutions)
    VIEWPORTS = [
        {"width": 1920, "height": 1080},
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},
        {"width": 1440, "height": 900},
        {"width": 1280, "height": 720},
        {"width": 1600, "height": 900},
        {"width": 2560, "height": 1440},
    ]

    # Locale rotation pool
    LOCALES = ["de-DE", "en-US", "en-GB", "de-AT", "de-CH"]

    async def fetch(self, config: XScraperConfig) -> list[RawItem]:
        """Fetch tweets from X.com profile.

        Args:
            config: Scraper configuration

        Returns:
            List of RawItem objects containing tweets
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

        # Try with proxy first, fallback to direct if proxy fails
        try:
            return await self._fetch_with_browser(config, proxy_server)
        except Exception as e:
            if proxy_server:
                logger.warning(f"Proxy failed: {e}. Retrying without proxy...")
                return await self._fetch_with_browser(config, None)
            raise

    async def _fetch_with_browser(
        self, config: XScraperConfig, proxy_server: str | None
    ) -> list[RawItem]:
        """Fetch tweets using Playwright browser.

        Args:
            config: Scraper configuration
            proxy_server: Optional proxy server URL

        Returns:
            List of RawItem objects containing tweets
        """
        # Random fingerprint
        user_agent = random.choice(self.USER_AGENTS)
        viewport = random.choice(self.VIEWPORTS)
        locale = random.choice(self.LOCALES)

        items = []

        try:
            async with async_playwright() as p:
                # Launch browser
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                    ],
                )

                # Create context with random fingerprint
                context_args = {
                    "user_agent": user_agent,
                    "viewport": viewport,
                    "locale": locale,
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
                url = f"https://x.com/{config.username}"
                logger.info(f"Fetching X.com profile: {url}" + (f" via proxy" if proxy_server else ""))

                await page.goto(url, wait_until="domcontentloaded", timeout=45000)

                # Wait for tweets to load (may need to wait for JS to render)
                try:
                    await page.wait_for_selector(
                        '[data-testid="tweet"]',
                        timeout=30000,
                    )
                except PlaywrightTimeout:
                    # Try waiting a bit more - X.com is slow to render
                    await page.wait_for_timeout(5000)
                    tweets_exist = await page.query_selector('[data-testid="tweet"]')
                    if not tweets_exist:
                        logger.warning(f"No tweets found for @{config.username}")
                        await browser.close()
                        return []

                # Scroll to load more tweets
                for _ in range(min(3, config.max_tweets // 10)):
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await page.wait_for_timeout(1000)

                # Extract tweets
                items = await self._extract_tweets(page, config)

                await browser.close()

        except PlaywrightTimeout as e:
            logger.error(f"Timeout scraping @{config.username}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error scraping @{config.username}: {e}")
            raise

        logger.info(f"Extracted {len(items)} tweets from @{config.username}")
        return items

    async def _extract_tweets(self, page, config: XScraperConfig) -> list[RawItem]:
        """Extract tweets from page.

        Args:
            page: Playwright page object
            config: Scraper configuration

        Returns:
            List of RawItem objects
        """
        items = []

        # Find all tweet elements
        tweet_elements = await page.query_selector_all('[data-testid="tweet"]')

        for i, tweet_el in enumerate(tweet_elements[: config.max_tweets]):
            try:
                # Extract tweet text
                text_el = await tweet_el.query_selector('[data-testid="tweetText"]')
                text = await text_el.inner_text() if text_el else ""

                if not text:
                    continue

                # Check if it's a reply (skip if not including replies)
                is_reply = False
                reply_indicator = await tweet_el.query_selector('[data-testid="socialContext"]')
                if reply_indicator:
                    indicator_text = await reply_indicator.inner_text()
                    if "replied" in indicator_text.lower() or "antwort" in indicator_text.lower():
                        is_reply = True

                if is_reply and not config.include_replies:
                    continue

                # Extract author
                author_el = await tweet_el.query_selector('[data-testid="User-Name"]')
                author = config.username
                if author_el:
                    author_links = await author_el.query_selector_all("a")
                    if author_links:
                        author = await author_links[0].inner_text()
                        author = author.lstrip("@")

                # Extract timestamp
                time_el = await tweet_el.query_selector("time")
                published_at = datetime.utcnow()
                tweet_url = ""
                if time_el:
                    datetime_attr = await time_el.get_attribute("datetime")
                    if datetime_attr:
                        try:
                            published_at = datetime.fromisoformat(datetime_attr.replace("Z", "+00:00"))
                        except ValueError:
                            pass

                    # Get tweet URL from parent link
                    parent_link = await time_el.query_selector("xpath=ancestor::a")
                    if parent_link:
                        href = await parent_link.get_attribute("href")
                        if href:
                            tweet_url = f"https://x.com{href}"

                # Generate external ID from URL or create one
                external_id = ""
                if tweet_url:
                    match = re.search(r"/status/(\d+)", tweet_url)
                    if match:
                        external_id = match.group(1)
                if not external_id:
                    external_id = f"{config.username}_{i}_{int(published_at.timestamp())}"

                # Create RawItem
                items.append(
                    RawItem(
                        external_id=external_id,
                        title=text[:100] + "..." if len(text) > 100 else text,
                        content=text,
                        url=tweet_url or f"https://x.com/{config.username}",
                        author=author,
                        published_at=published_at,
                        metadata={
                            "platform": "x.com",
                            "username": config.username,
                            "is_reply": is_reply,
                        },
                    )
                )

            except Exception as e:
                logger.warning(f"Error extracting tweet: {e}")
                continue

        return items

    async def validate(self, config: XScraperConfig) -> tuple[bool, str]:
        """Validate configuration by checking if profile exists.

        Args:
            config: Scraper configuration

        Returns:
            Tuple of (success, message)
        """
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=random.choice(self.USER_AGENTS),
                )
                page = await context.new_page()
                stealth = Stealth()
                await stealth.apply_stealth_async(page)

                url = f"https://x.com/{config.username}"
                response = await page.goto(url, timeout=15000)

                await browser.close()

                if response and response.status == 200:
                    return True, f"Profile @{config.username} found"
                else:
                    return False, f"Profile @{config.username} not found (HTTP {response.status if response else 'error'})"

        except Exception as e:
            return False, f"Validation error: {str(e)}"
