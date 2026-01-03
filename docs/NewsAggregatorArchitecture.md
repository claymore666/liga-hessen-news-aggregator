# News Aggregator - System Architecture

A configurable, self-hosted news aggregator with pluggable connectors, LLM-powered analysis, and a Vue-based dashboard.

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                 WEB GUI (Vue 3)                                 │
│  ┌────────────────────────────────┐  ┌────────────────────────────────────┐    │
│  │         DASHBOARD              │  │           ADMIN                    │    │
│  │  • Daily feed view             │  │  • Manage connectors               │    │
│  │  • Filter & search             │  │  • Configure sources               │    │
│  │  • Mark read/starred           │  │  • Define rules                    │    │
│  │  • Keyboard navigation         │  │  • Email export settings           │    │
│  └────────────────────────────────┘  └────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼ REST API
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              BACKEND (FastAPI)                                  │
│                                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│  │  Scheduler  │  │  Processor  │  │  LLM Service│  │  Export     │            │
│  │ (APScheduler)│  │  Pipeline   │  │  (Ollama)   │  │  Service    │            │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘            │
│         │                │                │                │                    │
│         ▼                ▼                ▼                ▼                    │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                         CORE ENGINE                                      │   │
│  │  • Connector Registry    • Normalizer       • Deduplicator              │   │
│  │  • Source Manager        • Rule Engine      • Scorer                    │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                        │                                        │
│                                        ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                      DATABASE (SQLite)                                   │   │
│  │  • items    • sources    • connectors    • rules    • settings          │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        ▲
                                        │
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          CONNECTOR LAYER                                        │
│                                                                                 │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐      │
│  │   RSS   │ │  HTML   │ │ Twitter │ │ Bluesky │ │LinkedIn │ │   PDF   │      │
│  │Connector│ │ Scraper │ │Connector│ │Connector│ │Connector│ │ Parser  │      │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘      │
│                                                                                 │
│  Each connector:                                                                │
│  • Implements BaseConnector interface                                           │
│  • Defines its own config schema                                                │
│  • Fetches & normalizes data to common Item format                             │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
                              ┌─────────────────┐
                              │ External Sources│
                              │ • Websites      │
                              │ • APIs          │
                              │ • Files         │
                              └─────────────────┘
```

---

## 2. Tech Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Frontend** | Vue 3 + Vite | SPA Dashboard |
| | TailwindCSS | Styling |
| | Pinia | State Management |
| | Vue Router | Navigation |
| **Backend** | Python 3.12 | Runtime |
| | FastAPI | REST API |
| | SQLAlchemy 2.0 | ORM |
| | APScheduler | Job Scheduling |
| | Pydantic | Validation |
| **Database** | SQLite | Storage |
| **LLM** | Ollama (primary) | Text Analysis |
| | OpenRouter (fallback) | API Fallback |
| **Infrastructure** | Docker | Containerization |
| | docker-compose | Orchestration |

---

## 3. Connector System

### 3.1 Base Connector Interface

```python
# backend/connectors/base.py

from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Type
from datetime import datetime

class RawItem(BaseModel):
    """Normalized item format returned by all connectors"""
    external_id: str              # Unique ID from source
    title: str
    content: str                  # Full text content
    url: str
    author: str | None = None
    published_at: datetime | None = None
    metadata: dict = {}           # Connector-specific data

class BaseConnector(ABC):
    """Abstract base class for all connectors"""

    # Connector metadata (override in subclass)
    connector_type: str           # e.g., "rss", "twitter"
    display_name: str             # e.g., "RSS Feed"
    description: str              # Shown in UI
    config_schema: Type[BaseModel]  # Pydantic model for config

    @abstractmethod
    async def fetch(self, config: BaseModel) -> list[RawItem]:
        """
        Fetch items from the configured source.
        Returns list of normalized RawItem objects.
        """
        pass

    @abstractmethod
    async def validate(self, config: BaseModel) -> tuple[bool, str]:
        """
        Validate the configuration (e.g., test connection).
        Returns (success, message).
        """
        pass

    def get_config_schema_json(self) -> dict:
        """Return JSON Schema for frontend form generation"""
        return self.config_schema.model_json_schema()
```

### 3.2 Connector Registry

```python
# backend/connectors/registry.py

from typing import Dict, Type
from .base import BaseConnector

class ConnectorRegistry:
    """Central registry for all available connectors"""

    _connectors: Dict[str, Type[BaseConnector]] = {}

    @classmethod
    def register(cls, connector_class: Type[BaseConnector]):
        """Decorator to register a connector"""
        cls._connectors[connector_class.connector_type] = connector_class
        return connector_class

    @classmethod
    def get(cls, connector_type: str) -> Type[BaseConnector]:
        """Get connector class by type"""
        if connector_type not in cls._connectors:
            raise ValueError(f"Unknown connector: {connector_type}")
        return cls._connectors[connector_type]

    @classmethod
    def list_all(cls) -> list[dict]:
        """List all registered connectors with metadata"""
        return [
            {
                "type": c.connector_type,
                "name": c.display_name,
                "description": c.description,
                "config_schema": c.config_schema.model_json_schema()
            }
            for c in cls._connectors.values()
        ]
```

### 3.3 Example Connectors

#### RSS Connector
```python
# backend/connectors/rss.py

import feedparser
import httpx
from pydantic import BaseModel, HttpUrl
from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry
from datetime import datetime
from time import mktime

class RSSConfig(BaseModel):
    url: HttpUrl

