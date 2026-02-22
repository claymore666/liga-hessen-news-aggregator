# LLM Prompt Tuning — Rationale, Progression & Results

Documents the iterative improvement of the LLM classification prompt for Liga Hessen's news aggregator: why we started, what we changed at each step, how we measured quality, and where we're headed.

**Source file:** `backend/services/processor.py` → `ANALYSIS_SYSTEM_PROMPT`
**Git tags:** `prompts-v1`, `prompts-v2`, `prompts-v3`, `prompts-v4`

**Related docs:**
- [LLM_PIPELINE.md](LLM_PIPELINE.md) — Technical architecture of the LLM worker
- [PROCESSING_ANALYTICS.md](../architecture/PROCESSING_ANALYTICS.md) — Processing logs and disagreement tracking
- [relevance-tuner/docs/TRAINING_GUIDE.md](../../../relevance-tuner/docs/TRAINING_GUIDE.md) — ML classifier training

---

## Background: The Problem

On 2026-02-20, a client reported "odd and irrelevant messages" appearing in the news feed. Investigation revealed two compounding issues:

1. **LLM processing had stopped** — gpu1 auto-shuts down outside active hours (Mon-Fri 7-16), and this was a Thursday evening. Items fetched after 15:00 went through the ML classifier but never reached the LLM for refinement.

2. **The ML classifier alone has ~30% false positives** — it's a fast pre-filter (embedding-based, ~3 items/sec) designed to be conservative. It lets borderline items through, relying on the LLM to make the final call. Without the LLM running, those borderline items appeared in the feed as-is.

This exposed a deeper issue: even when the LLM *was* running, the prompt was too permissive. Anything vaguely related to welfare topics was being marked relevant.

## Methodology

### How we measure quality

We use **Claude Haiku as an independent judge**. For each prompt version:

1. Sample 50 items from production: 25 marked relevant (random across high/medium/low) and 25 marked irrelevant (priority=none)
2. Send each item's title, summary, and priority to Haiku with Liga's mission context
3. Haiku independently classifies each item as correct or wrong, with reasoning
4. Calculate metrics:

| Metric | What it measures |
|--------|-----------------|
| **False positive rate (FP)** | % of "relevant" items that Haiku says should be irrelevant — noise in the feed |
| **False negative rate (FN)** | % of "irrelevant" items that Haiku says should be relevant — missed opportunities |
| **Overall accuracy** | % of all 50 items where the LLM and Haiku agree |

### Why Haiku as the judge?

- Consistent across evaluations (no human fatigue/inconsistency)
- Understands nuanced German social policy context
- Fast enough to evaluate 50 items in ~30 seconds
- Cost-effective for iterative testing

### How we reprocess

To test a prompt change, we reprocess items through the production LLM via the `/api/items/{id}/reprocess` endpoint. This calls the LLM inline (not via the background queue) and updates the item's priority, summary, and AK assignments. We use a 10-second delay between items to avoid overloading the Ollama API.

---

## Prompt Evolution

### v1 — The permissive original

**Git tag:** `prompts-v1` | **Commit:** `1f2e53a`

The initial prompt described Liga as a "Dachverband" (umbrella organization) and listed topic areas (Pflege, Kita, Integration, etc.) as relevant. The relevance question was essentially: *"Does this article touch on a topic that Liga works on?"*

**The core mistake:** Treating Liga as an umbrella that covers topics, rather than a lobby organization that *acts on* policy. This meant any article mentioning "Pflege" or "Kinder" was flagged — a recipe about healthy eating for kids, a nursing home anniversary, a local charity event.

**Result:** ~707 out of 1,368 items marked relevant (~52%). Far too noisy for a busy press officer to work with.

### v2 — Reframing as advocacy (the big shift)

**Git tag:** `prompts-v2` | **Commit:** `caffbdf`

The key insight: Liga doesn't just *monitor* social policy — it *lobbies*. The prompt should filter for items that Liga can **act on**, not just items that mention topics Liga cares about.

**Three fundamental changes:**

1. **Identity reframing** — Changed Liga's self-description from "Dachverband der 6 Wohlfahrtsverbände" to "LOBBY- UND ADVOCACY-ORGANISATION: Sie vertritt die Interessen der Freien Wohlfahrtspflege gegenüber Politik und Öffentlichkeit." This single change shifts how the LLM evaluates every item.

2. **Dual-question relevance gate** — Instead of "Is this about a Liga topic?", the prompt now requires two questions to be answered YES:
   - *"Geht es um ein GESETZ, einen HAUSHALT, eine STRUKTURELLE KRISE oder eine POLITISCHE ENTSCHEIDUNG?"*
   - *"Kann die Liga Hessen diesen Artikel für ihre Lobbyarbeit NUTZEN?"*

   Both must be yes. An article that merely *mentions* a topic but doesn't involve policy, budgets, or structural issues gets rejected.

3. **Priority = severity, not urgency** — The original prompt conflated "breaking news" with "important". A press conference about Pflege tomorrow isn't necessarily more important than a IGES study showing social contributions will hit 50% by 2035. We redefined priority as *severity of societal impact*.

