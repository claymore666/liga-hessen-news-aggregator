# Model Retraining Guide

Complete guide for retraining the embedding classifier and (optionally) the LLM when training data changes.

## Classifier-API Architecture

The classifier-api has three components using two different embedding models:

| Component | Model | Purpose | Trained? |
|-----------|-------|---------|----------|
| **EmbeddingClassifier** | nomic-v2 + sklearn RF | Relevance/Priority/AK classification | Yes (pkl file) |
| **VectorStore** | nomic-v2 + ChromaDB | Semantic search | No (just stores embeddings) |
| **DuplicateStore** | paraphrase-mpnet + ChromaDB | Duplicate/same-story detection | No (similarity search) |

**This guide covers retraining the EmbeddingClassifier only.** The other components don't require training - they use raw embedding similarity.

---

## Embedding Classifier Retraining (Primary)

The embedding classifier is the primary classification system. Retrain when:
- Training data significantly changes
- Classification accuracy drops
- New AK categories are added

### Step 1: Export Training Data

**Option A: From local database (gpu1)**

```bash
cd /home/kamienc/claude.ai/relevance-tuner/relevance-tuner
source venv/bin/activate

# Export from local database
python scripts/export_training_data.py

# With quality filters (recommended)
python scripts/export_training_data.py --min-content-length 200
```

**Option B: From remote database (docker-ai)**

```bash
# Create SSH tunnel to docker-ai API
ssh -L 9000:localhost:8000 docker-ai -N -f

# Export via tunnel
API_URL=http://localhost:9000/api python scripts/export_training_data.py --min-content-length 200

# Close tunnel when done
pkill -f "ssh -L 9000:localhost:8000"
```

This creates `data/final/{train,validation,test}.jsonl` with current labeled data.

### Step 2: Train Classifier

**CRITICAL**: Must set `EMBEDDING_BACKEND=nomic-v2` to use the correct embedding model!

```bash
EMBEDDING_BACKEND=nomic-v2 python train_embedding_classifier.py
```

**Output**: `models/embedding/embedding_classifier_nomic-v2.pkl`

Metrics are logged to `models/embedding/metrics.json` with full history.

### Step 3: Deploy

The model is volume-mounted, so no rebuild is needed - just copy the file:

```bash
# Backup current model
cp services/classifier-api/models/embedding_classifier_nomic-v2.pkl \
   services/classifier-api/models/embedding_classifier_nomic-v2.pkl.backup-$(date +%Y%m%d)

# Deploy new model
cp models/embedding/embedding_classifier_nomic-v2.pkl services/classifier-api/models/
cp models/embedding/metrics.json services/classifier-api/models/
```

The container will pick up the new model on next classification request (hot reload).

### Step 4: Verify

```bash
# Check health
curl -s http://localhost:8082/health | jq

# Test classification
curl -s -X POST http://localhost:8082/classify \
  -H "Content-Type: application/json" \
  -d '{"title": "Hessen kürzt Kita-Mittel", "content": "Die Landesregierung..."}' | jq
```

### Rollback

If the new model performs poorly:

```bash
cp services/classifier-api/models/embedding_classifier_nomic-v2.pkl.backup-YYYYMMDD \
   services/classifier-api/models/embedding_classifier_nomic-v2.pkl
```

---

## LLM Retraining (Optional)

The LLM provides summaries and detailed analysis. Currently using **base model with system prompt** (recommended over fine-tuning).

### When to Consider LLM Fine-tuning

- If base model quality degrades
- If specific output patterns are needed
- If faster inference is required

### Prerequisites

1. **Stop news-aggregator backend** (frees GPU):
   ```bash
   cd /home/kamienc/claude.ai/ligahessen/news-aggregator
   docker compose stop backend
   ```

2. **Unload Ollama models**:
   ```bash
   ollama stop qwen3:14b-q8_0
   ```

3. **Verify GPU is free** (~500MB or less):
   ```bash
   nvidia-smi --query-gpu=memory.used --format=csv,noheader
   ```

### LLM Fine-tuning Steps

#### Step 1: Relabel Training Data

```bash
cd /home/kamienc/claude.ai/relevance-tuner/relevance-tuner
source venv/bin/activate

# Use larger model for better quality labels
python scripts/label_with_ollama.py --all --model qwen3:32b
```

**Duration**: ~3 hours for 1000 items