@ConnectorRegistry.register
class RSSConnector(BaseConnector):
    connector_type = "rss"
    display_name = "RSS Feed"
    description = "Subscribe to any RSS or Atom feed"
    config_schema = RSSConfig

    async def fetch(self, config: RSSConfig) -> list[RawItem]:
        async with httpx.AsyncClient() as client:
            response = await client.get(str(config.url))
            feed = feedparser.parse(response.text)

        items = []
        for entry in feed.entries:
            published = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                published = datetime.fromtimestamp(mktime(entry.published_parsed))

            items.append(RawItem(
                external_id=entry.get('id', entry.link),
                title=entry.get('title', 'Untitled'),
                content=entry.get('summary', entry.get('description', '')),
                url=entry.link,
                author=entry.get('author'),
                published_at=published,
                metadata={
                    "feed_title": feed.feed.get('title'),
                    "tags": [t.term for t in entry.get('tags', [])]
                }
            ))
        return items

    async def validate(self, config: RSSConfig) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(str(config.url), timeout=10)
                feed = feedparser.parse(response.text)
                if feed.bozo and not feed.entries:
                    return False, f"Invalid feed: {feed.bozo_exception}"
                return True, f"Valid feed: {feed.feed.get('title', 'Unknown')}"
        except Exception as e:
            return False, str(e)
```

#### HTML Scraper Connector
```python
# backend/connectors/html.py

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, HttpUrl
from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry
from datetime import datetime
import hashlib

class HTMLConfig(BaseModel):
    url: HttpUrl
    item_selector: str            # CSS selector for items
    title_selector: str           # Relative to item
    content_selector: str | None = None
    link_selector: str | None = None  # If different from item
    date_selector: str | None = None
    date_format: str | None = None    # strptime format

@ConnectorRegistry.register
class HTMLConnector(BaseConnector):
    connector_type = "html"
    display_name = "HTML Scraper"
    description = "Scrape news from any website using CSS selectors"
    config_schema = HTMLConfig

    async def fetch(self, config: HTMLConfig) -> list[RawItem]:
        async with httpx.AsyncClient() as client:
            response = await client.get(str(config.url))
            soup = BeautifulSoup(response.text, 'lxml')

        items = []
        for element in soup.select(config.item_selector):
            title_el = element.select_one(config.title_selector)
            if not title_el:
                continue

            title = title_el.get_text(strip=True)

            # Generate ID from title hash if no explicit ID
            external_id = hashlib.md5(title.encode()).hexdigest()[:16]

            # Extract link
            link = str(config.url)
            if config.link_selector:
                link_el = element.select_one(config.link_selector)
                if link_el and link_el.get('href'):
                    link = link_el['href']
            elif title_el.get('href'):
                link = title_el['href']

            # Make relative URLs absolute
            if link.startswith('/'):
                link = f"{config.url.scheme}://{config.url.host}{link}"

            # Extract content
            content = ""
            if config.content_selector:
                content_el = element.select_one(config.content_selector)
                if content_el:
                    content = content_el.get_text(strip=True)

            items.append(RawItem(
                external_id=external_id,
                title=title,
                content=content,
                url=link,
                published_at=datetime.now(),  # Could parse from date_selector
                metadata={"source_url": str(config.url)}
            ))

        return items

    async def validate(self, config: HTMLConfig) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(str(config.url), timeout=10)
                soup = BeautifulSoup(response.text, 'lxml')
                items = soup.select(config.item_selector)
                if not items:
                    return False, f"No items found with selector: {config.item_selector}"
                return True, f"Found {len(items)} items"
        except Exception as e:
            return False, str(e)
```

#### Bluesky Connector
```python
# backend/connectors/bluesky.py

import httpx
import feedparser
from pydantic import BaseModel
from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry
from datetime import datetime
from time import mktime

class BlueskyConfig(BaseModel):
    handle: str  # e.g., "bundestag.bund.de" or "user.bsky.social"

@ConnectorRegistry.register
class BlueskyConnector(BaseConnector):
    connector_type = "bluesky"
    display_name = "Bluesky"
    description = "Follow Bluesky accounts via RSS"
    config_schema = BlueskyConfig

    async def fetch(self, config: BlueskyConfig) -> list[RawItem]:
        # Bluesky offers native RSS feeds
        rss_url = f"https://bsky.app/profile/{config.handle}/rss"

        async with httpx.AsyncClient() as client:
            response = await client.get(rss_url)
            feed = feedparser.parse(response.text)

        items = []
        for entry in feed.entries:
            published = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                published = datetime.fromtimestamp(mktime(entry.published_parsed))

            items.append(RawItem(
                external_id=entry.get('id', entry.link),
                title=entry.get('title', '')[:100],  # Bluesky has no titles
                content=entry.get('summary', ''),
                url=entry.link,
                author=config.handle,
                published_at=published,
                metadata={
                    "platform": "bluesky",
                    "handle": config.handle
                }
            ))
        return items

    async def validate(self, config: BlueskyConfig) -> tuple[bool, str]:
        try:
            rss_url = f"https://bsky.app/profile/{config.handle}/rss"
            async with httpx.AsyncClient() as client:
                response = await client.get(rss_url, timeout=10)
                if response.status_code == 404:
                    return False, f"Account not found: {config.handle}"
                feed = feedparser.parse(response.text)
                return True, f"Found {len(feed.entries)} posts"
        except Exception as e:
            return False, str(e)
