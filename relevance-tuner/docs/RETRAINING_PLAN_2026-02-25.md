# Classifier Retraining Plan — 2026-02-25

## Context

- Batch reprocessing of 566 items with improved LLM prompt completed (geographic filter, post-processing override, thinking mode)
- Current training set: 4,924 items (3,447 train) — all LLM-labeled
- Prod has 8,817 additional items that only the classifier saw (no LLM label)
- Current classifier: 74.6% accuracy, 61.7% F1, **10 false negatives** (mostly medium-priority edge cases)
- Eval set: 181 items (83 edge cases, 50 positives, 48 negatives)

## Goal

Improve classifier quality by getting independent LLM labels for items the classifier rejected. Data-driven approach: measure improvement at each step and only continue if gains are significant.

## Why not just add classifier-only items as negatives?

~~Original idea: use the 8,817 classifier-only items as negative training examples.~~

**Discarded.** This is self-reinforcement, not learning. The classifier labeled them as irrelevant — feeding its own labels back as training data just reinforces existing decisions (including errors). The classifier can't improve from its own output. We need **independent labels** from the LLM.

## Phase 1: Retrain with improved labels from reprocessed items

**Idea:** Yesterday we reprocessed 566 previously-relevant items through the improved LLM prompt (geographic filter, post-processing override, thinking mode). These items now have cleaner, corrected labels. The training data export already picked these up (4,924 items). Retrain and measure the gain from better label quality alone.

**Steps:**
1. Retrain classifier with `EMBEDDING_BACKEND=nomic-v2` on the already-exported data
2. Eval against fixed eval set
3. Compare with baseline (74.6% acc, 61.7% F1)

**Expected outcome:** Cleaner labels (especially geographic corrections) should reduce noise. Modest improvement expected.

## Phase 2: LLM-verify 1,000 classifier-only items

**Idea:** Some classifier-only items might be false negatives — relevant items the classifier wrongly rejected. LLM-labeling a sample finds and corrects these, giving the classifier new signal to learn from.

**Steps:**
1. Select ~1,000 classifier-only items from prod (random sample)
2. Pause LLM worker on prod to reserve Ollama compute
3. Run through LLM via `reprocess_robust.py` on prod (~7h at 25s/item)
4. Count how many the LLM reclassifies as relevant → this is our **false negative rate**
5. Re-export training data (now includes LLM labels for these 1,000 items)
6. Retrain classifier and eval
7. Resume LLM worker on prod

**Item IDs:** `data/phase2_1000_item_ids.txt` (1,000 items, random sample)
**Log:** `/tmp/reprocess_phase2.log`
**Started:** 2026-02-25 ~12:45, ETA ~7h

**Expected outcome:** If even 5-10% come back as relevant, that's 50-100 new positive examples the classifier was missing. Should improve recall.

## Phase 3: Full LLM processing (conditional)

**Decision gate:** Only proceed if Phase 2 shows significant classifier improvement.

- If improvement is significant: LLM-process all remaining ~7,800 classifier-only items (~54h)
- If not significant: stop here, diminishing returns

## Metrics

| Metric | Baseline | Phase 1 | Priority Fix (t=0.50) | Priority Fix (t=0.60) |
|--------|----------|---------|----------------------|----------------------|
| Accuracy | 74.6% | 81.8% | 66.3% | **73.5%** |
| F1 | 61.7% | 66.0% | 59.6% | **64.7%** |
| Precision | 50.7% | 64.0% | 43.3% | 49.4% |
| Recall | 78.7% | 68.1% | **95.7%** | **93.6%** |
| FP | 36 | 18 | 59 | 45 |
| FN | 10 | 15 | **2** | **3** |
| Training items | 4,924 | 4,924 | 5,098 | 5,098 |

### Phase 1 Analysis
- Precision improved massively (+13.3pp) — FP halved from 36 to 18
- Recall dropped (-10.6pp) — 5 more FN, model now too conservative
- Cleaner geographic labels taught it to reject non-Hessen, but overcorrected on some relevant items
- **Conclusion:** Phase 2 (more positive examples from LLM-labeling) is the right next step to recover recall

### Priority Fix Analysis (between Phase 1 and Phase 2)
**Discovery:** 4,112 items had `priority=none` in DB despite LLM analysis showing them as relevant (priority_suggestion=high/medium/low, scores 0.8-0.95). The `priority_score` was correctly set but the `priority` enum field was not persisted. Bug active from Jan 12 to Feb 12, fixed by Docker rebuild on Feb 16.

**Impact:** The export script uses `priority` to determine relevance labels. 4,106 items were MISLABELED as irrelevant in training data. Fixing this added ~778 more relevant training examples (from 1,477 to 2,255).

**Fixes applied:**
1. Repaired 3,925 items in prod DB (set priority from LLM metadata)
2. Fixed reprocess endpoint wrong priority mapping (was downshifting by 1 level)
3. Fixed missing event commit in LLM worker (events silently lost since Jan 20)

**Results:** Recall dramatically improved (+15pp at t=0.60). At threshold 0.55-0.60, model catches 94-96% of relevant items with only 2-3 false negatives. Best operating point depends on FP tolerance:
- **t=0.55**: 95.7% recall, 2 FN, 52 FP, F1=62.5%
- **t=0.60**: 93.6% recall, 3 FN, 45 FP, F1=64.7% (recommended)
- **t=0.70**: 85.1% recall, 7 FN, 36 FP, F1=65.0%

## Backups

Taken 2026-02-25 before starting:
- `backups/2026-02-25/liga_news_db_PROD.sql.gz` (36 MB)
- `backups/2026-02-25/classifier-chromadb.tar.gz` (311 MB)
- `backups/2026-02-25/embedding_classifier_nomic-v2.pkl` (9.6 MB)
- `backups/2026-02-25/training-data-final/` (previous training splits)
