"""Main FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

from config import settings
from database import init_db
from services.scheduler import start_scheduler, stop_scheduler
from services.proxy_manager import proxy_manager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler for startup/shutdown."""
    # Startup
    await init_db()
    start_scheduler()
    proxy_manager.start_background_search()
    yield
    # Shutdown
    proxy_manager.stop_background_search()
    stop_scheduler()


API_DESCRIPTION = """
## Liga Hessen News Aggregator API

This API powers the daily briefing system for the **Liga der Freien Wohlfahrtspflege Hessen** -
an umbrella organization of 6 welfare associations (AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden).

### Core Concepts

- **Sources**: Organizations/accounts to monitor (e.g., "Hessischer Landtag", "PRO ASYL")
- **Channels**: Specific feeds within a source (e.g., RSS feed, X/Twitter profile, LinkedIn page)
- **Items**: Individual news articles/posts fetched from channels
- **Rules**: Keyword or semantic rules for priority scoring

### Connector Types

| Type | Description |
|------|-------------|
| `rss` | RSS/Atom feeds (including Google Alerts) |
| `html` | Web scraping with CSS selectors |
| `x_scraper` | X/Twitter profiles (Playwright-based) |
| `mastodon` | Mastodon profiles |
| `bluesky` | Bluesky feeds |
| `telegram` | Public Telegram channels |
| `linkedin` | LinkedIn company pages (RSSHub or Playwright) |
| `instagram_scraper` | Instagram profiles |
| `pdf` | PDF document extraction |

### Priority Levels

- `critical`: Immediate action required (budget cuts, legislative deadlines)
- `high`: Important, needs attention within days
- `medium`: Relevant, monitor situation
- `low`: Background information

### Arbeitskreise (Working Groups)

- `AK1`: Grundsatz und Sozialpolitik (General & Social Policy)
- `AK2`: Migration und Flucht (Migration & Refugees)
- `AK3`: Gesundheit, Pflege und Senioren (Health, Care & Seniors)
- `AK4`: Eingliederungshilfe (Integration Assistance)
- `AK5`: Kinder, Jugend, Frauen und Familie (Children, Youth, Women & Family)
- `QAG`: Querschnitt (Cross-cutting: Digitalization, Climate, Housing)
"""

app = FastAPI(
    title=settings.app_name,
    description=API_DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "items", "description": "News items (articles, posts) fetched from sources"},
        {"name": "sources", "description": "Organizations/accounts to monitor"},
        {"name": "connectors", "description": "Available connector types and their configuration"},
        {"name": "rules", "description": "Priority scoring rules (keyword and semantic)"},
        {"name": "stats", "description": "Dashboard statistics and analytics"},
        {"name": "scheduler", "description": "Background fetch scheduler control"},
        {"name": "llm", "description": "LLM processing status and reprocessing"},
        {"name": "admin", "description": "Administrative operations"},
        {"name": "email", "description": "Email digest configuration"},
        {"name": "proxies", "description": "Proxy management for scraping"},
    ],
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


# Import and include routers
from api import items, sources, connectors, rules, stats, email, proxies, llm, admin  # noqa: E402
from api import scheduler as scheduler_api  # noqa: E402

app.include_router(items.router, prefix=settings.api_prefix, tags=["items"])
app.include_router(sources.router, prefix=settings.api_prefix, tags=["sources"])
app.include_router(connectors.router, prefix=settings.api_prefix, tags=["connectors"])
app.include_router(rules.router, prefix=settings.api_prefix, tags=["rules"])
app.include_router(stats.router, prefix=settings.api_prefix, tags=["stats"])
app.include_router(email.router, prefix=settings.api_prefix, tags=["email"])
app.include_router(proxies.router, prefix=settings.api_prefix, tags=["proxies"])
app.include_router(llm.router, prefix=settings.api_prefix, tags=["llm"])
app.include_router(admin.router, prefix=settings.api_prefix, tags=["admin"])
app.include_router(scheduler_api.router, prefix=settings.api_prefix, tags=["scheduler"])
