# Database Schema

## Overview

PostgreSQL 17 with async SQLAlchemy ORM. All models in `backend/models.py`.

## Configuration

Database can be configured via environment variables:

### Connection

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | Full connection string (takes precedence) | - |
| `DATABASE_HOST` | Hostname | - |
| `DATABASE_PORT` | Port | 5432 |
| `DATABASE_NAME` | Database name | liga_news |
| `DATABASE_USER` | Username | - |
| `DATABASE_PASSWORD` | Password | - |
| `DATABASE_DRIVER` | SQLAlchemy async driver | postgresql+asyncpg |

**Note**: PostgreSQL is required. No SQLite fallback.

### Connection Pool

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_POOL_SIZE` | Persistent connections | 5 |
| `DATABASE_POOL_MAX_OVERFLOW` | Extra connections allowed | 10 |
| `DATABASE_POOL_TIMEOUT` | Wait time for connection (seconds) | 30 |
| `DATABASE_POOL_RECYCLE` | Recycle connections after (seconds) | 1800 |

### Examples

```bash
# Full URL
DATABASE_URL=postgresql+asyncpg://user:pass@db.example.com:5432/liga_news

# Components (builds URL automatically)
DATABASE_HOST=db.example.com
DATABASE_USER=app
DATABASE_PASSWORD=secret
DATABASE_NAME=liga_news

# Local Docker (common development setup)
DATABASE_HOST=localhost
DATABASE_USER=postgres
DATABASE_PASSWORD=postgres
DATABASE_NAME=liga_news
```

## Entity Relationship

```
┌─────────┐       ┌──────────┐       ┌───────┐
│ Source  │──1:N──│ Channel  │──1:N──│ Item  │
└─────────┘       └──────────┘       └───┬───┘
                                         │
                       ┌─────────────────┼─────────────────┐
                       │                 │                 │
                       ▼                 ▼                 ▼
                ┌────────────┐    ┌────────────┐    ┌────────────┐
                │ ItemEvent  │    │ItemRuleMatch│   │similar_to  │
                └────────────┘    └────────────┘    │ (self-ref) │
                                        │          └────────────┘
                                        │
                                        ▼
                                  ┌──────────┐
                                  │   Rule   │
                                  └──────────┘
```

## Tables

### sources
Organizations (Wohlfahrtsverbände, government, media).

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Primary key |
| name | String(255) | Organization name |
| slug | String(255) | URL-friendly identifier |
| description | Text | Optional description |
| website | String(512) | Organization website |
| enabled | Boolean | Active status |
| created_at | DateTime | Creation timestamp |
| updated_at | DateTime | Last update |

**Relationships**: `channels` (one-to-many)

### channels
Individual news feeds within a source.

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Primary key |
| source_id | Integer | FK to sources |
| name | String(255) | Channel name |
| connector_type | String(50) | Connector type (rss, x_scraper, etc.) |
| config | JSON | Connector-specific config |
| enabled | Boolean | Active status |
| fetch_interval_minutes | Integer | Fetch frequency (default: 60) |
| last_fetched_at | DateTime | Last successful fetch |
| last_error | Text | Last error message |
| created_at | DateTime | Creation timestamp |

**Indexes**:
- `ix_channels_source_id`
- `ix_channels_connector_type`
- Unique: `(source_id, connector_type, name)`

### items
Individual news items.

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Primary key |
| channel_id | Integer | FK to channels |
| external_id | String(512) | Source-specific ID |
| content_hash | String(64) | MD5 for deduplication |
| title | Text | Item title |
| content | Text | Full content |
| summary | Text | LLM-generated summary |
| detailed_analysis | Text | Extended analysis |
| url | String(2048) | Source URL |
| author | String(255) | Author name |
| published_at | DateTime | Publication date |
| fetched_at | DateTime | Fetch timestamp |
| priority | Enum | high/medium/low/none |
| priority_score | Integer | 0-100 score |
| assigned_ak | String(50) | Legacy single AK |
| assigned_aks | JSONB | Array of AK codes |
| is_read | Boolean | Read status |
| is_starred | Boolean | Starred status |
| is_archived | Boolean | Archived status |
| needs_llm_processing | Boolean | LLM queue flag |
| similar_to_id | Integer | FK to duplicate item |
| metadata_ | JSONB | Additional metadata (GIN indexed) |
| created_at | DateTime | Creation timestamp |
| updated_at | DateTime | Last update |

**Indexes**:
- `ix_items_channel_id`
- `ix_items_external_id`
- `ix_items_content_hash`
- `ix_items_published_at`
- `ix_items_priority`
- `ix_items_is_read`
- `ix_items_needs_llm_processing`
- `ix_items_similar_to_id`
- `ix_items_feed` — composite `(published_at DESC, priority)` partial index WHERE `similar_to_id IS NULL AND is_archived = false` (main feed query)
- `ix_items_fetched_at` — `fetched_at DESC` (time-based stats, worker queries)
- `ix_items_title_trgm` — GIN trigram on `title` (ILIKE text search, requires `pg_trgm`)
- `ix_items_content_trgm` — GIN trigram on `content` (ILIKE text search, requires `pg_trgm`)
- `ix_items_needs_llm_queue` — partial `(fetched_at DESC)` WHERE `needs_llm_processing = true` (LLM worker queue)
- `ix_items_unread_priority` — partial `(priority)` WHERE `is_read = false` (dashboard unread count)
- `ix_items_starred` — partial `(id)` WHERE `is_starred = true`
- `ix_items_is_archived` — partial WHERE `is_archived = false`
- `ix_items_metadata_gin` — GIN `jsonb_path_ops` on `metadata` (fast JSON path lookups)
- `ix_items_assigned_aks_gin` — GIN `jsonb_path_ops` on `assigned_aks`

### rules
Keyword and semantic matching rules.

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Primary key |
| name | String(255) | Rule name |
| description | Text | Rule description |
| rule_type | Enum | keyword/regex/semantic |
| pattern | Text | Match pattern |
| target_priority | Enum | Priority to assign |
| target_ak | String(50) | AK to assign |
| enabled | Boolean | Active status |
| order | Integer | Evaluation order |
| match_count | Integer | Times matched |
| created_at | DateTime | Creation timestamp |

**Index**: `ix_rules_enabled_order`

### item_rule_matches
Junction table for item-rule matches.

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Primary key |
| item_id | Integer | FK to items |
| rule_id | Integer | FK to rules |
| matched_at | DateTime | Match timestamp |

### item_events
Audit log for item actions.

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Primary key |
| item_id | Integer | FK to items |
| event_type | String(50) | Event type |
| timestamp | DateTime | Event timestamp |
| ip_address | String(45) | Client IP |
| data | JSON | Event-specific data |

**Event types**: `created`, `read`, `starred`, `archived`, `priority_changed`, `refetched`, `duplicate_detected`

### settings
Key-value configuration store.

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Primary key |
| key | String(255) | Setting key (unique) |
| value | JSON | Setting value |
| updated_at | DateTime | Last update |

## Enums

### Priority
```python
class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"
```

### RuleType
```python
class RuleType(str, Enum):
    KEYWORD = "keyword"
    REGEX = "regex"
    SEMANTIC = "semantic"
