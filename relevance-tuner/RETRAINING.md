# Model Retraining Process

Complete guide for retraining the `liga-relevance` model when training data, prompts, or output format changes.

## Prerequisites

1. **Stop news-aggregator backend** (frees GPU memory):
   ```bash
   cd /home/kamienc/claude.ai/ligahessen/news-aggregator
   docker compose stop backend
   ```

2. **Unload Ollama models**:
   ```bash
   ollama stop liga-relevance 2>/dev/null
   ollama stop qwen3:32b 2>/dev/null
   ```

3. **Verify GPU is free** (~500MB or less):
   ```bash
   nvidia-smi --query-gpu=memory.used --format=csv,noheader
   ```

## Files to Update When Changing Output Format

| File | What to change |
|------|----------------|
| `LABELING_PROMPT.md` | Output format definition, field descriptions |
| `scripts/label_with_ollama.py` | Parse new fields, update `merge_labels()` |
| `train_qwen3.py` | `SYSTEM_PROMPT`, `format_example()` response_obj |
| `models/qwen3-trained/gguf/Modelfile` | `SYSTEM` prompt for inference |
| `news-aggregator/backend/services/processor.py` | Parse new fields from LLM response |
| `news-aggregator/backend/models.py` | Add new database columns if needed |
| `news-aggregator/backend/schemas.py` | Add new fields to API schemas |
| `news-aggregator/frontend/src/types/index.ts` | Add TypeScript types |
| `news-aggregator/frontend/src/views/ItemDetailView.vue` | Display new fields |

## Step-by-Step Retraining

### Step 1: Update Format Definitions

Edit `LABELING_PROMPT.md` with new output format, then update all files listed above to match.

### Step 2: Relabel Training Data

```bash
cd /home/kamienc/claude.ai/ligahessen/relevance-tuner
source venv/bin/activate

# Use larger model for better quality labels
python scripts/label_with_ollama.py --all --model qwen3:32b
```

**Duration**: ~3 hours for 1000 items at 5 items/min

**Output**: `data/reviewed/ollama_results/batch_*_labeled.jsonl`

### Step 3: Create Train/Val/Test Splits

```bash
python scripts/create_splits.py
```

**Output**: `data/final/{train,val,test}.jsonl`

### Step 4: Train Model

```bash
# Ensure GPU is free first!
python train_qwen3.py
```

**Duration**: ~45 min for 700 training examples

**Output**: `models/qwen3-trained/`

### Step 5: Convert to GGUF

```bash
cd models/qwen3-trained
python -c "
from unsloth import FastLanguageModel
model, tokenizer = FastLanguageModel.from_pretrained('.')
model.save_pretrained_gguf('gguf', tokenizer, quantization_method='q4_k_m')
"
```

**Output**: `models/qwen3-trained/gguf/liga-relevance-q4_k_m.gguf`

### Step 6: Deploy to Ollama

```bash
cd models/qwen3-trained/gguf
ollama create liga-relevance -f Modelfile
```

### Step 7: Restart Backend & Reprocess

```bash
# Start backend
cd /home/kamienc/claude.ai/ligahessen/news-aggregator
docker compose up -d backend

# Wait for startup
sleep 10

# Reprocess all items with new model
curl -X POST "http://localhost:8000/api/items/reprocess?force=true"
```

**Duration**: ~1.5 hours for 1600 items at 3 sec/item

## Fully Automated Script

**One command to do everything** (runs ~5 hours total):

```bash
cd /home/kamienc/claude.ai/ligahessen/relevance-tuner
./scripts/full_retrain.sh
```

This script:
1. Stops backend & unloads Ollama models (frees GPU)
2. Relabels all training data (~3h)
3. Creates train/val/test splits
4. Trains the model (~45min)
5. Converts to GGUF
6. Deploys to Ollama
7. Starts backend & triggers reprocessing

**Run in background** (recommended):
```bash
nohup ./scripts/full_retrain.sh > /tmp/full_retrain.log 2>&1 &
tail -f /tmp/full_retrain.log
```

Logs are saved to `/tmp/retrain_YYYYMMDD_HHMMSS/`

## Verification

After retraining, test the model:

```bash
ollama run liga-relevance "Titel: Test Kürzungen im Sozialbereich
Inhalt: Die Landesregierung plant massive Kürzungen bei sozialen Einrichtungen...
Quelle: Hessenschau
Datum: 2026-01-09"
```

Expected output should include all defined fields (summary, detailed_analysis, etc.) with proper content.

## Troubleshooting

### CUDA Out of Memory
- Stop news-aggregator backend
- Unload Ollama models
- Check `nvidia-smi` for other GPU processes

### Training Too Slow
- Reduce batch size in `train_qwen3.py` (default: 6)
- Reduce `max_seq_length` (default: 4096)

### Model Outputs Wrong Format
- Check `SYSTEM_PROMPT` in `train_qwen3.py` matches `Modelfile`
- Verify training data has correct format in `data/final/train.jsonl`

## Current Configuration

| Parameter | Value |
|-----------|-------|
| Base model | Qwen3-14B |
| Training method | LoRA (rank 16) |
| Batch size | 6 |
| Epochs | 3 |
| Max sequence length | 4096 |
| Quantization | Q4_K_M |
| Training examples | ~700 |