```

#### Twitter/X Connector (via Nitter)
```python
# backend/connectors/twitter.py

import httpx
import feedparser
from pydantic import BaseModel
from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry
from datetime import datetime
from time import mktime

class TwitterConfig(BaseModel):
    handle: str                           # Without @
    nitter_instance: str = "nitter.net"   # Nitter instance to use

@ConnectorRegistry.register
class TwitterConnector(BaseConnector):
    connector_type = "twitter"
    display_name = "Twitter/X"
    description = "Follow Twitter accounts via Nitter RSS proxy"
    config_schema = TwitterConfig

    async def fetch(self, config: TwitterConfig) -> list[RawItem]:
        rss_url = f"https://{config.nitter_instance}/{config.handle}/rss"

        async with httpx.AsyncClient() as client:
            response = await client.get(rss_url)
            feed = feedparser.parse(response.text)

        items = []
        for entry in feed.entries:
            published = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                published = datetime.fromtimestamp(mktime(entry.published_parsed))

            items.append(RawItem(
                external_id=entry.get('id', entry.link),
                title=entry.get('title', '')[:100],
                content=entry.get('description', ''),
                url=entry.link.replace(config.nitter_instance, 'twitter.com'),
                author=f"@{config.handle}",
                published_at=published,
                metadata={
                    "platform": "twitter",
                    "handle": config.handle
                }
            ))
        return items

    async def validate(self, config: TwitterConfig) -> tuple[bool, str]:
        try:
            rss_url = f"https://{config.nitter_instance}/{config.handle}/rss"
            async with httpx.AsyncClient() as client:
                response = await client.get(rss_url, timeout=10)
                if response.status_code == 404:
                    return False, f"Account not found or Nitter down"
                feed = feedparser.parse(response.text)
                return True, f"Found {len(feed.entries)} tweets"
        except Exception as e:
            return False, str(e)
```

#### PDF Connector
```python
# backend/connectors/pdf.py

import httpx
import fitz  # PyMuPDF
from pydantic import BaseModel, HttpUrl
from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry
from datetime import datetime
import hashlib
import io

class PDFConfig(BaseModel):
    url: HttpUrl                  # Direct PDF URL or page with PDF links
    is_direct_link: bool = True   # True if URL points directly to PDF
    link_selector: str | None = None  # CSS selector if is_direct_link=False

@ConnectorRegistry.register
class PDFConnector(BaseConnector):
    connector_type = "pdf"
    display_name = "PDF Document"
    description = "Extract text from PDF documents"
    config_schema = PDFConfig

    async def fetch(self, config: PDFConfig) -> list[RawItem]:
        async with httpx.AsyncClient() as client:
            response = await client.get(str(config.url))

        if not config.is_direct_link:
            # TODO: Parse HTML and find PDF links
            raise NotImplementedError("PDF link extraction not yet implemented")

        # Parse PDF
        pdf_bytes = io.BytesIO(response.content)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        full_text = ""
        for page in doc:
            full_text += page.get_text()

        # Use first line or metadata as title
        title = doc.metadata.get('title', '')
        if not title:
            first_line = full_text.split('\n')[0][:100]
            title = first_line if first_line else "PDF Document"

        external_id = hashlib.md5(response.content).hexdigest()[:16]

        return [RawItem(
            external_id=external_id,
            title=title,
            content=full_text,
            url=str(config.url),
            author=doc.metadata.get('author'),
            published_at=datetime.now(),
            metadata={
                "pages": len(doc),
                "pdf_metadata": doc.metadata
            }
        )]

    async def validate(self, config: PDFConfig) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.head(str(config.url), timeout=10)
                content_type = response.headers.get('content-type', '')
                if 'pdf' in content_type.lower():
                    return True, "Valid PDF URL"
                return False, f"Not a PDF: {content_type}"
        except Exception as e:
            return False, str(e)
```

### 3.4 Mastodon Connector

```python
# backend/connectors/mastodon.py

import httpx
import feedparser
from pydantic import BaseModel, field_validator
from .base import BaseConnector, RawItem
from .registry import ConnectorRegistry
from datetime import datetime
import re

class MastodonConfig(BaseModel):
    handle: str                   # @user@instance or user@instance
    use_api: bool = False         # Use API instead of RSS (requires token)
    api_token: str | None = None  # Optional API token for private accounts

    @field_validator('handle')
    @classmethod
    def parse_handle(cls, v: str) -> str:
        # Normalize handle: remove leading @, validate format
        v = v.lstrip('@')
        if '@' not in v:
            raise ValueError("Handle must be in format user@instance")
        return v

    @property
    def username(self) -> str:
        return self.handle.split('@')[0]

    @property
    def instance(self) -> str:
        return self.handle.split('@')[1]