```

### ConnectorType
```python
class ConnectorType(str, Enum):
    RSS = "rss"
    X_SCRAPER = "x_scraper"
    MASTODON = "mastodon"
    BLUESKY = "bluesky"
    LINKEDIN = "linkedin"
    INSTAGRAM = "instagram"
    INSTAGRAM_SCRAPER = "instagram_scraper"
    TELEGRAM = "telegram"
    HTML = "html"
    PDF = "pdf"
    GOOGLE_ALERTS = "google_alerts"
```

## Common Queries

### Get unprocessed items
```sql
SELECT * FROM items
WHERE needs_llm_processing = true
ORDER BY created_at ASC
LIMIT 100;
```

### Get items by priority
```sql
SELECT * FROM items
WHERE priority IN ('high', 'medium')
  AND is_archived = false
ORDER BY published_at DESC;
```

### Find duplicates
```sql
SELECT * FROM items
WHERE similar_to_id IS NOT NULL;
```

### Channel fetch status
```sql
SELECT c.name, c.last_fetched_at, c.last_error,
       COUNT(i.id) as item_count
FROM channels c
LEFT JOIN items i ON c.id = i.channel_id
GROUP BY c.id;
```

## Performance Optimizations

### JSONB Columns (2026-01-30)

The `items.metadata` and `items.assigned_aks` columns use PostgreSQL `JSONB` (not `JSON`). This enables:
- GIN indexing for fast `@>`, `?`, `?|` operator queries
- Faster JSON operations (binary storage, no re-parsing)
- Index-backed lookups for `pre_filter IS NULL`, `duplicate_checked IS NULL`, etc.

### Partial Indexes

Several indexes are partial (filtered), keeping them small and fast:
- **Feed index**: Only non-archived, non-duplicate items — covers the main news listing query
- **LLM queue**: Only items needing processing — the worker polls this frequently
- **Unread/starred/archived**: Only matching rows — dashboard counters use these

### Trigram Text Search

The `pg_trgm` extension enables GIN trigram indexes on `title` and `content` columns for fast `ILIKE` pattern matching without full-text search infrastructure.

### Auto-Migration

New indexes are created automatically on startup in `main.py:run_migrations()` using `CREATE INDEX CONCURRENTLY IF NOT EXISTS` with `AUTOCOMMIT` isolation. This is idempotent and safe to run on every deploy.

### Multi-Worker Limitation

The backend runs as a single uvicorn process because the scheduler, LLM worker, and classifier worker track status in-process memory. Running multiple gunicorn workers (tested 2026-01-30) causes the frontend to see "stopped" status when requests hit non-leader workers. Multi-worker support requires moving status tracking to the database or Redis.

## Migrations

Using Alembic for schema migrations:

```bash
# Generate migration
cd backend
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head
```

Migration files in `backend/alembic/versions/`.
