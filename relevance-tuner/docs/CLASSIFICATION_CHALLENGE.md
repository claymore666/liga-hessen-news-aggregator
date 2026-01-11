# The Liga Hessen Classification Challenge

*A strategic analysis for discussion*

---

## 1. The Mission

The **Liga der Freien Wohlfahrtspflege Hessen** is the umbrella organization for six major welfare associations in Hessen, Germany:

- AWO (Arbeiterwohlfahrt)
- Caritas (Catholic)
- Diakonie (Protestant)
- DRK (German Red Cross)
- Der Paritätische
- Jewish Communities

Together they operate **7,300 facilities**, employ **113,000 people**, and coordinate **160,000 volunteers** across Hessen.

**Our goal:** Build a daily news briefing system that automatically:
1. Aggregates news from 100+ sources (RSS, Twitter/X, LinkedIn, Telegram, etc.)
2. Classifies relevance to Liga's work
3. Assigns priority (critical/high/medium/low)
4. Routes to the correct working group (AK1-5, QAG)
5. Generates summaries and analysis for staff

---

## 2. The Classification Problem

### 2.1 What We Need to Classify

| Task | Classes | Description |
|------|---------|-------------|
| **Relevance** | Binary | Is this news item relevant to Liga's work? |
| **Priority** | 4-class | How urgently must Liga respond? |
| **AK Assignment** | 6-class | Which working group should handle this? |

### 2.2 The Working Groups (Arbeitskreise)

| Code | Focus Area | Examples |
|------|------------|----------|
| AK1 | Grundsatz und Sozialpolitik | Budget debates, social policy, funding |
| AK2 | Migration und Flucht | Refugees, asylum, integration |
| AK3 | Gesundheit, Pflege, Senioren | Nursing homes, healthcare, elderly care |
| AK4 | Eingliederungshilfe | Disability inclusion, BTHG |
| AK5 | Kinder, Jugend, Familie | Daycare (Kitas), youth services, families |
| QAG | Querschnitt | Digitalization, housing, climate |

### 2.3 Priority Levels

| Level | Response Time | Triggers |
|-------|---------------|----------|
| **critical** | 24-48 hours | Budget cuts, facility closures, urgent legislation |
| **high** | 1-2 weeks | Draft laws, hearings, new funding guidelines |
| **medium** | Monitor | Political debates, studies, announcements |
| **low** | Background | Portraits, positive coverage, historical pieces |

---

## 3. Our Training Data

### 3.1 Data Source

News items collected from the Liga news aggregator system:
- RSS feeds from major German news outlets
- Twitter/X accounts of politicians, ministries, welfare organizations
- LinkedIn posts from sector leaders
- Telegram channels
- Google Alerts for Liga-related keywords

### 3.2 Labeling Process

Items were labeled using an LLM (qwen3:32b) with a detailed system prompt defining:
- What makes content relevant to Liga
- How to assign priority based on urgency triggers
- Which AK should handle each topic area

### 3.3 Current Dataset Statistics

```
Total items:        985
├── Relevant:       251 (25.5%)
└── Irrelevant:     734 (74.5%)

Priority distribution (relevant only):
├── medium:         144 (57.4%)
├── high:            46 (18.3%)
├── low:             37 (14.7%)
└── critical:        19 (7.6%)

AK distribution (relevant only):
├── QAG:             65 (25.9%)
├── AK2:             60 (23.9%)
├── AK1:             56 (22.3%)
├── AK5:             47 (18.7%)
├── AK4:             12 (4.8%)   ← PROBLEM
└── AK3:             11 (4.4%)   ← PROBLEM
```

### 3.4 Data Quality Issues Discovered

Our Vector DB analysis found **79 items where neighbors strongly disagree** with the assigned label:

```
Examples of likely mislabels:

1. "Sportwetten-Werbung raus aus den Stadien!"
   Label: relevant=1, AK=QAG
   Neighbors: 100% say irrelevant
   Analysis: Sports betting regulation is not Liga's domain

2. "Ost-West-Lohnlücke: Die Ostdeutschen arbeiten ab heute..."
   Label: relevant=1, AK=AK1
   Neighbors: 86% say irrelevant
   Analysis: General wage statistics, no Hessen/welfare angle

3. "#Hessen bibbert: Nach einer erneut sehr kalten Nacht..."
   Label: relevant=1, AK=QAG, priority=low
   Neighbors: Majority say irrelevant
   Analysis: Weather report without social policy angle
```