@ConnectorRegistry.register
class MastodonConnector(BaseConnector):
    connector_type = "mastodon"
    display_name = "Mastodon"
    description = "Follow Mastodon/Fediverse accounts"
    config_schema = MastodonConfig

    async def fetch(self, config: MastodonConfig) -> list[RawItem]:
        if config.use_api and config.api_token:
            return await self._fetch_via_api(config)
        return await self._fetch_via_rss(config)

    async def _fetch_via_rss(self, config: MastodonConfig) -> list[RawItem]:
        # Mastodon native RSS: https://instance/@user.rss
        rss_url = f"https://{config.instance}/@{config.username}.rss"

        async with httpx.AsyncClient() as client:
            response = await client.get(rss_url)

        feed = feedparser.parse(response.text)
        items = []

        for entry in feed.entries:
            items.append(RawItem(
                external_id=entry.get('id', entry.link),
                title=self._extract_title(entry),
                content=entry.get('summary', ''),
                url=entry.link,
                author=f"@{config.handle}",
                published_at=datetime(*entry.published_parsed[:6]) if entry.get('published_parsed') else datetime.now(),
                metadata={
                    "instance": config.instance,
                    "handle": config.handle,
                    "source_type": "rss"
                }
            ))

        return items

    async def _fetch_via_api(self, config: MastodonConfig) -> list[RawItem]:
        # First, look up account ID
        api_base = f"https://{config.instance}/api/v1"
        headers = {"Authorization": f"Bearer {config.api_token}"} if config.api_token else {}

        async with httpx.AsyncClient() as client:
            # Lookup account
            lookup_url = f"{api_base}/accounts/lookup?acct={config.username}"
            response = await client.get(lookup_url, headers=headers)
            account = response.json()
            account_id = account['id']

            # Fetch statuses
            statuses_url = f"{api_base}/accounts/{account_id}/statuses?limit=40&exclude_replies=true"
            response = await client.get(statuses_url, headers=headers)
            statuses = response.json()

        items = []
        for status in statuses:
            # Handle boosts (reblogs)
            content_status = status.get('reblog') or status

            items.append(RawItem(
                external_id=status['id'],
                title=self._extract_title_from_content(content_status['content']),
                content=self._strip_html(content_status['content']),
                url=status['url'],
                author=f"@{content_status['account']['acct']}",
                published_at=datetime.fromisoformat(status['created_at'].replace('Z', '+00:00')),
                metadata={
                    "instance": config.instance,
                    "handle": config.handle,
                    "is_reblog": status.get('reblog') is not None,
                    "favorites": status['favourites_count'],
                    "reblogs": status['reblogs_count'],
                    "replies": status['replies_count'],
                    "source_type": "api"
                }
            ))

        return items

    def _extract_title(self, entry) -> str:
        """Extract title from RSS entry"""
        if entry.get('title'):
            return entry.title[:100]
        summary = self._strip_html(entry.get('summary', ''))
        return summary[:100] + '...' if len(summary) > 100 else summary

    def _extract_title_from_content(self, html_content: str) -> str:
        """Extract title from HTML content"""
        text = self._strip_html(html_content)
        return text[:100] + '...' if len(text) > 100 else text

    def _strip_html(self, html: str) -> str:
        """Remove HTML tags"""
        clean = re.sub(r'<[^>]+>', '', html)
        return clean.strip()

    async def validate(self, config: MastodonConfig) -> tuple[bool, str]:
        try:
            # Try RSS endpoint first (works without auth)
            rss_url = f"https://{config.instance}/@{config.username}.rss"
            async with httpx.AsyncClient() as client:
                response = await client.get(rss_url, timeout=10)
                if response.status_code == 200:
                    return True, f"Valid Mastodon account @{config.handle}"
                elif response.status_code == 404:
                    return False, f"Account not found: @{config.handle}"
                else:
                    return False, f"HTTP {response.status_code}"
        except Exception as e:
            return False, str(e)
```

---

## 4. Data Model

### 4.1 Database Schema

```python
# backend/models.py

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Float, ForeignKey, JSON
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime

Base = declarative_base()

class Connector(Base):
    """Registered connector types"""
    __tablename__ = "connectors"

    id = Column(Integer, primary_key=True)
    type = Column(String(50), unique=True, nullable=False)  # "rss", "twitter"
    display_name = Column(String(100), nullable=False)
    description = Column(Text)
    config_schema = Column(JSON)  # JSON Schema for config
    enabled = Column(Boolean, default=True)

    sources = relationship("Source", back_populates="connector")


class Source(Base):
    """Configured source instances"""
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    connector_id = Column(Integer, ForeignKey("connectors.id"), nullable=False)
    name = Column(String(200), nullable=False)          # User-friendly name
    config = Column(JSON, nullable=False)               # Connector-specific config
    enabled = Column(Boolean, default=True)
    schedule = Column(String(50), default="*/30 * * * *")  # Cron expression
    last_fetch = Column(DateTime)
    last_error = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    connector = relationship("Connector", back_populates="sources")
    items = relationship("Item", back_populates="source")


class Item(Base):
    """Fetched and processed items"""
    __tablename__ = "items"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    external_id = Column(String(255), nullable=False)   # ID from source

    # Content
    title = Column(String(500), nullable=False)
    content = Column(Text)
    content_hash = Column(String(64))                   # For deduplication
    url = Column(String(2000))
    author = Column(String(200))

    # Timestamps
    published_at = Column(DateTime)
    fetched_at = Column(DateTime, default=datetime.utcnow)

    # Processing results
    summary = Column(Text)                              # LLM-generated summary
    relevance_score = Column(Float, default=0.0)        # 0.0 - 1.0
    matched_rules = Column(JSON, default=list)          # List of rule IDs
    tags = Column(JSON, default=list)                   # Auto-generated tags

    # User state
    is_read = Column(Boolean, default=False)
    is_starred = Column(Boolean, default=False)
    is_hidden = Column(Boolean, default=False)

    # Metadata
    metadata = Column(JSON, default=dict)               # Connector-specific

    # Deduplication
    is_duplicate = Column(Boolean, default=False)
    duplicate_of_id = Column(Integer, ForeignKey("items.id"), nullable=True)

    source = relationship("Source", back_populates="items")

    __table_args__ = (
        # Unique constraint per source
        {"sqlite_autoincrement": True},
    )


