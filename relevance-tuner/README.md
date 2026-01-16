# Liga Hessen Relevance Tuner

Fine-tuned LLM and embedding classifier for news relevance classification for the Liga der Freien Wohlfahrtspflege Hessen.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         NEWS CLASSIFICATION SYSTEM                          │
└─────────────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────┐
                    │   news-aggregator   │
                    │      (backend)      │
                    └──────────┬──────────┘
                               │
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
   ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
   │  Ollama LLM     │ │ Classifier API  │ │ Classifier API  │
   │  qwen3:14b-q8_0 │ │ (nomic-v2)      │ │ (paraphrase)    │
   │                 │ │                 │ │                 │
   │  • Summary      │ │  • Relevance    │ │  • Duplicate    │
   │  • Analysis     │ │  • Priority     │ │    Detection    │
   │  • Tags         │ │  • AK (multi)   │ │  • Threshold    │
   └─────────────────┘ └─────────────────┘ │    0.75         │
                                           └─────────────────┘
```

## Quick Start

```bash
# Activate environment
source venv/bin/activate

# Label new data with local Ollama
python scripts/label_with_ollama.py --all --model qwen3:14b-q8_0

# Create train/val/test splits
python scripts/create_splits.py

# Train embedding classifier
python train_embedding_classifier.py

# Deploy to classifier-api
docker compose -f services/classifier-api/docker-compose.yml up -d --build
```

## Project Structure

```
relevance-tuner/
├── data/
│   ├── raw/
│   │   ├── batches/           # Input batches for labeling
│   │   └── news_unlabeled.jsonl
│   ├── reviewed/
│   │   └── ollama_results/    # Labeled output from Ollama
│   └── final/
│       ├── train.jsonl        # Training split (70%)
│       ├── validation.jsonl   # Validation split (15%)
│       ├── test.jsonl         # Test split (15%)
│       └── stats.json
├── scripts/
│   ├── label_with_ollama.py   # Batch labeling with local LLM
│   ├── create_splits.py       # Create train/val/test splits
│   ├── export_training_data.py # Export from news-aggregator DB
│   ├── sync_vectordb.py       # Sync vector store with DB
│   └── compare_classifier_vs_llm.py  # Evaluate classifier accuracy
├── services/
│   └── classifier-api/        # FastAPI embedding classifier service
│       ├── classifier.py      # EmbeddingClassifier + VectorStore + DuplicateStore
│       ├── main.py            # API endpoints
│       └── Dockerfile
├── models/
│   └── embedding_classifier_nomic-v2.pkl  # Trained classifier
├── train_embedding_classifier.py  # Embedding classifier training
├── train_qwen3.py             # LLM fine-tuning (optional)
├── LABELING_PROMPT.md         # Detailed labeling instructions
└── DATA_CREATION.md           # Data pipeline documentation
```

## Current Dataset (v3 - 2026-01-13)

| Split | Total | Relevant | Irrelevant |
|-------|-------|----------|------------|
| Train | 1175 | 157 (13%) | 1018 (87%) |
| Validation | 252 | 34 (13%) | 218 (87%) |
| Test | 253 | 33 (13%) | 220 (87%) |
| **Total** | **1680** | **224** | **1456** |

### AK Distribution (Multi-label)

| AK | Count | Description |
|----|-------|-------------|
| AK1 | 78 | Grundsatz und Sozialpolitik |
| AK2 | 70 | Migration und Flucht |
| AK5 | 37 | Kinder, Jugend, Familie |
| AK3 | 18 | Gesundheit, Pflege, Senioren |
| QAG | 11 | Querschnitt (Digitalisierung, Wohnen, Klima) |
| AK4 | 10 | Eingliederungshilfe |

### Priority Distribution (3-tier)

| Priority | Count | Description |
|----------|-------|-------------|
| low | 145 | Background info, general news |
| medium | 55 | Policy statements, party positions |
| high | 24 | Budget cuts, law changes, deadlines |

## Classifier API

GPU-accelerated embedding classifier running on port 8082.

### Models

| Model | Purpose | Dimension |
|-------|---------|-----------|
| `nomic-ai/nomic-embed-text-v2-moe` | Classification & semantic search | 768d |
| `paraphrase-multilingual-mpnet-base-v2` | Duplicate detection | 768d |

### Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check with store stats |
| `/classify` | POST | Classify article (relevance, priority, AKs) |
| `/search` | POST | Semantic search |
| `/find-duplicates` | POST | Find similar articles (threshold 0.75) |
| `/index` | POST | Index single item |
| `/index/batch` | POST | Batch index items |

### Example

```bash
# Classify an article
curl -s -X POST http://localhost:8082/classify \
  -H "Content-Type: application/json" \
  -d '{"title": "Hessen kürzt Mittel für Kitas", "content": "Die Landesregierung plant Kürzungen..."}' | jq

# Response
{
  "relevant": true,
  "relevance_confidence": 0.92,
  "priority": "high",
  "priority_confidence": 0.85,
  "ak": "AK5",
  "ak_confidence": 0.78,
  "aks": ["AK5", "AK1"],
  "ak_confidences": {"AK5": 0.78, "AK1": 0.45}
}
```

## LLM Analysis (Ollama)

For detailed analysis (summary, reasoning, tags), the backend uses `qwen3:14b-q8_0` with a system prompt.

```bash
# Check current model
curl -s "http://localhost:8000/api/llm/status" | jq '.model'

# Switch model
curl -X PUT "http://localhost:8000/api/llm/model" \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3:14b-q8_0"}'
```

## Training

### Embedding Classifier

```bash
source venv/bin/activate

# Export training data from production DB
python scripts/export_training_data.py

# Create splits
python scripts/create_splits.py

# Train classifier
python train_embedding_classifier.py

# Deploy
cd services/classifier-api
docker compose down && docker compose build && docker compose up -d
```

### LLM Fine-tuning (Optional)

See `RETRAINING.md` for full LLM fine-tuning workflow. Currently using base model with system prompt (better quality than fine-tuned).

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/export_training_data.py` | Export items from PostgreSQL to training format |
| `scripts/label_with_ollama.py` | Label batches with Ollama LLM |
| `scripts/create_splits.py` | Create stratified train/val/test splits |
| `scripts/sync_vectordb.py` | Sync vector store with database |
| `scripts/compare_classifier_vs_llm.py` | Compare classifier vs LLM accuracy |

## Storage

| Component | Size | Items |
|-----------|------|-------|
| PostgreSQL (liga_news) | ~24 MB | ~6400 |
| Vector store (nomic) | ~51 MB | ~2900 |
| Duplicate store (paraphrase) | ~45 MB | ~2900 |

## Requirements

- Python 3.11+
- CUDA GPU (RTX 3090 recommended)
- Docker & Docker Compose
- Ollama (for LLM inference)

### Python Dependencies

```bash
pip install -r requirements.txt
```
