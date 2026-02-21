# Embedding Classifier Training Guide

Complete step-by-step guide for retraining the embedding classifier. This is the primary classification model that decides relevance, priority, and AK assignment for incoming news items.

## What Gets Retrained

The embedding classifier is a **scikit-learn Random Forest** that runs on top of **nomic-v2 embeddings** (768-dim vectors). It classifies items in three stages:

```
Article text → nomic-v2 embedding (768d) → Stage 1: Relevant? (yes/no)
                                          → Stage 2: Priority (low/medium/high)
                                          → Stage 3: AK assignment (AK1-5, QAG)
```

The model file is a single `.pkl` file (~14 MB) containing all three classifiers.

**What does NOT get retrained:**
- The nomic-v2 embedding model itself (pre-trained, downloaded from HuggingFace)
- The paraphrase-mpnet duplicate detection model (separate, no training needed)
- The LLM (Ollama qwen3:14b-q8_0, uses system prompt, no fine-tuning)
- ChromaDB vector/duplicate stores (populated at runtime, not trained)

## Prerequisites

### Environment Setup

```bash
cd /home/kamienc/claude.ai/ligahessen/relevance-tuner

# Create venv if it doesn't exist
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Required Python Packages

The training script needs: `scikit-learn`, `numpy`, `sentence-transformers`, `torch`, `tqdm`

These are all in `requirements.txt`.

### Hardware Requirements

- **GPU**: Optional for training (sklearn is CPU-only), but the nomic-v2 embedding step is much faster with GPU
- **RAM**: ~4 GB for embedding + training
- **Disk**: ~500 MB for training data + model files
- **Duration**: ~5-10 minutes total (export + embed + train)

## Training Workflow

### Overview

```
Production DB (docker-ai) → SSH tunnel → Export script → train/val/test JSONL
                                                              ↓
                                                     Train classifier
                                                              ↓
                                                     New .pkl model
                                                              ↓
                                                     Deploy to container
                                                              ↓
                                                     Verify via /health
```

### Step 1: Export Training Data from Production

Training data comes from the production database on docker-ai. Items processed by the LLM already have curated relevance, priority, and AK labels — no manual labeling needed.

The export script automatically:
- **Excludes items not yet LLM-processed** (items with `needs_llm_processing=true` or no summary)
- **Fetches classifier-vs-LLM disagreements** from `/api/analytics/disagreements` and tags them
- **Forces disagreement items into the training split** (not val/test) so the classifier learns from its mistakes

**Option A: Via SSH tunnel (recommended)**

```bash
# Open SSH tunnel to docker-ai API
ssh -L 9000:localhost:8000 docker-ai -N -f

# Export with quality filters
cd /home/kamienc/claude.ai/ligahessen/relevance-tuner
source venv/bin/activate
API_URL=http://localhost:9000/api python scripts/export_training_data.py --min-content-length 200

# Close tunnel when done
pkill -f "ssh -L 9000:localhost:8000"
```

**Option B: From local database (gpu1)**

If the local database has recent data:

```bash
python scripts/export_training_data.py --min-content-length 200
```

**Dry run** (preview stats without writing files):

```bash
API_URL=http://localhost:9000/api python scripts/export_training_data.py --dry-run
```

**Output**: `data/final/train.jsonl`, `data/final/validation.jsonl`, `data/final/test.jsonl`, `data/final/stats.json`

**Splits**: 70% train / 15% validation / 15% test (stratified by relevance). Classifier-LLM disagreements are forced into the training split to maximize learning signal.

#### Export Filtering Options

| Flag | Purpose | Recommended |
|------|---------|-------------|
| `--min-content-length 200` | Skip items with sparse content (e.g., Eurostat notifications ~139 chars) | Yes |
| `--min-confidence 0.6` | Skip items where LLM had low confidence in its relevance score | Optional |

#### What Gets Exported

| Item Type | Condition | Label |
|-----------|-----------|-------|
| Relevant | LLM-processed, `priority` in [low, medium, high] | `relevant=true`, priority + AK labels |
| Irrelevant (classifier false positives) | LLM-processed, `priority = "none"`, has summary | `relevant=false` — these are items the classifier let through but the LLM rejected |
| Skipped | Items with `needs_llm_processing=true` or no summary | Not exported (no reliable ground truth) |
| Skipped | Items with `similar_to_id` (duplicates) | Not exported |
| Tagged | Classifier-LLM disagreements (from processing logs) | `provenance.is_disagreement=true`, forced into training split |

The majority of irrelevant training data (~3,700 items) consists of classifier false positives — items the classifier wrongly classified as relevant but the LLM correctly rejected. This is the most valuable training signal for improving the classifier.

#### Training Data Format (JSONL)

Each line is a JSON object:

```json
{
  "input": {
    "title": "Article title",
    "content": "Article content (max 5000 chars)",
    "source": "Source name",
    "date": "2026-02-21"
  },
  "labels": {
    "relevant": true,
    "priority": "medium",
    "ak": "AK3",
    "aks": ["AK3", "AK1"]
  },
  "provenance": {
    "item_id": 12345,
    "exported_at": "2026-02-21T12:00:00"
  }
}
```

### Step 2: Evaluate Current Model (Baseline)

Before training a new model, evaluate the **current deployed model** against the new test set. This gives a fair apples-to-apples comparison.

```bash
# Copy current model from classifier container
docker cp liga-classifier:/app/models/embedding_classifier_nomic-v2.pkl \
  models/embedding/embedding_classifier_nomic-v2.pkl.old

