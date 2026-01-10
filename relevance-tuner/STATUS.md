# Current Status (2026-01-10)

## Solution: Option B Implemented

**Option B (base model + system prompt) is now live and working.**

### What's Working

- **Base model `qwen3:14b-q8_0`** with detailed system prompt produces high-quality output
- **Distinct `summary` and `detailed_analysis`** - no more identical content
- **Correct AK classification** - tested with Pflege (AK3), Migration (AK2), Sozialpolitik (AK1)
- **Appropriate priority levels** - critical for budget cuts, high for reforms, low for irrelevant news
- **Valid JSON output** - parser handles responses correctly
- **No Liga speculation** - summaries focus on article facts, not hypothetical Liga reactions

### Test Results

| Item ID | Topic | AK | Priority | Relevance |
|---------|-------|----|---------:|-----------|
| 2438 | Pflege-Umfrage Hessen | AK3 | high | 0.85 |
| 2531 | Kommunalwahl Hessen | AK1 | critical | 1.0 |
| 2032 | Abschiebungen | AK2 | critical | 1.0 |
| 2897 | US ICE Policy | null | low | 0.0 |

Irrelevant items (US politics, sports, entertainment) correctly classified as low priority with null AK.

### Implementation Details

**File modified**: `news-aggregator/backend/services/processor.py`

1. Added `ANALYSIS_SYSTEM_PROMPT` constant (~52 lines) with:
   - Liga organization context
   - Arbeitskreise definitions
   - Priority criteria
   - Output format specification
   - Explicit "no speculation" rules

2. Modified `analyze()` method to pass system prompt:
   ```python
   response = await self.llm.complete(
       prompt,
       system=ANALYSIS_SYSTEM_PROMPT,
       temperature=0.1,
       max_tokens=1200,
   )
   ```

3. Model selection via API: `qwen3:14b-q8_0` (base model, not fine-tuned)

### Why This Works Better Than Fine-tuning

| Aspect | Fine-tuned Model | Base Model + Prompt |
|--------|-----------------|---------------------|
| Training data quality | Garbage-in-garbage-out | N/A |
| Output repetition | High (learned from training) | Low |
| Liga speculation | Present (learned from training) | Controlled by prompt |
| Iteration speed | Hours (retrain) | Minutes (edit prompt) |
| Model size | Same (15.7GB q8_0) | Same |

The fine-tuned model faithfully reproduced the flaws in its training data. The base model with a well-crafted prompt gives us direct control over output quality.

---

## Previous Issues (Resolved)

### Fine-tuned Model Problems
- `detailed_analysis` was repetitive (same sentences repeated)
- Liga speculation appeared despite training instructions
- Content felt formulaic, not insightful

### Root Cause
The labeling LLM (qwen3:14b-q8_0) produced low-quality training data:
- Repetitive filler text to meet minimum length requirements
- Liga speculation despite explicit instructions not to

**This was a garbage-in-garbage-out problem.**

---

## Assets

| Asset | Location | Status |
|-------|----------|--------|
| **Active model** | `qwen3:14b-q8_0` | Live in production |
| System prompt | `processor.py:ANALYSIS_SYSTEM_PROMPT` | Working |
| Fine-tuned model | `ollama: liga-relevance` | Backup (not recommended) |
| Training data | `data/final/*.jsonl` | 688 train / 148 val / 149 test |
| Training script | `train_qwen3.py` | 8-bit workflow working |

---

## Future Considerations

### If Classification Accuracy Needs Improvement

Consider **Option C (Hybrid)**:
- Use fine-tuned model for classification only (relevant, ak, priority)
- Use base model with prompt for summary/detailed_analysis generation

### If Response Time is Too Slow

Consider using a smaller base model:
- `qwen3:8b-q8_0` - faster but less accurate
- `qwen3:4b-q8_0` - much faster but may miss nuances

### To Improve Training Data Quality (if revisiting Option A)

- Use larger labeling model (qwen3:70b or claude-3.5-sonnet)
- Manually review/curate 200+ high-quality examples
- More diverse training examples (~2000+)

---

## Git History

```
16600f6 docs: add STATUS.md with current state and options
8d9c075 docs(retraining): update with 8-bit workflow and manual merge steps
1b18bd3 fix(training): switch to 8-bit training + manual CPU merge
f0749db docs: consolidate CLAUDE.md with API reference and Swagger links
```
