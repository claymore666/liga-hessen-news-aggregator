# Training Issues and Solutions

This document captures known issues, root causes, and solutions for the Liga Relevance model training pipeline.

## Issue #1: Model Produces Garbage Output ("zertrüten zertrüten...")

### Symptoms
- Fine-tuned model produces repeated nonsense tokens
- Training metrics look normal (loss decreasing, eval_loss reasonable)
- Base model works fine, LoRA adapter works fine, but merged model fails

### Root Cause
**4-bit to 16-bit dequantization during merge corrupts model weights.**

When using `unsloth/Qwen3-14B-bnb-4bit`:
1. Model loads in 4-bit quantized form
2. Training happens normally (loss decreases)
3. During merge, weights are dequantized from 4-bit to 16-bit
4. This dequantization introduces numerical errors
5. Merged model produces garbage

### Solution
Use 8-bit training with manual CPU merge:

```python
# In train_qwen3.py:
BASE_MODEL = "unsloth/Qwen3-14B"  # NOT the bnb-4bit version!
model, tokenizer = FastLanguageModel.from_pretrained(
    BASE_MODEL,
    load_in_4bit=False,
    load_in_8bit=True,  # Use 8-bit quantization
)
```

Then merge on CPU (8-bit merge with Unsloth is broken):
```python
from transformers import AutoModelForCausalLM
from peft import PeftModel
import torch

# Load 16-bit base model on CPU
base_model = AutoModelForCausalLM.from_pretrained(
    "Qwen/Qwen3-14B",
    torch_dtype=torch.bfloat16,
    device_map="cpu",  # Force CPU to avoid OOM
)

# Load and merge LoRA adapter
model = PeftModel.from_pretrained(base_model, "models/qwen3-trained/lora_adapter")
model = model.merge_and_unload()
model.save_pretrained("models/qwen3-trained/merged_manual")
```

### Why 8-bit Merge with Unsloth Fails
Unsloth's `save_pretrained_merged()` fails with 8-bit models due to SCB (Scale Column Bias) key mismatch:
```
RuntimeError: Unsloth: Extracted keys = {'model.layers.27.mlp.up_proj.SCB', ...} do not match!
```

The workaround is using PEFT with a fresh 16-bit base model on CPU.

---

## Issue #2: Truncated/Identical Summary and Detailed_Analysis

### Symptoms
- Model outputs truncated summaries ending with "..."
- `summary` and `detailed_analysis` are nearly identical
- Output is only ~200 characters

### Root Cause
**Training data contained fake truncated summaries.**

In the original `train_qwen3.py` (line 98):
```python
summary = record.get("summary", f"{content_preview[:200]}...")
```

This created "summaries" by truncating article content to 200 chars and adding "...".
- The model learned this truncation pattern
- `detailed_analysis` wasn't in training data at all

### Solution
1. Update labeling prompt to request proper `summary` and `detailed_analysis`
2. Labeling script stores these fields in `output` dict
3. Training script reads from `output` dict, no fallback truncation
4. Relabel all training data with larger model

See: Training data format in DATA_CREATION.md

---

## Issue #3: GPU OOM During 16-bit Merge

### Symptoms
```
torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 170.00 MiB.
GPU 0 has a total capacity of 23.55 GiB of which 46.69 MiB is free.
```

### Root Cause
16-bit Qwen3-14B needs ~28GB VRAM, RTX 3090 only has 24GB.

### Solution
Use CPU for merge (slower but works):
```python
base_model = AutoModelForCausalLM.from_pretrained(
    "Qwen/Qwen3-14B",
    torch_dtype=torch.bfloat16,
    device_map="cpu",  # Force CPU
)
```

---

## Quantization Reference

| Format | Size | Quality | Use Case |
|--------|------|---------|----------|
| q8_0 | 15.7GB | Best | Production (our target) |
| q4_k_m | ~8GB | Good | Space-constrained |
| 4-bit | Training only | N/A | DO NOT USE FOR MERGE |

**Always use q8_0** for the final GGUF model - matches the base `qwen3:14b-q8_0` we run in Ollama.

---

## Verified Working Pipeline (2026-01-10)

1. **Train with 8-bit** (`load_in_8bit=True`)
2. **Save LoRA adapter** (not merged model)
3. **Manual merge on CPU** with PEFT + 16-bit base
4. **Convert to GGUF q8_0** with llama.cpp
5. **Import to Ollama** with `ollama create`

This produces coherent German output matching the training data format.
