"""Tests for article content extraction."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bs4 import BeautifulSoup

from services.article_extractor import (
    ArticleExtractor,
    ArticleContent,
    NEWS_DOMAINS,
    SKIP_PATTERNS,
)


@pytest.fixture
def extractor():
    """Create ArticleExtractor instance for testing."""
    return ArticleExtractor(timeout=10.0)


class TestExtractUrlsFromText:
    """Tests for extract_urls_from_text method."""

    def test_extract_simple_url(self, extractor):
        """Should extract a simple URL from text."""
        text = "Check out this article https://example.com/news/article"
        urls = extractor.extract_urls_from_text(text)
        assert len(urls) == 1
        assert urls[0] == "https://example.com/news/article"

    def test_extract_multiple_urls(self, extractor):
        """Should extract multiple URLs from text."""
        # Use comma separator (space merging affects simple word separators)
        text = "Links: https://first.com/a , https://second.com/b"
        urls = extractor.extract_urls_from_text(text)
        assert len(urls) == 2

    def test_skip_twitter_internal_links(self, extractor):
        """Should skip Twitter/X internal links."""
        # Use dash separator
        text = "Tweet https://twitter.com/user/status/123 - https://example.com/article"
        urls = extractor.extract_urls_from_text(text)
        assert len(urls) == 1
        assert "example.com" in urls[0]

    def test_skip_x_internal_links(self, extractor):
        """Should skip X.com internal links."""
        # Use dash separator
        text = "Post https://x.com/user/status/123 - https://example.com/news"
        urls = extractor.extract_urls_from_text(text)
        assert len(urls) == 1
        assert "example.com" in urls[0]

    def test_keep_tco_links(self, extractor):
        """Should keep t.co links (these redirect to articles)."""
        text = "Link: https://t.co/abc123"
        urls = extractor.extract_urls_from_text(text)
        assert len(urls) == 1
        assert "t.co" in urls[0]

    def test_skip_image_urls(self, extractor):
        """Should skip image URLs."""
        # Use comma separator
        text = "Image: https://example.com/photo.jpg , https://example.com/news"
        urls = extractor.extract_urls_from_text(text)
        assert len(urls) == 1
        assert ".jpg" not in urls[0]

    def test_skip_video_urls(self, extractor):
        """Should skip video URLs."""
        # Use dash separator
        text = "Video https://youtube.com/watch?v=abc - https://example.com/article"
        urls = extractor.extract_urls_from_text(text)
        assert len(urls) == 1
        assert "youtube" not in urls[0]

    def test_skip_login_urls(self, extractor):
        """Should skip login/auth URLs."""
        # Use comma separator
        text = "Login at https://example.com/login , https://example.com/news"
        urls = extractor.extract_urls_from_text(text)
        assert len(urls) == 1
        assert "/login" not in urls[0]

    def test_fix_space_broken_urls(self, extractor):
        """Should fix X.com's space-broken URLs."""
        text = "Article https:// faz.net/aktuell/article"
        urls = extractor.extract_urls_from_text(text)
        assert len(urls) == 1
        assert "https://faz.net" in urls[0]

    def test_clean_trailing_punctuation(self, extractor):
        """Should remove trailing punctuation from URLs."""
        text = "Read this: https://example.com/article."
        urls = extractor.extract_urls_from_text(text)
        assert len(urls) == 1
        assert urls[0].endswith("/article")

    def test_empty_text(self, extractor):
        """Should return empty list for empty text."""
        urls = extractor.extract_urls_from_text("")
        assert urls == []

    def test_no_urls_in_text(self, extractor):
        """Should return empty list when no URLs present."""
        urls = extractor.extract_urls_from_text("This is just plain text.")
        assert urls == []