class Rule(Base):
    """User-defined rules for relevance scoring"""
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=0)               # Higher = checked first

    # Rule definition
    rule_type = Column(String(50), nullable=False)      # "keyword", "llm", "regex"
    config = Column(JSON, nullable=False)

    # Actions
    score_modifier = Column(Float, default=0.0)         # Add to relevance score
    tags_to_add = Column(JSON, default=list)            # Tags to apply

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Setting(Base):
    """Application settings"""
    __tablename__ = "settings"

    key = Column(String(100), primary_key=True)
    value = Column(JSON)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

### 4.2 Rule Types

```python
# backend/rules/types.py

from pydantic import BaseModel
from typing import Literal

class KeywordRuleConfig(BaseModel):
    """Simple keyword matching"""
    keywords: list[str]                    # Words to match
    match_mode: Literal["any", "all"] = "any"
    case_sensitive: bool = False
    match_in: list[str] = ["title", "content"]  # Fields to search

class RegexRuleConfig(BaseModel):
    """Regular expression matching"""
    pattern: str
    flags: list[str] = []                  # "i" for case-insensitive, etc.
    match_in: list[str] = ["title", "content"]

class LLMRuleConfig(BaseModel):
    """LLM-based semantic matching"""
    prompt: str                            # Question to ask LLM about the item
    threshold: float = 0.7                 # Confidence threshold

    # Example prompt:
    # "Is this article relevant to social policy in the German state of Hessen?
    #  Consider topics like: welfare, elderly care, childcare, migration.
    #  Answer with a confidence score between 0 and 1."
```

---

## 5. LLM Integration

### 5.1 LLM Service

```python
# backend/services/llm.py

from abc import ABC, abstractmethod
from pydantic import BaseModel
import httpx
import json

class LLMResponse(BaseModel):
    text: str
    model: str
    tokens_used: int | None = None

class BaseLLMProvider(ABC):
    @abstractmethod
    async def complete(self, prompt: str, system: str = None) -> LLMResponse:
        pass

class OllamaProvider(BaseLLMProvider):
    def __init__(self, host: str = "http://localhost:11434", model: str = "llama3.3:70b"):
        self.host = host
        self.model = model

    async def complete(self, prompt: str, system: str = None) -> LLMResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.host}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False
                }
            )
            data = response.json()
            return LLMResponse(
                text=data["message"]["content"],
                model=self.model,
                tokens_used=data.get("eval_count")
            )

class OpenRouterProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str = "meta-llama/llama-3.3-70b-instruct"):
        self.api_key = api_key
        self.model = model

    async def complete(self, prompt: str, system: str = None) -> LLMResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": messages
                }
            )
            data = response.json()
            return LLMResponse(
                text=data["choices"][0]["message"]["content"],
                model=self.model,
                tokens_used=data.get("usage", {}).get("total_tokens")
            )

class LLMService:
    """LLM service with fallback support"""

    def __init__(self, providers: list[BaseLLMProvider]):
        self.providers = providers

    async def complete(self, prompt: str, system: str = None) -> LLMResponse:
        last_error = None
        for provider in self.providers:
            try:
                return await provider.complete(prompt, system)
            except Exception as e:
                last_error = e
                continue
        raise last_error
```

### 5.2 Item Processor

```python
# backend/services/processor.py

from .llm import LLMService
from ..models import Item, Rule
import json

SYSTEM_PROMPT = """You are a news analysis assistant. Your task is to:
1. Analyze news items for relevance to specific topics
2. Generate concise summaries
3. Score relevance from 0.0 to 1.0

Always respond in valid JSON format."""

class ItemProcessor:
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    async def process(self, item: Item, rules: list[Rule]) -> dict:
        """Process an item: summarize, score relevance, match rules"""

        # Build prompt with rules context
        rules_context = self._build_rules_context(rules)

        prompt = f"""Analyze the following news item:

TITLE: {item.title}

CONTENT: {item.content[:3000]}  # Truncate for token limits

RULES TO CHECK:
{rules_context}

Respond with JSON:
{{
    "summary": "2-3 sentence summary in German",
    "relevance_score": 0.0-1.0,
    "matched_rules": ["rule_id_1", "rule_id_2"],
    "suggested_tags": ["tag1", "tag2"],
    "reasoning": "Brief explanation of scoring"
}}"""

        response = await self.llm.complete(prompt, SYSTEM_PROMPT)

        try:
            result = json.loads(response.text)
        except json.JSONDecodeError:
            # Fallback: extract JSON from response
            import re
            json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                result = {
                    "summary": response.text[:500],
                    "relevance_score": 0.5,
                    "matched_rules": [],
                    "suggested_tags": []
                }

        return result

    def _build_rules_context(self, rules: list[Rule]) -> str:
        lines = []
        for rule in rules:
            if rule.rule_type == "llm":
                lines.append(f"- {rule.id}: {rule.name}")
                lines.append(f"  Question: {rule.config.get('prompt', '')}")
        return "\n".join(lines)
```

---

## 6. API Endpoints

### 6.1 REST API Structure

