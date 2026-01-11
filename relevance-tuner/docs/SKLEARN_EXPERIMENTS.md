# Scikit-learn Classification Experiments

**Date:** 2026-01-10
**Goal:** Fast ML classification for relevance, priority, and AK assignment
**Target Accuracy:** 90-95% relevance, 80-95% priority/AK

## Executive Summary

We tested three approaches for fast classification before LLM summarization:

| Approach | Relevance | Priority | AK | Speed |
|----------|-----------|----------|-----|-------|
| TF-IDF + LogisticRegression | 83.9% | 63.2% | 39.5% | 3234/s |
| **Embeddings + RandomForest** | **85.2%** | 63.2% | **55.3%** | 595/s |
| Vector DB (k-NN) | 83.9% | 55.3% | 50.0% | 78/s |

**Winner:** Embeddings classifier (best accuracy, still fast)

**Key Finding:** Training data has ~79 mislabeled items, limiting all models.

---

## Approach 1: TF-IDF + Sklearn

**File:** `train_liga_classifiers.py`

### Method
- TF-IDF vectorizer (2000 features, unigrams + bigrams)
- Custom features: keyword counts, text length, Liga org mentions
- Hierarchical: Relevance → Priority → AK
- LogisticRegression for relevance, RandomForest for priority/AK

### Results
```
Relevance:  83.9%
Priority:   63.2% (97.4% within-1-level)
AK:         39.5%
Speed:      3234 items/sec (0.3ms per item)
```

### Pros
- Very fast (CPU only)
- No external dependencies
- Interpretable features

### Cons
- No semantic understanding ("Kita" ≠ "Kindergarten")
- Poor AK accuracy due to limited data
- Struggles with minority classes

---

## Approach 2: Sentence Embeddings + Sklearn

**File:** `train_embedding_classifier.py`

### Method
- Sentence-transformers: `paraphrase-multilingual-MiniLM-L12-v2`
- 384-dimensional embeddings (normalized for cosine similarity)
- Same hierarchical structure as TF-IDF approach
- RandomForest classifiers (200 trees, max_depth=15)

### Results
```
Relevance:  85.2%  (+1.3% vs TF-IDF)
Priority:   63.2%  (same)
AK:         55.3%  (+15.8% vs TF-IDF) ✓
Speed:      595 items/sec (1.7ms per item)
```

### Pros
- Semantic understanding (synonyms, related concepts)
- Significant AK improvement (+40% relative)
- Still fast enough for production
- Pre-trained on large German corpus

### Cons
- Requires GPU for optimal speed
- Larger model to load (~100MB)
- Priority accuracy unchanged (data issue, not representation)

---

## Approach 3: Vector Database (k-NN)

**File:** `train_vectordb_classifier.py`

### Method
- Same embeddings as Approach 2
- k-NN classification (k=7 for relevance, k=5 for priority/AK)
- Weighted voting by cosine similarity
- No training - just stores all items and finds similar ones

### Results
```
Relevance:  83.9%
Priority:   55.3%
AK:         50.0%
Speed:      78 items/sec (12.8ms per item)
```

### Pros
- **Found 79 potential mislabels in training data**
- Interpretable (shows similar items)
- No training needed - instant updates
- Useful for data augmentation

### Cons
- Slowest approach
- Lower accuracy than trained classifiers
- Quality depends entirely on database quality

---

## Critical Finding: Training Data Quality

The Vector DB approach revealed **79 items where neighbors strongly disagree** with the label:

```
Examples of likely labeling errors:

1. "Sportwetten-Werbung raus aus den Stadien!"
   Current: relevant=1
   Neighbors: 100% say irrelevant ← LIKELY MISLABEL

2. "Ost-West-Lohnlücke: Die Ostdeutschen arbeiten..."
   Current: relevant=1
   Neighbors: 86% say irrelevant ← LIKELY MISLABEL

3. "Mathias Wagner zur Debatte zu Zurückweisungen..."
   Current: relevant=1
   Neighbors: 71% say irrelevant ← BORDERLINE
```

**Impact:** These mislabels are limiting classifier accuracy. Cleaning them could improve all approaches.

---

## Training Data Distribution

| Class | Training Samples | Issue |
|-------|------------------|-------|
| **Relevant** | 213 (25.5%) | OK |
| **Irrelevant** | 623 (74.5%) | OK |
| | | |
| **Priority** | | |
| medium | 120 | OK |
| low | 38 | Limited |
| high | 37 | Limited |
| critical | 18 | **Very limited** |
| | | |
| **AK** | | |
| AK2 (Migration) | 55 | OK |
| QAG (Querschnitt) | 53 | OK |
| AK5 (Kinder/Familie) | 46 | OK |
| AK1 (Grundsatz) | 43 | OK |
| AK3 (Pflege) | **8** | **Too few!** |
| AK4 (Eingliederung) | **8** | **Too few!** |

