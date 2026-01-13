# Embedding Classifier Training Guide

Complete guide for training and retraining the embedding-based classifier used for fast pre-filtering of news items.

## Overview

The system uses a **two-stage classification pipeline**:

1. **Embedding Classifier** (this doc) - Fast pre-filtering using ML
   - NomicV2 embeddings (768-dim vectors)
   - RandomForest classifiers for relevance, priority, AK
   - ~33 items/sec throughput

2. **LLM Classifier** - Detailed analysis for relevant items
   - Qwen3-14B with system prompt
   - Full analysis: summary, argumentation, affected groups
   - See `RETRAINING.md` for LLM training

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Classification Pipeline                       │
├─────────────────────────────────────────────────────────────────┤
│  News Item                                                       │
│      ↓                                                           │
│  [Embedding Classifier] ──→ relevant=false ──→ Skip LLM         │
│      ↓ relevant=true                                            │
│  [LLM Classifier] ──→ Full analysis (priority, AK, summary)     │
│      ↓                                                           │
│  Database (curated labels)                                       │
└─────────────────────────────────────────────────────────────────┘
```

## Training Data Sources

### Option 1: Export from Production Database (Recommended)

Items processed by the LLM already have curated labels. Export directly:

```bash
cd /home/kamienc/claude.ai/relevance-tuner/relevance-tuner
source venv/bin/activate

# Preview what will be exported
python scripts/export_training_data.py --dry-run

# Export to data/final/
python scripts/export_training_data.py
```

**What gets exported:**
- **Relevant items**: Have `priority` in [low, medium, high, critical] + `assigned_ak`
- **Irrelevant items**: Have `priority: "none"` - no LLM processing needed

### Option 2: Manual Labeling with Ollama

For new unlabeled data, use the Ollama labeling pipeline:

```bash
# Export raw items
curl -s "http://localhost:8000/api/items?page_size=500" | \
  jq -c '.items[]' > data/raw/new_items.jsonl

# Label with Ollama
python scripts/label_with_ollama.py --all --model qwen3:32b

# Create splits
python scripts/create_splits.py
```

## Multi-Label Classification

### The Challenge

As of 2026-01, items can be assigned to **multiple AKs** (Arbeitskreise). Example:
- "Durchleuchtet das Pflegebudget!" → AK1 (budget) + AK3 (healthcare)

**Current distribution** (from 224 relevant items):
- 39 items (17.4%) have multiple AKs
- Most common: AK1+AK3, AK1+AK5, AK2+AK5

### Single-Label vs Multi-Label

| Approach | Implementation | Accuracy Trade-off |
|----------|----------------|-------------------|
| **Single-label** | `RandomForestClassifier` | Loses 17% of AK associations |
| **Multi-label** | `MultiOutputClassifier` | Captures all AK associations |

### Multi-Label Implementation

The experimental multi-label classifier uses:

```python
from sklearn.multioutput import MultiOutputClassifier
from sklearn.ensemble import RandomForestClassifier

# Binary matrix: each column = one AK
# [1,0,0,1,0,0] = AK1 + AK4
y_ak = create_multilabel_matrix(aks_list, AK_CLASSES)

ak_clf = MultiOutputClassifier(
    RandomForestClassifier(n_estimators=200, max_depth=15)
)
ak_clf.fit(X_embeddings, y_ak)
```

**Files:**
- `train_embedding_classifier.py` - Single-label (production)
- `experiments/train_multilabel_classifier.py` - Multi-label (experimental)

## Training Workflow

### Prerequisites

No GPU needed for embedding classifier training (CPU-only sklearn).

### Step-by-Step

```bash
cd /home/kamienc/claude.ai/relevance-tuner/relevance-tuner
source venv/bin/activate

# 1. Export training data from production
python scripts/export_training_data.py

# 2. Train classifier (uses EMBEDDING_BACKEND env var)
EMBEDDING_BACKEND=nomic-v2 python train_embedding_classifier.py

# 3. Deploy to classifier API
cp models/embedding/embedding_classifier_nomic-v2.pkl \
   /home/kamienc/claude.ai/ligahessen/relevance-tuner/services/classifier-api/models/

# 4. Restart classifier API
cd /home/kamienc/claude.ai/ligahessen/relevance-tuner/services/classifier-api
docker compose restart
```

### Comparing Models

To compare classifier predictions against LLM ground truth:

```bash
# Run comparison
python scripts/compare_classifier_vs_llm.py

# Output includes:
# - Relevance accuracy
# - AK accuracy (exact match and partial overlap)
# - Priority accuracy
# - Confusion matrices
```

## Backup & Rollback

### Creating Backups

```bash
# Backup classifiers + training data
mkdir -p models/backups/$(date +%Y%m%d)
cp models/embedding/*.pkl models/backups/$(date +%Y%m%d)/
tar -czvf models/backups/$(date +%Y%m%d)/training_data.tar.gz data/final/
```

### Rolling Back

```bash
# Restore from backup
cp models/backups/YYYYMMDD/embedding_classifier_nomic-v2.pkl models/embedding/

# Redeploy
cp models/embedding/embedding_classifier_nomic-v2.pkl \
   /path/to/classifier-api/models/
```

## Embedding Backends

| Backend | Relevance Acc | AK Acc | Speed |
|---------|---------------|--------|-------|
| **nomic-v2** | 89.9% | 71.1% | 33/sec |
| jina-v3 | 87.9% | 57.9% | 55/sec |
| sentence-transformers | 85.9% | 63.2% | 675/sec |
| ollama (local) | 71.8% | 36.8% | 37/sec |

**Recommendation**: Use `nomic-v2` for best accuracy.

## Current Dataset Statistics

As of 2026-01-13:

| Metric | Value |
|--------|-------|
| Total items | 1680 |
| Relevant | 224 (13.3%) |
| Irrelevant | 1456 (86.7%) |
| Multi-AK items | 39 (17.4% of relevant) |

**AK Distribution (relevant only):**
| AK | Count |
|----|-------|
| AK1 | 78 |
| AK2 | 70 |
| AK5 | 37 |
| AK3 | 18 |
| QAG | 11 |
| AK4 | 10 |

## Files Reference

| Path | Description |
|------|-------------|
| `train_embedding_classifier.py` | Main training script (single-label) |
| `experiments/train_multilabel_classifier.py` | Multi-label experiment |
| `scripts/export_training_data.py` | Export from production DB |
| `scripts/compare_classifier_vs_llm.py` | Model comparison |
| `models/embedding/*.pkl` | Trained classifiers |
| `models/backups/YYYYMMDD/` | Dated backups |
| `data/final/` | Training/validation/test splits |
| `config.py` | AK_CLASSES, PRIORITY_LEVELS, backend configs |

## Troubleshooting

### Low AK Accuracy

- Check AK distribution - underrepresented classes perform worse
- AK3, AK4 historically have few examples
- Consider class weighting or oversampling

### Embedding Model Not Found

```bash
# For sentence-transformers backends
pip install sentence-transformers

# For Ollama backend
ollama pull nomic-embed-text:137m-v1.5-fp16
```

### Multi-Label vs Single-Label Mismatch

If production uses single-label but training data has multi-label:
- Use primary AK only: `assigned_ak` field (first AK)
- Or switch to multi-label classifier
