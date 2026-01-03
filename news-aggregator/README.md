# News Aggregator

A flexible news aggregation system with pluggable connectors for monitoring multiple communication channels.

## Features

- **Pluggable Connector System**: RSS, Twitter, Bluesky, Mastodon, LinkedIn, PDF, HTML scraping
- **LLM Integration**: Ollama (local) + OpenRouter (cloud fallback) for summarization and rule matching
- **Vue 3 Frontend**: Dashboard, source management, rule configuration
- **Rule-based Filtering**: Keyword matching, regex patterns, LLM-powered semantic rules
- **Email Export**: Daily briefing reports via email

## Implementation Status

| Component | Status |
|-----------|--------|
| Database Layer (SQLAlchemy) | ✅ Complete |
| Models (Connector, Source, Item, Rule, Setting) | ✅ Complete |
| REST API (FastAPI) | ✅ Complete |
| Pipeline & Scheduler | ✅ Complete |
| Test Suite | ✅ Complete |
| Connectors | ⏳ In progress |
| Frontend | ⏳ Not started |

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | Vue 3 + Vite + TailwindCSS + Pinia |
| Backend | Python 3.12 + FastAPI + SQLAlchemy |
| Database | SQLite |
| LLM | Ollama (gpu1) + OpenRouter (fallback) |
| Deployment | Docker + docker-compose |

## Project Structure

```
news-aggregator/
├── backend/
│   ├── api/             # FastAPI routes
│   │   ├── connectors.py
│   │   ├── sources.py
│   │   ├── items.py
│   │   ├── rules.py
│   │   └── stats.py
│   ├── connectors/      # Source connectors (RSS, Twitter, etc.)
│   ├── services/        # Business logic
│   │   ├── pipeline.py  # Item processing pipeline
│   │   └── scheduler.py # APScheduler integration
│   ├── tests/           # pytest test suite
│   │   ├── test_models.py
│   │   ├── test_api.py
│   │   └── test_pipeline.py
│   ├── models.py        # SQLAlchemy models
│   ├── schemas.py       # Pydantic schemas
│   ├── database.py      # Database session management
│   ├── config.py        # Application configuration
│   └── main.py          # FastAPI application entry
├── frontend/            # Vue 3 application (not started)
├── docker-compose.yml
└── README.md
```

## Documentation

See [docs/](../docs/) for detailed architecture documentation:

- [Architecture Overview](../docs/NewsAggregatorArchitecture.md)
- [Documentation Index](../docs/INDEX.md)

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

### Frontend (not yet implemented)

```bash
cd frontend
npm install
npm run dev
```

## API Endpoints

```
GET  /api/connectors              # List all connector types
GET  /api/connectors/{type}/schema # Get config schema

GET  /api/sources                 # List all sources
POST /api/sources                 # Create source
GET  /api/sources/{id}            # Get source
PUT  /api/sources/{id}            # Update source
DELETE /api/sources/{id}          # Delete source

GET  /api/items                   # List items (paginated, filtered)
GET  /api/items/{id}              # Get item
PATCH /api/items/{id}             # Update item state
POST /api/items/bulk-update       # Bulk update items

GET  /api/rules                   # List rules
POST /api/rules                   # Create rule
PUT  /api/rules/{id}              # Update rule
DELETE /api/rules/{id}            # Delete rule

GET  /api/stats                   # Get statistics
```

## License

MIT
