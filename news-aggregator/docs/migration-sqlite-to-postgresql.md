# SQLite to PostgreSQL Migration Guide

**Date:** 2026-01-12
**Reason:** SQLite has concurrency issues and database locks under parallel fetch workloads

## Pre-Migration State

- **Database:** SQLite (`liga_news.db`)
- **Location:** Docker volume `liga-news-data` at `/app/data/`
- **Size:** ~9.7MB + WAL files (~8MB)

### Data Counts
| Table | Records |
|-------|---------|
| items | 1517 |
| channels | 116 |
| sources | 76 |
| rules | 10 |
| settings | 4 |
| item_rule_matches | 0 |

---

## Migration Steps

### Step 1: Export Data from SQLite

```bash
# Create export directory
mkdir -p /tmp/sqlite-export

# Export each table as CSV
docker compose exec backend sqlite3 -header -csv /app/data/liga_news.db \
  "SELECT * FROM items;" > /tmp/sqlite-export/items.csv

docker compose exec backend sqlite3 -header -csv /app/data/liga_news.db \
  "SELECT * FROM channels;" > /tmp/sqlite-export/channels.csv

docker compose exec backend sqlite3 -header -csv /app/data/liga_news.db \
  "SELECT * FROM sources;" > /tmp/sqlite-export/sources.csv

docker compose exec backend sqlite3 -header -csv /app/data/liga_news.db \
  "SELECT * FROM rules;" > /tmp/sqlite-export/rules.csv

docker compose exec backend sqlite3 -header -csv /app/data/liga_news.db \
  "SELECT * FROM settings;" > /tmp/sqlite-export/settings.csv

docker compose exec backend sqlite3 -header -csv /app/data/liga_news.db \
  "SELECT * FROM item_rule_matches;" > /tmp/sqlite-export/item_rule_matches.csv

# Full SQL dump as backup
docker compose exec backend sqlite3 /app/data/liga_news.db ".dump" \
  > /tmp/sqlite-export/full_dump.sql
```

### Step 2: Stop Backend Container

```bash
docker compose stop backend
```

### Step 3: Update docker-compose.yml

Add PostgreSQL service and update backend DATABASE_URL:

```yaml
services:
  db:
    image: postgres:17-alpine
    container_name: liga-news-db
    restart: unless-stopped
    environment:
      POSTGRES_DB: liga_news
      POSTGRES_USER: liga
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U liga -d liga_news"]
      interval: 10s
      timeout: 5s
      retries: 5

  backend:
    # ... existing config ...
    environment:
      - DATABASE_URL=postgresql+asyncpg://liga:${POSTGRES_PASSWORD}@db:5432/liga_news
      # ... rest of env vars ...
    depends_on:
      db:
        condition: service_healthy

volumes:
  postgres-data:
    name: liga-news-postgres
```

### Step 4: Set PostgreSQL Password

```bash
# Add to .env file
echo "POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 24)" >> .env
```

### Step 5: Start PostgreSQL Container

```bash
# Start only the database first
docker compose up -d db

# Wait for it to be healthy
docker compose exec db pg_isready -U liga -d liga_news
```

### Step 6: Create Schema via Application

The application's `init_db()` creates the schema automatically on first start.

```bash
# Start backend briefly to create schema
docker compose up -d backend

# Wait for schema creation
sleep 10

# Stop backend for data import
docker compose stop backend
```

### Step 7: Import Data into PostgreSQL