# Evaluate old model on new test data
EMBEDDING_BACKEND=nomic-v2 python scripts/evaluate_model.py \
  --model models/embedding/embedding_classifier_nomic-v2.pkl.old \
  --label baseline
```

This saves baseline metrics to `models/embedding/metrics.json` for later comparison.

### Step 3: Train Classifier

**CRITICAL**: Always set `EMBEDDING_BACKEND=nomic-v2`! The default backend (`ollama`) uses a completely different embedding model with incompatible vectors.

```bash
EMBEDDING_BACKEND=nomic-v2 python train_embedding_classifier.py
```

**What happens during training:**

1. Loads `data/final/train.jsonl` + `validation.jsonl` for training
2. Loads `data/final/test.jsonl` for evaluation
3. Computes nomic-v2 embeddings for all texts (the slow part, ~2-3 min)
4. Trains three classifiers:
   - **LogisticRegression** for relevance (binary, balanced class weights)
   - **RandomForestClassifier** (n=300, depth=30) for priority (3-class)
   - **RandomForestClassifier** (n=300, depth=30) for AK (6-class)
5. Evaluates on test set and prints classification report
6. Saves model to `models/embedding/embedding_classifier_nomic-v2.pkl`
7. Appends metrics to `models/embedding/metrics.json` (training history)

**Output includes:**
- Relevance accuracy + F1 score
- Priority accuracy + within-1-level accuracy
- AK accuracy + per-class breakdown
- Speed benchmark (items/sec)
- Example predictions on test cases
- Comparison with previous training runs

### Step 4: Deploy to Classifier Container

The classifier API container on gpu1 volume-mounts `/app/models/`. Copy the new model in:

```bash
# Backup current model inside the container
docker exec liga-classifier cp /app/models/embedding_classifier_nomic-v2.pkl \
  /app/models/embedding_classifier_nomic-v2.pkl.backup-$(date +%Y%m%d)

# Copy new model into the container
docker cp models/embedding/embedding_classifier_nomic-v2.pkl \
  liga-classifier:/app/models/embedding_classifier_nomic-v2.pkl

# Also copy metrics
docker cp models/embedding/metrics.json \
  liga-classifier:/app/models/metrics.json
```

The classifier API hot-reloads the model on the next classification request — no container restart needed.

### Step 5: Verify

```bash
# Health check (should show search_index_items count)
curl -s http://localhost:8082/health | jq

# Test classification with a clearly relevant article
curl -s -X POST http://localhost:8082/classify \
  -H "Content-Type: application/json" \
  -d '{"title": "Hessen kürzt Kita-Mittel um 50 Millionen Euro", "content": "Die Landesregierung plant massive Kürzungen bei der Kinderbetreuung."}' | jq

# Test with a clearly irrelevant article
curl -s -X POST http://localhost:8082/classify \
  -H "Content-Type: application/json" \
  -d '{"title": "Champions League: Bayern München gewinnt 3:0", "content": "Mit einem souveränen Sieg qualifiziert sich der FC Bayern für das Viertelfinale."}' | jq
```

**Expected results:**
- Kita article → `relevant: true`, priority `high`, AK `AK5`
- Bayern article → `relevant: false`

### Step 6: Monitor in Production

After deployment, monitor the classifier's performance on real items:

```bash
# Check processing analytics for classifier disagreements with LLM
ssh docker-ai 'curl -s http://localhost:8000/api/analytics/disagreements | jq .'

# Check recent items and their classifications
ssh docker-ai 'curl -s "http://localhost:8000/api/items?page_size=10" | jq ".items[] | {title: .title, priority: .priority, source: .source.name}"'
```

## Rollback

If the new model performs worse:

```bash
# Restore from the backup we made before training
docker cp /home/kamienc/claude.ai/ligahessen/backups/2026-02-21/classifier-model.tar.gz /tmp/
docker exec liga-classifier bash -c "cd /app/models && tar xzf /tmp/classifier-model.tar.gz"