```
/api
├── /connectors
│   ├── GET    /                    # List all connector types
│   └── GET    /{type}/schema       # Get config schema for connector
│
├── /sources
│   ├── GET    /                    # List all sources
│   ├── POST   /                    # Create new source
│   ├── GET    /{id}                # Get source details
│   ├── PUT    /{id}                # Update source
│   ├── DELETE /{id}                # Delete source
│   ├── POST   /{id}/test           # Test source connection
│   └── POST   /{id}/fetch          # Trigger manual fetch
│
├── /items
│   ├── GET    /                    # List items (paginated, filtered)
│   ├── GET    /{id}                # Get item details
│   ├── PATCH  /{id}                # Update item (read, starred, hidden)
│   ├── POST   /bulk-update         # Bulk update items
│   └── GET    /stats               # Get statistics
│
├── /rules
│   ├── GET    /                    # List all rules
│   ├── POST   /                    # Create rule
│   ├── PUT    /{id}                # Update rule
│   ├── DELETE /{id}                # Delete rule
│   └── POST   /{id}/test           # Test rule against sample text
│
├── /export
│   ├── POST   /email               # Send email digest
│   └── GET    /daily-report        # Get daily report as JSON/HTML
│
└── /settings
    ├── GET    /                    # Get all settings
    └── PUT    /                    # Update settings
```

### 6.2 API Implementation

```python
# backend/api/items.py

from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session
from datetime import datetime, date
from typing import Optional

router = APIRouter(prefix="/api/items", tags=["items"])

@router.get("/")
async def list_items(
    db: Session = Depends(get_db),
    # Filtering
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    source_id: Optional[int] = None,
    is_read: Optional[bool] = None,
    is_starred: Optional[bool] = None,
    min_score: Optional[float] = None,
    search: Optional[str] = None,
    tags: Optional[list[str]] = Query(None),
    # Pagination
    page: int = 1,
    per_page: int = 50,
    # Sorting
    sort_by: str = "published_at",
    sort_order: str = "desc"
):
    """List items with filtering, pagination, and sorting"""
    query = db.query(Item).filter(Item.is_hidden == False)

    if date_from:
        query = query.filter(Item.published_at >= date_from)
    if date_to:
        query = query.filter(Item.published_at <= date_to)
    if source_id:
        query = query.filter(Item.source_id == source_id)
    if is_read is not None:
        query = query.filter(Item.is_read == is_read)
    if is_starred is not None:
        query = query.filter(Item.is_starred == is_starred)
    if min_score:
        query = query.filter(Item.relevance_score >= min_score)
    if search:
        query = query.filter(
            Item.title.ilike(f"%{search}%") |
            Item.content.ilike(f"%{search}%")
        )

    # Count total before pagination
    total = query.count()

    # Sort
    sort_col = getattr(Item, sort_by, Item.published_at)
    if sort_order == "desc":
        query = query.order_by(sort_col.desc())
    else:
        query = query.order_by(sort_col.asc())

    # Paginate
    items = query.offset((page - 1) * per_page).limit(per_page).all()

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page
    }

@router.patch("/{item_id}")
async def update_item(
    item_id: int,
    updates: ItemUpdate,
    db: Session = Depends(get_db)
):
    """Update item state (read, starred, hidden)"""
    item = db.query(Item).get(item_id)
    if not item:
        raise HTTPException(404, "Item not found")

    for field, value in updates.dict(exclude_unset=True).items():
        setattr(item, field, value)

    db.commit()
    return item

@router.post("/bulk-update")
async def bulk_update_items(
    item_ids: list[int],
    updates: ItemUpdate,
    db: Session = Depends(get_db)
):
    """Bulk update multiple items"""
    db.query(Item).filter(Item.id.in_(item_ids)).update(
        updates.dict(exclude_unset=True),
        synchronize_session=False
    )
    db.commit()
    return {"updated": len(item_ids)}
```

---

## 7. Vue Frontend

### 7.1 Project Structure

```
frontend/
├── src/
│   ├── main.js
│   ├── App.vue
│   │
│   ├── api/                      # API client
│   │   ├── index.js
│   │   ├── items.js
│   │   ├── sources.js
│   │   └── rules.js
│   │
│   ├── stores/                   # Pinia stores
│   │   ├── items.js
│   │   ├── sources.js
│   │   └── settings.js
│   │
│   ├── composables/              # Reusable logic
│   │   ├── useKeyboard.js
│   │   ├── useInfiniteScroll.js
│   │   └── useFilters.js
│   │
│   ├── components/
│   │   ├── layout/
│   │   │   ├── AppHeader.vue
│   │   │   ├── AppSidebar.vue
│   │   │   └── AppLayout.vue
│   │   │
│   │   ├── items/
│   │   │   ├── ItemList.vue
│   │   │   ├── ItemCard.vue
│   │   │   ├── ItemDetail.vue
│   │   │   └── ItemFilters.vue
│   │   │
│   │   ├── sources/
│   │   │   ├── SourceList.vue
│   │   │   ├── SourceForm.vue
│   │   │   └── ConnectorPicker.vue
│   │   │
│   │   └── rules/
│   │       ├── RuleList.vue
│   │       ├── RuleForm.vue
│   │       └── RuleTest.vue
│   │
│   ├── views/
│   │   ├── DashboardView.vue     # Main feed view
│   │   ├── SourcesView.vue       # Manage sources
│   │   ├── RulesView.vue         # Manage rules
│   │   └── SettingsView.vue      # App settings
│   │
│   └── router/
│       └── index.js
│
├── index.html
├── vite.config.js
├── tailwind.config.js
└── package.json
```

### 7.2 Key Components

