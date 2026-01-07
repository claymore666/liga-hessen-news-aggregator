# Training Data Creation Process

This document describes how training data is created for the Liga Hessen Relevance Classifier.

## Overview

```
Raw News Data → Ollama Labeling → Train/Val/Test Split → Fine-tuning → Ollama Model
```

## Data Sources

### News Articles

Collected from the news-aggregator system:

```bash
# Export from news aggregator API
curl -s "http://localhost:8000/api/items?relevant_only=false&page_size=500" | \
jq -c '.items[] | {
  input: {
    title: .title,
    content: (.content // "")[0:2000],
    source: (.source_name // "unknown"),
    date: (.published_at // "unknown")[0:10]
  },
  labels: { relevant: null, priority: null, ak: null, reaction_type: null },
  provenance: { source_type: "news", reasoning: null, affected_groups: [] }
}' > data/raw/news_unlabeled.jsonl
```

**Sources**: hessenschau, FAZ Rhein-Main, PRO ASYL, BMAS, Google Alerts, Social Media (X, Mastodon, Bluesky)

## Label Schema

```json
{
  "input": {
    "title": "Article title",
    "content": "Article content (max 2000 chars)",
    "source": "Source name",
    "date": "YYYY-MM-DD"
  },
  "labels": {
    "relevant": true/false,
    "priority": "critical|high|medium|low|null",
    "ak": "AK1|AK2|AK3|AK4|AK5|QAG|null",
    "reaction_type": null
  },
  "provenance": {
    "source_type": "news",
    "reasoning": "Why this classification",
    "affected_groups": []
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

### Priority Levels

| Priority | Trigger | Response Time |
|----------|---------|---------------|
| critical | Budget cuts, law introductions | < 24h |
| high | Funding deadlines, draft regulations | 1 week |
| medium | Policy statements, party positions | Monitor |
| low | Background info, general news | For reference |

## Labeling Pipeline

### Step 1: Prepare Batches

```bash
# Split unlabeled items into batches of 50
split -l 50 data/raw/news_unlabeled.jsonl data/raw/batches/batch_

# Rename to numbered format
cd data/raw/batches
i=0; for f in batch_*; do mv "$f" "batch_$(printf '%02d' $i).jsonl"; ((i++)); done
```

### Step 2: Label with Ollama

Uses local Ollama with qwen3:14b-q8_0 for labeling:

```bash
source venv/bin/activate

# Label all batches
python scripts/label_with_ollama.py --all

# Or resume from where you left off
python scripts/label_with_ollama.py --all --resume

# Or label specific batch
python scripts/label_with_ollama.py --batch 5
```

**Performance**: ~16-20 items/min on RTX 3090 with qwen3:14b-q8_0

**Output**: `data/reviewed/ollama_results/batch_XX_labeled.jsonl`

### Step 3: Create Splits

```bash
python scripts/create_splits.py
```

Creates stratified train/val/test splits (70/15/15) maintaining class balance.

**Output**:
- `data/final/train.jsonl`
- `data/final/validation.jsonl`
- `data/final/test.jsonl`
- `data/final/stats.json`

### Step 4: Train Model

```bash
python train_qwen3.py
```

Trains Qwen3-14B with LoRA adapters and exports to GGUF.

### Step 5: Import to Ollama

```bash
cd models/qwen3-trained/gguf
ollama create liga-relevance -f Modelfile
```

## Dataset Statistics (v2 - 2026-01-06)

| Split | Total | Relevant | Irrelevant |
|-------|-------|----------|------------|
| Train | 704 | 205 (29%) | 499 (71%) |
| Validation | 151 | 44 (29%) | 107 (71%) |
| Test | 153 | 45 (29%) | 108 (71%) |
| **Total** | **1008** | **294** | **714** |

**AK Distribution**:
- QAG: 101 (34%)
- AK2: 63 (21%)
- AK5: 56 (19%)
- AK1: 51 (17%)
- AK4: 12 (4%)
- AK3: 11 (4%)

**Priority Distribution**:
- medium: 199 (68%)
- high: 48 (16%)
- low: 44 (15%)
- critical: 3 (1%)

**Labeling**: qwen3:14b-q8_0 via Ollama

## Labeling Script Details

### `scripts/label_with_ollama.py`

Key features:
- Processes items in chunks of 10 for accuracy
- Progress bar with ETA
- Automatic retry on failures
- Resume support (skip completed batches)

Options:
```bash
--batch N       # Process single batch
--all           # Process all batches
--model MODEL   # Ollama model (default: qwen3:14b-q8_0)
--resume        # Skip completed batches
--dry-run       # Show what would be done
```

### `scripts/create_splits.py`

Key features:
- Stratified splitting (maintains relevant/irrelevant ratio)
- Deduplication by title
- Stats generation

Options:
```bash
--include-old   # Merge with previously labeled data
```

## Quality Control

### Spot Check Categories

After labeling, verify:

1. **Critical items** - Budget cuts, law changes marked critical
2. **DRK mentions** - Attacks on rescue workers → relevant (DRK is Liga member)
3. **Antisemitism** - Reports → relevant (Jewish communities are Liga member)
4. **Sports/Crime** - General news → irrelevant
5. **Edge cases** - Cultural events even if DRK/Jewish → usually irrelevant

### Expected Ratios

- ~25-35% relevant (for mixed news feed)
- Sports, crime, international news → irrelevant
- Welfare policy, Liga members, target groups → relevant

## Extending the Dataset

1. Export new items from news-aggregator
2. Split into batches
3. Run labeling: `python scripts/label_with_ollama.py --all --resume`
4. Regenerate splits: `python scripts/create_splits.py`
5. Retrain: `python train_qwen3.py`

## Files Reference

| File | Purpose |
|------|---------|
| `LABELING_PROMPT.md` | Full labeling instructions (German) |
| `scripts/label_with_ollama.py` | Ollama batch labeling |
| `scripts/create_splits.py` | Train/val/test splitting |
| `train_qwen3.py` | Model fine-tuning |
| `data/final/stats.json` | Current dataset statistics |
