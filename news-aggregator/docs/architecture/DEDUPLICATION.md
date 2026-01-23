# Deduplication System

This document describes the semantic deduplication system used to group similar news articles covering the same story from different sources.

## Intention

News aggregation from multiple sources (RSS feeds, Google Alerts, social media) inevitably produces multiple articles about the same story. Rather than showing users 50+ articles about "Pflegekosten steigen" from different outlets, the system:

1. **Detects** semantically similar articles using embeddings
2. **Groups** them under a primary item (first occurrence)
3. **Displays** the primary with expandable duplicates in the UI

This reduces noise while preserving access to all coverage angles.

## Architecture Overview

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  News Sources   │────▶│    Pipeline     │────▶│   PostgreSQL    │
│  (RSS, Alerts)  │     │  (backend)      │     │   (items)       │
└─────────────────┘     └────────┬────────┘     └─────────────────┘
                                 │
                                 │ /find-duplicates
                                 ▼
                        ┌─────────────────┐
                        │  Classifier API │
                        │  (ChromaDB)     │
                        └─────────────────┘
```

## Components

### 1. Classifier API (gpu1:8082)

Provides the `/find-duplicates` endpoint using ChromaDB for vector similarity search.

**Location**: `relevance-tuner/services/classifier-api/`

**Key Classes**:
- `DuplicateStore` (classifier.py:506) - ChromaDB wrapper for duplicate detection
- `ParaphraseEmbedder` - Sentence-transformers model wrapper

### 2. Pipeline (backend)

Calls the classifier API during item ingestion to detect duplicates.

**Location**: `news-aggregator/backend/services/pipeline.py`

**Key Method**: `process_items()` lines 129-180

### 3. Frontend

Displays grouped items with expandable duplicate lists.

**Location**: `news-aggregator/frontend/src/components/nachrichten/MessageList.vue`

## Embedding Models

The system uses **two different embedding models** for different purposes:

| Model | Purpose | Optimized For |
|-------|---------|---------------|
| `nomic-ai/nomic-embed-text-v2-moe` | Classification & semantic search | Topic understanding |
| `paraphrase-multilingual-mpnet-base-v2` | **Duplicate detection** | Paraphrase similarity |

The paraphrase model is specifically trained to recognize that "Pflegekosten steigen" and "Eigenanteil für Pflege erhöht sich" are semantically equivalent, even with different wording.

## Process Flow

### During Item Ingestion

```python
# pipeline.py:129-180 (simplified)
async def process_items(self, items):
    for item in items:
        # 1. URL-based deduplication (exact match)
        if await self._is_duplicate(channel_id, external_id, content_hash):
            continue

        # 2. Semantic duplicate detection
        duplicates = await self.relevance_filter.find_duplicates(
            title=item.title,
            content=item.content,
            threshold=0.75,  # Configurable
        )

        if duplicates:
            # Link to primary item
            similar_to_id = duplicates[0]["id"]
        else:
            similar_to_id = None

        # 3. Store item with duplicate link
        new_item = Item(
            ...
            similar_to_id=similar_to_id,
        )
```

### Duplicate Detection (Classifier API)

```python
# classifier.py:600-650 (DuplicateStore.find_duplicates)
def find_duplicates(self, title, content, threshold=0.75):
    # 1. Create embedding from title + content
    text = f"{title} {content}"
    embedding = self.embedder.encode([text])

    # 2. Query ChromaDB for similar items
    results = self.collection.query(
        query_embeddings=[embedding],
        n_results=5,
    )

    # 3. Filter by threshold and return
    duplicates = []
    for id, distance, metadata in zip(...):
        score = 1 - distance  # Convert distance to similarity
        if score >= threshold:
            duplicates.append({
                "id": id,
                "title": metadata["title"],
                "score": score,
            })

    return duplicates
```

## Thresholds

| Threshold | Meaning | Use Case |
|-----------|---------|----------|
| **0.75** | Default - catches same-story with different wording | Production |
| 0.80 | More conservative - fewer false positives | High precision |
| 0.70 | More aggressive - catches looser similarities | High recall |

**Typical Similarity Scores**:
- **0.95-1.00**: Exact duplicates (same article, different URLs)
- **0.85-0.95**: Same story, same angle, minor rewording
- **0.75-0.85**: Same story, different angle/emphasis
- **0.50-0.75**: Related topic, different story
- **<0.50**: Unrelated

## Data Model

### PostgreSQL Schema

```sql
-- items table
CREATE TABLE items (
    id SERIAL PRIMARY KEY,
    title VARCHAR,
    content TEXT,
    url VARCHAR UNIQUE,
    similar_to_id INTEGER REFERENCES items(id),  -- Links to primary item
    ...
);