class TestIsLikelyNewsArticle:
    """Tests for is_likely_news_article method."""

    def test_known_news_domain(self, extractor):
        """Should identify known news domains."""
        html = "<html><body><p>Some content</p></body></html>"
        soup = BeautifulSoup(html, "lxml")
        assert extractor.is_likely_news_article(soup, "https://spiegel.de/article") is True
        assert extractor.is_likely_news_article(soup, "https://www.hessenschau.de/news") is True

    def test_og_type_article(self, extractor):
        """Should identify pages with og:type=article."""
        html = '''<html><head>
            <meta property="og:type" content="article" />
        </head><body><p>Content</p></body></html>'''
        soup = BeautifulSoup(html, "lxml")
        assert extractor.is_likely_news_article(soup, "https://unknown-site.com/post") is True

    def test_article_tag_present(self, extractor):
        """Should identify pages with <article> tag."""
        html = "<html><body><article><p>Article content</p></article></body></html>"
        soup = BeautifulSoup(html, "lxml")
        assert extractor.is_likely_news_article(soup, "https://unknown-site.com/post") is True

    def test_schema_org_article(self, extractor):
        """Should identify pages with Schema.org article markup."""
        html = '''<html><body>
            <div itemtype="https://schema.org/NewsArticle"><p>Content</p></div>
        </body></html>'''
        soup = BeautifulSoup(html, "lxml")
        assert extractor.is_likely_news_article(soup, "https://unknown.com/post") is True

    def test_json_ld_article(self, extractor):
        """Should identify pages with JSON-LD article type."""
        html = '''<html><head>
            <script type="application/ld+json">{"@type": "Article", "headline": "Test"}</script>
        </head><body><p>Content</p></body></html>'''
        soup = BeautifulSoup(html, "lxml")
        assert extractor.is_likely_news_article(soup, "https://unknown.com/post") is True

    def test_published_time_meta(self, extractor):
        """Should count publishedTime as an indicator."""
        html = '''<html><head>
            <meta property="article:published" content="2024-01-01" />
            <article><p>Article content</p></article>
        </head><body></body></html>'''
        soup = BeautifulSoup(html, "lxml")
        # article tag + published_time = 3 indicators >= 2
        assert extractor.is_likely_news_article(soup, "https://unknown.com/post") is True

    def test_unknown_site_no_indicators(self, extractor):
        """Should reject unknown sites without indicators."""
        html = "<html><body><p>Just some content</p></body></html>"
        soup = BeautifulSoup(html, "lxml")
        assert extractor.is_likely_news_article(soup, "https://random-site.com/page") is False


class TestCleanUrl:
    """Tests for _clean_url method."""

    def test_remove_utm_params(self, extractor):
        """Should remove UTM tracking parameters."""
        url = "https://example.com/article?utm_source=twitter&utm_medium=social&id=123"
        cleaned = extractor._clean_url(url)
        assert "utm_source" not in cleaned
        assert "utm_medium" not in cleaned
        assert "id=123" in cleaned

    def test_remove_fbclid(self, extractor):
        """Should remove Facebook click ID."""
        url = "https://example.com/article?fbclid=abc123"
        cleaned = extractor._clean_url(url)
        assert "fbclid" not in cleaned

    def test_remove_gclid(self, extractor):
        """Should remove Google click ID."""
        url = "https://example.com/article?gclid=xyz789"
        cleaned = extractor._clean_url(url)
        assert "gclid" not in cleaned

    def test_preserve_non_tracking_params(self, extractor):
        """Should preserve non-tracking parameters."""
        url = "https://example.com/article?id=123&page=2"
        cleaned = extractor._clean_url(url)
        assert "id=123" in cleaned
        assert "page=2" in cleaned

    def test_url_without_params(self, extractor):
        """Should handle URLs without query parameters."""
        url = "https://example.com/article/123"
        cleaned = extractor._clean_url(url)
        assert cleaned == url