**To reach target accuracy, we need:**
- More AK3/AK4 examples (at least 30-50 each)
- More critical/high priority examples
- Clean the ~79 mislabeled items

---

## Recommended Production Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                     RECOMMENDED PIPELINE                         │
└─────────────────────────────────────────────────────────────────┘

    ┌─────────────┐
    │  News Item  │
    └──────┬──────┘
           │
           ▼
    ┌─────────────────────────────────────────────┐
    │  1. EMBEDDING CLASSIFIER (~2ms)             │
    │     Model: paraphrase-multilingual-MiniLM   │
    │     → relevant: bool (85% accuracy)         │
    │     → priority: critical/high/medium/low    │
    │     → ak: AK1-5/QAG (55% accuracy)          │
    └─────────────────────┬───────────────────────┘
                          │
           ┌──────────────┴──────────────┐
           │ relevant=False?             │
           │                             │
    ┌──────▼──────┐              ┌───────▼───────┐
    │    YES      │              │      NO       │
    │  Skip LLM   │              │  Continue     │
    │  Save item  │              │               │
    └─────────────┘              └───────┬───────┘
                                         │
                                         ▼
                          ┌─────────────────────────────────────────────┐
                          │  2. VECTOR DB: Find Similar (~10ms)         │
                          │     → 3 most similar Liga reactions         │
                          │     → Use as LLM context (few-shot)         │
                          └─────────────────────┬───────────────────────┘
                                                │
                                                ▼
                          ┌─────────────────────────────────────────────┐
                          │  3. LLM SUMMARIZATION (~1.3s)               │
                          │     Model: qwen3:14b-q8_0                   │
                          │     Context: Similar items from Vector DB   │
                          │     → summary                               │
                          │     → detailed_analysis                     │
                          │     → argumentationskette                   │
                          └─────────────────────┬───────────────────────┘
                                                │
                                                ▼
                          ┌─────────────────────────────────────────────┐
                          │  FINAL OUTPUT                               │
                          │  {                                          │
                          │    "relevant": true,       // from ML       │
                          │    "priority": "high",     // from ML       │
                          │    "ak": "AK3",            // from ML       │
                          │    "summary": "...",       // from LLM      │
                          │    "detailed_analysis": "...",              │
                          │    "argumentationskette": "..."             │
                          │  }                                          │
                          └─────────────────────────────────────────────┘
```

---

## Files Created

| File | Purpose |
|------|---------|
| `train_sklearn_v2.py` | Initial experiment (abandoned - slow) |
| `train_priority_classifier.py` | Dedicated priority classifier |
| `train_ak_classifier.py` | Dedicated AK classifier |
| `train_liga_classifiers.py` | TF-IDF hierarchical (Approach 1) |
| `train_embedding_classifier.py` | **Embeddings (Approach 2) - RECOMMENDED** |
| `train_vectordb_classifier.py` | Vector DB k-NN (Approach 3) |

## Models Saved

| Path | Model |
|------|-------|
| `models/liga_ml/liga_classifier.pkl` | TF-IDF classifier |
| `models/embedding/embedding_classifier.pkl` | **Embeddings classifier** |
| `models/vectordb/vectordb_classifier.pkl` | Vector DB |

---

## Next Steps

1. **Export and review 79 mislabel candidates** → Fix training data
2. **Add more AK3/AK4 examples** → Use Vector DB to find similar unlabeled items
3. **Integrate with news-aggregator** → ML classification + LLM summarization
4. **Use Vector DB for LLM context** → Find similar Liga reactions for few-shot

---

## Usage Examples

### Embedding Classifier (Production)
```python
from train_embedding_classifier import EmbeddingClassifier

clf = EmbeddingClassifier.load()
result = clf.predict(
    title="Hessen kürzt Kita-Mittel",
    content="Die Landesregierung plant Kürzungen...",
    source="hessenschau.de"
)
# {'relevant': True, 'priority': 'high', 'ak': 'AK5', ...}
```

### Vector DB (Find Similar)
```python
from train_vectordb_classifier import VectorDBClassifier

db = VectorDBClassifier.load()
similar = db.find_similar(
    title="Pflegenotstand verschärft sich",
    content="Immer mehr Heime melden Personalmangel...",
    k=5,
    filter_relevant=True
)
# Returns 5 most similar relevant items with their labels
```

### Find Mislabels
```python
db = VectorDBClassifier.load()
candidates = db.find_misclassified_candidates(threshold=0.7)
# Returns items where 70%+ of neighbors disagree with label
```
