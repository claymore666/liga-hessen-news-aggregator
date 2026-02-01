# Article Extractor

Full article content extraction service used by RSS and other connectors to fetch article body text from URLs.

## Extraction Pipeline

The extractor tries multiple strategies in order:

### 1. httpx + trafilatura (Primary)
- Fetches HTML via `httpx` with Googlebot user-agent headers (helps bypass some paywalls)
- Extracts article content using `trafilatura`
- Fast and lightweight — handles most news sites

### 2. Wayback Machine Fallback
- If the primary extraction fails, checks the Internet Archive's Wayback Machine
- Useful for paywalled or geo-restricted content that was previously crawled

### 3. Playwright SPA Fallback
- If trafilatura fails to detect an article AND the page contains SPA markers, falls back to headless Chromium
- **SPA detection markers**: `<div id="app">`, `<div id="root">`, `noscript` tags mentioning JavaScript
- Uses the shared [browser pool](BROWSER_POOL.md) for resource efficiency
- Waits for JavaScript rendering, then re-runs trafilatura on the rendered HTML
- Used as an async context manager to properly manage browser resources

## Googlebot Headers

The extractor uses Googlebot-compatible headers to improve content accessibility:
- `User-Agent: Googlebot/2.1` (or similar)
- Some news sites serve full article content to search engine crawlers

## Usage

```python
from services.article_extractor import ArticleExtractor

extractor = ArticleExtractor()
result = await extractor.extract(url)
# result contains: title, content, author, published_date
```

## Troubleshooting

- **SPA fallback not triggering**: Check that the page actually contains SPA markers. Static sites that simply fail trafilatura extraction won't trigger the fallback.
- **Playwright errors**: See [Browser Pool troubleshooting](../operations/TROUBLESHOOTING.md#browser-pool--playwright-issues)
- **Paywall content**: Googlebot headers work for some sites but not all. The Wayback Machine fallback may help for previously-crawled content.
