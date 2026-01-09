"""X.com (Twitter) scraper connector using Playwright."""

import json
import logging
import random
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from pydantic import BaseModel, Field

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry

logger = logging.getLogger(__name__)

# Path to saved X.com cookies (from scripts/x_auth.py)
COOKIE_FILE = Path(__file__).parent.parent / "data" / "x_cookies.json"


class XScraperConfig(BaseModel):
    """Configuration for X.com scraper connector."""

    username: str = Field(..., description="X/Twitter username (without @)")
    use_proxy: bool = Field(default=False, description="Use proxy rotation")
    include_replies: bool = Field(default=False, description="Include replies")
    max_tweets: int = Field(default=20, ge=1, le=100, description="Maximum tweets to fetch")
    follow_links: bool = Field(default=True, description="Follow links to fetch article content")
    max_links_per_tweet: int = Field(default=1, ge=1, le=3, description="Max article links to follow per tweet")


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

    @staticmethod
    def _load_cookies() -> list[dict] | None:
        """Load saved X.com cookies from file.

        Returns:
            List of cookie dicts or None if no cookies found
        """
        if not COOKIE_FILE.exists():
            logger.debug(f"No cookie file found at {COOKIE_FILE}")
            return None

        try:
            with open(COOKIE_FILE) as f:
                cookies = json.load(f)

            # Verify we have the essential auth_token
            auth_token = next((c for c in cookies if c.get("name") == "auth_token"), None)
            if not auth_token:
                logger.warning("Cookie file exists but missing auth_token")
                return None

            logger.info(f"Loaded {len(cookies)} X.com cookies from file")
            return cookies

        except Exception as e:
            logger.error(f"Error loading cookies: {e}")
            return None

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

                # Load and inject saved cookies for authentication
                cookies = self._load_cookies()
                if cookies:
                    await context.add_cookies(cookies)
                    logger.info("Injected saved X.com cookies for authenticated access")

                page = await context.new_page()

                # Apply stealth mode
                stealth = Stealth()
                await stealth.apply_stealth_async(page)

                # Navigate to profile
                url = f"https://x.com/{config.username}"
                auth_status = "authenticated" if cookies else "unauthenticated"
                logger.info(f"Fetching X.com profile: {url} ({auth_status})" + (f" via proxy" if proxy_server else ""))

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
        """Extract tweets from page with optional link following.

        Args:
            page: Playwright page object
            config: Scraper configuration

        Returns:
            List of RawItem objects
        """
        # Import article extractor if link following is enabled
        article_extractor = None
        if config.follow_links:
            try:
                from services.article_extractor import ArticleExtractor
                article_extractor = ArticleExtractor()
            except ImportError:
                logger.warning("ArticleExtractor not available, disabling link following")

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

                # Extract and follow links if enabled
                combined_content = text
                extracted_links = []
                linked_articles = []

                if article_extractor:
                    # Method 1: Extract URLs from tweet text
                    extracted_links = article_extractor.extract_urls_from_text(text)

                    # Method 2: Extract URLs from card elements (article previews)
                    card_links = await self._extract_card_links(tweet_el)
                    for card_url in card_links:
                        if card_url not in extracted_links:
                            extracted_links.append(card_url)

                    # Try to fetch article content from first valid link(s)
                    for link_url in extracted_links[: config.max_links_per_tweet]:
                        try:
                            article = await article_extractor.fetch_article(link_url)
                            if article and article.is_article:
                                linked_articles.append({
                                    "url": article.url,
                                    "title": article.title,
                                    "domain": article.source_domain,
                                    "content_length": len(article.content),
                                })
                                # Combine tweet text with article content
                                combined_content = f"""Tweet von @{author}:
{text}

---

Verlinkter Artikel von {article.source_domain}:
{article.title or 'Unbekannter Titel'}

{article.content[:4000]}"""
                                logger.info(f"Fetched article from {article.source_domain} ({len(article.content)} chars)")
                                break  # Only use first valid article
                        except Exception as e:
                            logger.debug(f"Failed to fetch article from {link_url}: {e}")

                # Create RawItem
                items.append(
                    RawItem(
                        external_id=external_id,
                        title=text[:100] + "..." if len(text) > 100 else text,
                        content=combined_content,
                        url=tweet_url or f"https://x.com/{config.username}",
                        author=author,
                        published_at=published_at,
                        metadata={
                            "platform": "x.com",
                            "username": config.username,
                            "is_reply": is_reply,
                            "original_tweet_text": text,
                            "extracted_links": extracted_links,
                            "linked_articles": linked_articles,
                        },
                    )
                )

            except Exception as e:
                logger.warning(f"Error extracting tweet: {e}")
                continue

        return items

    async def _extract_card_links(self, tweet_el) -> list[str]:
        """Extract article links from tweet card elements.

        X.com shows article previews in card elements. This method extracts
        the actual article URLs from these cards, bypassing t.co redirects.

        Args:
            tweet_el: Playwright element handle for the tweet

        Returns:
            List of external article URLs found in cards
        """
        links = []

        try:
            # Method 1: Find card wrapper elements with links
            # X.com uses data-testid="card.wrapper" for article cards
            card_wrappers = await tweet_el.query_selector_all('[data-testid="card.wrapper"] a')
            for link_el in card_wrappers:
                href = await link_el.get_attribute("href")
                if href and self._is_external_article_url(href):
                    links.append(href)

            # Method 2: Find links in card layouts (large/small media cards)
            card_links = await tweet_el.query_selector_all('[data-testid*="card.layout"] a')
            for link_el in card_links:
                href = await link_el.get_attribute("href")
                if href and self._is_external_article_url(href):
                    if href not in links:
                        links.append(href)

            # Method 3: Find any link that looks like an article (not X internal)
            # Sometimes cards don't have specific testids
            all_links = await tweet_el.query_selector_all('a[href*="://"]')
            for link_el in all_links:
                href = await link_el.get_attribute("href")
                if href and self._is_external_article_url(href):
                    if href not in links:
                        links.append(href)

        except Exception as e:
            logger.debug(f"Error extracting card links: {e}")

        return links

    def _is_external_article_url(self, url: str) -> bool:
        """Check if URL is an external article link (not X internal).

        Args:
            url: URL to check

        Returns:
            True if URL is an external article link
        """
        if not url:
            return False

        # Skip X/Twitter internal links (but allow t.co which redirects to articles)
        skip_domains = ("x.com", "twitter.com", "pic.twitter.com", "pbs.twimg.com")
        for domain in skip_domains:
            if domain in url:
                return False

        # Allow t.co links - they redirect to actual article URLs
        if "t.co/" in url:
            return True

        # Skip common non-article patterns
        skip_patterns = [
            r"/login", r"/signin", r"/auth",
            r"\.(jpg|jpeg|png|gif|mp4|mp3)(\?|$)",
            r"youtube\.com/watch", r"youtu\.be/",
        ]
        for pattern in skip_patterns:
            if re.search(pattern, url, re.I):
                return False

        # Must be http/https
        if not url.startswith(("http://", "https://")):
            return False

        return True

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
