# Project Knowledge

This file provides guidance to Claude Code when working with this repository.

## Quick Reference

| Resource | Location |
|----------|----------|
| GitHub | https://github.com/claymore666/liga-hessen-news-aggregator |
| API Docs (Swagger) | http://localhost:8000/docs |
| OpenAPI JSON | http://localhost:8000/openapi.json |
| Backend | http://localhost:8000 |
| Frontend | http://localhost:3000 |

## Development Guidelines

**API First**: Always use the REST API for data operations. Full documentation at `/docs` (Swagger UI).

```bash
# Prefer API calls over docker exec
curl -s http://localhost:8000/api/items?limit=10 | jq '.'
curl -s http://localhost:8000/api/stats/by-connector | jq '.'

# Docker exec only when necessary (always with timeout)
timeout 30 docker exec liga-news-backend python -c "..."
```

**Python Scripts**: Always use the venv when running scripts outside Docker:
```bash
cd news-aggregator/backend && source venv/bin/activate && python script.py
```

**Testing**: Run `pytest tests/` before committing. Create tests for new features.

**Missing Endpoints**: Discuss implementing proper API endpoints rather than creating workarounds.

## Git Workflow

```
main (production) ← dev (integration) ← milestone/X-name (feature work)
```

Never commit directly to `main` or `dev`. Create PRs from milestone branches.

---

## Project Overview

**Daily-Briefing-System** for **Liga der Freien Wohlfahrtspflege Hessen** - automated news aggregation and analysis for the Hessian welfare association umbrella organization (AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden).

### Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.12 + FastAPI + SQLAlchemy + SQLite |
| Frontend | Vue 3 + Vite + TailwindCSS + Pinia |
| LLM | Ollama (`liga-relevance` fine-tuned model) + OpenRouter fallback |
| Scraping | Playwright (stealth mode) for X/Twitter, Instagram, LinkedIn |

### Working Groups (Arbeitskreise)

| Code | Focus Area |
|------|------------|
| AK1 | Grundsatz und Sozialpolitik |
| AK2 | Migration und Flucht |
| AK3 | Gesundheit, Pflege und Senioren |
| AK4 | Eingliederungshilfe |
| AK5 | Kinder, Jugend, Frauen und Familie |
| QAG | Digitalisierung, Klimaschutz, Wohnen |

### Priority Levels

- `critical`: Immediate action (<24h) - budget cuts, legislation deadlines
- `high`: Important (1 week) - hearings, draft regulations
- `medium`: Monitor - political statements, party positions
- `low`: Background information

---

## API Reference

Full interactive documentation: **http://localhost:8000/docs**

### Key Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/items` | List items (paginated, filterable) |
| `GET /api/items/{id}` | Get single item with full details |
| `PATCH /api/items/{id}` | Update item (read status, priority, content) |
| `POST /api/items/{id}/reprocess` | Reprocess single item through LLM |
| `POST /api/items/{id}/refetch` | Re-scrape and extract linked articles |
| `POST /api/items/reprocess` | Batch reprocess items |
| `GET /api/sources` | List all sources |
| `POST /api/sources/{id}/fetch` | Manually fetch source |
| `GET /api/sources/errors` | Sources with fetch errors |
| `GET /api/stats/by-connector` | Item counts by connector type |
| `GET /api/stats/by-source` | Item counts by source |
| `GET /api/stats/by-priority` | Item counts by priority |
| `GET /api/admin/db-stats` | Database statistics |

### Common API Patterns

```bash
# Get recent items
curl -s "http://localhost:8000/api/items?limit=50" | jq '.items[0]'

# Filter by priority
curl -s "http://localhost:8000/api/items?priority=critical" | jq '.total'

# Reprocess items through LLM
curl -X POST "http://localhost:8000/api/items/reprocess?limit=100&force=true"

# Fetch specific source
curl -X POST "http://localhost:8000/api/sources/42/fetch"

# Check processing stats
curl -s "http://localhost:8000/api/admin/db-stats" | jq '.'
```

---

## Connectors

| Type | Description | Status |
|------|-------------|--------|
| `rss` | RSS/Atom feeds, Google Alerts | Stable |
| `x_scraper` | X/Twitter via Playwright | Stable (requires cookies) |
| `linkedin` | LinkedIn via Playwright | Stable (requires cookies) |
| `mastodon` | Mastodon RSS + API | Stable |
| `bluesky` | Bluesky native feeds | Stable |
| `telegram` | Public channels via t.me/s/ | Stable |
| `instagram_scraper` | Instagram via Playwright | Stable (max ~12 posts) |
| `html` | CSS selector-based scraping | Stable |
| `pdf` | PDF extraction (PyMuPDF) | Stable |

### Cookie Authentication (X.com, LinkedIn)

When cookies expire:
```bash
cd news-aggregator/backend && source venv/bin/activate

# X.com cookies
python scripts/extract_chrome_cookies.py
docker cp /tmp/x_cookies.json liga-news-backend:/app/data/x_cookies.json

# LinkedIn cookies
python scripts/extract_linkedin_cookies.py
docker cp /tmp/linkedin_cookies.json liga-news-backend:/app/data/linkedin_cookies.json
```

### Helper Scripts

```bash
# Fetch multiple sources
./scripts/fetch_sources.sh 100 116 137

# Fetch all X sources with no items
./scripts/fetch_sources.sh $(curl -s http://localhost:8000/api/stats/by-source | \
  jq -r '[.[] | select(.item_count == 0 and .connector_type == "x_scraper")] | .[].source_id' | tr '\n' ' ')
```

---

## LLM Processing

### Fine-tuned Model: `liga-relevance`

Custom Qwen3-14B model trained for Liga relevance classification.

**Input Format:**
```
Titel: {title}
Inhalt: {content[:2000]}
Quelle: {source_name}
Datum: {YYYY-MM-DD}
```

**Output Format (JSON):**
```json
{
  "summary": "4+ sentences summary",
  "detailed_analysis": "10+ sentences analysis with Liga context",
  "relevant": true,
  "relevance_score": 0.85,
  "priority": "high",
  "assigned_ak": "AK3",
  "tags": ["pflege", "finanzierung"],
  "reasoning": "Brief explanation"
}
```

### Data Storage

For `x_scraper` items, original content is preserved:
- `content`: Combined tweet + extracted linked article (for LLM)
- `metadata.original_tweet_text`: Original tweet text preserved
- `metadata.linked_articles`: Extracted article metadata

---

## Docker Operations

```bash
# Container status
docker compose ps

# Logs
docker logs liga-news-backend -f

# Rebuild after code changes
cd news-aggregator && docker compose down && docker compose up -d --build

# Direct DB query (use API instead when possible)
docker exec liga-news-backend sqlite3 /app/data/news.db "SELECT COUNT(*) FROM items;"
```

---

## Long-Running Tasks

For tasks like batch fetching, use background execution:

```
1. Start with run_in_background: true
2. Check progress with TaskOutput (block: false)
3. Read output file: /tmp/claude/.../tasks/{task_id}.output
4. Final result: TaskOutput with block: true
```

Show progress regularly rather than waiting silently for completion.

---

## Documentation Index

| File | Content |
|------|---------|
| `docs/DailyBriefingArchitecture.md` | Technical architecture, DB schema |
| `docs/Stakeholder-Datenbank...md` | 80+ stakeholders, social media handles |
| `relevance-tuner/` | Model training scripts and data |
