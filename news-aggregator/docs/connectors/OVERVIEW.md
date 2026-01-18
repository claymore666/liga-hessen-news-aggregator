# Connectors Overview

## Architecture

Connectors fetch items from external sources (RSS feeds, social media, etc.) and normalize them into a common format.

```
┌─────────────────────────────────────────────────────────────┐
│                    ConnectorRegistry                         │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│  │   RSS   │ │ Mastodon│ │ X Scrape│ │ Bluesky │  ...      │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘           │
└───────┼──────────┼──────────┼──────────┼───────────────────┘
        │          │          │          │
        └──────────┴──────────┴──────────┘
                       │
                       ▼
               ┌───────────────┐
               │   RawItem     │
               │  (normalized) │
               └───────────────┘
```

**Files**:
- `backend/connectors/base.py` - Base classes
- `backend/connectors/registry.py` - Connector registry
- `backend/connectors/*.py` - Individual connectors

## Available Connectors

| Type | Name | Description | Auth |
|------|------|-------------|------|
| `rss` | RSS Feed | Standard RSS/Atom feeds | None |
| `mastodon` | Mastodon | Fediverse timelines | Access token |
| `bluesky` | Bluesky | Bluesky profiles/feeds | None (public) |
| `x_scraper` | X/Twitter Scraper | Browser-based scraping | Cookies |
| `twitter` | Twitter (Nitter) | RSS via Nitter instances | None |
| `instagram` | Instagram | Via proxy services | API key |
| `instagram_scraper` | Instagram Scraper | Browser-based scraping | Cookies |
| `linkedin` | LinkedIn | Scraping | Cookies |
| `telegram` | Telegram | Public channels | Bot token |
| `html` | HTML Scraper | Generic web scraping | None |
| `pdf` | PDF | PDF document parsing | None |
| `google_alerts` | Google Alerts | RSS from Google Alerts | None |

## RawItem Format

All connectors return items in this normalized format:

```python
class RawItem(BaseModel):
    external_id: str      # Unique ID from source
    title: str            # Item title
    content: str          # Full text content
    url: str              # Source URL
    author: str | None    # Author name
    published_at: datetime | None  # Publication date
    metadata: dict        # Connector-specific data
```

## Configuration

Each connector has a Pydantic config model defining its parameters:

```python
class RSSConfig(BaseModel):
    url: HttpUrl
    custom_title: str | None = None
    follow_links: bool = True
    verify_ssl: bool = True
```

Configs are stored as JSON in the `channels.config` column.

## Connector Categories

### HTTP-Based (Fast)
- RSS, Twitter (Nitter), Google Alerts
- Simple HTTP requests
- High concurrency limits (5-10)

### API-Based (Moderate)
- Mastodon, Bluesky, Telegram
- Authenticated API calls
- Moderate concurrency (5)

### Browser-Based (Slow)
- X Scraper, Instagram Scraper, LinkedIn
- Playwright automation
- Low concurrency (2)
- ~30-60 seconds per fetch

### Proxy-Based
- Instagram (via bibliogram, etc.)
- Dependent on third-party services

## Concurrency Limits

Each connector type has a semaphore limit in the scheduler:

```python
SOURCE_TYPE_LIMITS = {
    "x_scraper": 2,          # Browser-based, slow
    "instagram_scraper": 2,  # Browser-based
    "linkedin": 2,           # Scraping
    "rss": 10,               # Lightweight HTTP
    "mastodon": 5,           # API-based
    "bluesky": 5,            # API-based
    "twitter": 5,            # RSS-based (Nitter)
    "telegram": 5,           # API-based
    "html": 5,               # HTTP scraping
    "pdf": 3,                # Heavy processing
    "google_alerts": 5,      # RSS-based
    "instagram": 5,          # Proxy services
}
```

## Link Following

Some connectors support following links to fetch full article content:

```python
config = {"url": "...", "follow_links": True}
```

The `ArticleExtractor` service uses trafilatura to extract clean text from URLs.

**Supported by**: RSS, HTML

## Connector Details

### RSS (`rss`)

Standard RSS/Atom feed parser.

