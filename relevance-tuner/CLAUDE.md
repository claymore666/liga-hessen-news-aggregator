# Relevance Tuner - Claude Code Instructions

## CRITICAL RULES

1. **Output format changes require explicit user approval** - Never modify the LLM output JSON schema (fields, structure) without asking first
2. **Output changes require parser updates** - Any approved format change must also update the backend parser in `news-aggregator/backend/services/processor.py`

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

## Ollama Models Used

- **Labeling**: `qwen3:32b` (recommended, ~5 items/min) or `qwen3:14b` (~16 items/min)
- **Output**: `liga-relevance` (fine-tuned Qwen3-14B classifier)
