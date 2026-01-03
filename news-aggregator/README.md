# News Aggregator

A flexible news aggregation system with pluggable connectors for monitoring multiple communication channels.

## Features

- **Pluggable Connector System**: RSS, Twitter, Bluesky, Mastodon, LinkedIn, PDF, HTML scraping
- **LLM Integration**: Ollama (local) + OpenRouter (cloud fallback) for summarization and rule matching
- **Vue 3 Frontend**: Dashboard, source management, rule configuration
- **Rule-based Filtering**: Keyword matching, regex patterns, LLM-powered semantic rules
- **Email Export**: Daily briefing reports via email

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
│   ├── connectors/      # Source connectors (RSS, Twitter, etc.)
│   ├── services/        # Business logic (LLM, scheduler, etc.)
│   ├── api/             # FastAPI routes
│   └── models.py        # SQLAlchemy models
├── frontend/
│   ├── src/
│   │   ├── components/  # Vue components
│   │   ├── stores/      # Pinia stores
│   │   └── views/       # Page views
│   └── ...
├── docker-compose.yml
└── README.md
```

## Documentation

See [docs/](../docs/) for detailed architecture documentation:

- [Architecture Overview](../docs/NewsAggregatorArchitecture.md)
- [Documentation Index](../docs/INDEX.md)

## Development

```bash
# Backend
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

## License

MIT
