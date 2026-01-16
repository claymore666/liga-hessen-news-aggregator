# Current Status (2026-01-16)

## Production System

The classification system uses a **hybrid approach**:

1. **Embedding Classifier** (primary) - Fast classification via classifier-api
2. **LLM Analysis** (secondary) - Detailed summaries and analysis via Ollama

### Active Components

| Component | Model | Purpose | Status |
|-----------|-------|---------|--------|
| Classifier API | nomic-v2 + sklearn | Relevance, Priority, AK | ✅ Production |
| Duplicate Store | paraphrase-mpnet | Same-story detection | ✅ Production |
| Ollama LLM | qwen3:14b-q8_0 | Summary, detailed_analysis | ✅ Production |

### Classifier API Health

```bash
curl -s http://localhost:8082/health | jq
```

```json
{
  "status": "ok",
  "model": "nomic-ai/nomic-embed-text-v2-moe",
  "gpu": true,
  "gpu_name": "NVIDIA GeForce RTX 3090",
  "vector_store_items": 2898,
  "duplicate_store_items": 2898,
  "duplicate_model": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
}
```

## Classification Pipeline

```
New Article → Duplicate Check (0.75 threshold)
                    ↓
            [Not duplicate]
                    ↓
            Embedding Classifier
                    ↓
        ┌───────────┴───────────┐
        ↓                       ↓
   [Relevant]              [Irrelevant]
        ↓                       ↓
   LLM Analysis              Archive
        ↓
   Store with AK/Priority
```

### Multi-label AK Classification

Items can be assigned to multiple Arbeitskreise (working groups). The classifier returns:
- `ak`: Primary AK (highest confidence)
- `aks`: All AKs above threshold
- `ak_confidences`: Confidence scores per AK

## Duplicate Detection

Uses `paraphrase-multilingual-mpnet-base-v2` for semantic similarity:

| Score Range | Interpretation |
|-------------|----------------|
| 0.95-1.00 | Same article, different source |
| 0.75-0.95 | Same story, different wording |
| 0.50-0.75 | Related topic |
| < 0.50 | Different stories |

**Threshold**: 0.75 (catches same-story articles from different sources)

## Dataset

| Metric | Value |
|--------|-------|
| Total items | 1680 |
| Relevant | 224 (13%) |
| Irrelevant | 1456 (87%) |
| Train/Val/Test | 70/15/15 split |

## Storage

| Component | Size |
|-----------|------|
| PostgreSQL | ~24 MB |
| Vector store (nomic) | ~51 MB |
| Duplicate store (paraphrase) | ~45 MB |
| **Total** | ~120 MB |

## Known Issues

None currently.

## Recent Changes

| Date | Change |
|------|--------|
| 2026-01-16 | Added duplicate store with paraphrase model |
| 2026-01-16 | Lowered duplicate threshold to 0.75 |
| 2026-01-15 | Fixed async context bug in vector store indexing |
| 2026-01-13 | Added multi-label AK classification |
| 2026-01-13 | Migrated training data export to PostgreSQL |
| 2026-01-12 | Added item processing audit trail |

## Monitoring Commands

```bash
# Check classifier health
curl -s http://localhost:8082/health | jq

# Check LLM status
curl -s http://localhost:8000/api/llm/status | jq

# Check backend health
curl -s http://localhost:8000/health | jq

# View classifier logs
docker logs liga-classifier --tail 50

# View backend logs
docker logs liga-news-backend --tail 50
```

## Deployment

### Rebuild Classifier API

```bash
cd /home/kamienc/claude.ai/ligahessen/relevance-tuner/services/classifier-api
docker compose down && docker compose build --no-cache && docker compose up -d
```

### Restart Backend

```bash
cd /home/kamienc/claude.ai/ligahessen/news-aggregator
docker compose restart backend
```

### Sync Vector Stores

```bash
cd /home/kamienc/claude.ai/relevance-tuner/relevance-tuner
source venv/bin/activate
python scripts/sync_vectordb.py
```
