"""Article content extraction from URLs.

Extracts article content from news links found in tweets and other social media posts.
Uses heuristic detection to identify news articles and trafilatura for content extraction.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs, urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Known news domains (German focus)
NEWS_DOMAINS = {
    # National news
    "spiegel.de", "zeit.de", "faz.net", "sueddeutsche.de", "tagesschau.de",
    "bild.de", "welt.de", "focus.de", "taz.de", "tagesspiegel.de",
    "handelsblatt.com", "wiwo.de", "stern.de", "n-tv.de", "zdf.de", "phoenix.de",
    # Hessen regional
    "hessenschau.de", "fr.de", "fnp.de", "op-online.de", "hna.de",
    "giessener-allgemeine.de", "mittelhessen.de", "fuldaerzeitung.de",
    # Welfare/Social organizations
    "proasyl.de", "caritas.de", "diakonie.de", "awo.org", "drk.de",
    "der-paritaetische.de", "zentralwohlfahrtsstelle.de",
    # Government
    "bmas.de", "bmfsfj.de", "bmg.bund.de", "hessen.de",
    "soziales.hessen.de", "innen.hessen.de",
    # Hessen politics
    "hessischer-landtag.de", "cdu-hessen.de", "spd-hessen.de",
    "gruene-hessen.de", "fdp-hessen.de", "dielinke-hessen.de",
    "staatskanzlei.hessen.de", "kultusministerium.hessen.de",
    "finanzministerium.hessen.de", "wirtschaft.hessen.de",
    # Wire services
    "dpa.de", "epd.de", "kna.de",
    # Research institutes
    "diw.de",
}

# URL patterns to skip (not articles)
SKIP_PATTERNS = [
    r"/login", r"/signin", r"/auth", r"/account", r"/register",
    r"\.(jpg|jpeg|png|gif|pdf|mp4|mp3|webp)(\?|$)",
    r"twitter\.com/intent", r"x\.com/intent",
    r"youtube\.com/watch", r"youtu\.be/",
    r"instagram\.com/p/", r"facebook\.com/share",
    r"/datenschutz", r"/impressum", r"/agb",
]


@dataclass
class ArticleContent:
    """Extracted article content."""
    url: str
    title: str | None
    content: str
    is_article: bool
    source_domain: str


class ArticleExtractor:
    """Extract article content from URLs found in social media posts."""

    # Googlebot headers for bypassing soft paywalls
    # Many news sites serve full content to search engine crawlers
    GOOGLEBOT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Referer": "https://www.google.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    }

    # Standard browser headers (fallback)
    BROWSER_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    }

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def extract_urls_from_text(self, text: str) -> list[str]:
        """Extract external URLs from text (e.g., tweet content).

        Filters out internal Twitter/X links and non-article URLs.
        Handles X.com's space-broken URLs (e.g., "https:// faz.net/article").
        """
        # First, fix space-broken URLs that X.com creates
        # Pattern: https:// domain.tld/path with spaces in protocol or path
        fixed_text = re.sub(r'https?://\s+', 'https://', text)  # Fix space after ://
        # Remove spaces within URLs (between word chars, or after punctuation like -)
        fixed_text = re.sub(r'([/\w.-])\s+([/\w.-])', r'\1\2', fixed_text)

        # Match URLs
        url_pattern = r'https?://[^\s<>"\'{}|\\^`\[\]]+'
        urls = re.findall(url_pattern, fixed_text)

        filtered = []
        for url in urls:
            # Clean trailing punctuation
            url = url.rstrip('.,;:!?)"\']')

            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # Skip Twitter/X internal links (but keep t.co - these redirect to articles)
            if domain in ("x.com", "twitter.com", "pic.twitter.com"):
                continue

            # Skip patterns we don't want
            if any(re.search(p, url, re.I) for p in SKIP_PATTERNS):
                continue

            filtered.append(url)

        return filtered

    def is_likely_news_article(self, soup: BeautifulSoup, url: str) -> bool:
        """Heuristic detection of news articles.

        Uses multiple signals:
        - Known news domain
        - og:type = article
        - <article> tag present
        - Schema.org article markup
        - publishedTime meta tag
        """
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")

        # Check if known news domain
        if any(d in domain for d in NEWS_DOMAINS):
            return True

        # Count article indicators
        indicators = 0

        # og:type = article
        og_type = soup.find("meta", property="og:type")
        if og_type and "article" in og_type.get("content", "").lower():
            indicators += 2

        # <article> tag present
        if soup.find("article"):
            indicators += 2

        # Schema.org article markup
        schema_pattern = re.compile(r"schema\.org.*(Article|NewsArticle|BlogPosting)", re.I)
        if soup.find(attrs={"itemtype": schema_pattern}):
            indicators += 2

        # JSON-LD with article type
        for script in soup.find_all("script", type="application/ld+json"):
            if script.string and '"@type"' in script.string:
                if re.search(r'"@type"\s*:\s*"(Article|NewsArticle|BlogPosting)"', script.string, re.I):
                    indicators += 2
                    break

        # publishedTime or datePublished meta
        if soup.find("meta", property=re.compile(r"published|datePublished", re.I)):
            indicators += 1
        if soup.find("meta", attrs={"name": re.compile(r"published|datePublished", re.I)}):
            indicators += 1

        # author meta tag
        if soup.find("meta", attrs={"name": "author"}) or soup.find("meta", property="article:author"):
            indicators += 1

        return indicators >= 2

    def _clean_url(self, url: str) -> str:
        """Clean URL by removing tracking parameters and fixing common issues.

        Args:
            url: URL to clean

        Returns:
            Cleaned URL
        """
        from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

        parsed = urlparse(url)

        # Remove common tracking parameters
        tracking_params = {
            "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
            "fbclid", "gclid", "ref", "source", "xtor", "wtmc",
        }

        if parsed.query:
            params = parse_qs(parsed.query)
            # Remove tracking parameters
            cleaned_params = {k: v for k, v in params.items() if k.lower() not in tracking_params}
            new_query = urlencode(cleaned_params, doseq=True)
            parsed = parsed._replace(query=new_query)

        return urlunparse(parsed)

    async def resolve_redirect(self, url: str) -> str:
        """Resolve redirects to get final URL (e.g., t.co -> actual URL).

        Args:
            url: URL to resolve

        Returns:
            Final URL after following redirects
        """
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
            ) as client:
                response = await client.head(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    },
                )
                return str(response.url)
        except Exception as e:
            logger.debug(f"Could not resolve redirects for {url}: {e}")
            return url

    async def fetch_article(self, url: str) -> ArticleContent | None:
        """Fetch and extract article content from URL.

        Returns None if:
        - URL cannot be fetched
        - Content doesn't look like a news article
        - Extracted content is too short
        """
        try:
            # Resolve t.co and other redirects first
            if "t.co" in url:
                url = await self.resolve_redirect(url)
                logger.debug(f"Resolved t.co URL to: {url}")

            # Resolve Google redirect URLs (used by Google Alerts RSS)
            # Format: https://www.google.com/url?rct=j&sa=t&url=ACTUAL_URL&ct=ga&...
            if "google.com/url" in url or "google.de/url" in url:
                parsed = urlparse(url)
                params = parse_qs(parsed.query)
                if "url" in params:
                    url = params["url"][0]
                    logger.debug(f"Resolved Google redirect to: {url}")

            # Clean up URL - remove common tracking parameters
            url = self._clean_url(url)

            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "")

            # Check if this is a known news domain (for Wayback fallback)
            is_news_domain = any(d in domain for d in NEWS_DOMAINS)

            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                verify=True,
            ) as client:
                # Try Googlebot headers first for better paywall bypass
                response = await client.get(url, headers=self.GOOGLEBOT_HEADERS)
                response.raise_for_status()

            html = response.text
            soup = BeautifulSoup(html, "lxml")

            # Check if it's likely an article
            is_article = self.is_likely_news_article(soup, url)

            if not is_article:
                # SPA pages may not have article indicators in unrendered HTML
                # Try Playwright before giving up
                if len(html) > 5000 and _looks_like_spa(html):
                    logger.info(f"Not detected as article but SPA markers found for {domain}, trying Playwright...")
                    rendered_article = await _fetch_with_playwright(url, domain)
                    if rendered_article:
                        logger.info(f"Playwright rescued non-article SPA page for {domain}: {len(rendered_article.content)} chars")
                        return rendered_article
                logger.debug(f"URL {url} does not appear to be a news article")
                return None

            # Extract title
            title = self._extract_title(soup)

            # Extract content
            content = self._extract_content(soup, html)

            # If content is too short and it's a news domain, try Wayback Machine
            if (not content or len(content) < 100) and is_news_domain:
                logger.info(f"Insufficient content from {domain}, trying Wayback Machine...")
                wayback_content = await self._try_wayback_machine(url)
                if wayback_content and len(wayback_content.content) > len(content or ""):
                    logger.info(f"Got better content from Wayback Machine: {len(wayback_content.content)} chars")
                    return wayback_content

            # Playwright fallback for JS-rendered pages (SPAs like Angular, React, Vue)
            # Check before the <100 early return so we can rescue 0-149 char content
            if (not content or len(content) < 150) and len(html) > 5000:
                if _looks_like_spa(html):
                    original_len = len(content) if content else 0
                    logger.info(f"Short content ({original_len} chars) with SPA markers detected for {domain}, trying Playwright...")
                    rendered_article = await _fetch_with_playwright(url, domain)
                    if rendered_article and len(rendered_article.content) > original_len:
                        logger.info(f"Playwright fallback succeeded for {domain}: {len(rendered_article.content)} chars (was {original_len})")
                        return rendered_article

            if not content or len(content) < 100:
                logger.debug(f"Insufficient content extracted from {url}: {len(content) if content else 0} chars")
                return None

            logger.info(f"Extracted article from {domain}: {len(content)} chars")

            return ArticleContent(
                url=url,
                title=title,
                content=content,
                is_article=is_article,
                source_domain=domain,
            )

        except httpx.TimeoutException:
            logger.warning(f"Timeout fetching article from {url}")
            return None
        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP error fetching {url}: {e.response.status_code}")
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch article from {url}: {e}")
            return None

    def _extract_title(self, soup: BeautifulSoup) -> str | None:
        """Extract article title from HTML."""
        # Try og:title first (usually cleanest)
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"].strip()

        # Try twitter:title
        twitter_title = soup.find("meta", attrs={"name": "twitter:title"})
        if twitter_title and twitter_title.get("content"):
            return twitter_title["content"].strip()

        # Try <title> tag
        if soup.title and soup.title.string:
            return soup.title.string.strip()

        # Try h1
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)

        return None

    def _extract_content(self, soup: BeautifulSoup, html: str) -> str:
        """Extract main content from HTML.

        Uses trafilatura if available, falls back to BeautifulSoup heuristics.
        Optimized for German news sites with paywall bypassing.
        """
        # Try trafilatura first (best results)
        try:
            import trafilatura
            content = trafilatura.extract(
                html,
                favor_precision=True,      # Prioritize article body over peripheral text
                include_comments=False,
                include_tables=False,
                include_formatting=False,  # Strip all HTML/trackers for clean text
                include_images=False,      # Prevent image-based tracking pixels
                no_fallback=False,
            )
            if content and len(content) > 100:
                return content
        except ImportError:
            logger.warning("trafilatura not installed, using fallback extraction")
        except Exception as e:
            logger.debug(f"trafilatura extraction failed: {e}")

        # Fallback: BeautifulSoup heuristics
        return self._extract_content_fallback(soup)

    def _extract_content_fallback(self, soup: BeautifulSoup) -> str:
        """Fallback content extraction using BeautifulSoup."""
        # Remove unwanted elements
        for tag in soup.find_all([
            "script", "style", "nav", "header", "footer", "aside",
            "iframe", "noscript", "svg", "form", "button",
        ]):
            tag.decompose()

        # Remove common non-content classes
        for selector in [
            ".advertisement", ".ad-container", ".social-share",
            ".related-articles", ".comments", ".newsletter",
            "[class*='cookie']", "[class*='consent']",
        ]:
            for el in soup.select(selector):
                el.decompose()

        # Try <article> tag first
        article = soup.find("article")
        if article:
            text = article.get_text(separator=" ", strip=True)
            if len(text) > 200:
                return text

        # Try main content area
        main = soup.find("main")
        if main:
            text = main.get_text(separator=" ", strip=True)
            if len(text) > 200:
                return text

        # Try common content class patterns
        content_patterns = [
            re.compile(r"article.*(body|content|text)", re.I),
            re.compile(r"(story|post|entry).*(body|content|text)", re.I),
            re.compile(r"^content$", re.I),
        ]

        for pattern in content_patterns:
            el = soup.find(class_=pattern)
            if el:
                text = el.get_text(separator=" ", strip=True)
                if len(text) > 200:
                    return text

        # Last resort: body with aggressive cleanup
        body = soup.find("body")
        if body:
            text = body.get_text(separator=" ", strip=True)
            # Truncate if too long
            return text[:5000] if len(text) > 5000 else text

        return ""

    async def _try_wayback_machine(self, url: str) -> ArticleContent | None:
        """Try to fetch article from Wayback Machine (archive.org).

        Useful for hard paywalls where even Googlebot headers don't help.
        The Wayback Machine often has cached versions from before paywalls
        were implemented or from when the article was freely accessible.

        Args:
            url: Original article URL

        Returns:
            ArticleContent if successful, None otherwise
        """
        try:
            # Query Wayback Machine availability API
            api_url = f"https://archive.org/wayback/available?url={url}"

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(api_url)
                response.raise_for_status()
                data = response.json()

            # Check if there's an archived snapshot
            snapshots = data.get("archived_snapshots", {})
            closest = snapshots.get("closest", {})

            if not closest.get("available"):
                logger.debug(f"No Wayback Machine snapshot available for {url}")
                return None

            archive_url = closest.get("url")
            if not archive_url:
                return None

            logger.info(f"Found Wayback snapshot: {archive_url}")

            # Fetch the archived page
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(archive_url, headers=self.BROWSER_HEADERS)
                response.raise_for_status()

            html = response.text
            soup = BeautifulSoup(html, "lxml")

            # Remove Wayback Machine toolbar/overlay elements
            for selector in ["#wm-ipp-base", "#wm-ipp", ".wb-autocomplete-suggestions"]:
                for el in soup.select(selector):
                    el.decompose()

            # Extract content
            title = self._extract_title(soup)
            content = self._extract_content(soup, html)

            if not content or len(content) < 100:
                logger.debug(f"Insufficient content from Wayback snapshot: {len(content) if content else 0} chars")
                return None

            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "")

            return ArticleContent(
                url=url,  # Return original URL, not archive URL
                title=title,
                content=content,
                is_article=True,
                source_domain=domain,
            )

        except httpx.TimeoutException:
            logger.debug(f"Timeout fetching from Wayback Machine for {url}")
            return None
        except Exception as e:
            logger.debug(f"Wayback Machine fallback failed for {url}: {e}")
            return None


# SPA framework markers that indicate JS rendering is needed
_SPA_MARKERS = [
    "{{ ",        # Angular/Vue template expressions
    "ng-app",     # AngularJS
    "ng-bind",    # AngularJS
    "data-reactroot",  # React
    "__NEXT_DATA__",   # Next.js
    "v-app",      # Vuetify/Vue
    "nuxt",       # Nuxt.js
    "_nuxt",      # Nuxt.js build artifacts
    "app-root",   # Angular
]


def _looks_like_spa(html: str) -> bool:
    """Check if HTML contains SPA framework markers suggesting JS rendering is needed."""
    html_lower = html.lower()
    return any(marker.lower() in html_lower for marker in _SPA_MARKERS)


async def _fetch_with_playwright(url: str, domain: str) -> ArticleContent | None:
    """Fetch article using Playwright browser for JS-rendered pages.

    Uses the shared browser pool to render the page and re-extract content
    with trafilatura.
    """
    from services.browser_pool import browser_pool

    context = None
    page = None
    try:
        browser = await browser_pool.get_browser()
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="de-DE",
        )
        page = await context.new_page()

        logger.debug(f"Playwright fallback: navigating to {url}")
        await page.goto(url, wait_until="networkidle", timeout=15000)

        html = await page.content()

        # Re-extract with trafilatura on rendered HTML
        try:
            import trafilatura
            content = trafilatura.extract(
                html,
                favor_precision=True,
                include_comments=False,
                include_tables=False,
                include_formatting=False,
                include_images=False,
                no_fallback=False,
            )
        except Exception as e:
            logger.debug(f"Playwright trafilatura extraction failed for {url}: {e}")
            content = None

        if not content or len(content) < 100:
            # Try BeautifulSoup fallback on rendered HTML
            soup = BeautifulSoup(html, "lxml")
            extractor = ArticleExtractor()
            content = extractor._extract_content_fallback(soup)

        if not content or len(content) < 100:
            logger.debug(f"Playwright fallback: insufficient content from {url}: {len(content) if content else 0} chars")
            return None

        # Extract title from rendered page
        soup = BeautifulSoup(html, "lxml")
        extractor = ArticleExtractor()
        title = extractor._extract_title(soup)

        return ArticleContent(
            url=url,
            title=title,
            content=content,
            is_article=True,
            source_domain=domain,
        )

    except Exception as e:
        logger.warning(f"Playwright fallback failed for {url}: {e}")
        return None
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
        if context:
            try:
                await context.close()
            except Exception:
                pass
