# Keyword Classification Challenges

## Problem Statement

Simple keyword matching for AK (Arbeitskreis) classification has limitations:

1. **Ambiguous keywords** - Same word has different meanings depending on context
2. **False positives** - Keywords match unrelated content due to German word composition
3. **Cross-AK overlap** - Some topics span multiple working groups

Current keyword-based classification achieves ~71% AK accuracy. To improve, we need **context-aware keyword matching**.

## Goal

Develop a context-aware classification approach that considers:
- Surrounding words/phrases (collocation)
- Document-level topic signals
- Negative context indicators (what makes a keyword NOT apply)

## Ambiguous Keywords

### `tagespflege` (AK3 vs AK5)

**Problem**: Means both "elderly day care" (AK3) and "child day care" (AK5)

| Context | Correct AK | Example |
|---------|------------|---------|
| Senioren, Pflege, ältere Menschen | AK3 | "Tagespflege für Senioren" |
| Kita, Kinder, Betreuung, Eltern | AK5 | "Tagespflege für Kleinkinder" |

**Observed matches**:
- "Studie zeigt großen Reformbedarf in Hessens Kitas" → AK5 (child context)
- "Maßnahmenpaket zur Stärkung frühkindlicher Bildung" → AK5 (child context)

**Solution approach**: Check for co-occurring keywords
```python
if "tagespflege" in text:
    if any(kw in text for kw in ["kita", "kinder", "eltern", "frühkindlich"]):
        return "AK5"
    elif any(kw in text for kw in ["senioren", "pflege", "ältere"]):
        return "AK3"
```

### `fachkräftemangel` (Generic)

**Problem**: Labor shortage applies to ALL sectors, not AK3-specific

| Context | Correct AK | Example |
|---------|------------|---------|
| Pflege, Krankenhaus, Gesundheit | AK3 | "Fachkräftemangel in der Pflege" |
| Kita, Erzieher | AK5 | "Fachkräftemangel in Kitas" |
| General economy | None/AK1 | "Fachkräftemangel bremst Wirtschaft" |

**Observed matches**:
- "Arbeitsmarkt von schwächelnder Konjunktur geprägt" → Generic economy
- "Kommunen durch Personal der Landesverwaltung unterstützen" → Generic administration

**Solution approach**: Only count if combined with sector-specific keywords
```python
if "fachkräftemangel" in text:
    if any(kw in text for kw in ["pflege", "krankenhaus", "gesundheit"]):
        boost_ak3()
    elif any(kw in text for kw in ["kita", "erzieher", "betreuung"]):
        boost_ak5()
    # else: ignore as generic
```

### `wohngruppe` (AK4 vs AK2)

**Problem**: "Residential group" applies to both disability housing AND refugee housing

| Context | Correct AK | Example |
|---------|------------|---------|
| Behinderung, Eingliederung, Teilhabe | AK4 | "Wohngruppe für Menschen mit Behinderung" |
| Geflüchtete, Asyl, Unterbringung | AK2 | "Wohngruppe für unbegleitete Minderjährige" |

**Observed match (false positive)**:
- "Abschiebungen 2025: Eine neue Härte" → AK2 (refugee context, NOT AK4)

**Solution approach**: Check for disability vs migration context
```python
if "wohngruppe" in text:
    if any(kw in text for kw in ["behinder", "eingliederung", "teilhabe"]):
        return "AK4"
    elif any(kw in text for kw in ["geflüchtet", "asyl", "abschieb"]):
        return "AK2"
```

### `autismus` (AK4 vs AK3)

**Problem**: Autism can be discussed as disability (AK4) or mental health (AK3)

| Context | Correct AK | Example |
|---------|------------|---------|
| Eingliederung, Teilhabe, Schule | AK4 | "Autismus und schulische Inklusion" |
| Psychiatrie, Diagnose, Therapie | AK3 | "Autismus-Spektrum-Störung Diagnostik" |

**Observed match**:
- "Psychische Erkrankungen treffen nicht nur Betroffene" → AK3 (mental health context)

**Decision**: Currently excluded from AK4 keywords due to ambiguity. Could add with context check.

## False Positive Patterns

### German Word Composition

The word `sucht` appears in many unrelated compound words:

| Word | Meaning | Relevant? |
|------|---------|-----------|
| Sucht | Addiction | Yes (AK3) |
| Suchthilfe | Addiction support | Yes (AK3) |
| versucht | attempted | No |
| gesucht | searched/wanted | No |
| besucht | visited | No |
| Sehnsucht | longing | No |

