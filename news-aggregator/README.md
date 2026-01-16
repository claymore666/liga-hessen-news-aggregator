# News Aggregator

A news aggregation system for Liga der Freien Wohlfahrtspflege Hessen to monitor policy-relevant news from multiple channels. Features LLM-powered relevance filtering and summarization.

## Features

- **Multi-Channel Monitoring**: RSS, X/Twitter, Bluesky, Mastodon, Instagram, LinkedIn, Telegram, Google Alerts, PDF documents, HTML scraping
- **LLM Integration**: Ollama for relevance classification, summarization, and priority scoring
- **Embedding-based Pre-filter**: Fast relevance classification using sentence transformers
- **Semantic Duplicate Detection**: Groups similar articles across sources using paraphrase embeddings
- **Multi-AK Assignment**: Items can be assigned to multiple Arbeitskreise (AK1-5, QAG)
- **System Stats Dashboard**: Real-time monitoring with worker controls
- **Item Audit Trail**: Full history of processing events per item
- **Vue 3 Frontend**: Dashboard with keyboard navigation, source/channel management, rule configuration
- **Smart Scheduling**: Per-channel fetch intervals with parallel fetching by source type
- **Rule-based Filtering**: Keyword matching, regex patterns, LLM-powered semantic rules
- **Email Briefings**: Automated daily digest reports

## Implementation Status

| Component | Status |
|-----------|--------|
| Database Layer (SQLAlchemy + async) | ✅ Complete |
| Models (Source, Channel, Item, Rule) | ✅ Complete |
| REST API (FastAPI) | ✅ Complete |
| Pipeline & Scheduler | ✅ Complete |
| Connectors (14 types) | ✅ Complete |
| Frontend (Vue 3) | ✅ Complete |
| LLM Processing (Ollama) | ✅ Complete |
| Embedding Classifier | ✅ Complete |
| Docker Deployment | ✅ Complete |
| Test Suite | ✅ Complete |

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | Vue 3 + Vite + TailwindCSS + Pinia |
| Backend | Python 3.12 + FastAPI + SQLAlchemy (async) |
| Database | PostgreSQL 17 |
| LLM | Ollama (qwen3:14b-q8_0) |
| Embeddings | nomic-embed-text-v2-moe (classification), paraphrase-multilingual-mpnet (duplicates) |
| Scraping | Playwright (for X/Instagram) |
| Deployment | Docker Compose |

## Connectors

| Connector | Description |
|-----------|-------------|
| `rss` | RSS/Atom feeds |
| `x_scraper` | X/Twitter via Playwright browser automation |
| `bluesky` | Bluesky social network |
| `mastodon` | Mastodon/Fediverse instances |
| `instagram` | Instagram API |
| `instagram_scraper` | Instagram via browser automation |
| `linkedin` | LinkedIn posts |
| `telegram` | Telegram channels |
| `google_alerts` | Google Alerts RSS feeds |
| `html` | Generic HTML scraping |
| `pdf` | PDF document extraction |

## Project Structure

```
news-aggregator/
├── backend/
│   ├── api/             # FastAPI routes
│   │   ├── sources.py   # Source/channel management
│   │   ├── items.py     # Item CRUD and filtering
│   │   ├── rules.py     # Rule management
│   │   ├── scheduler.py # Manual fetch triggers
│   │   ├── llm.py       # LLM status and reprocessing
│   │   ├── email.py     # Email briefing endpoints
│   │   └── stats.py     # Statistics
│   ├── connectors/      # Source connectors
│   ├── services/
│   │   ├── pipeline.py  # Item processing pipeline
│   │   ├── scheduler.py # APScheduler jobs
│   │   ├── processor.py # LLM processing
│   │   ├── relevance_filter.py # Embedding classifier
│   │   └── proxy_manager.py    # Proxy rotation
│   ├── tests/           # pytest test suite
│   ├── models.py        # SQLAlchemy models
│   ├── schemas.py       # Pydantic schemas
│   ├── database.py      # Async database session
│   ├── config.py        # Application configuration
│   └── main.py          # FastAPI application
├── frontend/
│   ├── src/
│   │   ├── views/       # Page components
│   │   ├── components/  # Reusable components
│   │   ├── stores/      # Pinia stores
│   │   └── api/         # API client
│   └── ...
├── docker-compose.yml
└── README.md
```

## Deployment

The application runs on gpu1 via Docker Compose:

```bash
cd news-aggregator
docker compose up -d --build
```

Access at: http://localhost:5173 (frontend) / http://localhost:8000 (API)

### Container Management

```bash
docker compose ps              # Check status
docker compose logs -f backend # Follow backend logs
docker compose restart         # Restart without rebuild
```

## Development

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run development server
uvicorn main:app --reload

# Run tests
pytest tests/
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## API Endpoints

### Sources & Channels
```
GET    /api/sources                    # List sources with channels
POST   /api/sources                    # Create source
GET    /api/sources/{id}               # Get source details
PUT    /api/sources/{id}               # Update source
DELETE /api/sources/{id}               # Delete source
POST   /api/sources/{id}/channels      # Add channel to source
DELETE /api/sources/{sid}/channels/{cid} # Remove channel
```

### Items
```
GET    /api/items                      # List items (paginated, filtered)
GET    /api/items/{id}                 # Get item details
PATCH  /api/items/{id}                 # Update item (read/star/archive)
POST   /api/items/{id}/read            # Mark as read
POST   /api/items/{id}/unread          # Mark as unread
POST   /api/items/bulk-update          # Bulk update items
```

### Rules
```
GET    /api/rules                      # List rules
POST   /api/rules                      # Create rule
PUT    /api/rules/{id}                 # Update rule
DELETE /api/rules/{id}                 # Delete rule
```

### Scheduler
```
GET    /api/scheduler/status           # Job status
POST   /api/scheduler/fetch/{channel_id} # Trigger channel fetch
POST   /api/scheduler/fetch-all        # Fetch all channels
```

### LLM
```
GET    /api/llm/status                 # LLM availability
POST   /api/llm/reprocess/{item_id}    # Reprocess single item
```

## License

MIT
