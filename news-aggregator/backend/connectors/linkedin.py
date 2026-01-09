"""LinkedIn scraper connector using Playwright."""

import json
import logging
import random
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from pydantic import BaseModel, Field, field_validator

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry

logger = logging.getLogger(__name__)

# Path to saved LinkedIn cookies
COOKIE_FILE = Path(__file__).parent.parent / "data" / "linkedin_cookies.json"


class LinkedInConfig(BaseModel):
    """Configuration for LinkedIn scraper connector."""

    profile_url: str = Field(
        ...,
        description="LinkedIn profile URL (company or personal)",
        json_schema_extra={
            "examples": [
                "https://linkedin.com/company/microsoft",
                "https://linkedin.com/in/satya-nadella",
            ]
        },
    )
    use_proxy: bool = Field(default=False, description="Use proxy rotation")
    max_posts: int = Field(default=10, ge=1, le=50, description="Maximum posts to fetch")
    follow_links: bool = Field(default=True, description="Follow links to fetch article content")
    max_links_per_post: int = Field(default=1, ge=1, le=3, description="Max article links to follow per post")

    @field_validator("profile_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate and normalize LinkedIn URL."""
        v = v.strip().rstrip("/")
        # Add https if missing
        if not v.startswith("http"):
            v = f"https://{v}"
        # Normalize to linkedin.com (not www.linkedin.com)
        v = v.replace("www.linkedin.com", "linkedin.com")
        # Validate it's a LinkedIn URL
        parsed = urlparse(v)
        if "linkedin.com" not in parsed.netloc:
            raise ValueError("URL must be a LinkedIn URL")
        return v

    @property
    def profile_type(self) -> str:
        """Determine if this is a company or personal profile."""
        if "/company/" in self.profile_url:
            return "company"
        elif "/in/" in self.profile_url:
            return "personal"
        else:
            return "unknown"

    @property
    def profile_id(self) -> str:
        """Extract the profile ID from URL."""
        if "/company/" in self.profile_url:
            match = re.search(r"/company/([^/]+)", self.profile_url)
            return match.group(1) if match else ""
        elif "/in/" in self.profile_url:
            match = re.search(r"/in/([^/]+)", self.profile_url)
            return match.group(1) if match else ""
        return ""


@ConnectorRegistry.register
class LinkedInConnector(BaseConnector):
    """LinkedIn scraper using Playwright.

    Scrapes posts directly from LinkedIn company pages and personal profiles
    using headless Chromium. Requires cookies from an authenticated session.

    Note: LinkedIn has strict anti-scraping measures. Use a dedicated account
    and don't scrape too frequently to avoid bans.
    """

    connector_type = "linkedin"
    display_name = "LinkedIn"
    description = "Scrape posts from LinkedIn company pages or personal profiles"
    config_schema = LinkedInConfig

    # User-Agent rotation pool (modern desktop browsers)
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]

    # Viewport rotation pool
    VIEWPORTS = [
        {"width": 1920, "height": 1080},
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},
        {"width": 1440, "height": 900},
        {"width": 1280, "height": 720},
    ]

    # Locale rotation pool
    LOCALES = ["de-DE", "en-US", "en-GB", "de-AT", "de-CH"]

    @staticmethod
    def _load_cookies() -> list[dict] | None:
        """Load saved LinkedIn cookies from file."""
        if not COOKIE_FILE.exists():
            logger.debug(f"No cookie file found at {COOKIE_FILE}")
            return None

        try:
            with open(COOKIE_FILE) as f:
                cookies = json.load(f)

            # Verify we have essential cookies
            li_at = next((c for c in cookies if c.get("name") == "li_at"), None)
            if not li_at:
                logger.warning("Cookie file exists but missing li_at (session cookie)")
                return None

            logger.info(f"Loaded {len(cookies)} LinkedIn cookies from file")
            return cookies

        except Exception as e:
            logger.error(f"Error loading cookies: {e}")
            return None

    async def fetch(self, config: LinkedInConfig) -> list[RawItem]:
        """Fetch posts from LinkedIn profile.

        Args:
            config: Scraper configuration

        Returns:
            List of RawItem objects containing posts
        """
        # Check for cookies first
        cookies = self._load_cookies()
        if not cookies:
            logger.warning(
                "No LinkedIn cookies available. LinkedIn requires authentication. "
                "Run scripts/extract_linkedin_cookies.py to set up cookies."
            )
            return []

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
            return await self._fetch_with_browser(config, cookies, proxy_server)
        except Exception as e:
            if proxy_server:
                logger.warning(f"Proxy failed: {e}. Retrying without proxy...")
                return await self._fetch_with_browser(config, cookies, None)
            raise

    async def _fetch_with_browser(
        self, config: LinkedInConfig, cookies: list[dict], proxy_server: str | None
    ) -> list[RawItem]:
        """Fetch posts using Playwright browser."""
        # Random fingerprint
        user_agent = random.choice(self.USER_AGENTS)
        viewport = random.choice(self.VIEWPORTS)
        locale = random.choice(self.LOCALES)

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
                    "locale": locale,
                    "timezone_id": "Europe/Berlin",
                }

                if proxy_server:
                    context_args["proxy"] = {"server": proxy_server}

                context = await browser.new_context(**context_args)

                # Inject cookies
                await context.add_cookies(cookies)
                logger.info("Injected LinkedIn cookies for authenticated access")

                page = await context.new_page()

                # Apply stealth mode
                stealth = Stealth()
                await stealth.apply_stealth_async(page)

                # Build URL based on profile type
                if config.profile_type == "company":
                    url = f"{config.profile_url}/posts/"
                else:
                    url = f"{config.profile_url}/recent-activity/all/"

                logger.info(
                    f"Fetching LinkedIn {config.profile_type} profile: {url}"
                    + (f" via proxy" if proxy_server else "")
                )

                await page.goto(url, wait_until="domcontentloaded", timeout=45000)

                # Wait for posts to load
                try:
                    # LinkedIn post selectors (may need updates if LinkedIn changes)
                    await page.wait_for_selector(
                        ".feed-shared-update-v2, .occludable-update",
                        timeout=30000,
                    )
                except PlaywrightTimeout:
                    # Try waiting longer
                    await page.wait_for_timeout(5000)
                    posts_exist = await page.query_selector(
                        ".feed-shared-update-v2, .occludable-update"
                    )
                    if not posts_exist:
                        logger.warning(f"No posts found for {config.profile_url}")
                        await browser.close()
                        return []

                # Scroll to load more posts
                for _ in range(min(3, config.max_posts // 5)):
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await page.wait_for_timeout(1500)

                # Extract posts
                items = await self._extract_posts(page, config)

                await browser.close()

        except PlaywrightTimeout as e:
            logger.error(f"Timeout scraping {config.profile_url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error scraping {config.profile_url}: {e}")
            raise

        logger.info(f"Extracted {len(items)} posts from {config.profile_url}")
        return items

    async def _extract_posts(self, page, config: LinkedInConfig) -> list[RawItem]:
        """Extract posts from LinkedIn page with optional link following."""
        # Import article extractor if link following is enabled
        article_extractor = None
        if config.follow_links:
            try:
                from services.article_extractor import ArticleExtractor

                article_extractor = ArticleExtractor()
            except ImportError:
                logger.warning("ArticleExtractor not available, disabling link following")

        items = []

        # Find all post elements
        post_elements = await page.query_selector_all(
            ".feed-shared-update-v2, .occludable-update"
        )

        for i, post_el in enumerate(post_elements[: config.max_posts]):
            try:
                # Extract post text
                text_el = await post_el.query_selector(
                    ".feed-shared-update-v2__description, .feed-shared-text, "
                    ".update-components-text"
                )
                text = ""
                if text_el:
                    text = await text_el.inner_text()
                    text = text.strip()

                if not text:
                    # Try to get reshared content
                    reshare_el = await post_el.query_selector(
                        ".feed-shared-mini-update-v2__description"
                    )
                    if reshare_el:
                        text = await reshare_el.inner_text()
                        text = text.strip()

                if not text:
                    continue

                # Extract author
                author_el = await post_el.query_selector(
                    ".update-components-actor__name, "
                    ".feed-shared-actor__name, "
                    ".update-components-actor__title"
                )
                author = config.profile_id
                if author_el:
                    author = await author_el.inner_text()
                    author = author.strip().split("\n")[0]

                # Extract timestamp (LinkedIn often uses relative times)
                time_el = await post_el.query_selector(
                    ".update-components-actor__sub-description, "
                    ".feed-shared-actor__sub-description, "
                    "time"
                )
                published_at = datetime.utcnow()
                if time_el:
                    time_text = await time_el.inner_text()
                    # Parse relative time (e.g., "2h", "3d", "1w")
                    published_at = self._parse_relative_time(time_text)

                # Try to get post URL
                post_url = config.profile_url
                link_el = await post_el.query_selector(
                    'a[href*="/posts/"], a[href*="/activity/"]'
                )
                if link_el:
                    href = await link_el.get_attribute("href")
                    if href:
                        if href.startswith("/"):
                            post_url = f"https://linkedin.com{href}"
                        else:
                            post_url = href

                # Generate external ID
                external_id = ""
                if "/posts/" in post_url or "/activity/" in post_url:
                    match = re.search(r"(\d{19})", post_url)
                    if match:
                        external_id = match.group(1)
                if not external_id:
                    external_id = f"linkedin_{config.profile_id}_{i}_{int(published_at.timestamp())}"

                # Extract and follow links if enabled
                combined_content = text
                extracted_links = []
                linked_articles = []

                if article_extractor:
                    extracted_links = article_extractor.extract_urls_from_text(text)

                    # Try to fetch article content from first valid link(s)
                    for link_url in extracted_links[: config.max_links_per_post]:
                        try:
                            article = await article_extractor.fetch_article(link_url)
                            if article and article.is_article:
                                linked_articles.append({
                                    "url": article.url,
                                    "title": article.title,
                                    "domain": article.source_domain,
                                    "content_length": len(article.content),
                                })
                                # Combine post text with article content
                                combined_content = f"""LinkedIn-Post von {author}:
{text}

---

Verlinkter Artikel von {article.source_domain}:
{article.title or 'Unbekannter Titel'}

{article.content[:4000]}"""
                                logger.info(f"Fetched article from {article.source_domain} ({len(article.content)} chars)")
                                break  # Only use first valid article
                        except Exception as e:
                            logger.debug(f"Failed to fetch article from {link_url}: {e}")

                items.append(
                    RawItem(
                        external_id=external_id,
                        title=text[:100] + "..." if len(text) > 100 else text,
                        content=combined_content,
                        url=post_url,
                        author=author,
                        published_at=published_at,
                        metadata={
                            "platform": "linkedin",
                            "profile_type": config.profile_type,
                            "profile_id": config.profile_id,
                            "profile_url": config.profile_url,
                            "original_post_text": text,
                            "extracted_links": extracted_links,
                            "linked_articles": linked_articles,
                        },
                    )
                )

            except Exception as e:
                logger.warning(f"Error extracting post: {e}")
                continue

        return items

    @staticmethod
    def _parse_relative_time(time_text: str) -> datetime:
        """Parse LinkedIn's relative time strings."""
        now = datetime.utcnow()
        time_text = time_text.lower().strip()

        # Common patterns
        patterns = [
            (r"(\d+)\s*m(?:in)?", "minutes"),
            (r"(\d+)\s*h(?:r|our)?", "hours"),
            (r"(\d+)\s*d(?:ay)?", "days"),
            (r"(\d+)\s*w(?:eek)?", "weeks"),
            (r"(\d+)\s*mo(?:nth)?", "months"),
            (r"(\d+)\s*y(?:ear)?", "years"),
        ]

        from datetime import timedelta

        for pattern, unit in patterns:
            match = re.search(pattern, time_text)
            if match:
                value = int(match.group(1))
                if unit == "minutes":
                    return now - timedelta(minutes=value)
                elif unit == "hours":
                    return now - timedelta(hours=value)
                elif unit == "days":
                    return now - timedelta(days=value)
                elif unit == "weeks":
                    return now - timedelta(weeks=value)
                elif unit == "months":
                    return now - timedelta(days=value * 30)
                elif unit == "years":
                    return now - timedelta(days=value * 365)

        return now

    async def validate(self, config: LinkedInConfig) -> tuple[bool, str]:
        """Validate configuration."""
        # Check profile type
        if config.profile_type == "unknown":
            return False, "Invalid LinkedIn URL. Must be /company/... or /in/..."

        if not config.profile_id:
            return False, "Could not extract profile ID from URL"

        # Check for cookies
        cookies = self._load_cookies()
        if not cookies:
            return False, (
                "No LinkedIn cookies found. "
                "Run scripts/extract_linkedin_cookies.py to authenticate."
            )

        # Try to load the page
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=random.choice(self.USER_AGENTS),
                )
                await context.add_cookies(cookies)
                page = await context.new_page()
                stealth = Stealth()
                await stealth.apply_stealth_async(page)

                response = await page.goto(config.profile_url, timeout=15000)

                # Check for login redirect
                current_url = page.url
                if "login" in current_url or "authwall" in current_url:
                    await browser.close()
                    return False, "Cookies expired or invalid. Re-run cookie extraction."

                await browser.close()

                if response and response.status == 200:
                    return True, f"LinkedIn {config.profile_type} profile accessible: {config.profile_id}"
                else:
                    status = response.status if response else "error"
                    return False, f"Profile not accessible (HTTP {status})"

        except Exception as e:
            return False, f"Validation error: {str(e)}"