-- Relationship: duplicates point to their primary
-- Primary items have similar_to_id = NULL
```

### Item Relationships

```
Primary Item (id=100, similar_to_id=NULL)
├── Duplicate 1 (id=105, similar_to_id=100)
├── Duplicate 2 (id=108, similar_to_id=100)
└── Duplicate 3 (id=112, similar_to_id=100)
```

### SQLAlchemy Model

```python
# models.py
class Item(Base):
    similar_to_id = Column(Integer, ForeignKey("items.id"), nullable=True)

    # Relationships
    similar_to = relationship("Item", remote_side=[id], backref="duplicates")
```

## API Endpoints

### Backend API

```bash
# List items (primary only, with nested duplicates)
GET /api/items?group_duplicates=true

# Response includes:
{
  "items": [
    {
      "id": 100,
      "title": "Pflegekosten steigen...",
      "similar_to_id": null,
      "duplicates": [
        {"id": 105, "title": "Eigenanteil erhöht...", "source": "FAZ"},
        {"id": 108, "title": "Pflege wird teurer...", "source": "WDR"}
      ]
    }
  ]
}
```

### Classifier API

```bash
# Find duplicates for a new item
POST http://gpu1:8082/find-duplicates
{
  "title": "Pflegekosten steigen auf Rekordniveau",
  "content": "Die Eigenanteile für Pflegeheimbewohner...",
  "threshold": 0.75
}

# Response
{
  "duplicates": [
    {"id": "11075", "title": "Kosten für Pflege im Heim...", "score": 0.89}
  ],
  "has_duplicates": true
}

# Index health
GET http://gpu1:8082/health
{
  "duplicate_index_items": 6268,
  "duplicate_model": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
}
```

## Frontend Display

The `MessageList.vue` component handles duplicate grouping:

```vue
<!-- Primary item with expand toggle -->
<div v-for="item in items">
  <button v-if="item.duplicates?.length" @click="toggleExpand(item.id)">
    +{{ item.duplicates.length }}
  </button>

  <!-- Expanded duplicates list -->
  <ul v-if="expandedItems.has(item.id)">
    <li v-for="dup in item.duplicates">
      {{ dup.title }} ({{ dup.source }})
    </li>
  </ul>
</div>
```

## Synchronization

### Problem: ChromaDB ↔ PostgreSQL Drift

When items are deleted from PostgreSQL (cleanup, deduplication), ChromaDB retains stale entries. This causes:
- Detection failures (items reference non-existent IDs)
- False duplicate matches

### Solution: Manual Sync

```bash
# On docker-ai

# 1. Get IDs from both stores
chromadb_ids=$(curl -s 'http://gpu1:8082/ids' | jq -r '.ids[]' | sort)
pg_ids=$(docker exec liga-news-db psql -U liga -d liga_news -t -A -c 'SELECT id FROM items;' | sort)

# 2. Find stale entries
stale_ids=$(comm -23 <(echo "$chromadb_ids") <(echo "$pg_ids"))

# 3. Delete from ChromaDB
stale_json=$(echo "$stale_ids" | jq -R . | jq -s .)
curl -X POST 'http://gpu1:8082/delete' \
  -H 'Content-Type: application/json' \
  -d "{\"ids\": $stale_json}"
```

### Automatic Sync (TODO)

Consider adding a scheduled job to sync indexes periodically.

## Troubleshooting

### Duplicates Not Being Detected

1. **Check classifier API health**:
   ```bash
   curl http://gpu1:8082/health | jq '.duplicate_index_items'
   ```

2. **Verify item is indexed**:
   ```bash
   curl http://gpu1:8082/ids | jq '.ids | map(select(. == "11075"))'
   ```

3. **Test similarity manually**:
   ```bash
   curl -X POST http://gpu1:8082/find-duplicates \
     -H "Content-Type: application/json" \
     -d '{"title": "Test title", "content": "Test content", "threshold": 0.70}'
   ```

### Stale References in Database

```sql
-- Find items pointing to non-existent similar_to_id
SELECT id, title, similar_to_id
FROM items
WHERE similar_to_id IS NOT NULL
  AND similar_to_id NOT IN (SELECT id FROM items);