#### Step 2: Create Splits

```bash
python scripts/create_splits.py
```

#### Step 3: Train Model

```bash
python train_qwen3.py
```

**Duration**: ~45 min for 700 training examples

#### Step 4: Merge and Convert

```bash
# Manual merge with PEFT (Unsloth's 8-bit merge is broken)
python << 'EOF'
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
import torch
import os

print("Loading 16-bit base model on CPU...")
base_model = AutoModelForCausalLM.from_pretrained(
    "Qwen/Qwen3-14B",
    torch_dtype=torch.bfloat16,
    device_map="cpu",
)

tokenizer = AutoTokenizer.from_pretrained("models/qwen3-trained/lora_adapter")

print("Loading LoRA adapter...")
model = PeftModel.from_pretrained(base_model, "models/qwen3-trained/lora_adapter")

print("Merging LoRA weights...")
model = model.merge_and_unload()

print("Saving merged model...")
output_dir = "models/qwen3-trained/merged_manual"
os.makedirs(output_dir, exist_ok=True)
model.save_pretrained(output_dir)
tokenizer.save_pretrained(output_dir)
print(f"Merged model saved to {output_dir}")
EOF

# Convert to GGUF q8_0
python llama.cpp/convert_hf_to_gguf.py models/qwen3-trained/merged_manual \
  --outfile models/qwen3-trained/gguf/liga-relevance-q8_0.gguf \
  --outtype q8_0
```

#### Step 5: Deploy to Ollama

```bash
cd models/qwen3-trained/gguf
ollama create liga-relevance -f Modelfile
```

#### Step 6: Restart Services

```bash
cd /home/kamienc/claude.ai/ligahessen/news-aggregator
docker compose up -d backend
```

---

## Automated Scripts

| Script | Purpose | Duration |
|--------|---------|----------|
| `scripts/full_retrain.sh` | Complete LLM pipeline: relabel → train → GGUF → deploy | ~5h |
| `scripts/auto_train_pipeline.sh` | Train → GGUF → deploy (skips relabeling) | ~1h |

```bash
# Run full retrain in background
nohup ./scripts/full_retrain.sh > /tmp/full_retrain.log 2>&1 &
tail -f /tmp/full_retrain.log
```

---

## Configuration Reference

### Embedding Classifier

| Parameter | Value |
|-----------|-------|
| Embedding model | nomic-ai/nomic-embed-text-v2-moe |
| Dimension | 768 |
| Classifier | Random Forest (n=300, depth=30) |
| Training examples | ~3500 |
| Model location | `models/embedding/embedding_classifier_nomic-v2.pkl` |
| Deployed location | `services/classifier-api/models/embedding_classifier_nomic-v2.pkl` |
| Metrics history | `models/embedding/metrics.json` |

### LLM (if fine-tuning)

| Parameter | Value |
|-----------|-------|
| Base model | Qwen3-14B (8-bit quantized) |
| Training method | LoRA (rank 16) |
| Batch size | 4 |
| Epochs | 3 |
| Output quantization | q8_0 (15.7GB GGUF) |

---

## Troubleshooting

### CUDA Out of Memory
- Stop news-aggregator backend
- Unload Ollama models
- Check `nvidia-smi` for other GPU processes

### Classifier Accuracy Drops
- Check if training data distribution changed
- Verify labels are correct
- Consider adding more training examples

### LLM Output Wrong Format
- Check `SYSTEM_PROMPT` in `train_qwen3.py` matches `Modelfile`
- Verify training data has correct format

### Wrong Embedding Backend
If you see incompatible embeddings or poor accuracy:
```bash
# Check which backend was used
grep -i backend models/embedding/metrics.json | tail -1

# Always use nomic-v2 for production
EMBEDDING_BACKEND=nomic-v2 python train_embedding_classifier.py
```

---

## Files Reference

| File | Purpose |
|------|---------|
| `train_embedding_classifier.py` | Train embedding classifier |
| `train_qwen3.py` | Train LLM (optional) |
| `scripts/export_training_data.py` | Export from PostgreSQL |
| `scripts/label_with_ollama.py` | Label with Ollama |
| `scripts/create_splits.py` | Create train/val/test splits |
| `LABELING_PROMPT.md` | Labeling instructions |
| `TRAINING_ISSUES.md` | Known issues and solutions |
