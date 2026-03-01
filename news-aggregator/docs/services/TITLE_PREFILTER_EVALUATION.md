# Title Pre-Filter Evaluation (2026-03-01)

## Summary

**Conclusion: Title-only pre-filtering cannot achieve 0 false negatives and was removed from the pipeline.**

We evaluated using a small LLM (qwen3:8b) as a fast title-based pre-filter between the embedding classifier and the full LLM (qwen3:14b). The goal was to reject obvious false positives before the expensive full analysis (~15s/item), saving GPU time. While the 8b model is fast (78-130ms/item) and catches many irrelevant items, it inevitably loses relevant articles whose titles don't reveal their social welfare relevance. Since losing relevant articles is unacceptable for Liga, the feature was reverted.

## Background

### Pipeline (before this experiment)
```
RSS/Scraper → Classifier (embedding, ~5ms) → LLM (qwen3:14b, ~15s)
```

### Problem
The embedding classifier has a ~36% false positive rate at the 0.25 threshold. Items the classifier marks as "relevant" still go to the 14b LLM, which correctly rejects most of them. But each wasted LLM call costs ~15 seconds of GPU time.

### Hypothesis
A small LLM (qwen3:8b) checking just the title could quickly reject obvious false positives (party politics, sports, entertainment) in ~130ms, saving the 15s full analysis.

## Implementation

### What was built
- `backend/services/title_prefilter.py` — Ollama chat calls, batch orchestration, model switching
- `backend/prompts/title_prefilter.txt` — German system prompt with relevant/irrelevant examples
- Integration into classifier worker (`needs_title_check` flag) and LLM worker (pre-filter phase)
- API endpoints: `POST /items/{id}/prefilter` and `POST /items/prefilter`
- VRAM model switching: unload 14b → run 8b batch → unload 8b → 14b auto-loads

### VRAM constraint
Both models don't fit simultaneously on RTX 3090 (24GB):
- qwen3:8b: 7.6GB
- qwen3:14b-q8_0: 17.7GB
- Baseline VRAM: ~2.8GB
- Total: 28.1GB > 24GB

Model switch overhead: ~3.3s total (unload: 7ms, cold load 8b: 1.1s, cold load 14b: 2.2s).

### Prompt evolution
The prompt went through 3 iterations:
1. **v1**: Basic keyword matching — 98% accuracy on small sample (49 items)
2. **v2**: Aligned with main LLM prompt — too strict (91% filter rate), rejected valid items
3. **v3 (final)**: Balanced with "IM ZWEIFEL: relevant!" bias, explicit RELEVANT/IRRELEVANT examples, soft geographic filter, individual portrait rejection

## Evaluation Results

### Dataset
- **1,528 items** from 7 days of production data
- **376 truly relevant** (as determined by the full 14b LLM analysis)
- **1,152 truly irrelevant**
- All items had both classifier scores and full LLM ground truth

### Model comparison: Title-only, concurrency=4

| Metric | qwen3:8b | qwen3:14b-q8_0 |
|--------|----------|-----------------|
| Speed | **78ms/item** | 170ms/item |
| Total (1528 items) | **119s** | 259s |
| False negatives | 143/376 (**38.0%**) | 98/376 (**26.1%**) |
| Correct rejections | **879/1152 (76.3%)** | 820/1152 (71.2%) |
| Items sent to LLM | 506 | 610 |

### False negatives by classifier confidence zone

| Zone | Items | 8b FN | 14b FN |
|------|-------|-------|--------|
| clf < 0.25 (hard cut) | 197 (3 rel) | 3/3 (100%) | 0/3 (0%) |
| clf 0.25-0.50 (edge) | 561 (70 rel) | 50/70 (71%) | 38/70 (54%) |
| clf 0.50-0.80 (medium) | 462 (156 rel) | 67/156 (43%) | 49/156 (31%) |
| clf >= 0.80 (high) | 308 (147 rel) | 23/147 (16%) | 11/147 (7%) |

### Content augmentation test (8b, clf 0.25-0.80 zone, 1023 items)