#### Item List with Keyboard Navigation
```vue
<!-- src/components/items/ItemList.vue -->
<template>
  <div class="item-list" @keydown="handleKeydown" tabindex="0" ref="listRef">
    <ItemCard
      v-for="(item, index) in items"
      :key="item.id"
      :item="item"
      :selected="index === selectedIndex"
      @click="selectItem(index)"
      @toggle-read="toggleRead(item)"
      @toggle-star="toggleStar(item)"
    />

    <div v-if="loading" class="loading">Loading...</div>
    <div v-if="!loading && !items.length" class="empty">No items found</div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useItemsStore } from '@/stores/items'
import { useMagicKeys } from '@vueuse/core'

const store = useItemsStore()
const items = computed(() => store.items)
const selectedIndex = ref(0)
const listRef = ref(null)

// Keyboard shortcuts
const { j, k, o, m, s, r } = useMagicKeys()

watch(j, (pressed) => {
  if (pressed) moveSelection(1)  // Next item
})

watch(k, (pressed) => {
  if (pressed) moveSelection(-1) // Previous item
})

watch(o, (pressed) => {
  if (pressed) openSelected()    // Open in new tab
})

watch(m, (pressed) => {
  if (pressed) toggleRead(items.value[selectedIndex.value])
})

watch(s, (pressed) => {
  if (pressed) toggleStar(items.value[selectedIndex.value])
})

function moveSelection(delta) {
  const newIndex = selectedIndex.value + delta
  if (newIndex >= 0 && newIndex < items.value.length) {
    selectedIndex.value = newIndex
    // Scroll into view
    const cards = listRef.value?.querySelectorAll('.item-card')
    cards?.[newIndex]?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }
}

function openSelected() {
  const item = items.value[selectedIndex.value]
  if (item?.url) {
    window.open(item.url, '_blank')
    store.markAsRead(item.id)
  }
}

async function toggleRead(item) {
  await store.updateItem(item.id, { is_read: !item.is_read })
}

async function toggleStar(item) {
  await store.updateItem(item.id, { is_starred: !item.is_starred })
}

// Load more on scroll
const { arrivedState } = useScroll(listRef)
watch(() => arrivedState.bottom, (arrived) => {
  if (arrived && store.hasMore) {
    store.loadMore()
  }
})
</script>
```

#### Dashboard View
```vue
<!-- src/views/DashboardView.vue -->
<template>
  <AppLayout>
    <template #sidebar>
      <div class="p-4 space-y-4">
        <!-- Date picker -->
        <div>
          <label class="block text-sm font-medium mb-1">Date</label>
          <input
            type="date"
            v-model="filters.date"
            class="w-full rounded border p-2"
          />
        </div>

        <!-- Source filter -->
        <div>
          <label class="block text-sm font-medium mb-1">Source</label>
          <select v-model="filters.sourceId" class="w-full rounded border p-2">
            <option :value="null">All sources</option>
            <option v-for="s in sources" :key="s.id" :value="s.id">
              {{ s.name }}
            </option>
          </select>
        </div>

        <!-- Status filter -->
        <div class="flex gap-2">
          <button
            @click="filters.isRead = null"
            :class="{ 'bg-blue-500 text-white': filters.isRead === null }"
            class="flex-1 rounded border p-2"
          >
            All
          </button>
          <button
            @click="filters.isRead = false"
            :class="{ 'bg-blue-500 text-white': filters.isRead === false }"
            class="flex-1 rounded border p-2"
          >
            Unread
          </button>
          <button
            @click="filters.isStarred = true"
            :class="{ 'bg-blue-500 text-white': filters.isStarred === true }"
            class="flex-1 rounded border p-2"
          >
            Starred
          </button>
        </div>

        <!-- Relevance threshold -->
        <div>
          <label class="block text-sm font-medium mb-1">
            Min. Relevance: {{ filters.minScore || 0 }}
          </label>
          <input
            type="range"
            v-model.number="filters.minScore"
            min="0"
            max="1"
            step="0.1"
            class="w-full"
          />
        </div>

        <!-- Stats -->
        <div class="border-t pt-4 text-sm text-gray-600">
          <div>Total: {{ stats.total }}</div>
          <div>Unread: {{ stats.unread }}</div>
          <div>Today: {{ stats.today }}</div>
        </div>
      </div>
    </template>

    <template #main>
      <div class="flex h-full">
        <!-- Item list -->
        <div class="w-1/2 border-r overflow-auto">
          <ItemList
            :items="items"
            :selected-id="selectedItem?.id"
            @select="selectItem"
          />
        </div>

        <!-- Detail panel -->
        <div class="w-1/2 overflow-auto">
          <ItemDetail v-if="selectedItem" :item="selectedItem" />
          <div v-else class="p-8 text-center text-gray-500">
            Select an item to view details
          </div>
        </div>
      </div>
    </template>
  </AppLayout>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useItemsStore } from '@/stores/items'
import { useSourcesStore } from '@/stores/sources'

const itemsStore = useItemsStore()
const sourcesStore = useSourcesStore()

const filters = ref({
  date: new Date().toISOString().split('T')[0],
  sourceId: null,
  isRead: null,
  isStarred: null,
  minScore: 0
})

const selectedItem = ref(null)

const items = computed(() => itemsStore.items)
const sources = computed(() => sourcesStore.sources)
const stats = computed(() => itemsStore.stats)

// Reload when filters change
watch(filters, () => {
  itemsStore.loadItems(filters.value)
}, { deep: true })

function selectItem(item) {
  selectedItem.value = item
  if (!item.is_read) {
    itemsStore.markAsRead(item.id)
  }
}

onMounted(() => {
  sourcesStore.loadSources()
  itemsStore.loadItems(filters.value)
})
</script>
```