**Additional changes:**
- Massively expanded the NICHT RELEVANT exclusion list (sports, lifestyle, PR events, operational news, personality items, foreign politics)
- Added `argumentationskette` output field — forces the LLM to articulate *what arguments Liga could make* from this article
- Explicit instruction that summary/analysis must contain only facts, no speculation about "what Liga could do"

**Haiku verification:**

We ran two batches of 25 items each to check stability:

| Metric | Batch 1 | Batch 2 | Average |
|--------|---------|---------|---------|
| FP rate | 0% | 21% | 10.5% |
| FN rate | 56% | 36% | 46% |
| Overall | 72% | 72% | 72% |

**Interpretation:** The advocacy reframing killed most false positives (batch 1 had zero!), but the prompt overshot — it was now too strict, missing items that *are* relevant for lobbying. The high FN rate (46%) meant nearly half of truly relevant items were being discarded.

### v3 — Intermediate (skipped)

**Git tag:** `prompts-v3` | **Commit:** `1b7e09b`

Minor wording refinements. Not independently verified — we moved directly to v4 after analyzing the v2 false negatives in detail.

### v4 — Targeted edge case fixes

**Git tag:** `prompts-v4` | **Commit:** `ab69ce7`

Instead of broadly loosening the prompt, we analyzed the specific items Haiku flagged as false negatives and identified three systematic blind spots:

**Blind spot 1: Förderprogramme (funding programs)**

v2 missed items about Hessengeld (230M budget program), Wohnbauförderung, and Kitaförderung. These are government spending programs — clearly relevant for Liga's advocacy on social budgets. But because they weren't framed as "Kürzungen" (cuts) or "Gesetze" (laws), the dual-question filter rejected them.

**Fix:** Added "Förderprogramme wie Hessengeld, Wohnbauförderung, Kitaförderung" explicitly to the RELEVANT list under budget items.

**Blind spot 2: Federal policy cascading to municipalities**

When the BAMF cuts integration course funding, it's a federal decision — but it directly impacts Liga member organizations running those courses in Hessen. v2 treated federal policy as only relevant when it explicitly mentioned Hessen.

**Fix:** Added "Bundespolitische Entscheidungen die Kommunen/Wohlfahrt direkt betreffen" as a relevance criterion.

**Blind spot 3: Other states' internal politics**

v2 had no clear rule for items from other Bundesländer. "Brandenburg residents unhappy with care" — is that relevant because it's about Pflege, or irrelevant because it's Brandenburg? The LLM was inconsistent.

**Fix:** Added explicit exclusion: "Umfragen/Berichte aus ANDEREN Bundesländern ohne bundesweiten Politikbezug" to NICHT RELEVANT, with the escape hatch: "es sei denn es geht um ein Bundesgesetz."

Also added social housing (Sozialer Wohnungsbau) as its own relevance category — cost overruns, funding programs, and structural barriers in housing are core Liga advocacy territory.

**Haiku verification:**

| Metric | v2 average | **v4** | Change |
|--------|-----------|--------|--------|
| FP rate | 10.5% | **24%** | Worse (more noise) |
| FN rate | 46% | **28%** | Better (fewer missed) |
| Overall | 72% | **72%** | Same |

**Production impact — 1,368 items reprocessed:**

| Priority | v1/v2 | v4 | Change |
|----------|-------|-----|--------|
| high | ~25 | 3 | -88% |
| medium | ~120 | 44 | -63% |
| low | ~560 | 98 | -82% |
| none | ~660 | 1,223 | +85% |
| **Total relevant** | **~707** | **145** | **-80%** |

The feed went from 707 items (overwhelming) to 145 items (manageable). The FP/FN tradeoff is now more balanced — v2 was strongly biased toward rejecting items (high FN), v4 catches more relevant items at the cost of slightly more noise.

---

## The 72% Accuracy Ceiling

A striking finding: overall accuracy has stayed at exactly 72% across all prompt versions. The improvements change *what types* of errors occur (FP vs FN), but the total error rate remains constant.

**Why the ceiling exists:**