# Or restore from the in-container backup
docker exec liga-classifier cp /app/models/embedding_classifier_nomic-v2.pkl.backup-YYYYMMDD \
  /app/models/embedding_classifier_nomic-v2.pkl
```

## Metrics History

Training metrics are appended to `models/embedding/metrics.json` with each run. The training script shows a comparison table at the end.

**Last known metrics** (nomic-v2, Jan 20 2026, 3519 training items):

| Metric | Value |
|--------|-------|
| Relevance accuracy | 89.6% |
| Priority accuracy | 56.1% |
| Priority within-1-level | 95.9% |
| AK accuracy | 69.9% |
| Speed | 126.8 items/sec |

**Target improvements** with more training data (expected ~6000+ items from prod):
- Relevance accuracy: >92%
- AK accuracy: >75% (more examples per AK class)

## Configuration Reference

### Embedding Backend (nomic-v2)

| Parameter | Value |
|-----------|-------|
| Model | `nomic-ai/nomic-embed-text-v2-moe` |
| Dimensions | 768 |
| Max text length | 2000 chars |
| Task prefix | `search_document: ` |
| Batch size | 16 |

### Classifier Hyperparameters (from `config.py`)

| Parameter | Value | Used by |
|-----------|-------|---------|
| `lr_c` | 0.5 | Relevance (LogisticRegression) |
| `lr_max_iter` | 1000 | Relevance |
| `rf_n_estimators` | 300 | Priority + AK (RandomForest) |
| `rf_max_depth` | 30 | Priority + AK |
| `class_weight` | `balanced` | All classifiers |

### AK Categories

| AK | Full Name | Topics |
|----|-----------|--------|
| AK1 | Grundsatz und Sozialpolitik | Budget, funding, general social policy |
| AK2 | Migration und Flucht | Refugees, asylum, integration |
| AK3 | Gesundheit, Pflege und Senioren | Healthcare, nursing, elderly care |
| AK4 | Eingliederungshilfe | Disability services, inclusion |
| AK5 | Kinder, Jugend, Frauen und Familie | Childcare, youth, family |
| QAG | Querschnitt | Digitalization, climate, housing |

### Priority Levels

| Priority | Meaning | Examples |
|----------|---------|----------|
| high | Immediate action needed | Budget cuts, new legislation, deadlines |
| medium | Relevant policy developments | Party positions, coalition talks, statements |
| low | Background information | General news, press releases, reports |

## File Reference

| File | Purpose |
|------|---------|
| `scripts/export_training_data.py` | Export labeled items from prod DB to JSONL |
| `train_embedding_classifier.py` | Train the embedding classifier |
| `config.py` | Hyperparameters, AK/priority definitions, backend configs |
| `utils/embeddings.py` | Embedding backends (nomic-v2, ollama, sentence-transformers, etc.) |
| `utils/data_loading.py` | Training data loading utilities |
| `models/embedding/embedding_classifier_nomic-v2.pkl` | Trained model (output) |
| `models/embedding/metrics.json` | Training metrics history |
| `data/final/train.jsonl` | Training split (70%) |
| `data/final/validation.jsonl` | Validation split (15%) |
| `data/final/test.jsonl` | Test split (15%) |
| `data/final/stats.json` | Dataset statistics |

## Troubleshooting

### "No items found!" during export

- Check that the API is reachable: `curl -s http://localhost:8000/api/items?page_size=1 | jq .total`
- If using SSH tunnel: verify `ssh -L 9000:localhost:8000 docker-ai` is running
- Set `API_URL=http://localhost:9000/api` when using the tunnel

### Wrong embedding backend

If accuracy drops dramatically, you may have trained with the wrong backend:

```bash
# Check which backend was used (in metrics.json)
cat models/embedding/metrics.json | jq 'keys'

# Always use nomic-v2 for production
EMBEDDING_BACKEND=nomic-v2 python train_embedding_classifier.py
```

### CUDA out of memory

The embedding step uses GPU if available. If OOM:

```bash
# Force CPU-only embeddings
CUDA_VISIBLE_DEVICES="" EMBEDDING_BACKEND=nomic-v2 python train_embedding_classifier.py
```

This is slower but works with any amount of RAM.

### Model file not found in container

```bash
# Check what's in the container
docker exec liga-classifier ls -la /app/models/

# Check volume mounts
docker inspect liga-classifier | jq '.[0].Mounts'
```

### Low AK accuracy

Some AKs have very few training examples (AK4: ~25, QAG: ~30). With more production data these should improve. If specific AKs remain low, consider:

- Adding more labeled examples for underrepresented AKs
- Adjusting `class_weight` in RandomForest config
- Using the multi-label classifier (`experiments/train_multilabel_classifier.py`)
