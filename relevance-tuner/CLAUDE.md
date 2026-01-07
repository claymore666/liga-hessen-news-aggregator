# Relevance Tuner - Claude Code Instructions

## Project Overview

Fine-tuning pipeline for Liga Hessen news relevance classification.

## Key Documentation

| File | Purpose |
|------|---------|
| `README.md` | Quick start, project structure, current stats |
| `LABELING_PROMPT.md` | Detailed labeling criteria for the LLM |
| `DATA_CREATION.md` | Full data pipeline documentation |

## Common Tasks

### Add More Training Data

```bash
# 1. Export from news-aggregator
curl -s "http://localhost:8000/api/items?page_size=500" | jq -c '.items[]' > data/raw/new_items.jsonl

# 2. Split into batches
split -l 50 data/raw/new_items.jsonl data/raw/batches/batch_

# 3. Label with Ollama
source venv/bin/activate
python scripts/label_with_ollama.py --all --resume

# 4. Create splits
python scripts/create_splits.py

# 5. Train
python train_qwen3.py
```

### Retrain Model

```bash
source venv/bin/activate
python train_qwen3.py

# Import to Ollama
cd models/qwen3-trained/gguf
ollama create liga-relevance -f Modelfile
```

## Current Dataset

- **1008 items** labeled with qwen3:14b-q8_0
- **29% relevant**, 71% irrelevant
- Stratified splits: 70/15/15

## Training Config

- Base: Qwen3-14B (4-bit quantized)
- Method: LoRA rank 16
- Batch size: 6 (fits in 24GB VRAM)
- Epochs: 3
- Output: GGUF q4_k_m for Ollama

## Paths

| Path | Content |
|------|---------|
| `data/raw/batches/` | Unlabeled input batches |
| `data/reviewed/ollama_results/` | Labeled output |
| `data/final/` | Train/val/test splits |
| `models/qwen3-trained/` | Fine-tuned model |
| `scripts/` | Labeling and data scripts |

## Ollama Models Used

- **Labeling**: `qwen3:14b-q8_0` (~16 items/min)
- **Output**: `liga-relevance` (fine-tuned classifier)