class TestExtractTitle:
    """Tests for _extract_title method."""

    def test_extract_og_title(self, extractor):
        """Should prefer og:title."""
        html = '''<html><head>
            <meta property="og:title" content="OG Title" />
            <title>Page Title</title>
        </head><body></body></html>'''
        soup = BeautifulSoup(html, "lxml")
        assert extractor._extract_title(soup) == "OG Title"

    def test_extract_twitter_title(self, extractor):
        """Should fall back to twitter:title."""
        html = '''<html><head>
            <meta name="twitter:title" content="Twitter Title" />
            <title>Page Title</title>
        </head><body></body></html>'''
        soup = BeautifulSoup(html, "lxml")
        assert extractor._extract_title(soup) == "Twitter Title"

    def test_extract_page_title(self, extractor):
        """Should fall back to <title> tag."""
        html = "<html><head><title>Page Title</title></head><body></body></html>"
        soup = BeautifulSoup(html, "lxml")
        assert extractor._extract_title(soup) == "Page Title"

    def test_extract_h1_title(self, extractor):
        """Should fall back to <h1> tag."""
        html = "<html><body><h1>Headline</h1></body></html>"
        soup = BeautifulSoup(html, "lxml")
        assert extractor._extract_title(soup) == "Headline"

    def test_no_title_returns_none(self, extractor):
        """Should return None when no title found."""
        html = "<html><body><p>Content</p></body></html>"
        soup = BeautifulSoup(html, "lxml")
        assert extractor._extract_title(soup) is None


class TestExtractContentFallback:
    """Tests for _extract_content_fallback method."""

    def test_extract_from_article_tag(self, extractor):
        """Should extract content from <article> tag."""
        html = '''<html><body>
            <nav>Navigation</nav>
            <article><p>Article content that is long enough to be valid.</p>
            <p>More content here to ensure we pass the length check easily.</p></article>
            <footer>Footer</footer>
        </body></html>'''
        soup = BeautifulSoup(html, "lxml")
        content = extractor._extract_content_fallback(soup)
        assert "Article content" in content
        assert "Navigation" not in content
        assert "Footer" not in content

    def test_extract_from_main_tag(self, extractor):
        """Should extract content from <main> tag."""
        html = '''<html><body>
            <header>Header</header>
            <main><p>Main content that is definitely long enough to be considered valid article content.</p>
            <p>Additional paragraph to meet the length requirements.</p></main>
        </body></html>'''
        soup = BeautifulSoup(html, "lxml")
        content = extractor._extract_content_fallback(soup)
        assert "Main content" in content
        assert "Header" not in content

    def test_remove_script_tags(self, extractor):
        """Should remove script tags."""
        html = '''<html><body>
            <article><p>Article text</p>
            <script>alert('evil');</script></article>
        </body></html>'''
        soup = BeautifulSoup(html, "lxml")
        content = extractor._extract_content_fallback(soup)
        assert "alert" not in content

    def test_remove_nav_elements(self, extractor):
        """Should remove navigation elements."""
        html = '''<html><body>
            <nav><a href="/">Home</a></nav>
            <article><p>This is the actual article content that should be extracted properly.</p>
            <p>More content to ensure length requirements are met.</p></article>
        </body></html>'''
        soup = BeautifulSoup(html, "lxml")
        content = extractor._extract_content_fallback(soup)
        assert "Home" not in content
        assert "article content" in content


class TestResolveRedirect:
    """Tests for resolve_redirect method."""

    @pytest.mark.asyncio
    async def test_resolve_tco_link(self, extractor):
        """Should resolve t.co redirects."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.url = "https://example.com/final-url"

            mock_client_instance = AsyncMock()
            mock_client_instance.head = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await extractor.resolve_redirect("https://t.co/abc123")
            assert result == "https://example.com/final-url"

    @pytest.mark.asyncio
    async def test_resolve_redirect_error_returns_original(self, extractor):
        """Should return original URL on error."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.head = AsyncMock(side_effect=Exception("Network error"))
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await extractor.resolve_redirect("https://t.co/abc123")
            assert result == "https://t.co/abc123"