**Impact:** These mislabels create noise in training data, limiting all classifier accuracy.

---

## 4. Approaches Tested

### 4.1 Fine-tuned LLM (Qwen3-14B)

**Method:** Fine-tune Qwen3-14B with LoRA on the labeled dataset.

**Results:**
- Relevance: ~90% accuracy
- Priority: ~60% accuracy
- AK: ~60% accuracy
- Speed: ~46 items/min (1.3s per item)

**Issues:**
- Output format instability
- Hallucinated JSON fields
- Expensive to retrain
- Quality degradation noticed after fine-tuning

**Current status:** Abandoned in favor of base model + system prompt (commit `c7560a6`)

### 4.2 TF-IDF + Sklearn (Hierarchical)

**Method:**
- TF-IDF vectorizer (2000 features, bigrams)
- Custom keyword features
- LogisticRegression for relevance
- RandomForest for priority/AK

**Results:**
```
Relevance:  83.9%
Priority:   63.2%
AK:         39.5%
Speed:      3234 items/sec
```

**Pros:** Extremely fast, no GPU needed
**Cons:** No semantic understanding, poor AK accuracy

### 4.3 Sentence Embeddings + Sklearn

**Method:**
- Sentence-transformers: `paraphrase-multilingual-MiniLM-L12-v2`
- 384-dimensional semantic embeddings
- Same hierarchical classifier structure

**Results:**
```
Relevance:  85.2%  (+1.3%)
Priority:   63.2%  (same)
AK:         55.3%  (+15.8%) ✓
Speed:      595 items/sec
```

**Pros:** Semantic understanding, significant AK improvement
**Cons:** Requires embedding model, priority unchanged

### 4.4 Vector Database (k-NN)

**Method:**
- Same embeddings as above
- k-NN classification (find k nearest neighbors, vote)
- No training - just similarity search

**Results:**
```
Relevance:  83.9%
Priority:   55.3%
AK:         50.0%
Speed:      78 items/sec
```

**Pros:** Found data quality issues, interpretable, no training
**Cons:** Slowest, lower accuracy than trained classifiers

---

## 5. Summary of Results

| Approach | Relevance | Priority | AK | Speed | GPU |
|----------|-----------|----------|-----|-------|-----|
| Fine-tuned LLM | ~90% | ~60% | ~60% | 0.8/s | Yes |
| TF-IDF + Sklearn | 83.9% | 63.2% | 39.5% | 3234/s | No |
| **Embeddings + Sklearn** | **85.2%** | 63.2% | **55.3%** | 595/s | Optional |
| Vector DB (k-NN) | 83.9% | 55.3% | 50.0% | 78/s | Optional |

**Best performer:** Embeddings + Sklearn for classification accuracy
**Best for data quality:** Vector DB for finding mislabels and similar items

---

## 6. The Core Challenges

### 6.1 Insufficient Training Data

| Class | Samples | Minimum Recommended |
|-------|---------|---------------------|
| AK3 (Pflege) | 8 | 50+ |
| AK4 (Eingliederung) | 8 | 50+ |
| critical priority | 19 | 50+ |
| high priority | 46 | 100+ |

**Impact:** Classifiers cannot learn reliable patterns for rare classes.

### 6.2 Label Quality Issues

~79 items (8% of dataset) appear to be mislabeled based on neighbor analysis.

**Root cause:** LLM labeling without human review. The labeling prompt is comprehensive but the LLM makes mistakes, especially on:
- Borderline cases (sports betting regulation - is it social policy?)
- Multi-topic items (wage statistics mentioning Hessen)
- Implicit relevance (weather affecting vulnerable populations)

### 6.3 Class Imbalance

```
Priority distribution is highly skewed:
├── irrelevant: 74.5%
├── medium:     14.4%
├── high:        4.4%
├── low:         4.5%
└── critical:    2.2%
```

Standard classifiers struggle with this imbalance even with class weighting.

### 6.4 Semantic Complexity

Some classifications require deep domain knowledge:

```
"Pflegekräfte fordern bessere Arbeitsbedingungen"
→ AK3 (Pflege) - clear

"Ministerin kündigt Reform an, die Pflege, Kitas und Eingliederung betrifft"
→ AK1? AK3? AK5? QAG? - overlapping domains

"Inflation belastet besonders arme Familien"
→ AK1 (Sozialpolitik)? AK5 (Familien)? QAG (Armut)? - multiple valid answers
```

