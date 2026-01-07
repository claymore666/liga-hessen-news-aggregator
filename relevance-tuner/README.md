# Liga Hessen Relevance Tuner

Fine-tuned LLM for classifying news relevance to the Liga der Freien Wohlfahrtspflege Hessen.

## High-Level Process

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        RELEVANCE TUNER PIPELINE                             │
└─────────────────────────────────────────────────────────────────────────────┘

1. DATA COLLECTION
   news-aggregator API → news_unlabeled.jsonl → batches/batch_XX.jsonl

2. LABELING (local Ollama)
   batches/ → qwen3:14b-q8_0 → ollama_results/batch_XX_labeled.jsonl
   ~16 items/min | Uses LABELING_PROMPT.md criteria

3. SPLIT CREATION
   ollama_results/*.jsonl → train.jsonl (70%) / val.jsonl (15%) / test.jsonl (15%)
   Stratified by relevant/irrelevant ratio

4. FINE-TUNING (Unsloth + LoRA)
   Qwen3-14B (4-bit) + LoRA adapters → GGUF export
   ~25 min on RTX 3090

5. DEPLOYMENT
   GGUF → ollama create liga-relevance → production inference
```

## Quick Start

```bash
# Activate environment
source venv/bin/activate

# Label new data with local Ollama
python scripts/label_with_ollama.py --all --model qwen3:14b-q8_0

# Create train/val/test splits
python scripts/create_splits.py

# Train model
python train_qwen3.py

# Import to Ollama
cd models/qwen3-trained/gguf
ollama create liga-relevance -f Modelfile
```

## Project Structure

```
relevance-tuner/
├── data/
│   ├── raw/
│   │   ├── batches/           # Input batches for labeling
│   │   ├── liga_reactions/    # Liga press releases, statements
│   │   └── news_unlabeled.jsonl
│   ├── reviewed/
│   │   └── ollama_results/    # Labeled output from Ollama
│   └── final/
│       ├── train.jsonl        # Training split (70%)
│       ├── validation.jsonl   # Validation split (15%)
│       ├── test.jsonl         # Test split (15%)
│       └── stats.json
├── scripts/
│   ├── label_with_ollama.py   # Batch labeling with local LLM
│   ├── create_splits.py       # Create train/val/test splits
│   └── benchmark_models.py    # Compare model approaches
├── models/
│   ├── qwen3-trained/         # Fine-tuned Qwen3 model (production)
│   │   └── gguf/              # GGUF + Modelfile for Ollama
│   └── sklearn/               # Experimental sklearn classifier
├── train_qwen3.py             # Training script (Unsloth + LoRA)
├── train_sklearn.py           # Sklearn training (experimental)
├── LABELING_PROMPT.md         # Detailed labeling instructions
└── DATA_CREATION.md           # Data pipeline documentation
```

## Current Dataset (v2)

| Split | Total | Relevant | Irrelevant |
|-------|-------|----------|------------|
| Train | 704 | 205 (29%) | 499 (71%) |
| Validation | 151 | 44 (29%) | 107 (71%) |
| Test | 153 | 45 (29%) | 108 (71%) |
| **Total** | **1008** | **294** | **714** |

### AK Distribution
- QAG: 101 (34%) - Querschnitt (Digitalisierung, Wohnen, Klima)
- AK2: 63 (21%) - Migration und Flucht
- AK5: 56 (19%) - Kinder, Jugend, Familie
- AK1: 51 (17%) - Grundsatz und Sozialpolitik
- AK4: 12 (4%) - Eingliederungshilfe
- AK3: 11 (4%) - Gesundheit, Pflege, Senioren

### Priority Distribution
- medium: 199 (68%)
- high: 48 (16%)
- low: 44 (15%)
- critical: 3 (1%)

## Training Configuration

| Parameter | Value |
|-----------|-------|
| Base Model | Qwen3-14B (4-bit) |
| Method | LoRA (rank 16) |
| Epochs | 3 |
| Batch Size | 6 |
| Learning Rate | 2e-4 |
| Output | GGUF (q4_k_m) for Ollama |

## Scripts

### `scripts/label_with_ollama.py`

Batch labeling using local Ollama LLM. Reads batches from `data/raw/batches/`, applies the labeling criteria from `LABELING_PROMPT.md`, outputs to `data/reviewed/ollama_results/`.

**Features**:
- Processes items in chunks of 10 for better accuracy
- Live progress bar with ETA calculation
- Resume support (skips already-completed batches)
- Handles JSON parsing edge cases from LLM output

```bash
python scripts/label_with_ollama.py --batch 0        # Single batch
python scripts/label_with_ollama.py --all            # All batches
python scripts/label_with_ollama.py --all --resume   # Skip done
python scripts/label_with_ollama.py --all --model qwen3:32b  # Different model
```

**Performance**: ~16-20 items/min with qwen3:14b-q8_0 on RTX 3090

### `scripts/create_splits.py`

Creates stratified train/val/test splits from labeled data. Maintains class balance (relevant/irrelevant ratio) across all splits.

**Features**:
- Deduplication by title
- Stratified splitting (preserves class ratios)
- Generates `stats.json` with dataset statistics

```bash
python scripts/create_splits.py              # From Ollama results only
python scripts/create_splits.py --include-old  # Merge with old data
```

### `train_qwen3.py`

Fine-tunes Qwen3-14B using Unsloth + LoRA. Exports to GGUF for Ollama.

**Features**:
- 4-bit quantized base model (fits in 24GB VRAM)
- LoRA adapters (only 0.43% of params trained)
- Automatic GGUF export with Modelfile
- Cosine learning rate schedule

**Config** (editable in script):
```python
BATCH_SIZE = 6           # Per-device batch size
GRADIENT_ACCUMULATION = 1  # No accumulation needed
EPOCHS = 3
LORA_RANK = 16
LEARNING_RATE = 2e-4
```

### `data/final/analyze_quality.py`

Quality analysis of training data. Checks for duplicates, class balance, AK distribution, cross-split contamination.

```bash
cd data/final && python analyze_quality.py
```

## Model Usage

After training and importing to Ollama:

```bash
ollama run liga-relevance "Titel: Hessen kürzt Mittel für Kitas
Inhalt: Die Landesregierung plant Kürzungen..."
```

Output format (JSON):
```json
{
  "summary": "Die hessische Landesregierung plant drastische Kürzungen...",
  "relevant": true,
  "relevance_score": 1.0,
  "priority": "critical",
  "assigned_ak": "AK5",
  "tags": ["ak5", "critical"],
  "reasoning": "Kürzungen bei Kitas betreffen direkt AK5 (Kinder, Jugend, Familie)"
}
```

### Production Performance

| Metric | Value |
|--------|-------|
| Speed | ~46 items/min (~1.3s per item) |
| Relevance Accuracy | ~90% |
| AK Accuracy | ~60% |
| Model Size | 15.7GB (q8_0 GGUF) |

## Alternative Approaches

### Scikit-learn (Knowledge Distillation)

We experimented with training a fast scikit-learn classifier using the Qwen3-labeled data (teacher-student approach). Results:

| Metric | Sklearn | Qwen3 Fine-tuned |
|--------|---------|------------------|
| Relevance Accuracy | 75% | 90% |
| AK Accuracy | 50% | 60% |
| Speed | 0.7ms | 1300ms |

**Conclusion**: Qwen3 is significantly more accurate, especially on nuanced cases. At ~46 items/min, it's fast enough for current needs.

**Future consideration**: With significantly more training data (5000+ items), sklearn accuracy may improve enough to justify a hybrid approach:
- Sklearn for high-confidence cases (fast path)
- Qwen3 for uncertain cases (accurate fallback)

Scripts preserved in `train_sklearn.py` and `scripts/benchmark_models.py` for future experimentation.

## Requirements

- Python 3.11+
- CUDA GPU (RTX 3090 recommended)
- Ollama (for labeling and inference)
- ~20GB VRAM for training

### Python Dependencies

```bash
pip install unsloth datasets trl requests
```