```bash
# Copy CSV files into PostgreSQL container
docker cp /tmp/sqlite-export/. liga-news-db:/tmp/import/

# Import data in dependency order
docker compose exec db psql -U liga -d liga_news << 'EOF'

-- Disable triggers during import
SET session_replication_role = 'replica';

-- Import sources first (referenced by channels)
\copy sources(id, name, url, connector_type, is_active, created_at, updated_at) FROM '/tmp/import/sources.csv' WITH (FORMAT csv, HEADER true);

-- Import channels (references sources)
\copy channels(id, source_id, name, channel_id, url, config, is_active, last_fetched_at, fetch_interval_minutes, error_count, last_error, created_at, updated_at) FROM '/tmp/import/channels.csv' WITH (FORMAT csv, HEADER true, NULL '');

-- Import rules
\copy rules(id, name, description, rule_type, pattern, priority_boost, target_priority, enabled, "order", created_at, updated_at) FROM '/tmp/import/rules.csv' WITH (FORMAT csv, HEADER true, NULL '');

-- Import items (references channels)
\copy items(id, channel_id, external_id, title, description, url, author, published_at, fetched_at, metadata, relevance_score, is_hidden, is_saved, is_archived, assigned_ak, tags, notes, hidden_reason, processed_at, priority) FROM '/tmp/import/items.csv' WITH (FORMAT csv, HEADER true, NULL '');

-- Import settings
\copy settings("key", value, description, updated_at) FROM '/tmp/import/settings.csv' WITH (FORMAT csv, HEADER true, NULL '');

-- Import item_rule_matches (if any)
-- \copy item_rule_matches(id, item_id, rule_id, matched_at, match_details) FROM '/tmp/import/item_rule_matches.csv' WITH (FORMAT csv, HEADER true, NULL '');

-- Re-enable triggers
SET session_replication_role = 'origin';

-- Update sequences to avoid ID conflicts
SELECT setval('sources_id_seq', (SELECT MAX(id) FROM sources));
SELECT setval('channels_id_seq', (SELECT MAX(id) FROM channels));
SELECT setval('items_id_seq', (SELECT MAX(id) FROM items));
SELECT setval('rules_id_seq', (SELECT MAX(id) FROM rules));

EOF
```

### Step 8: Verify Migration

```bash
# Check record counts
docker compose exec db psql -U liga -d liga_news -c "
SELECT 'items' as table_name, COUNT(*) FROM items
UNION ALL SELECT 'channels', COUNT(*) FROM channels
UNION ALL SELECT 'sources', COUNT(*) FROM sources
UNION ALL SELECT 'rules', COUNT(*) FROM rules
UNION ALL SELECT 'settings', COUNT(*) FROM settings;
"
```

Expected output:
```
 table_name | count
------------+-------
 items      |  1517+
 channels   |   116
 sources    |    76
 rules      |    10
 settings   |     4
```

**Note:** Item count may be higher if new items were fetched during migration.

### Step 9: Start Application

```bash
docker compose up -d
```

### Step 10: Test Application

```bash
# Check health
curl http://localhost:8000/health

# Verify data via API
curl http://localhost:8000/api/v1/stats
```

---

## Rollback Procedure

If migration fails, revert to SQLite:

```bash
# Stop everything
docker compose down

# Restore original docker-compose.yml
git checkout docker-compose.yml

# Start with SQLite
docker compose up -d
```

The SQLite database remains intact in the `liga-news-data` volume.

---

## Post-Migration

### Cleanup (after confirming PostgreSQL works)

```bash
# Remove export files
rm -rf /tmp/sqlite-export

# Optionally remove old SQLite volume (DESTRUCTIVE)
# docker volume rm liga-news-data
```

### Benefits of PostgreSQL

1. **Better concurrency:** Multiple readers and writers without locks
2. **Connection pooling:** Efficient connection management
3. **Better JSON support:** Native JSONB with indexing
4. **Scalability:** Can handle much larger datasets
5. **Mature tooling:** pg_dump, pg_restore, replication options

---

## Migration Log (2026-01-12)

**Migration completed successfully on gpu1.**

### Timeline
- **13:50** - Started migration, exported SQLite data
- **13:55** - Updated docker-compose.yml for PostgreSQL
- **13:57** - Started PostgreSQL container
- **13:59** - Ran import script via backend container
- **14:00** - Verified all data imported correctly

### Final Counts
| Table | Count |
|-------|-------|
| sources | 76 |
| channels | 116 |
| rules | 10 |
| items | 1521 |
| settings | 4 |

### Configuration
- **PostgreSQL version:** 17-alpine
- **Database name:** liga_news
- **User:** liga
- **Volume:** liga-news-postgres

### Files Changed
- `docker-compose.yml` - Added PostgreSQL service, updated DATABASE_URL
- `.env` - Added POSTGRES_PASSWORD