The task requires nuanced judgment calls that a general-purpose LLM struggles with via prompting alone:
- Is a Hessen education budget dispute "social policy" or "education policy"? (Liga's scope is ambiguous here)
- Is a federal care study from Brandenburg relevant because it reveals systemic issues, or irrelevant because it's another state?
- Is an SPD proposal for social media age limits "youth protection policy" or "media regulation"?

These are judgment calls where reasonable humans (and models) can disagree. The prompt can be made more precise, but each precision gain in one category creates edge cases in another.

---

## Model Comparison (2026-02-22)

To check whether a different model could do better, we benchmarked three models on a fixed 20-item test set with haiku-verified ground truth.

**Script:** `relevance-tuner/scripts/compare_llm_models.py` — reproducible benchmark with the same items, same prompt, same evaluation criteria.

| Model | Accuracy | JSON parse | tok/s | Avg latency | Verdict |
|-------|----------|------------|-------|-------------|---------|
| **qwen3:14b-q8_0** | **80%** | 100% | 44.9 | 25.0s | Best quality, production model |
| glm-4.7-flash-tools | 60% | 65% | 95.8 | 14.8s | Poor recall, unreliable JSON output |
| nemotron-3-nano-30b | 40% | 95% | 185.3 | 4.7s | Extremely fast but binary (high or none, no nuance) |

**Key observations:**
- **qwen3 is 2x more accurate** than the next-best model on this task
- Smaller/faster models lack the nuance for German social policy classification — they tend to either classify everything as relevant or everything as irrelevant
- JSON parsing reliability matters: glm-4.7 failed to produce valid JSON 35% of the time
- We also tested tuning nemotron's Modelfile (temperature 0.7→0.3, context 12K→16K) — no improvement (still 40%)

**Conclusion:** The bottleneck is not the model but the prompting approach. qwen3 is already the right model; to improve further we need to move beyond prompt engineering.

---

## Remaining Issues

### False positives still passing through (v4)
- Lifestyle/HR surveys without policy substance (Gen Z job preferences)
- Consumer protection debates (social media age restrictions — not welfare policy)
- Local campaign events (candidate visits to Kitas without policy content)
- Transport strikes without welfare connection (Autobahn GmbH)
- Administrative tools (salary calculators, Gehaltsrechner)

### False negatives still missed (v4)
- Hessen education funding disputes (IGS budget cuts — arguably Liga-relevant as social infrastructure)
- Child welfare structural issues (abuse victim compensation gaps in Kitas)
- Cross-state care studies with federal policy implications

---

## Iteration Workflow

Every prompt or model change follows this workflow to ensure reproducibility:

### 1. Backup (REQUIRED before any change)

```bash
cd /home/kamienc/claude.ai/ligahessen/relevance-tuner
./scripts/backup_iteration.sh
```

This snapshots: production DB, classifier model, ChromaDB, system prompt, and creates a git tag (`prompts-vN`). Use `--dry-run` to preview.

### 2. Make the change

Edit `ANALYSIS_SYSTEM_PROMPT` in `backend/services/processor.py`, or switch models.

### 3. Evaluate against fixed eval set

```bash
# LLM evaluation (offline, uses snapshotted content)
python scripts/run_llm_eval.py --prompt-tag prompts-v5

# ML classifier evaluation
EMBEDDING_BACKEND=nomic-v2 python scripts/run_classifier_eval.py --label current
```

### 4. Compare results

```bash
python scripts/compare_eval_results.py
```

### 5. Deploy (if improvement confirmed)

Reprocess production items via `/api/items/{id}/reprocess`, monitor disagreements.

### Backup requirements

- Each reprocessing iteration costs ~4 hours of GPU compute
- **ALWAYS** run `backup_iteration.sh` before making changes
- Backups protect that investment — if a prompt change degrades quality, you can roll back
- Git tags mark each iteration for easy comparison

### Running evaluations

| Command | What it does |
|---------|-------------|
| `python scripts/run_llm_eval.py` | Run Ollama against eval set |
| `python scripts/run_classifier_eval.py` | Run ML classifier against eval set |
| `python scripts/compare_eval_results.py` | Compare all results |
| `python scripts/curate_eval_set.py` | Build/update the eval set |
| `python scripts/compare_llm_models.py` | Quick model comparison (20 items) |

### Evaluation methodology

- **Fixed eval set** (`evaluations/eval_set.json`): 150+ items with snapshotted content
- **Ground truth**: Haiku-verified labels (relevant, priority, AKs, reasoning)
- **Categories**: 50 positives, 50 negatives, 50 edge cases
- **Reproducible**: Same items, same content — metrics are directly comparable across runs
- **Results stored**: `evaluations/results/` — committed to repo for history

---

## Path Forward

### Short term: ML classifier retraining (next step)

The ML classifier (embedding-based, runs before LLM) was trained on 3,519 items in January 2026. Production now has 4,311+ LLM-curated items — including 3,697 items the classifier flagged as relevant but the LLM correctly rejected. Retraining on this data should reduce the classifier's false positive rate, meaning fewer items waste LLM compute.

See [TRAINING_GUIDE.md](../../../relevance-tuner/docs/TRAINING_GUIDE.md).

### Medium term: Model distillation (fine-tuning)

To break through the 72% ceiling, we plan to fine-tune qwen3:14b on Liga-specific classification data:

1. Use Claude Haiku to create gold-standard labels for ~5,000 items (priority + reasoning)
2. Convert to instruction-tuning format (system prompt + article → JSON output)
3. Fine-tune using QLoRA on the labeled dataset
4. Compare fine-tuned model vs base+prompt on a held-out test set

The hypothesis: encoding Liga's judgment directly into model weights (via examples) will outperform describing that judgment in natural language (via prompts). The 72% ceiling likely comes from inherent ambiguity in the prompt — fine-tuning would let the model learn from hundreds of resolved edge cases instead of trying to describe the decision boundary in words.
