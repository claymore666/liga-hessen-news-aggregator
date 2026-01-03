# Documentation Index

This repository contains documentation for building a **News Aggregator** system, with an initial use case for the **Liga der Freien Wohlfahrtspflege Hessen**.

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     DOCUMENTATION STRUCTURE                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────┐     ┌─────────────────┐                   │
│  │   GENERIC       │     │   LIGA-SPECIFIC │                   │
│  │   SYSTEM        │     │   USE CASE      │                   │
│  │                 │     │                 │                   │
│  │ • Architecture  │     │ • Stakeholders  │                   │
│  │ • Tech Stack    │     │ • Keywords      │                   │
│  │ • Connectors    │     │ • Sources       │                   │
│  │ • Deployment    │     │ • Rules         │                   │
│  └─────────────────┘     └─────────────────┘                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Documents

### System Architecture (Generic)

| Document | Description |
|----------|-------------|
| **[NewsAggregatorArchitecture.md](NewsAggregatorArchitecture.md)** | Main technical architecture: connectors, data model, API, Vue frontend, LLM integration, deployment |
| **[FREE_LLMS.md](FREE_LLMS.md)** | LLM provider comparison: OpenRouter, Groq, Mistral, Ollama - free tiers and costs |
| **[../CLAUDE.md](../CLAUDE.md)** | Claude Code guidance: project overview, key concepts, file structure (root level) |

### Liga Hessen Use Case

| Document | Description |
|----------|-------------|
| **[DailyBriefingArchitecture.md](DailyBriefingArchitecture.md)** | Liga-specific configuration: sources, keywords, stakeholders, priority rules |
| **[Daily-Briefing-System für die Liga...md](Daily-Briefing-System%20für%20die%20Liga%20der%20Freien%20Wohlfahrtspflege%20Hessen.md)** | Business requirements: trigger keywords, reaction times, monitoring strategy |
| **[Stakeholder-Datenbank...md](Stakeholder-Datenbank%20für%20das%20Daily-Briefing-System%20der%20Liga%20Hessen.md)** | 80+ stakeholders: ministries, politicians, journalists, NGOs with social media handles |
| **[Umfassende Social Media Analyse...md](Umfassende%20Social%20Media%20Analyse%20der%20Liga%20der%20Freien%20Wohlfahrtspflege%20Hessen.md)** | Liga's social media presence, campaigns, communication patterns |
| **[liga_hessen_recherche.md](liga_hessen_recherche.md)** | Research notes: Liga structure, member organizations, contacts |

### Reference

| Document | Description |
|----------|-------------|
| **[Hessen.md](Hessen.md)** | (Empty placeholder) |

---

## Tech Stack Summary

| Layer | Technology |
|-------|------------|
| Frontend | Vue 3 + Vite + TailwindCSS + Pinia |
| Backend | Python 3.12 + FastAPI + SQLAlchemy |
| Database | SQLite |
| LLM | Ollama (gpu1) + OpenRouter (fallback) |
| Deployment | Docker + docker-compose |

---

## Quick Links

### For Development
- [Connector Interface](NewsAggregatorArchitecture.md#3-connector-system) - How to add new source types
- [Data Model](NewsAggregatorArchitecture.md#4-data-model) - Database schema
- [API Endpoints](NewsAggregatorArchitecture.md#6-api-endpoints) - REST API reference
- [Vue Components](NewsAggregatorArchitecture.md#7-vue-frontend) - Frontend structure

### For Configuration (Liga Use Case)
- [Source Configuration](DailyBriefingArchitecture.md#1-datenquellen-module) - RSS feeds, scrapers, social media
- [Trigger Keywords](Daily-Briefing-System%20für%20die%20Liga%20der%20Freien%20Wohlfahrtspflege%20Hessen.md#die-25-kritischsten-trigger-keywords-für-eilmeldungen) - High-priority keywords
- [Stakeholder Database](DailyBriefingArchitecture.md#stakeholder-seed-data) - SQL seed data

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         WEB GUI (Vue 3)                         │
│              Dashboard  │  Admin  │  Settings                   │
└────────────────────────────────┬────────────────────────────────┘
                                 │ REST API
┌────────────────────────────────┴────────────────────────────────┐
│                      BACKEND (FastAPI)                          │
│  Scheduler → Connectors → Normalizer → LLM → Database           │
└────────────────────────────────┬────────────────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
┌──────────────┐        ┌──────────────┐        ┌──────────────┐
│     RSS      │        │   Twitter    │        │     PDF      │
│   Bluesky    │        │   LinkedIn   │        │    HTML      │
└──────────────┘        └──────────────┘        └──────────────┘
     Sources                 Social                Documents
```

---

## File Tree

```
docs/
├── INDEX.md                          # This file
├── NewsAggregatorArchitecture.md     # Generic system architecture
├── FREE_LLMS.md                      # LLM provider comparison
│
├── DailyBriefingArchitecture.md      # Liga-specific architecture
├── Daily-Briefing-System...md        # Liga business requirements
├── Stakeholder-Datenbank...md        # Liga stakeholders database
├── Umfassende Social Media...md      # Liga social media analysis
├── liga_hessen_recherche.md          # Liga research notes
│
└── Hessen.md                         # (placeholder)

../CLAUDE.md                          # Claude Code guidance (root level)
```

---

## Status

| Component | Status |
|-----------|--------|
| Architecture Design | ✅ Complete |
| Data Model | ✅ Complete |
| Connector Interface | ✅ Defined |
| API Specification | ✅ Defined |
| Frontend Design | ✅ Defined |
| Liga Configuration | ✅ Complete |
| **Backend Core** | ✅ Implemented |
| **Database Layer** | ✅ Implemented |
| **REST API** | ✅ Implemented |
| **Pipeline/Scheduler** | ✅ Implemented |
| **Test Suite** | ✅ Implemented |
| Connectors | ⏳ In progress |
| Frontend | ⏳ Not started |

---

*Last updated: January 2026*
