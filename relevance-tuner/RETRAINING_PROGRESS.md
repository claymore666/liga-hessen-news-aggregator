# Model Retraining Progress - 2026-01-09

## Problem Identified

The `liga-relevance` model produces truncated, identical content for `summary` and `detailed_analysis` fields.

### Root Cause Analysis

| Issue | Cause | Evidence |
|-------|-------|----------|
| Short summaries (avg 1.6 sentences) | Training data had only 1-2 sentence summaries | 99.7% of training items failed validation (need ≥4 sentences) |
| Short detailed_analysis (avg 3.5 sentences) | Training data had only 3-4 sentences | 100% failed validation (need ≥10 sentences) |
| Input content too short | Most data from tweets without article content | 90.4% of items had <500 chars, avg 347 chars |
| Prompt said "up to" not "minimum" | LLM produced minimal output | "bis zu 8 Sätze" → model wrote 1-2 |

### Data Quality Metrics (Before Fix)

```
=== SUMMARY Analysis (287 relevant items) ===
  1 sentences: 131 items (45.6%)
  2 sentences: 140 items (48.8%)
  3 sentences: 15 items (5.2%)
  4 sentences: 1 items (0.3%)
  Average: 1.6 sentences
  Validation failures: 286/287 (99.7%)

=== DETAILED_ANALYSIS (287 relevant items) ===
  2 sentences: 22 items (7.7%)
  3 sentences: 146 items (50.9%)
  4 sentences: 88 items (30.7%)
  5 sentences: 27 items (9.4%)
  Average: 3.5 sentences
  Validation failures: 287/287 (100.0%)

=== CONTENT LENGTH DISTRIBUTION ===
  Total items: 695
  Average content length: 347 chars (TOO SHORT!)
  0-200 chars: 188 (27.1%) - mostly tweets
  200-500 chars: 440 (63.3%) - social media posts
  500-1000 chars: 17 (2.4%)
  1000-2000 chars: 31 (4.5%)
  2000+ chars: 19 (2.7%)
```

## Fixes Implemented

### 1. Updated Labeling Prompt ✅

File: `scripts/label_with_ollama.py` (lines 207-216)

Changed from:
```
- summary: Reine Fakten (bis zu 8 Sätze)
- detailed_analysis: Fakten + Zitate + Auswirkungen (bis zu 15 Sätze)
```

To:
```
MINDESTLÄNGEN - STRIKT EINHALTEN:
- summary: MINIMUM 4 Sätze, MAXIMUM 8 Sätze
- detailed_analysis: MINIMUM 10 Sätze, MAXIMUM 15 Sätze

VERBOTEN:
- Weniger als 4 Sätze bei summary = UNGÜLTIG
- Weniger als 10 Sätze bei detailed_analysis = UNGÜLTIG
```

### 2. Added Validation with Retry ✅

File: `scripts/label_with_ollama.py` (lines 237-272, 319-381)

```python
def count_sentences(text: str) -> int:
    """Count sentences by '. ' or end patterns."""
    sentences = re.split(r'[.!?]\s+(?=[A-ZÄÖÜ])|[.!?]$', text)
    return len([s for s in sentences if s.strip()])

def validate_label(label: dict) -> tuple[bool, str]:
    """Validate minimum length requirements."""
    if not label or not label.get("relevant"):
        return True, ""  # Non-relevant items pass

    summary_sentences = count_sentences(label.get("summary", ""))
    detailed_sentences = count_sentences(label.get("detailed_analysis", ""))

    errors = []
    if summary_sentences < 4:
        errors.append(f"summary has {summary_sentences} sentences (minimum 4)")
    if detailed_sentences < 10:
        errors.append(f"detailed_analysis has {detailed_sentences} sentences (minimum 10)")

    return (len(errors) == 0, "; ".join(errors))
```

Retry logic added to `label_items()`:
- Validates each label after LLM response
- Retries failed items up to 2 times with stronger emphasis
- Logs validation failures

### 3. Re-fetching Sources with Article Extraction 🔄 IN PROGRESS

Using API endpoint: `POST /api/sources/fetch-all?training_mode=true`

This:
- Fetches all enabled channels
- Extracts full article content from linked URLs (t.co → actual article)
- Disables filters for training data collection

**Current Status**: Running (see backend logs)

```bash
# Monitor progress
docker logs -f liga-news-backend 2>&1 | grep -E "Extracted|Fetched.*new"
```

## Next Steps

| Step | Status | Command |
|------|--------|---------|
| 1. Wait for fetch to complete | 🔄 Running | `docker logs -f liga-news-backend` |
| 2. Check content lengths after fetch | ⏳ Pending | Check items with >1000 chars |
| 3. Export training data | ⏳ Pending | Filter items with substantial content |
| 4. Relabel with updated prompt | ⏳ Pending | `python scripts/label_with_ollama.py --all` |
| 5. Verify validation passes | ⏳ Pending | Check sentence counts |
| 6. Retrain model | ⏳ Pending | `python train_qwen3.py` |
| 7. Deploy to Ollama | ⏳ Pending | `ollama create liga-relevance -f Modelfile` |

## Expected Outcome

After retraining with proper data:
- `summary`: 4-8 sentences of factual content
- `detailed_analysis`: 10-15 sentences with quotes, numbers, context
- No more truncated "..." endings
- No more identical summary/detailed_analysis

## Files Modified

- `scripts/label_with_ollama.py` - Added validation, retry logic, updated prompt
- `LABELING_PROMPT.md` - Updated with minimum length requirements and examples
- `RETRAINING_PROGRESS.md` - This document

## Monitoring Commands

```bash
# Watch fetch progress
docker logs -f liga-news-backend 2>&1 | grep -E "Extracted|Fetched.*items"

# Check content lengths in database
curl -s "http://localhost:8000/api/items?page_size=100" | \
  python3 -c "import json,sys; items=json.load(sys.stdin)['items']; \
  print(f'Items >1000 chars: {sum(1 for i in items if len(i.get(\"content\",\"\") or \"\") > 1000)}')"

# Test validation function
source venv/bin/activate && python -c "
from scripts.label_with_ollama import validate_label, count_sentences
test = {'relevant': True, 'summary': 'One.', 'detailed_analysis': 'Short.'}
print(validate_label(test))  # Should fail
"
```
