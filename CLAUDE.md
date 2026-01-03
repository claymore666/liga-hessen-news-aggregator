# Project Knowledge

This file provides guidance to Claude Code when working with this repository.

## Git Branching Workflow

**CRITICAL**: Always follow this branching strategy for this project.

```
main (production-ready)
  └── dev (integration branch)
        └── milestone/X-name (feature work)
```

### Workflow Rules
1. **Never commit directly to `main` or `dev`**
2. **Always work on a milestone branch** (e.g., `milestone/1-core-backend`)
3. When milestone work is complete:
   - Create PR: `milestone/X` → `dev`
   - Review and merge
4. When ready for release:
   - Create PR: `dev` → `main`
   - Review and merge

### Branch Naming Convention
- `milestone/1-core-backend`
- `milestone/2-connector-system`
- `milestone/3-llm-integration`
- `milestone/4-vue-frontend`
- `milestone/5-deployment`

### Current Branches
- `main` - Production branch
- `dev` - Integration/staging branch
- `milestone/1-core-backend` - Current work (Core Backend issues #1-5)

## Project Structure

- `docs/` - Architecture documentation
- `news-aggregator/` - Application code
  - `backend/` - Python/FastAPI
  - `frontend/` - Vue 3/Vite

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | Vue 3 + Vite + TailwindCSS + Pinia |
| Backend | Python 3.12 + FastAPI + SQLAlchemy |
| Database | SQLite |
| LLM | Ollama (gpu1) + OpenRouter (fallback) |

## Testing Requirements

**IMPORTANT**: Always create tests for each feature.

- Backend: pytest + pytest-asyncio
- Run tests before committing: `pytest tests/`
- Test files mirror source structure: `tests/test_<module>.py`
- Minimum coverage for new features

## GitHub Repository

https://github.com/claymore666/liga-hessen-news-aggregator

See `docs/INDEX.md` for full documentation index.

---

## Projektbeschreibung

Dieses Repository enthält das **Daily-Briefing-System** der **Liga der Freien Wohlfahrtspflege Hessen** – ein automatisiertes System zur Erfassung, Filterung und Analyse politischer Nachrichten für den hessischen Wohlfahrtsverband.

## Kernkonzepte

### Liga Hessen
- Dachverband von 6 Wohlfahrtsverbänden: AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden
- Vertritt 113.000 Beschäftigte und 160.000 Ehrenamtliche in 7.300 Einrichtungen
- Hauptthemen: Pflege, Kita, Migration, Eingliederungshilfe, Sozialfinanzierung
- Primärer politischer Kontakt: HMAIJS (Ministerin Heike Hofmann)

### Arbeitskreise (AK)
- **AK 1**: Grundsatz und Sozialpolitik
- **AK 2**: Migration und Flucht
- **AK 3**: Gesundheit, Pflege und Senioren
- **AK 4**: Eingliederungshilfe
- **AK 5**: Kinder, Jugend, Frauen und Familie
- **QAG**: Digitalisierung, Klimaschutz, Wohnen

### Dringlichkeitsstufen im Briefing-System
- 🔴 **EILIG**: Haushaltskürzungen, Gesetzeseinbringungen (<24h)
- 🟠 **WICHTIG**: Anhörungsfristen, Richtlinienentwürfe (1 Woche)
- 🟡 **BEOBACHTEN**: Politische Aussagen, Parteipositionierungen
- 🔵 **INFORMATION**: Hintergrundberichte, zur Kenntnis

## System-Architektur

Das Daily-Briefing-System folgt einer dreistufigen Pipeline:

1. **Datenerfassung**: RSS-Feeds (inkl. Google Alerts), HTML-Scraping, Social Media (Mastodon, Twitter via Nitter, Bluesky), Landtag-PDF-Dokumente
2. **Duplikat-Erkennung**: Dreistufig (GUID → Titel-Ähnlichkeit → Content-Hash)
3. **Keyword-Filter (Stufe 1)**: Trigger-Kategorien mit Gewichtung (finanz_kritisch=10, struktur=8, reform=6, etc.)
4. **LLM-Verarbeitung (Stufe 2)**: Multi-Provider-Fallback (OpenRouter → Groq → Mistral)

### Hybridansatz: Eigenes System + Google Alerts

| Aspekt | Eigenes System | Google Alerts (RSS) |
|--------|----------------|---------------------|
| Stärke | Tiefe, Struktur, LLM-Analyse | Breite, Agenturen, Regionalpresse |
| Quellen | ~15 kuratierte | Hunderte (dpa, epd, KNA, Regionalmedien) |

Google Alerts werden als RSS-Feeds eingebunden (keine offizielle API).

### Web-Interface

- **Dashboard** (`/`): Live-Ansicht aller Meldungen mit 🆕-Markierung für neue Items
- **Admin** (`/admin`): Quellen konfigurieren, Keywords bearbeiten, System-Status
- **Echtzeit**: WebSocket-Updates, Browser-Notifications bei 🔴 EILIG-Meldungen

### LLM-Provider-Strategie

| Priorität | Anbieter | Modell | Tägliches Limit |
|-----------|----------|--------|-----------------|
| Primär | OpenRouter | Llama 3.3 70B | 1.000 Requests |
| Backup | Groq | Llama 3.1 8B | 14.400 Requests |
| Fallback | Mistral | Devstral 2 | ~33 Mio. Tokens |

## Wichtige Trigger-Keywords

**Höchste Priorität** (finanz_kritisch):
Kürzung, Streichung, Haushaltssperre, Finanzierungslücke, Kahlschlag, Förderentzug

**Struktur-Trigger**:
Schließung, Abbau, existenzbedrohend, Insolvenz, Personalreduzierung

**Reform-Trigger**:
Gesetzesänderung, Novelle, Anhörung, Regierungsentwurf, Bundesratsentscheidung

## RSS-Feeds für Monitoring

Primäre Quellen (siehe Stakeholder-Datenbank):
- `hessenschau.de/index.rss`
- `faz.net/rss/aktuell/rhein-main/`
- `fr.de/?_XML=rss`
- `proasyl.de/news/feed/`
- `bmas.de/DE/Service/Newsletter/RSS/rss.html`

## Dokumentstruktur

| Datei | Inhalt |
|-------|--------|
| `DailyBriefingArchitecture.md` | Technische Systemarchitektur, Datenbank-Schema, Projektstruktur |
| `Daily-Briefing-System für die Liga...md` | Fachliche Anforderungen, Trigger-Keywords, Priorisierungsmatrix |
| `Stakeholder-Datenbank...md` | 80+ Stakeholder, Social-Media-Handles, RSS-Feeds |
| `FREE_LLMS.md` | LLM-API-Vergleich, kostenlose Kontingente |
| `liga_hessen_recherche.md` | Organisationsstruktur der Liga |
| `Umfassende Social Media Analyse...md` | Social-Media-Strategie und Kampagnen |

## Sprachhinweise

Die Dokumentation ist durchgehend auf **Deutsch** verfasst. Code-Beispiele und Konfigurationen verwenden deutsche Bezeichner (z.B. `zustaendiger_ak`, `dringlichkeit`).
