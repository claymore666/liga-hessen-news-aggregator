# Current Status (2026-01-10)

## What Works

- **Model produces coherent JSON output** - no more garbage text
- **8-bit training pipeline is functional** - train → manual merge → GGUF q8_0 → Ollama
- **Classification accuracy seems reasonable** - correctly identifies AK, priority, relevance

## What Doesn't Work

- **Output quality issues**:
  - `detailed_analysis` is repetitive (same sentences repeated)
  - Liga speculation still appears ("Die Liga könnte...", "könnte betroffen sein")
  - Content feels formulaic, not insightful

## Root Cause Analysis

The fine-tuned model learned these patterns from the training data. The labeling LLM (qwen3:14b-q8_0) produced:
- Repetitive filler text to meet minimum length requirements
- Liga speculation despite explicit instructions not to

**This is a garbage-in-garbage-out problem** - the fine-tuned model faithfully reproduces what it was trained on.

## Options Going Forward

### Option A: Better Training Data
- Use larger labeling model (qwen3:70b or claude-3.5-sonnet)
- Manually review/curate training examples
- More diverse training examples (~2000+ instead of ~1000)
- **Effort**: High (days of work)
- **Outcome**: Uncertain - may still have quality issues

### Option B: Skip Fine-tuning, Use Prompt Engineering
- Use base qwen3:14b-q8_0 (or larger) with detailed system prompt
- The LABELING_PROMPT.md already has comprehensive instructions
- No training overhead, instant iteration on prompt
- **Effort**: Low (hours)
- **Outcome**: May be good enough for the use case

### Option C: Hybrid Approach
- Use fine-tuned model for classification only (relevant, ak, priority)
- Use base model with prompt for summary/detailed_analysis generation
- **Effort**: Medium
- **Outcome**: Best of both worlds?

## Current Assets

| Asset | Location | Status |
|-------|----------|--------|
| Fine-tuned model | `ollama: liga-relevance` | Working (15.7GB q8_0) |
| Training data | `data/final/*.jsonl` | 688 train / 148 val / 149 test |
| Labeling prompt | `LABELING_PROMPT.md` | Comprehensive, needs enforcement |
| Training script | `train_qwen3.py` | 8-bit workflow working |

## Recommendation

**Try Option B first** - use the base model with the existing LABELING_PROMPT.md:

```bash
# In news-aggregator backend, change processor.py to use:
model = "qwen3:14b-q8_0"  # Base model, not liga-relevance

# With LABELING_PROMPT.md content as system prompt
```

If classification accuracy suffers, consider Option C (fine-tuned for classification, base for text generation).

## Git History

```
8d9c075 docs(retraining): update with 8-bit workflow and manual merge steps
1b18bd3 fix(training): switch to 8-bit training + manual CPU merge
f0749db docs: consolidate CLAUDE.md with API reference and Swagger links
```
