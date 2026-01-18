# Relevance Tuner - Claude Code Instructions

## CRITICAL RULES

1. **ALWAYS VERIFY MODEL SELECTION** - Before any LLM operation, check which model is active:
   ```bash
   curl -s "http://localhost:8000/api/llm/status" | jq '.model'
   ```
   - **Production model**: `qwen3:14b-q8_0` (base model with system prompt)
   - **NOT recommended**: `liga-relevance` (fine-tuned, has quality issues)
   - Model reverts to config default after Docker rebuild - ALWAYS re-check!
   - When in doubt, ASK THE USER which model to use

2. **Output format changes require explicit user approval** - Never modify the LLM output JSON schema (fields, structure) without asking first
3. **Output changes require parser updates** - Any approved format change must also update the backend parser in `news-aggregator/backend/services/processor.py`

## Project Overview

Fine-tuning pipeline for Liga Hessen news relevance classification.

## Key Documentation

| File | Purpose |
|------|---------|
| `README.md` | Quick start, project structure, current stats |
| `LABELING_PROMPT.md` | Detailed labeling criteria for the LLM |
| `DATA_CREATION.md` | Full data pipeline documentation |
| `RETRAINING.md` | Complete retraining guide with all steps |

## Common Tasks

### Add More Training Data

```bash
cd /home/kamienc/claude.ai/ligahessen/relevance-tuner
source venv/bin/activate

# 1. Export from news-aggregator
curl -s "http://localhost:8000/api/items?page_size=500" | jq -c '.items[]' > data/raw/new_items.jsonl

# 2. Split into batches
split -l 50 data/raw/new_items.jsonl data/raw/batches/batch_

# 3. Label with Ollama
python scripts/label_with_ollama.py --all --resume

# 4. Create splits
python scripts/create_splits.py

# 5. Train
python train_qwen3.py
```

### Retrain Model

**Full automated retraining** (relabel + train + deploy, ~5 hours):
```bash
./scripts/full_retrain.sh
```

**Training only** (if training data already exists):
```bash
cd /home/kamienc/claude.ai/ligahessen/relevance-tuner
source venv/bin/activate

python train_qwen3.py

# Import to Ollama
cd models/qwen3-trained/gguf
ollama create liga-relevance -f Modelfile
```

See `RETRAINING.md` for complete documentation.

## Automation Scripts

| Script | Purpose | Duration |
|--------|---------|----------|
| `scripts/full_retrain.sh` | Complete pipeline: relabel → train → GGUF → deploy → reprocess | ~5h |
| `scripts/auto_train_pipeline.sh` | Train → GGUF → deploy (skips relabeling) | ~1h |
| `scripts/label_with_ollama.py` | Relabel training data with specified model | ~3h |
| `scripts/create_splits.py` | Create train/val/test splits from labeled data | <1min |

### Usage Examples

```bash
cd /home/kamienc/claude.ai/ligahessen/relevance-tuner
source venv/bin/activate

# Full retraining (background) - venv included in script
nohup ./scripts/full_retrain.sh > /tmp/full_retrain.log 2>&1 &
tail -f /tmp/full_retrain.log

# Just relabel with different model
python scripts/label_with_ollama.py --all --model qwen3:70b

# Train after manual relabeling - venv included in script
./scripts/auto_train_pipeline.sh
```

### Prerequisites for Training

1. **Stop news-aggregator backend** (frees GPU):
   ```bash
   cd /home/kamienc/claude.ai/ligahessen/news-aggregator
   docker compose stop backend
   ```

2. **Unload Ollama models**:
   ```bash
   ollama stop liga-relevance
   ```

3. **Verify GPU is free** (<1GB used):
   ```bash
   nvidia-smi --query-gpu=memory.used --format=csv,noheader
   ```

## Current Dataset

- **1010 items** labeled with qwen3:32b
- **41% relevant** (410 items), 59% irrelevant
- Stratified splits: 70/15/15
- Output format: summary, detailed_analysis, argumentationskette

## Training Config

- Base: Qwen3-14B (4-bit quantized)
- Method: LoRA rank 16
- Batch size: 6 (fits in 24GB VRAM)
- Epochs: 3
- Output: GGUF q8_0 for Ollama (always use q8_0)