---

## 8. Scheduler & Pipeline

```python
# backend/scheduler.py

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from .connectors.registry import ConnectorRegistry
from .services.processor import ItemProcessor
from .models import Source, Item
import logging

logger = logging.getLogger(__name__)

class FetchScheduler:
    def __init__(self, db_session_factory, llm_service):
        self.scheduler = AsyncIOScheduler()
        self.db_factory = db_session_factory
        self.processor = ItemProcessor(llm_service)

    def start(self):
        """Initialize and start the scheduler"""
        with self.db_factory() as db:
            sources = db.query(Source).filter(Source.enabled == True).all()
            for source in sources:
                self._add_source_job(source)

        self.scheduler.start()
        logger.info(f"Scheduler started with {len(sources)} sources")

    def _add_source_job(self, source: Source):
        """Add a job for a source"""
        self.scheduler.add_job(
            self._fetch_source,
            CronTrigger.from_crontab(source.schedule),
            id=f"source_{source.id}",
            args=[source.id],
            replace_existing=True
        )

    async def _fetch_source(self, source_id: int):
        """Fetch items from a single source"""
        with self.db_factory() as db:
            source = db.query(Source).get(source_id)
            if not source or not source.enabled:
                return

            try:
                # Get connector and fetch
                connector_cls = ConnectorRegistry.get(source.connector.type)
                connector = connector_cls()
                config = connector.config_schema(**source.config)
                raw_items = await connector.fetch(config)

                # Process each item
                rules = db.query(Rule).filter(Rule.enabled == True).all()

                for raw in raw_items:
                    # Check for duplicates
                    existing = db.query(Item).filter(
                        Item.source_id == source_id,
                        Item.external_id == raw.external_id
                    ).first()

                    if existing:
                        continue  # Skip duplicates

                    # Create item
                    item = Item(
                        source_id=source_id,
                        external_id=raw.external_id,
                        title=raw.title,
                        content=raw.content,
                        url=raw.url,
                        author=raw.author,
                        published_at=raw.published_at,
                        metadata=raw.metadata
                    )

                    # Process with LLM
                    result = await self.processor.process(item, rules)
                    item.summary = result.get("summary")
                    item.relevance_score = result.get("relevance_score", 0)
                    item.matched_rules = result.get("matched_rules", [])
                    item.tags = result.get("suggested_tags", [])

                    db.add(item)

                source.last_fetch = datetime.utcnow()
                source.last_error = None
                db.commit()

                logger.info(f"Fetched {len(raw_items)} items from {source.name}")

            except Exception as e:
                source.last_error = str(e)
                db.commit()
                logger.error(f"Error fetching {source.name}: {e}")
```

---

## 9. Deployment

### 9.1 Docker Setup

```dockerfile
# backend/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for PDF parsing
RUN apt-get update && apt-get install -y \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```dockerfile
# frontend/Dockerfile
FROM node:20-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

### 9.2 Docker Compose

```yaml
# docker-compose.yml
version: "3.8"

services:
  backend:
    build: ./backend
    container_name: aggregator-backend
    restart: unless-stopped
    volumes:
      - ./data:/app/data          # SQLite DB
      - ./config:/app/config      # Configuration files
    environment:
      - DATABASE_URL=sqlite:///data/aggregator.db
      - OLLAMA_HOST=http://gpu1.braustube.ddnss.de:11434
      - OLLAMA_MODEL=llama3.3:70b
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
    ports:
      - "8000:8000"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  frontend:
    build: ./frontend
    container_name: aggregator-frontend
    restart: unless-stopped
    ports:
      - "8080:80"
    depends_on:
      - backend

volumes:
  data:
```

### 9.3 Environment Variables

```bash
# .env.example

# Database
DATABASE_URL=sqlite:///data/aggregator.db

# LLM - Primary (Ollama on gpu1)
OLLAMA_HOST=http://gpu1.braustube.ddnss.de:11434
OLLAMA_MODEL=llama3.3:70b

# LLM - Fallback (OpenRouter)
OPENROUTER_API_KEY=sk-or-...

# Email Export (optional)
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
EMAIL_FROM=
EMAIL_TO=

# App
LOG_LEVEL=INFO
FETCH_INTERVAL_MINUTES=30
```

---

## 10. Project Roadmap

### Phase 1: MVP (Core Functionality)
- [ ] Backend setup (FastAPI, SQLAlchemy, SQLite)
- [ ] Connector framework + RSS connector
- [ ] Basic Vue frontend (list, detail, filters)
- [ ] Manual fetch trigger

### Phase 2: Intelligence
- [ ] LLM integration (Ollama)
- [ ] Rule engine (keyword + LLM rules)
- [ ] Auto-summarization
- [ ] Relevance scoring

### Phase 3: More Connectors
- [ ] HTML scraper connector
- [ ] Bluesky connector
- [ ] Twitter/Nitter connector
- [ ] PDF connector

### Phase 4: Polish
- [ ] Keyboard shortcuts
- [ ] Email export
- [ ] Dark mode
- [ ] Mobile responsive

### Phase 5: Advanced
- [ ] LinkedIn connector (API)
- [ ] Deduplication across sources
- [ ] Bulk actions
- [ ] Custom dashboards

---

*Architecture Document v1.0*
*Created: January 2026*
