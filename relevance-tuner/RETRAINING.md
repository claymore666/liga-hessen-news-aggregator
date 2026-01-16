# Model Retraining Guide

Complete guide for retraining the embedding classifier and (optionally) the LLM when training data changes.

## Embedding Classifier Retraining (Primary)

The embedding classifier is the primary classification system. Retrain when:
- Training data significantly changes
- Classification accuracy drops
- New AK categories are added

### Step 1: Export Training Data

```bash
cd /home/kamienc/claude.ai/relevance-tuner/relevance-tuner
source venv/bin/activate

# Export from production database
python scripts/export_training_data.py
```

This creates `data/final/{train,validation,test}.jsonl` with current labeled data.

### Step 2: Train Classifier

```bash
python train_embedding_classifier.py
```

**Output**: `services/classifier-api/models/embedding_classifier_nomic-v2.pkl`

### Step 3: Deploy

```bash
cd services/classifier-api
docker compose down && docker compose build && docker compose up -d
```

### Step 4: Verify

```bash
# Check health
curl -s http://localhost:8082/health | jq

# Test classification
curl -s -X POST http://localhost:8082/classify \
  -H "Content-Type: application/json" \
  -d '{"title": "Hessen kürzt Kita-Mittel", "content": "Die Landesregierung..."}' | jq
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
| Classifier | Scikit-learn (Logistic Regression) |
| Training examples | ~1680 |

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
