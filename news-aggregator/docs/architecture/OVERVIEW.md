# System Architecture

## Overview

The Liga Hessen News Aggregator is a multi-component system that:
1. Fetches news from various sources (RSS, social media, websites)
2. Classifies relevance using ML and LLM
3. Stores and indexes items for search
4. Displays a prioritized news feed

## Components

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (Vue.js)                        │
│                         Port 3000                                │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Backend (FastAPI)                           │
│                         Port 8000                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │   API    │  │ Services │  │Connectors│  │   Background     │ │
│  │Endpoints │  │ Pipeline │  │  RSS/X/  │  │    Workers       │ │
│  │          │  │ Processor│  │  etc.    │  │ LLM/Classifier   │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
        │                │                          │
        ▼                ▼                          ▼
┌───────────────┐ ┌───────────────┐        ┌───────────────┐
│  PostgreSQL   │ │  Classifier   │        │    Ollama     │
│   Port 5432   │ │   Port 8082   │        │  Port 11434   │
│               │ │  (ChromaDB)   │        │    (LLM)      │
└───────────────┘ └───────────────┘        └───────────────┘
```

## Backend Components

### API Layer (`backend/api/`)
REST endpoints for frontend and external access:
- `items.py` - News item CRUD and filtering
- `sources.py` - Source/channel management
- `admin.py` - System administration
- `llm.py` - LLM status and control
- `scheduler.py` - Scheduler control

### Services Layer (`backend/services/`)
Business logic and processing:
- `scheduler.py` - Parallel channel fetching with rate limits
- `pipeline.py` - Item processing, deduplication, classification
- `llm_worker.py` - Async LLM analysis worker
- `classifier_worker.py` - ML classification worker
- `article_extractor.py` - Full article content extraction

### Connectors (`backend/connectors/`)
Source-specific fetchers:
- `rss.py` - RSS/Atom feeds
- `x_scraper.py` - Twitter/X via Playwright
- `mastodon.py` - Mastodon API
- `bluesky.py` - Bluesky API
- `linkedin.py` - LinkedIn scraping
- And more...

## External Services

### Classifier API (Port 8082)
ML-based classification service:
- Relevance scoring (0-1)
- Priority assignment (high/medium/low/none)
- AK (Arbeitskreis) assignment
- Duplicate detection
- Semantic search

Uses two embedding models:
- `nomic-embed-text-v2` - Classification and search
- `paraphrase-multilingual-mpnet` - Duplicate detection

### Ollama (Port 11434)
Local LLM for detailed analysis:
- Summary generation
- Detailed analysis
- Priority reasoning
- Uses `qwen3:14b-q8_0` model

## Data Stores

### PostgreSQL
Primary relational database:
- Sources and channels
- Items with metadata
- Rules and matches
- User preferences

### ChromaDB (via Classifier)
Vector database for embeddings:
- Search index (nomic embeddings)
- Duplicate index (paraphrase embeddings)

## Background Workers

Two async workers run continuously:

### Classifier Worker
- Processes new items through ML classifier
- Assigns initial relevance and priority
- Runs before LLM for fast initial classification

### LLM Worker
- Processes items needing detailed analysis
- Generates summaries and reasoning
- Runs after classifier for deeper understanding

## Request Flow

1. **Frontend** makes API request
2. **API layer** validates and routes
3. **Services** perform business logic
4. **Database** persists data
5. **Response** returned to frontend

## Scheduled Operations

The scheduler runs periodic tasks:
- Channel fetching (per-channel intervals)
- Old item cleanup (housekeeping)
- Proxy validation (for scrapers)

See [SCHEDULER.md](../services/SCHEDULER.md) for details.