---

## 7. Target Accuracy vs Current State

| Task | Current Best | Target | Gap |
|------|--------------|--------|-----|
| Relevance | 85.2% | 90-95% | 5-10% |
| Priority | 63.2% | 80-95% | 17-32% |
| AK | 55.3% | 80-95% | 25-40% |

**Priority and AK are significantly below target.**

---

## 8. Potential Strategies to Discuss

### Strategy A: More Training Data

**Approach:** Use Vector DB to find similar unlabeled items, label them (LLM + human review), retrain.

**Effort:** High (manual review needed)
**Expected gain:** +5-15% across all tasks
**Risk:** Time-consuming, may still have labeling inconsistencies

### Strategy B: Hybrid ML + LLM

**Approach:**
- ML for relevance filtering (fast, 85%+ accuracy)
- ML for priority/AK as "suggestion"
- LLM for final priority/AK assignment with ML suggestion as context

**Effort:** Medium (integration work)
**Expected gain:** Best of both worlds
**Risk:** Added complexity, LLM still slow

### Strategy C: Focus on Relevance Only

**Approach:** Use ML only for binary relevance. Let LLM handle priority/AK.

**Effort:** Low
**Expected gain:** Simplifies system, leverages LLM strengths
**Risk:** No speed improvement for priority/AK

### Strategy D: Multi-label Classification

**Approach:** Allow items to be assigned to multiple AKs instead of forcing single choice.

**Effort:** Medium (requires label restructuring)
**Expected gain:** More realistic for overlapping topics
**Risk:** Requires relabeling data

### Strategy E: Active Learning Loop

**Approach:**
1. ML makes predictions
2. Low-confidence items flagged for human review
3. Reviewed items added to training set
4. Retrain periodically

**Effort:** High (requires review UI)
**Expected gain:** Continuous improvement
**Risk:** Requires ongoing human effort

### Strategy F: Clean Existing Data First

**Approach:** Review the 79 flagged mislabels, fix them, retrain on cleaner data.

**Effort:** Low-medium (few hours of review)
**Expected gain:** +2-5% immediately
**Risk:** May find more issues requiring larger review

---

## 9. Questions for Discussion

1. **Data quality vs quantity:** Should we prioritize fixing the 79 mislabels or gathering more AK3/AK4 examples?

2. **Acceptable accuracy:** Is 85% relevance accuracy good enough if we have a "send to LLM anyway" fallback for uncertain cases?

3. **Multi-label AKs:** Many items legitimately span multiple AKs. Should we restructure to allow this?

4. **Human-in-the-loop:** Is there appetite for a review queue where staff validate ML predictions?

5. **Speed vs accuracy tradeoff:** The embedding classifier is 8x slower than TF-IDF but more accurate. Which matters more?

6. **Priority granularity:** Is the 4-level priority (critical/high/medium/low) too fine-grained? Would 2-3 levels work better?

7. **LLM role:** Should ML handle everything with LLM only for summarization, or should LLM remain the authority on classification?

---

## 10. Recommended Next Steps

Based on the experiments, here's a suggested path forward:

### Immediate (this week)
1. ✅ Review and fix the 79 mislabel candidates
2. Add 30-50 more AK3/AK4 examples via Vector DB similarity search
3. Retrain embedding classifier on cleaned data

### Short-term (next 2 weeks)
4. Implement hybrid pipeline: ML classification → Vector DB context → LLM summarization
5. Add confidence thresholds: low-confidence ML predictions go to LLM for verification

### Medium-term (next month)
6. Build simple review UI for staff to validate/correct predictions
7. Implement active learning loop
8. Consider multi-label AK classification

---

## Appendix: File Reference

| File | Purpose |
|------|---------|
| `train_embedding_classifier.py` | Best performing classifier |
| `train_vectordb_classifier.py` | Similarity search + mislabel detection |
| `train_liga_classifiers.py` | TF-IDF baseline |
| `LABELING_PROMPT.md` | Detailed labeling criteria |
| `SKLEARN_EXPERIMENTS.md` | Technical results summary |
| `models/embedding/` | Saved embedding classifier |
| `models/vectordb/` | Saved vector database |
