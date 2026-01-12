# Classifier Training Guide

## Overview

The embedding classifier uses a two-stage approach:
1. **NomicV2 embeddings** - Convert text to 768-dim vectors
2. **RandomForest classifiers** - Predict relevance, priority, and AK

## Current Training Data Imbalance

As of 2026-01-12, the training data has severe AK imbalance:

| AK | Count | % | Status |
|----|-------|---|--------|
| null | 536 | 77.9% | OK (irrelevant items) |
| AK2 | 42 | 6.1% | OK |
| AK5 | 35 | 5.1% | OK |
| QAG | 33 | 4.8% | OK |
| AK1 | 28 | 4.1% | OK |
| **AK3** | **8** | **1.2%** | **CRITICAL - needs more data** |
| **AK4** | **6** | **0.9%** | **CRITICAL - needs more data** |

**Total: 688 training examples**

### Impact

The classifier performs poorly on AK3 (Health/Care) and AK4 (Disability/Inclusion) because it has almost no examples to learn from. Articles about hospitals, nursing care, disability services get misclassified as AK1 or assigned no AK.

## Training Pipeline

### 1. Data Collection

Items flow through the news-aggregator pipeline:
```
Fetch → Classifier (pre-filter) → LLM (full analysis) → Database
```

LLM-processed items with `llm_analysis` in metadata are the source of ground truth labels.

### 2. Using VectorDB for Finding Training Candidates

The classifier API includes a ChromaDB vector store for semantic search:

```bash
# Search for AK3-related content
curl -X POST "http://gpu1:8082/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "Pflege Krankenhaus Altenpflege Pflegeheim Gesundheit", "top_k": 20}'

# Search for AK4-related content
curl -X POST "http://gpu1:8082/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "Behinderung Inklusion Eingliederungshilfe BTHG Teilhabe", "top_k": 20}'
```

### 3. Manual Classification Workflow

To improve AK3/AK4 classification:

1. **Find candidates** using vector search (semantic similarity to AK keywords)
2. **Review in news-aggregator UI** - check LLM analysis and correct if needed
3. **Export corrected items** to training data
4. **Retrain classifier** with balanced dataset

### 4. Adding Items to VectorDB

Items can be indexed for search:

```bash
# Single item
curl -X POST "http://gpu1:8082/index" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "3254",
    "title": "Betrug im Krankenhaus?: Der große Krach um die Pflegebudgets",
    "content": "...",
    "metadata": {"priority": "medium", "source": "FAZ", "assigned_ak": "AK3"}
  }'

# Batch index
curl -X POST "http://gpu1:8082/index/batch" \
  -H "Content-Type: application/json" \
  -d '{"items": [...]}'
```

### 5. Exporting Training Data

```bash
# Export LLM-processed items from PostgreSQL
docker compose exec db psql -U liga -d liga_news -c "
SELECT id, title, content, priority, assigned_ak, metadata
FROM items
WHERE metadata->'llm_analysis' IS NOT NULL
  AND assigned_ak IS NOT NULL
" --csv > training_export.csv
```

### 6. Retraining the Classifier

```bash
cd ~/claude.ai/relevance-tuner/relevance-tuner
source venv/bin/activate

# Create balanced training splits
python scripts/create_splits.py --balance-ak

# Train new classifier
python train_embedding_classifier.py

# Deploy to classifier API
cp models/embedding_classifier_nomic-v2.pkl services/classifier-api/models/
docker compose -f services/classifier-api/docker-compose.yml restart
```

## Recommended Actions

### Short-term: Get More AK3/AK4 Data

1. Let LLM worker process the 908 backlog items
2. Search database for healthcare/disability keywords
3. Manually review and correct AK assignments
4. Target: At least 30 examples each for AK3 and AK4

### Long-term: Automated Rebalancing

1. Monitor AK distribution in production
2. Flag underrepresented categories
3. Prioritize manual review for weak categories
4. Periodic retraining with balanced data

## API Endpoints Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/classify` | POST | Classify text → relevance, priority, AK |
| `/search` | POST | Semantic search in vector store |
| `/similar` | POST | Find similar items by ID |
| `/index` | POST | Add single item to vector store |
| `/index/batch` | POST | Add multiple items to vector store |
| `/health` | GET | Service status + vector store count |

## Files

| Path | Description |
|------|-------------|
| `data/final/train.jsonl` | Training data (688 examples) |
| `data/final/validation.jsonl` | Validation data |
| `data/final/test.jsonl` | Test data |
| `models/embedding_classifier_nomic-v2.pkl` | Trained classifier |
| `services/classifier-api/` | Production API service |