| Input | ms/item | FN rate | Correct rejections |
|-------|---------|---------|-------------------|
| Title only | 211ms* | 119/226 (53%) | 646/797 (81%) |
| Title + 250B | 96ms** | 120/226 (53%) | 649/797 (81%) |
| Title + 512B | 114ms** | 110/226 (49%) | 628/797 (79%) |
| Title + 1024B | 143ms** | 102/226 (45%) | 625/797 (78%) |

\* Sequential. \*\* Concurrency=4.

Adding up to 1KB of article content barely reduces false negatives (53% → 45%).

### Combined strategy analysis

We tested every combination of classifier threshold + 8b gate:

| Strategy | → LLM | FN | Recall | LLM savings |
|----------|-------|-----|--------|-------------|
| Current (clf≥0.25, no 8b) | 432 | **0** | **100%** | 0% |
| clf≥0.50, 0.25-0.50 via 8b | 285 | 3 | 95.8% | 37.5% |
| clf≥0.80, 0.25-0.80 via 8b | 209 | 10 | 85.9% | 54.2% |
| 8b hard gate on all | 169 | 15 | 78.9% | 62.9% |

**No combination achieves 0 false negatives.** Every threshold loses relevant items.

### Why title-only fails

Examples of relevant articles the 8b/14b title check incorrectly rejects:

| Title | clf | Why relevant |
|-------|-----|-------------|
| "Koalitionschaos im Bundestag – Neue Asylregeln nach Hammelsprung beschl..." | 0.98 | Asyl policy — but title focuses on political drama |
| "KHAG: Einigung auf Nachbesserungen an Klinikreform" | 0.89 | Hospital reform — "KHAG" abbreviation obscures topic |
| "CDU-Parteitag: Der Mindestlohn soll nicht mehr für alle gelten" | 0.77 | Minimum wage — not in pre-filter keyword list |
| "Kompromiss gefunden: Grundsteuer steigt auf 695 Prozent" | 0.46 | Property tax affecting social housing costs |
| "Bundestag verabschiedet Reform: Mehr Rechte für leibliche Väter" | 0.44 | Family law reform — only apparent from content |

These articles are relevant because of their *content and implications*, not their titles. A title-only check fundamentally cannot catch them.

### Classifier retraining check

We also verified whether the retrained classifier (Feb 25, 5098 training items, 805 features) would catch the 8b's false negatives. Result: only 5/76 items crossed the 0.80 threshold after retraining. The classifier struggles with the same items — titles that don't contain explicit social welfare keywords.

## Decision

**The title pre-filter was removed from the pipeline** because:

1. **0 false negatives is a hard requirement** — Liga missing actionable news is worse than processing extra items
2. **No title-only approach achieves 0 FN** — neither 8b nor 14b, with or without content snippets
3. **The 8b as a soft priority signal has no value** — if every item goes to the 14b anyway, running 8b first just wastes GPU cycles
4. **Model switching overhead** — even when the 8b saves LLM calls, it adds 3.3s of VRAM switching per batch

## What remains

- The **prompt file** (`backend/prompts/title_prefilter.txt`) and **service module** (`backend/services/title_prefilter.py`) remain in the codebase for potential future use
- The **API endpoints** (`POST /items/{id}/prefilter`, `POST /items/prefilter`) remain available for manual/diagnostic use
- The **classifier worker** no longer sets `needs_title_check` flag
- The **LLM worker** no longer runs the pre-filter phase
- Config defaults to `TITLE_PREFILTER_ENABLED=false`

## Potential future approaches

If LLM processing cost becomes a bottleneck:
1. **Cheaper cloud LLM** for title+content pre-filter (e.g., Haiku at ~$0.001/item)
2. **Improved classifier** with more training data specifically for the false positive categories
3. **Two-stage LLM**: quick title+summary pass with 14b (shorter prompt, ~2s) before full analysis (~15s)
4. **Accept some FN**: if 95.8% recall becomes acceptable, clf≥0.50 + 8b gate saves 37.5% of LLM calls with only 3 lost items per 456
