# Training Data Creation Process

This document describes how training data is created for the Liga Hessen Relevance Classifier.

## Overview

```
PostgreSQL DB → Export Script → Train/Val/Test Split → Embedding Classifier Training
                                                     → (Optional) LLM Fine-tuning
```

## Data Sources

Training data is exported from the production news-aggregator PostgreSQL database:

```bash
source venv/bin/activate
python scripts/export_training_data.py
```

**Sources include**: hessenschau, FAZ Rhein-Main, PRO ASYL, BMAS, Google Alerts RSS, Social Media (X, Mastodon, Bluesky)

## Label Schema

```json
{
  "input": {
    "title": "Article title",
    "content": "Article content (max 2000 chars)",
    "source": "Source name"
  },
  "labels": {
    "relevant": true,
    "priority": "high",
    "aks": ["AK5", "AK1"]
  }
}
```

### Arbeitskreise (AK) Categories

| AK | Name | Topics |
|----|------|--------|
| AK1 | Grundsatz und Sozialpolitik | Budget, funding, general social policy |
| AK2 | Migration und Flucht | Refugees, asylum, integration services |
| AK3 | Gesundheit, Pflege und Senioren | Healthcare, nursing, elderly care |
| AK4 | Eingliederungshilfe | Disability services, inclusion |
| AK5 | Kinder, Jugend, Frauen und Familie | Childcare, youth, family services |
| QAG | Querschnitt | Digitalization, climate, housing |

**Note**: Items can have multiple AKs (multi-label classification).

### Priority Levels (3-tier)

| Priority | Trigger | Examples |
|----------|---------|----------|
| high | Budget cuts, law introductions, deadlines | Landeshaushalt, Gesetzesänderungen |
| medium | Policy statements, party positions | Koalitionsverhandlungen, Stellungnahmen |
| low | Background info, general news | Pressemitteilungen, Berichte |

## Data Pipeline

### Step 1: Export from Production Database

```bash
source venv/bin/activate
python scripts/export_training_data.py
```

Exports items that have been manually reviewed or LLM-processed with confirmed relevance.

**Output**: `data/final/train.jsonl`, `data/final/validation.jsonl`, `data/final/test.jsonl`

### Step 2: Create Splits (if using raw batches)

```bash
python scripts/create_splits.py
```

Creates stratified train/val/test splits (70/15/15) maintaining class balance.

**Output**:
- `data/final/train.jsonl`
- `data/final/validation.jsonl`
- `data/final/test.jsonl`
- `data/final/stats.json`

### Step 3: Train Embedding Classifier

```bash
python train_embedding_classifier.py
```

Trains scikit-learn classifiers on nomic-v2 embeddings.

**Output**: `services/classifier-api/models/embedding_classifier_nomic-v2.pkl`

### Step 4: Deploy Classifier

```bash
cd services/classifier-api
docker compose down && docker compose build && docker compose up -d
```

## Dataset Statistics (v3 - 2026-01-13)

| Split | Total | Relevant | Irrelevant |
|-------|-------|----------|------------|
| Train | 1175 | 157 (13%) | 1018 (87%) |
| Validation | 252 | 34 (13%) | 218 (87%) |
| Test | 253 | 33 (13%) | 220 (87%) |
| **Total** | **1680** | **224** | **1456** |

**AK Distribution**:
| AK | Count |
|----|-------|
| AK1 | 78 |
| AK2 | 70 |
| AK5 | 37 |
| AK3 | 18 |
| QAG | 11 |
| AK4 | 10 |

**Priority Distribution**:
| Priority | Count |
|----------|-------|
| low | 145 |
| medium | 55 |
| high | 24 |

## Alternative: LLM Labeling (for new data)

If you need to label new unlabeled data:

### Prepare Batches

```bash
# Split unlabeled items into batches of 50
split -l 50 data/raw/news_unlabeled.jsonl data/raw/batches/batch_

# Rename to numbered format
cd data/raw/batches
i=0; for f in batch_*; do mv "$f" "batch_$(printf '%02d' $i).jsonl"; ((i++)); done
```

### Label with Ollama

```bash
source venv/bin/activate

# Label all batches
python scripts/label_with_ollama.py --all

# Resume from where you left off
python scripts/label_with_ollama.py --all --resume

# Use different model
python scripts/label_with_ollama.py --all --model qwen3:32b
```

**Performance**: ~16-20 items/min on RTX 3090 with qwen3:14b-q8_0

**Output**: `data/reviewed/ollama_results/batch_XX_labeled.jsonl`

## Quality Control

### Spot Check Categories

After labeling, verify:

1. **High priority items** - Budget cuts, law changes marked high
2. **DRK mentions** - Attacks on rescue workers → relevant (DRK is Liga member)
3. **Antisemitism** - Reports → relevant (Jewish communities are Liga member)
4. **Sports/Crime** - General news → irrelevant
5. **Edge cases** - Cultural events even if DRK/Jewish → usually irrelevant

### Expected Ratios

- ~10-15% relevant (for production news feed)
- Sports, crime, international news → irrelevant
- Welfare policy, Liga members, target groups → relevant

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/export_training_data.py` | Export from PostgreSQL to training format |
| `scripts/create_splits.py` | Create stratified train/val/test splits |
| `scripts/label_with_ollama.py` | Label batches with Ollama LLM |
| `scripts/sync_vectordb.py` | Sync vector store with database |
| `scripts/compare_classifier_vs_llm.py` | Evaluate classifier vs LLM accuracy |
| `train_embedding_classifier.py` | Train embedding classifier |

## Files Reference

| File | Purpose |
|------|---------|
| `LABELING_PROMPT.md` | Full labeling instructions (German) |
| `data/final/stats.json` | Current dataset statistics |
| `services/classifier-api/models/*.pkl` | Trained classifier models |