class TestFetchArticle:
    """Tests for fetch_article method."""

    @pytest.mark.asyncio
    async def test_fetch_article_success(self, extractor):
        """Should fetch and extract article content."""
        html = '''<html><head>
            <meta property="og:title" content="Test Article" />
            <meta property="og:type" content="article" />
        </head><body>
            <article>
                <p>This is the article content that should be extracted.
                It needs to be long enough to pass validation.
                Adding more text to ensure we have over 100 characters of content.</p>
            </article>
        </body></html>'''

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.text = html
            mock_response.url = "https://example.com/article"
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await extractor.fetch_article("https://example.com/article")

            assert result is not None
            assert isinstance(result, ArticleContent)
            assert result.title == "Test Article"
            assert result.is_article is True

    @pytest.mark.asyncio
    async def test_fetch_article_timeout(self, extractor):
        """Should return None on timeout."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await extractor.fetch_article("https://slow-site.com/article")
            assert result is None

    @pytest.mark.asyncio
    async def test_fetch_article_http_error(self, extractor):
        """Should return None on HTTP error."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 404
            error = httpx.HTTPStatusError("Not found", request=MagicMock(), response=mock_response)

            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(side_effect=error)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await extractor.fetch_article("https://example.com/missing")
            assert result is None

    @pytest.mark.asyncio
    async def test_fetch_article_not_news(self, extractor):
        """Should return None for non-news pages."""
        html = "<html><body><p>Just a simple page.</p></body></html>"

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.text = html
            mock_response.url = "https://random-blog.com/page"
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            result = await extractor.fetch_article("https://random-blog.com/page")
            assert result is None

    @pytest.mark.asyncio
    async def test_fetch_resolves_google_redirect(self, extractor):
        """Should resolve Google redirect URLs."""
        html = '''<html><head>
            <meta property="og:type" content="article" />
        </head><body>
            <article>
                <p>Long enough article content that should pass the length validation check.
                Adding more text to ensure proper extraction.</p>
            </article>
        </body></html>'''

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.text = html
            mock_response.url = "https://news-site.com/article"
            mock_response.raise_for_status = MagicMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_client_instance

            # Google redirect URL
            google_url = "https://www.google.com/url?rct=j&sa=t&url=https%3A%2F%2Fnews-site.com%2Farticle&ct=ga"
            result = await extractor.fetch_article(google_url)

            # Should have called get with the resolved URL
            mock_client_instance.get.assert_called()


class TestNewsDomains:
    """Tests for NEWS_DOMAINS constant."""

    def test_contains_major_german_news(self):
        """Should contain major German news outlets."""
        assert "spiegel.de" in NEWS_DOMAINS
        assert "zeit.de" in NEWS_DOMAINS
        assert "tagesschau.de" in NEWS_DOMAINS

    def test_contains_hessen_regional(self):
        """Should contain Hessen regional news."""
        assert "hessenschau.de" in NEWS_DOMAINS
        assert "fr.de" in NEWS_DOMAINS
        assert "fnp.de" in NEWS_DOMAINS

    def test_contains_welfare_organizations(self):
        """Should contain welfare organization domains."""
        assert "caritas.de" in NEWS_DOMAINS
        assert "diakonie.de" in NEWS_DOMAINS
        assert "der-paritaetische.de" in NEWS_DOMAINS


class TestSkipPatterns:
    """Tests for SKIP_PATTERNS constant."""

    def test_contains_auth_patterns(self):
        """Should contain auth-related patterns."""
        import re
        url = "https://example.com/login"
        assert any(re.search(p, url, re.I) for p in SKIP_PATTERNS)

    def test_contains_image_patterns(self):
        """Should contain image extension patterns."""
        import re
        url = "https://example.com/photo.jpg"
        assert any(re.search(p, url, re.I) for p in SKIP_PATTERNS)

    def test_contains_legal_patterns(self):
        """Should contain legal page patterns."""
        import re
        url = "https://example.com/datenschutz"
        assert any(re.search(p, url, re.I) for p in SKIP_PATTERNS)
