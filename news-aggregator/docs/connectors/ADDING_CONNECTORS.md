# Adding New Connectors

## Quick Start

1. Create a new file in `backend/connectors/`
2. Define config model and connector class
3. Register with `@ConnectorRegistry.register`
4. Import in `__init__.py`

## Step-by-Step Guide

### 1. Create Connector File

Create `backend/connectors/my_source.py`:

```python
"""My Source connector."""

import logging
from datetime import datetime
from pydantic import BaseModel, Field, HttpUrl

from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry

logger = logging.getLogger(__name__)


class MySourceConfig(BaseModel):
    """Configuration for My Source connector."""

    url: HttpUrl = Field(..., description="Source URL")
    api_key: str | None = Field(default=None, description="API key (optional)")
    max_items: int = Field(default=50, ge=1, le=100, description="Max items to fetch")


@ConnectorRegistry.register
class MySourceConnector(BaseConnector):
    """My Source connector.

    Fetches items from My Source.
    """

    connector_type = "my_source"
    display_name = "My Source"
    description = "Fetch items from My Source"
    config_schema = MySourceConfig

    async def fetch(self, config: MySourceConfig) -> list[RawItem]:
        """Fetch items from source."""
        items = []

        # Your fetch logic here
        # ...

        for item_data in raw_data:
            items.append(
                RawItem(
                    external_id=item_data["id"],
                    title=item_data["title"],
                    content=item_data["content"],
                    url=item_data["url"],
                    author=item_data.get("author"),
                    published_at=parse_date(item_data.get("date")),
                    metadata={
                        "source_type": "my_source",
                        # Additional metadata
                    },
                )
            )

        return items

    async def validate(self, config: MySourceConfig) -> tuple[bool, str]:
        """Validate configuration."""
        try:
            # Test connection
            # ...
            return True, "Connection successful"
        except Exception as e:
            return False, f"Error: {str(e)}"
```

### 2. Register in __init__.py

Add import to `backend/connectors/__init__.py`:

```python
from .my_source import MySourceConnector
```

### 3. Add Concurrency Limit

Add to `SOURCE_TYPE_LIMITS` in `backend/services/scheduler.py`:

```python
SOURCE_TYPE_LIMITS = {
    # ... existing entries ...
    "my_source": 5,  # Adjust based on source requirements
}
```

## Config Model Guidelines

### Required Fields

Use `...` for required fields:

```python
url: HttpUrl = Field(..., description="Required URL")
```

### Optional Fields

Use `default` for optional fields:

```python
api_key: str | None = Field(default=None, description="Optional API key")
```

### Validation Constraints

Use Pydantic validators:

```python
max_items: int = Field(default=50, ge=1, le=100)  # 1-100 range
timeout: float = Field(default=30.0, gt=0)        # Positive
```

### Sensitive Fields

Mark sensitive fields:

```python
api_key: str = Field(..., json_schema_extra={"format": "password"})
```

## Fetch Method Guidelines

### Return Format

Always return `list[RawItem]`:

```python
async def fetch(self, config: MyConfig) -> list[RawItem]:
    return [
        RawItem(
            external_id="unique-id-from-source",
            title="Item Title",
            content="Full content text",
            url="https://source.com/item",
            author="Author Name",  # Optional
            published_at=datetime.now(),  # Optional
            metadata={},  # Optional extra data
        )
    ]
```

### External ID

Must be unique and stable:
- Good: `item.id`, `post.guid`, `tweet.id`
- Bad: Random values, timestamps alone

### Content Handling

- Strip HTML if needed
- Truncate extremely long content
- Handle encoding issues

```python
from html import unescape
content = unescape(raw_html)
content = content[:50000]  # Limit size
```

### Date Parsing

Handle various date formats:

```python
from dateutil.parser import parse as parse_date

def safe_parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return parse_date(date_str)
    except (ValueError, TypeError):
        return None
```

### Error Handling

Log errors but don't crash:

```python
async def fetch(self, config: MyConfig) -> list[RawItem]:
    items = []
    for item_data in raw_items:
        try:
            items.append(self._parse_item(item_data))
        except Exception as e:
            logger.warning(f"Failed to parse item: {e}")
            continue
    return items
```

## HTTP Client Usage

Use `httpx` for async HTTP:

```python
import httpx

async def fetch(self, config: MyConfig) -> list[RawItem]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            str(config.url),
            headers={"User-Agent": "NewsAggregator/1.0"},
        )
        response.raise_for_status()
        data = response.json()
```

### SSL Issues

For sites with certificate problems:

```python
import ssl

def create_legacy_ssl_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    return ctx

# Use in client
async with httpx.AsyncClient(verify=create_legacy_ssl_context()) as client:
    ...
```

## Browser-Based Connectors

For JavaScript-rendered content, use Playwright:

```python
from playwright.async_api import async_playwright

async def fetch(self, config: MyConfig) -> list[RawItem]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            await page.goto(str(config.url))
            await page.wait_for_selector(".item")

            items = await page.query_selector_all(".item")
            # Parse items...

        finally:
            await browser.close()
```

**Note**: Browser-based connectors are slow (~30-60s). Set low concurrency limit.

## Validate Method Guidelines

Test that configuration works:

```python
async def validate(self, config: MyConfig) -> tuple[bool, str]:
    try:
        # Quick connectivity test
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(str(config.url))
            response.raise_for_status()

        # Parse response to verify format
        data = response.json()
        item_count = len(data.get("items", []))

        return True, f"Connected successfully ({item_count} items available)"

    except httpx.TimeoutException:
        return False, "Connection timeout"
    except httpx.HTTPStatusError as e:
        return False, f"HTTP error: {e.response.status_code}"
    except Exception as e:
        return False, f"Error: {str(e)}"
```

## Testing

### Manual Testing

```python
import asyncio
from connectors.my_source import MySourceConnector, MySourceConfig

async def test():
    connector = MySourceConnector()
    config = MySourceConfig(url="https://example.com")

    # Test validation
    success, msg = await connector.validate(config)
    print(f"Validate: {success} - {msg}")

    # Test fetch
    items = await connector.fetch(config)
    print(f"Fetched {len(items)} items")
    for item in items[:3]:
        print(f"  - {item.title}")

asyncio.run(test())
```

### Unit Tests

Create `backend/tests/test_connectors/test_my_source.py`:

```python
import pytest
from connectors.my_source import MySourceConnector, MySourceConfig

@pytest.mark.asyncio
async def test_fetch():
    connector = MySourceConnector()
    config = MySourceConfig(url="https://example.com")

    items = await connector.fetch(config)

    assert isinstance(items, list)
    for item in items:
        assert item.external_id
        assert item.title
        assert item.url

@pytest.mark.asyncio
async def test_validate_success():
    connector = MySourceConnector()
    config = MySourceConfig(url="https://example.com")

    success, message = await connector.validate(config)

    assert success is True
```

## Metadata Best Practices

Include useful metadata for debugging/display:

```python
metadata={
    "source_type": "my_source",
    "original_url": str(original_url),
    "tags": ["tag1", "tag2"],
    "engagement": {
        "likes": 100,
        "shares": 50,
    },
    # Anything useful for filtering/analysis
}
```

## Checklist

- [ ] Config model with Field descriptions
- [ ] `@ConnectorRegistry.register` decorator
- [ ] Implement `fetch()` returning `list[RawItem]`
- [ ] Implement `validate()` returning `tuple[bool, str]`
- [ ] Import in `__init__.py`
- [ ] Add to `SOURCE_TYPE_LIMITS`
- [ ] Test fetch and validate
- [ ] Handle errors gracefully
- [ ] Log at appropriate levels
