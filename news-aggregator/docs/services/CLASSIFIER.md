# Classifier Service

## Overview

The classifier provides fast ML-based classification using embedding models. It runs as a separate service on port 8082.

**Files**:
- `relevance-tuner/services/classifier-api/` - Classifier service
- `backend/services/relevance_filter.py` - Client wrapper
- `backend/services/classifier_worker.py` - Background worker

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Backend                               │
│  ┌───────────────────────────────────────────────────┐  │
│  │           RelevanceFilter (Client)                 │  │
│  │  - classify()      - search()                      │  │
│  │  - index_item()    - find_duplicates()             │  │
│  └─────────────────────────┬─────────────────────────┘  │
└─────────────────────────────┼───────────────────────────┘
                              │ HTTP
                              ▼
┌─────────────────────────────────────────────────────────┐
│              Classifier API (Port 8082)                  │
│  ┌───────────────────────────────────────────────────┐  │
│  │  EmbeddingClassifier                               │  │
│  │  - sklearn classifiers (relevance, priority, AK)   │  │
│  │  - nomic-embed-text-v2 embeddings                 │  │
│  └───────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────┐  │
│  │  VectorStore (ChromaDB)                            │  │
│  │  - Semantic search index                           │  │
│  └───────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────┐  │
│  │  DuplicateStore (ChromaDB)                         │  │
│  │  - Paraphrase embeddings for similarity            │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## Models

### Embedding Models

| Model | Purpose | Dimensions |
|-------|---------|------------|
| nomic-ai/nomic-embed-text-v2-moe | Classification & search | 768 |
| paraphrase-multilingual-mpnet-base-v2 | Duplicate detection | 768 |

### Sklearn Classifiers

Trained on labeled data:
- **relevance_clf** - Binary relevance (relevant/not)
- **priority_clf** - Priority level (high/medium/low/none)
- **ak_clf** - Multi-label AK assignment

## API Endpoints

### Health Check
```http
GET /health
```
Returns:
```json
{
  "status": "ok",
  "model": "nomic-v2",
  "gpu": true,
  "gpu_name": "NVIDIA GeForce RTX 3090",
  "search_index_items": 3152,
  "duplicate_index_items": 3152
}
```

### Classify
```http
POST /classify
{
  "title": "Article title",
  "content": "Article content",
  "source": "Source name"
}
```
Returns:
```json
{
  "relevant": true,
  "confidence": 0.87,
  "priority": "medium",
  "priority_confidence": 0.72,
  "assigned_aks": ["AK1", "AK3"],
  "ak_confidences": {"AK1": 0.85, "AK3": 0.65}
}
```

### Search
```http
POST /search
{
  "query": "Sozialpolitik Hessen",
  "limit": 10
}
```

### Find Duplicates
```http
POST /find-duplicates
{
  "title": "Article title",
  "content": "Article content",
  "threshold": 0.75
}
```
Returns:
```json
{
  "duplicates": [
    {"id": "123", "score": 0.92, "title": "Similar article"}
  ]
}
```

### Index Item
```http
POST /index
{
  "id": "item_123",
  "title": "Article title",
  "content": "Article content"
}
```

### Batch Index
```http
POST /index/batch
{
  "items": [
    {"id": "1", "title": "...", "content": "..."},
    {"id": "2", "title": "...", "content": "..."}
  ]
}
```

### Storage Stats
```http
GET /storage
```
Returns:
```json
{
  "search_index_size_bytes": 56326714,
  "search_index_items": 3152,
  "duplicate_index_size_bytes": 67015808,
  "duplicate_index_items": 3152
}
```

### Sync Duplicate Store
```http
POST /sync-duplicate-store
```
Copies all items from search index to duplicate index.

## Classification Pipeline

### 1. Text Embedding

```python
# Combine title and content
text = f"{title}\n\n{content[:4000]}"

# Generate embedding
embedding = model.encode(text)  # 768-dim vector
```

### 2. Classification

```python
# Relevance
relevant_prob = relevance_clf.predict_proba([embedding])[0][1]
relevant = relevant_prob > 0.5

# Priority
priority_probs = priority_clf.predict_proba([embedding])[0]
priority = ["none", "low", "medium", "high"][np.argmax(priority_probs)]

# AKs (multi-label)
ak_probs = ak_clf.predict_proba([embedding])
assigned_aks = [ak for ak, prob in zip(AK_LABELS, ak_probs) if prob > 0.5]
```

### 3. Confidence Thresholds

| Confidence | Action |
|------------|--------|
| > 0.75 | Use classifier result directly |
| 0.25 - 0.75 | Queue for LLM verification |
| < 0.25 | Mark as irrelevant |

## Duplicate Detection

### Similarity Thresholds

| Score | Interpretation |
|-------|----------------|
| > 0.97 | Exact duplicate (same article) |
| 0.75 - 0.97 | Same story, different source |
| 0.50 - 0.75 | Related topics |
| < 0.50 | Unrelated |

### Process

```python
# 1. Generate paraphrase embedding
embedding = paraphrase_model.encode(f"{title}\n{content}")

# 2. Search duplicate index
results = duplicate_store.query(embedding, n_results=5)

# 3. Filter by threshold
duplicates = [r for r in results if r.score > threshold]
```

## Backend Integration

### RelevanceFilter Client

```python
class RelevanceFilter:
    def __init__(self, base_url: str):
        self.base_url = base_url

    async def classify(self, title: str, content: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/classify",
                json={"title": title, "content": content}
            )
            return response.json()
```

### Classifier Worker

Background worker for batch classification:

```python
class ClassifierWorker:
    async def run(self):
        while True:
            items = await get_unclassified_items()
            for item in items:
                result = await classifier.classify(
                    item.title, item.content
                )
                await update_item(item, result)
```

## Training

See [relevance-tuner/CLAUDE.md](../../relevance-tuner/CLAUDE.md) for training details.

### Quick Retrain

```bash
cd relevance-tuner
python train_embedding_classifier.py
docker compose restart classifier
```

## Configuration

### Environment Variables

```bash
CLASSIFIER_API_URL=http://localhost:8082
CLASSIFIER_ENABLED=true
```

### Docker Compose

```yaml
classifier:
  build: ./relevance-tuner/services/classifier-api
  ports:
    - "8082:8082"
  volumes:
    - ./data/vectordb:/app/data/vectordb
    - ./data/duplicatedb:/app/data/duplicatedb
  deploy:
    resources:
      reservations:
        devices:
          - capabilities: [gpu]
```

## Monitoring

### Health Check

```bash
curl http://localhost:8082/health | jq
```

### GPU Usage

```bash
nvidia-smi --query-gpu=memory.used --format=csv,noheader
```

### Logs

```bash
docker compose logs classifier
```

## Troubleshooting

### Classifier Not Available
1. Check container: `docker compose ps classifier`
2. Check health: `curl http://localhost:8082/health`
3. Check logs: `docker compose logs classifier`

### Low Accuracy
1. Check training data quality
2. Verify label balance
3. Consider retraining with more data

### Slow Classification
1. Check GPU is being used
2. Reduce batch size
3. Check for memory issues