-- Clear stale references
UPDATE items SET similar_to_id = NULL
WHERE similar_to_id NOT IN (SELECT id FROM items);
```

### ChromaDB Out of Sync

See "Synchronization" section above.

## Configuration

### Backend (.env)

```env
CLASSIFIER_API_URL=http://gpu1:8082
```

### Threshold Tuning

The threshold is set in `pipeline.py:139`:
```python
duplicates = await self.relevance_filter.find_duplicates(
    title=normalized.title,
    content=normalized.content,
    threshold=0.75,  # Adjust here
)
```

## Off-Hours Behavior (gpu1 Downtime)

### Problem

The classifier API runs on gpu1, which has scheduled downtime:
- **Active hours**: 7:00-16:00 (configurable)
- **Weekends**: May be inactive depending on config
- **Sleep/suspend**: Host suspends after idle timeout

During downtime, items are fetched and stored but:
- Classification doesn't run
- Duplicate detection doesn't run
- Vector indexing doesn't run

### Solution: Backlog Processing

The `ClassifierWorker` (backend/services/classifier_worker.py) runs continuously and catches up on missed work when gpu1 becomes available.

**Processing Priorities**:

```python
# classifier_worker.py:134-155
while self._running:
    # Priority 1: Classify unprocessed items
    processed = await self._process_unclassified_items()
    if processed > 0:
        continue

    # Priority 2: Re-check duplicates for items that missed check
    duplicates_checked = await self._process_unchecked_duplicates()
    if duplicates_checked > 0:
        continue

    # Priority 3: Index items that weren't indexed
    indexed = await self._process_unindexed_items()
    if indexed > 0:
        continue

    # No work, sleep 60s
    await asyncio.sleep(self.idle_sleep)
```

### Tracking Flags

Items track their processing state via metadata:

| Flag | Purpose | Set When |
|------|---------|----------|
| `metadata_.pre_filter` | Classification result | After classifier processes item |
| `metadata_.duplicate_checked` | Dedup check completed | After duplicate check (even if none found) |
| `metadata_.duplicate_checked_at` | Timestamp of check | Same as above |
| `metadata_.duplicate_score` | Similarity score | If duplicate found |
| `metadata_.vectordb_indexed` | ChromaDB indexing | After successful indexing |

### Backlog Query

Items needing duplicate check are found by:

```sql
-- classifier_worker.py:392-395
SELECT * FROM items
WHERE similar_to_id IS NULL
  AND metadata_->'duplicate_checked' IS NULL
  AND fetched_at >= (NOW() - INTERVAL '7 days')
ORDER BY fetched_at DESC
LIMIT 50;
```

The 7-day limit is configurable via `DUPLICATE_CHECK_DAYS` environment variable (0 = no limit).

### Monitoring Backlog

```bash
# Check queue sizes via API
curl http://localhost:8000/api/admin/stats | jq '.processing_queue'

# Response includes:
{
  "awaiting_classifier": 150,    # Need classification
  "awaiting_dedup": 45,          # Need duplicate check
  "awaiting_vectordb": 23        # Need indexing
}
```

### Typical Catchup Timeline

When gpu1 wakes after overnight downtime:

1. **0-5 min**: Classifier API initializes, loads models
2. **5-10 min**: Classification backlog processed (fastest)
3. **10-30 min**: Duplicate check backlog processed
4. **30-60 min**: Vector indexing backlog processed

Times vary based on backlog size and batch sizes.

## Performance

| Metric | Value |
|--------|-------|
| Embedding generation | ~50ms per item |
| ChromaDB query | ~10ms |
| Index size (6K items) | ~45 MB |
| Memory usage | ~500 MB (includes model) |
| Backlog batch size | 50 items (configurable) |
| Idle sleep | 60s between checks |

## Related Documentation

- [Classifier API](../../relevance-tuner/CLAUDE.md) - Full classifier documentation
- [Pipeline](PIPELINE.md) - Item processing pipeline
- [Troubleshooting](../operations/TROUBLESHOOTING.md) - Common issues