## Paths

| Path | Content |
|------|---------|
| `data/raw/batches/` | Unlabeled input batches |
| `data/reviewed/ollama_results/` | Labeled output |
| `data/final/` | Train/val/test splits |
| `models/qwen3-trained/` | Fine-tuned model |
| `scripts/` | Labeling and data scripts |

## Ollama Models

| Model | Purpose | Status |
|-------|---------|--------|
| `qwen3:14b-q8_0` | **Production inference** | ✅ Use this with system prompt |
| `qwen3:32b` | Labeling training data | Good quality, slower |
| `liga-relevance` | Fine-tuned classifier | ⚠️ NOT recommended (quality issues) |

### Switching Models
```bash
# Check current model
curl -s "http://localhost:8000/api/llm/status" | jq '.model'

# Switch to base model (recommended)
curl -X PUT "http://localhost:8000/api/llm/model" -H "Content-Type: application/json" -d '{"model": "qwen3:14b-q8_0"}'
```

## Classifier API (Embedding Service)

Runs on port 8082. Provides classification, semantic search, and duplicate detection.

### Models Used

| Model | Purpose |
|-------|---------|
| `nomic-ai/nomic-embed-text-v2-moe` | Classification & semantic search (768d) |
| `paraphrase-multilingual-mpnet-base-v2` | Duplicate detection (better same-story detection) |

### Duplicate Detection

Uses a separate duplicate index with paraphrase embeddings for better semantic similarity:
- **Threshold**: 0.75 (catches same-story articles with different wording)
- True duplicates (same article, different source): ~0.97 similarity
- Same story, different angles: 0.75-0.90 similarity
- Unrelated articles: <0.50 similarity

```bash
# Check classifier health (shows search_index_items and duplicate_index_items)
curl -s http://localhost:8082/health | jq '.'

# Test duplicate detection
curl -s -X POST http://localhost:8082/find-duplicates \
  -H "Content-Type: application/json" \
  -d '{"title": "Article title", "content": "Article content", "threshold": 0.75}'

# Sync search index to duplicate index (backfill after adding duplicate detection)
curl -s -X POST http://localhost:8082/sync-duplicate-store | jq '.'
```

### Index Management

When deleting items from the database, also remove them from the vector store to prevent orphaned embeddings causing duplicate detection errors.

```bash
# List all indexed item IDs
curl -s http://localhost:8082/ids | jq '.count'

# Delete specific items from both search and duplicate indexes
curl -s -X POST http://localhost:8082/delete \
  -H "Content-Type: application/json" \
  -d '{"ids": ["123", "456", "789"]}'

# Response shows how many were deleted from each index
# {"deleted_from_search": 3, "deleted_from_duplicate": 3}
```

See `news-aggregator/docs/operations/TROUBLESHOOTING.md` for full procedure on resetting and reloading items.

### Rebuilding Classifier

```bash
cd /home/kamienc/claude.ai/ligahessen/relevance-tuner/services/classifier-api
docker compose down && docker compose build --no-cache && docker compose up -d
```

## Database & Storage

### PostgreSQL Database

- **Container**: `liga-news-db`
- **Database**: `liga_news`
- **User**: `liga`
- **Password**: stored in `.env` as `POSTGRES_PASSWORD`

```bash
# Connect to database
docker exec -it liga-news-db psql -U liga -d liga_news

# Check database size
docker exec liga-news-db psql -U liga -d liga_news -c "SELECT pg_size_pretty(pg_database_size('liga_news'));"
```

### ChromaDB Indexes

| Index | Path | Model | Purpose |
|-------|------|-------|---------|
| Search index | `/app/data/vectordb` | nomic-v2 | Classification & semantic search |
| Duplicate index | `/app/data/duplicatedb` | paraphrase-mpnet | Duplicate detection |

```bash
# Check store sizes
docker exec liga-classifier du -sh /app/data/vectordb /app/data/duplicatedb
```

### Disk Usage (as of Jan 2026)

| Component | Size |
|-----------|------|
| PostgreSQL | ~24 MB |
| Vector store (nomic) | ~51 MB |
| Duplicate store (paraphrase) | ~45 MB |
| **Total** | ~120 MB |