**Config**:
```json
{
  "url": "https://example.com/feed.xml",
  "custom_title": "My Feed",
  "follow_links": true,
  "verify_ssl": true
}
```

**Notes**:
- `follow_links` fetches full article from linked URLs
- `verify_ssl=false` for sites with certificate issues

**Special Handling**:
- **Eurostat feeds**: Automatically enriched with SDMX metadata. When the feed URL contains "eurostat", the connector extracts the dataset ID from the title and fetches additional metadata from Eurostat's SDMX API:
  - Full dataset name (in German)
  - Time period coverage (oldest/latest)
  - Number of data points
  - Link to methodology documentation

  Example transformation:
  ```
  Before: "Market production - quarterly data" (33 chars)
  After:  "Dataset: Marktproduktion - vierteljährliche Daten
          Beschreibung: Market production - quarterly data
          Zeitraum: von 2010-Q1 bis 2025-Q3
          Datenpunkte: 2986
          Methodische Hinweise: https://..." (233 chars)
  ```

- **Google Alerts feeds**: Extracts actual source domain from article URLs (see `source_domain` in metadata)

### Mastodon (`mastodon`)

Fediverse timeline fetching via API.

**Config**:
```json
{
  "instance_url": "https://mastodon.social",
  "access_token": "...",
  "timeline": "home"
}
```

**Timelines**: `home`, `public`, `hashtag`

### Bluesky (`bluesky`)

Bluesky profile feeds (public, no auth needed).

**Config**:
```json
{
  "handle": "user.bsky.social",
  "include_replies": false,
  "include_reposts": false
}
```

### X/Twitter Scraper (`x_scraper`)

Browser-based scraping using Playwright.

**Config**:
```json
{
  "username": "example",
  "use_proxy": false,
  "include_replies": false
}
```

**Notes**:
- Requires exported cookies from authenticated session
- ~36 seconds per fetch due to browser overhead
- Low concurrency limit (2)

### Twitter via Nitter (`twitter`)

RSS feeds from Nitter instances.

**Config**:
```json
{
  "username": "example",
  "nitter_instance": "https://nitter.net"
}
```

**Notes**:
- No authentication needed
- Depends on Nitter instance availability

### Instagram (`instagram`)

Via third-party proxy services (bibliogram, etc.).

**Config**:
```json
{
  "username": "example",
  "proxy_url": "https://bibliogram.art"
}
```

### Instagram Scraper (`instagram_scraper`)

Browser-based scraping.

**Config**:
```json
{
  "username": "example",
  "max_posts": 10
}
```

**Notes**:
- Requires exported cookies
- Browser-based, slow

### LinkedIn (`linkedin`)

Web scraping of public profiles/pages.

**Config**:
```json
{
  "profile_url": "https://linkedin.com/company/...",
  "max_posts": 10
}
```

**Notes**:
- Requires cookies from authenticated session
- Rate limited

### Telegram (`telegram`)

Public channel messages via Bot API.

**Config**:
```json
{
  "channel_username": "example",
  "bot_token": "..."
}
```

### HTML Scraper (`html`)

Generic web page scraping with CSS selectors.

**Config**:
```json
{
  "url": "https://example.com/news",
  "item_selector": ".article",
  "title_selector": "h2",
  "content_selector": ".body",
  "link_selector": "a"
}
```

### PDF (`pdf`)

PDF document parsing.

**Config**:
```json
{
  "url": "https://example.com/report.pdf"
}
```

### Google Alerts (`google_alerts`)

RSS feeds from Google Alerts.

**Config**:
```json
{
  "feed_url": "https://www.google.com/alerts/feeds/..."
}
```

## Error Handling

Connector errors are stored in `channels.last_error`:

```python
try:
    items = await connector.fetch(config)
except Exception as e:
    channel.last_error = str(e)
```

Common errors:
- `TimeoutError` - Connection/browser timeout
- `401/403` - Authentication failed
- `rate_limit` - Too many requests
- `parse_error` - Invalid response format

## Validation

Connectors can validate their configuration:

```python
success, message = await connector.validate(config)
# Returns: (True, "Valid feed: Example (10 entries)")
# Or: (False, "HTTP error: 404")
```

Used by the UI when adding/editing channels.
