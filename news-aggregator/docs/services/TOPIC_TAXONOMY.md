# Topic Taxonomy

The system assigns exactly **one topic** to each relevant news item using a fixed taxonomy of 61 German social policy topics.

## How It Works

1. **LLM analyzes item** for relevance and priority (`processor.py:analyze_from_data_with_messages()`)
2. **If relevant**, a follow-up LLM call classifies the item into one taxonomy topic (`processor.py:extract_topics()`, line 483)
3. **Validation** checks the LLM response against the canonical list (`topic_taxonomy.py:validate_topic()`)
4. **Storage** in `item.metadata_["llm_analysis"]["topic"]` (JSONB, not a separate table)

Items that don't match any topic get **"Sonstiges"** with an optional `topic_suggestion` field.

## Taxonomy (56 topics)

Defined in `backend/services/topic_taxonomy.py`:

| Working Group | Topics |
|---------------|--------|
| **AK1** Grundsatz/Sozialpolitik | Sozialpolitik, Haushalt und Finanzen, Steuerpolitik, Sozialleistungen, Bürokratieabbau, Vergaberecht, Ehrenamt, Wohlfahrtsverbände, Tarifpolitik |
| **AK2** Migration/Flucht | Migration und Flucht |
| **AK3** Gesundheit/Pflege/Senioren | Pflege, Gesundheitsversorgung, Psychische Gesundheit, Sucht und Prävention, Senioren und Alter, Hospiz und Palliativ |
| **AK4** Eingliederungshilfe | Behinderung und Inklusion |
| **AK5** Kinder/Jugend/Familie | Kita und Kinderbetreuung, Kinder- und Jugendhilfe, Kinderschutz, Familienpolitik, Kinderarmut |
| **QAG** Querschnitt | Digitalisierung, Wohnen und Wohnungsnot, Armut und Existenzsicherung, Obdachlosigkeit |
| **Übergreifend** | Fachkräftemangel, Arbeitsmarkt, Bildung und Ausbildung, Gleichstellung, Gewalt und Gewaltschutz, Demokratie und Extremismus, Klimaschutz und Soziales, Recht und Gesetzgebung |

Plus **"Sonstiges"** as fallback.

### Merges (2026-03-16)

| Removed topic | Merged into | Reason |
|---------------|-------------|--------|
| Integration | Migration und Flucht | Same AK2 domain; integration = post-migration policy |
| Demenz | Pflege | Sub-topic of care; only 5 items |
| Barrierefreiheit | Behinderung und Inklusion | Same AK4 domain; only 3 items |
| Eingliederungshilfe | Behinderung und Inklusion | Same AK4 domain; legal framework for disability inclusion |
| Schuldnerberatung | Armut und Existenzsicherung | Sub-topic of poverty; only 3 items |

## Adding a New Topic

New topics require code changes — there is no UI for topic management.

### Steps

1. **Check "Sonstiges" suggestions** to see if there's demand for a new topic:
   ```bash
   ssh docker-ai 'docker exec liga-news-db psql -U liga -d liga_news -c "
   SELECT metadata_->'\''llm_analysis'\''->>'\'topic_suggestion'\'' as suggestion, COUNT(*)
   FROM items
   WHERE metadata_->'\''llm_analysis'\''->>'\'topic'\'' = '\''Sonstiges'\''
   AND metadata_->'\''llm_analysis'\''->>'\'topic_suggestion'\'' IS NOT NULL
   GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
   "'
   ```

2. **Add the topic** to `backend/services/topic_taxonomy.py`:
   - Insert in the appropriate AK section
   - Use German, capitalize like existing entries

3. **Update the LLM prompt** in `backend/services/processor.py` (`extract_topics()` method, ~line 504):
   - The taxonomy list is auto-generated from `TOPIC_TAXONOMY`, so it appears automatically
   - Add **disambiguation rules** in the `UNTERSCHEIDUNGEN` section if the new topic overlaps with existing ones
   - Add **examples** in the `BEISPIELE` section if helpful

4. **Rebuild and deploy**:
   ```bash
   # QA (gpu1)
   cd /home/kamienc/claude.ai/ligahessen/news-aggregator
   docker compose up -d --build backend

   # Production (docker-ai)
   ssh docker-ai
   cd /home/kamienc/projects/liga-hessen-news-aggregator/news-aggregator
   docker compose -f docker-compose.prod.yml up -d --build backend
   ```

5. **Optionally re-classify "Sonstiges" items** that should now match the new topic:
   ```bash
   # Find item IDs with matching suggestions
   ssh docker-ai 'docker exec liga-news-db psql -U liga -d liga_news -c "
   SELECT id FROM items
   WHERE metadata_->'\''llm_analysis'\''->>'\'topic'\'' = '\''Sonstiges'\''
   AND metadata_->'\''llm_analysis'\''->>'\'topic_suggestion'\'' ILIKE '\''%new topic keyword%'\''
   "'
   # Reprocess each via API
   curl -s -X POST http://localhost:8000/api/items/{id}/reprocess
   ```

## Where Topics Appear

| Component | Endpoint / File | Description |
|-----------|----------------|-------------|
| Word cloud | `GET /api/stats/topic-counts` | Übersicht dashboard |
| Grouped list | `GET /api/items/by-topic` | Nachrichten page |
| Item detail | `item.metadata_["llm_analysis"]["topic"]` | Per-item metadata |

### Frontend components

- `TopicWordCloud.vue` — word cloud with font size proportional to count, clickable
- `TopicList.vue` — collapsible grouped view with "Sonstiges" section

## LLM Prompt Details

The topic extraction prompt (`processor.py:extract_topics()`):
- Lists all taxonomy topics (excluding "Sozialpolitik" from main list — offered as last resort)
- Includes disambiguation rules for overlapping topics (e.g., Tarifpolitik vs Pflege when care workers strike)
- Provides classification examples
- Uses temperature 0.2 for consistency
- Returns JSON: `{"topic": "..."}` or `{"topic": "Sonstiges", "topic_suggestion": "..."}`

## Key Files

| File | Purpose |
|------|---------|
| `backend/services/topic_taxonomy.py` | Canonical taxonomy list + validation |
| `backend/services/processor.py` | `extract_topics()` — LLM classification prompt |
| `backend/api/stats.py` | `/stats/topic-counts` endpoint |
| `backend/api/items.py` | `/items/by-topic` endpoint |
| `frontend/src/components/TopicWordCloud.vue` | Word cloud component |
| `frontend/src/components/nachrichten/TopicList.vue` | Grouped topic list |