**Solution**: Only use specific compound words (`suchthilfe`, `suchtberatung`, `suchtprävention`), never bare `sucht`.

### Generic Terms

Some keywords are too broad:

| Keyword | Problem | Better Alternative |
|---------|---------|-------------------|
| `förderung` | Matches any funding/support | Remove or require context |
| `teilhabe` | Used broadly in social policy | Keep only for AK4 with disability context |
| `therapie` | Medical AND psychological | Consider splitting by context |

## Implementation Ideas

### 1. Keyword Pairs (Co-occurrence)

Define required context for ambiguous keywords:

```python
CONTEXTUAL_KEYWORDS = {
    "tagespflege": {
        "AK3": ["senioren", "pflege", "ältere", "alten"],
        "AK5": ["kita", "kinder", "eltern", "frühkindlich"],
    },
    "fachkräftemangel": {
        "AK3": ["pflege", "krankenhaus", "gesundheit", "klinik"],
        "AK5": ["kita", "erzieher", "betreuung"],
    },
}
```

### 2. Negative Context (Exclusion)

Define when a keyword should NOT count:

```python
EXCLUDE_CONTEXT = {
    "wohngruppe": {
        "AK4": ["geflüchtet", "asyl", "abschieb", "migration"],  # exclude if refugee context
    },
}
```

### 3. Embedding-Based Context

Use embeddings to measure semantic similarity to AK definitions:

```python
def classify_with_context(text, keyword, embedder):
    # Get embedding of text around keyword
    context_embedding = embedder.embed(extract_context(text, keyword, window=100))

    # Compare to AK definition embeddings
    similarities = {
        ak: cosine_similarity(context_embedding, ak_definition_embedding)
        for ak, ak_definition_embedding in AK_EMBEDDINGS.items()
    }
    return max(similarities, key=similarities.get)
```

## Keywords Removed from config.py (2026-01-11)

**Status:** The keyword dictionaries (`AK_KEYWORDS`, `LIGA_KEYWORDS`, `HESSEN_KEYWORDS`, `IRRELEVANT_KEYWORDS`, `URGENT_KEYWORDS`) have been **removed from config.py** because they are not used in the production system.

### Production Architecture

The current production pipeline does NOT use keyword-based classification:

1. **Relevance**: Semantic embedding classifier (NomicV2 + RandomForest)
2. **Priority & AK**: LLM-based (qwen3 with LABELING_PROMPT.md)

Keywords for priority scoring are defined separately in `news-aggregator/backend/services/processor.py` as `PRIORITY_KEYWORDS`.

### Legacy Code

The old TF-IDF classifier (`train_liga_classifiers.py`) still uses keywords, but they are now defined locally in that file rather than imported from config.py. This script is not used in production.

### Reference: Keyword Analysis

The analysis below is preserved for reference if keyword-based classification is ever revisited.

<details>
<summary>Archived Keyword Analysis (click to expand)</summary>

#### Critical Ambiguity Issues

| Keyword | Problem |
|---------|---------|
| `liga` | Matches "Bundesliga" (football) |
| `werkstatt` | Matches car repair shops |
| `reform`, `förderung` | Too generic |
| `integration` | Tech context |
| `therapie`, `reha` | Wellness/sports |

#### Recommended Keyword Improvements (if ever used)

- Use compound forms: `liga der freien wohlfahrtspflege` instead of `liga`
- Use specific terms: `behindertenwerkstatt` instead of `werkstatt`
- Add sports blocklist: `bundesliga`, `dfb`, `spieltag`, etc.

</details>

## Next Steps

1. **Implement contextual keyword matching** - Add co-occurrence rules for ambiguous keywords
2. **Test on production data** - Measure improvement in classification accuracy
3. **Consider hybrid approach** - Combine keyword context with embedding classifier
4. **Expand training data** - Use contextual matching to find better AK3/AK4 examples
5. **Apply keyword efficiency fixes** - Implement priority 1 changes from audit above

## Related Files

- `config.py` - Main keyword definitions
- `train_embedding_classifier.py` - Current classifier using keywords as features
- `services/classifier-api/classifier.py` - Production classifier

---
*Created: 2026-01-11*
*Last updated: 2026-01-11 (keywords removed from config.py - not used in production)*
