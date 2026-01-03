# Daily-Briefing-System Architektur
## Liga der Freien Wohlfahrtspflege Hessen

---

## Systemübersicht

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATENERFASSUNG                                    │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐         │
│  │  RSS   │ │  HTML  │ │Mastodon│ │Twitter │ │Bluesky │ │Landtag │         │
│  │ Feeds  │ │Scraper │ │  API   │ │ Nitter │ │  RSS   │ │  PDF   │         │
│  └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘         │
│      │          │          │          │          │          │               │
│      └──────────┴──────────┴──────────┴──────────┴──────────┘               │
│                                                                             │
│  ┌────────┐ ┌────────┐                                                      │
│  │LinkedIn│ │YouTube │  ← Phase 2 (API-basiert, höherer Aufwand)           │
│  │  API   │ │  RSS   │                                                      │
│  └────────┘ └────────┘                                                      │
│                                 │                                           │
│                                 ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    RAW ITEMS DATABASE                                │   │
│  │                    (SQLite / JSON)                                   │   │
│  │                    ~50-150 Einträge/Tag                              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         KEYWORD-FILTER (STUFE 1)                            │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Trigger-Keywords scannen:                                           │   │
│  │  • Finanz: Kürzung, Streichung, Haushaltssperre, Förderentzug       │   │
│  │  • Reform: Gesetzesänderung, Novelle, Anhörung, Verordnung          │   │
│  │  • Struktur: Schließung, Abbau, existenzbedrohend                   │   │
│  │  • Themen: Pflegenotstand, Kitaplätze, Migrationsberatung           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                     ┌──────────────┴──────────────┐                         │
│                     ▼                              ▼                        │
│              ┌────────────┐                 ┌────────────┐                  │
│              │  RELEVANT  │                 │ IRRELEVANT │                  │
│              │  ~20-40%   │                 │  ~60-80%   │                  │
│              └─────┬──────┘                 └────────────┘                  │
│                    │                              │                         │
│                    │                              └──► Archiv (optional)    │
└────────────────────┼────────────────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      LLM-VERARBEITUNG (STUFE 2)                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Multi-Provider Pipeline                           │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │   │
│  │  │  OpenRouter │  │    Groq     │  │   Mistral   │                  │   │
│  │  │   (Primär)  │  │  (Backup)   │  │ (Fallback)  │                  │   │
│  │  │ Llama 3.3   │  │ Llama 3.1   │  │  Devstral   │                  │   │
│  │  │    70B      │  │    8B       │  │     2       │                  │   │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                  │   │
│  │         │                │                │                          │   │
│  │         └────────────────┴────────────────┘                          │   │
│  │                          │                                           │   │
│  │                          ▼                                           │   │
│  │  ┌───────────────────────────────────────────────────────────────┐  │   │
│  │  │ Pro Artikel:                                                   │  │   │
│  │  │ 1. Zusammenfassung (2-3 Sätze)                                │  │   │
│  │  │ 2. Dringlichkeitsstufe (🔴🟠🟡🔵)                              │  │   │
│  │  │ 3. Zuständiger AK (AK1-5, QAG)                                │  │   │
│  │  │ 4. Handlungsempfehlung (1 Satz)                               │  │   │
│  │  │ 5. Relevante Stakeholder                                       │  │   │
│  │  └───────────────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         OUTPUT-GENERIERUNG                                  │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                     Daily Briefing (Markdown)                        │  │
│  │  ┌────────────────────────────────────────────────────────────────┐ │  │
│  │  │ 🔴 EILMELDUNGEN (Reaktion <24h)                                │ │  │
│  │  │ 🟠 WICHTIG (Reaktion diese Woche)                              │ │  │
│  │  │ 🟡 BEOBACHTEN (Strategische Relevanz)                          │ │  │
│  │  │ 🔵 INFORMATION (Zur Kenntnis)                                  │ │  │
│  │  └────────────────────────────────────────────────────────────────┘ │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                              │
│              ┌───────────────┼───────────────┐                              │
│              ▼               ▼               ▼                              │
│        ┌──────────┐   ┌──────────┐   ┌──────────┐                          │
│        │  E-Mail  │   │  Webhook │   │   Datei  │                          │
│        │  (SMTP)  │   │ (Slack)  │   │  (.md)   │                          │
│        └──────────┘   └──────────┘   └──────────┘                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Komponenten-Spezifikation

### 1. Datenquellen-Module

#### 1.1 RSS-Feed-Scraper
```yaml
quellen:
  - name: "hessenschau"
    url: "https://www.hessenschau.de/index.rss"
    kategorie: "Landespolitik"
    priorität: 1
    
  - name: "FAZ Rhein-Main"
    url: "https://www.faz.net/rss/aktuell/rhein-main/"
    kategorie: "Regionales"
    priorität: 1
    
  - name: "Frankfurter Rundschau"
    url: "https://www.fr.de/?_XML=rss"
    kategorie: "Medien"
    priorität: 2
    
  - name: "FR Politik"
    url: "https://www.fr.de/politik/?_XML=rss"
    kategorie: "Bundespolitik"
    priorität: 2
    
  - name: "BMAS"
    url: "https://www.bmas.de/DE/Service/Newsletter/RSS/rss.html"
    kategorie: "Bundesgesetzgebung"
    priorität: 1
    
  - name: "PRO ASYL"
    url: "https://www.proasyl.de/news/feed/"
    kategorie: "Migration"
    priorität: 2
    
  - name: "HLS Sucht"
    url: "https://hls-online.org/?type=100"
    kategorie: "Gesundheit"
    priorität: 3
```

#### 1.2 HTML-Scraper (Pressemitteilungen)
```yaml
websites:
  # Ministerien
  - name: "HMAIJS"
    url: "https://soziales.hessen.de/presse"
    selektor: "article.news-item, div.press-release"
    priorität: 1
    
  - name: "Hessischer Landtag"
    url: "https://hessischer-landtag.de/presse"
    selektor: "div.press-item"
    priorität: 1
    
  # Liga und Mitglieder
  - name: "Liga Hessen"
    url: "https://liga-hessen.de/aktuelles"
    selektor: "article, div.news-entry"
    priorität: 1
    
  - name: "Diakonie Hessen"
    url: "https://diakonie-hessen.de/presse"
    priorität: 2
    
  - name: "DRK Hessen"
    url: "https://drk-hessen.de/presse"
    priorität: 2
    
  - name: "Paritätischer Hessen"
    url: "https://paritaet-hessen.org/aktuelles"
    priorität: 2
    
  # Kommunale Spitzenverbände
  - name: "Hessischer Städtetag"
    url: "https://hess-staedtetag.de/aktuelles"
    priorität: 2
    
  - name: "Hessischer Landkreistag"
    url: "https://hlt.de/aktuelles"
    priorität: 2
    
  # Sozialpartner
  - name: "DGB Hessen-Thüringen"
    url: "https://hessen-thueringen.dgb.de/presse"
    priorität: 3
    
  - name: "VhU"
    url: "https://vhu.de/presse"
    priorität: 3
```

#### 1.3 Social Media Scraper

**Mastodon (einfach, offene API):**
```yaml
mastodon:
  # Landesregierung
  - handle: "@landesregierung@social.hessen.de"
    name: "Landesregierung Hessen"

  - handle: "@borisrhein@social.hessen.de"
    name: "Boris Rhein (MP)"

  - handle: "@HessischerLandtag@social.bund.de"
    name: "Landtag"

  # Ministerien
  - handle: "@wirtschafthessen@social.hessen.de"
    name: "Wirtschaftsministerium"
```

#### 1.3.1 Vollständige Ministerien-Social-Media-Matrix

**Alle hessischen Ministerien müssen überwacht werden** – jedes kann Themen posten, die für Liga-AKs relevant sind.

| Ministerium | Twitter/X | Instagram | Mastodon | Relevante AKs |
|-------------|-----------|-----------|----------|---------------|
| **Staatskanzlei** | @RegHessen | @regierunghessen | @landesregierung@social.hessen.de | Alle |
| **HMAIJS (Soziales)** | @SozialHessen | @sozialhessen | - | AK 1,2,4,5 |
| **Gesundheit/Pflege** | - | - | - | AK 3, 5 |
| **Finanzen** | @finanzenhessen | @finanzenhessen | - | AK 1 |
| **Kultus** | @SchuleHessen | @schulehessen | - | AK 5 |
| **Inneres** | - | @innen.hessen | - | AK 2 |
| **Wirtschaft** | @WirtschaftHE | @wirtschafthessen | @wirtschafthessen@social.hessen.de | AK 1, QAG Wohnen |
| **Justiz** | @Justiz_Hessen | @justiz__hessen | - | AK 1 |
| **Umwelt** | - | @umwelthessen | - | QAG Klima |
| **Digitales** | @DigitalHessen | @digitalhessen | - | QAG Digi |
| **Wissenschaft** | - | @wissenschafthessen | - | AK 3 |

**Minister/innen mit persönlichen Accounts (höhere Relevanz!):**

| Person | Funktion | Twitter/X | Instagram | Follower |
|--------|----------|-----------|-----------|----------|
| Boris Rhein | MP | @boris_rhein | @boris.rhein | hoch |
| Heike Hofmann | Soziales | @hofmann_heike | @heike_hofmann | - |
| Kaweh Mansoori | Wirtschaft, stellv. MP | @El_KaWeh_ | @kawehmansoori | 3.700 / 6.600 |
| Kristina Sinemus | Digitales | @KSinemus | - | 1.400 |
| Ingmar Jung | Umwelt | - | @ingmarjung | 3.800 |
| Timon Gremmels | Wissenschaft | @Timon_Gremmels | @gremmels | - |
| Manfred Pentz | Europa | @Ma_Pentz | @manfredpentz | 5.100 |
| Armin Schwarz | Kultus | - | @armin_schwarz_cdu | - |

**Minister ohne persönliche Social-Media-Präsenz:**
- Diana Stolz (Gesundheit) – keine Accounts gefunden
- Roman Poseck (Inneres) – keine Accounts gefunden

**Bluesky-Status Hessen (Stand: Januar 2026):**
- Hessische Landesregierung: **KEINE offiziellen Bluesky-Accounts**
- Hessischer Landtag: **KEIN offizieller Account**
- Einige hessische SPD/Grüne-MdL könnten individuell vertreten sein
- **Monitoring-Empfehlung:** Bundespolitiker und Journalisten auf Bluesky sind relevanter

**LinkedIn-Status Hessen:**
- Hessische Ministerien: **meist inaktiv oder nicht vorhanden**
- Minister/innen: Einzelne Profile (nicht priorisiert)
- **Monitoring-Empfehlung:** Fokus auf BMAS und Bundespolitik-Accounts

#### 1.3.2 Journalisten Landespolitik Hessen

**Wichtig:** Journalisten breaken oft Nachrichten zuerst auf Twitter/X, bevor sie in der Zeitung erscheinen. Monitoring der relevanten Journalisten-Accounts erhöht die Reaktionsgeschwindigkeit.

| Name | Medium | Funktion | Twitter/X | Schwerpunkt | Relevanz |
|------|--------|----------|-----------|-------------|----------|
| **Pitt von Bebenburg** | FR | Chefreporter | @PvBebenburg | Landespolitik | ⭐⭐⭐ |
| **Peter Hanack** | FR | Redakteur | - | Soziales, Bildung | ⭐⭐⭐ |
| **Dr. Ewald Hetrodt** | FAZ | Parlamentskorr., LPK-Sprecher | - | Landtag | ⭐⭐⭐ |
| **Simone Behse** | hr | Landtagskorr., LPK-Vorstand | - | Landtag | ⭐⭐⭐ |
| **Christian P. Stadtfeld** | OSTHESSEN|NEWS | Chefredakteur, LPK-Vorstand | - | Digital, Osthessen | ⭐⭐ |
| **Timo Steppat** | FAZ | Korrespondent Wiesbaden | - | Landespolitik | ⭐⭐ |
| **Mechthild Harting** | FAZ | Redakteurin | - | Planung, Umwelt | ⭐ |
| **Jörn Perske** | hessenschau | Online-Redakteur | - | Osthessen, Social | ⭐ |

**Landespressekonferenz Hessen (LPK):**
- ~80 Mitglieder, akkreditierte Journalisten für Landespolitik
- Sitz im Hessischen Landtag, Wiesbaden
- Vorstand (2023): Hetrodt (FAZ), Behse (hr), Stadtfeld (OSTHESSEN|NEWS)

**Hinweis Twitter/X-Exodus:**
- **Hessischer Rundfunk** verlässt X nach der Bundestagswahl 2025
- **SPD Hessen** hat Twitter-Accounts bereits gelöscht
- Viele Journalisten wechseln zu **LinkedIn** oder **Mastodon**
- Für Echtzeit-Monitoring wird Twitter dennoch weiter relevant bleiben

**Relevanz-Matrix: Welches Ministerium für welchen AK?**

```
                    AK 1    AK 2    AK 3    AK 4    AK 5    QAG     QAG      QAG
                  Grundsatz Migration Pflege  Eingl.  Kinder  Klima   Digi    Wohnen
──────────────────────────────────────────────────────────────────────────────────
HMAIJS (Soziales)    ●●●      ●●●      ●       ●●●     ●●●
Gesundheit/Pflege             ●       ●●●              ●●
Finanzen             ●●●      ●●       ●●      ●●      ●●      ●●      ●●      ●●
Kultus                                                 ●●●
Inneres                      ●●●
Wirtschaft           ●●                                        ●              ●●●
Justiz               ●
Umwelt                                                         ●●●
Digitales                                                              ●●●
Wissenschaft                          ●●
──────────────────────────────────────────────────────────────────────────────────
●●● = Hauptzuständigkeit    ●● = häufig relevant    ● = gelegentlich relevant
```

**Twitter/X (via Nitter RSS-Proxy):**
```yaml
twitter_via_nitter:
  base_url: "https://nitter.net"  # Oder andere Nitter-Instanz
  accounts:
    - handle: "SozialHessen"
      name: "Sozialministerium"
      priorität: 1
      
    - handle: "RegHessen"
      name: "Landesregierung"
      priorität: 1
      
    - handle: "landtag_hessen"
      name: "Landtag"
      priorität: 1
      
    - handle: "hessenschaude"
      name: "hessenschau"
      priorität: 1
      
    - handle: "FR_de"
      name: "Frankfurter Rundschau"
      priorität: 2
      
    - handle: "bmas_bund"
      name: "BMAS"
      priorität: 1
      
    - handle: "ProAsyl"
      name: "PRO ASYL"
      priorität: 2
      
    - handle: "ParitaetHessen"
      name: "Paritätischer"
      priorität: 2
      
    - handle: "DrkHessen"
      name: "DRK"
      priorität: 2
```

**Bluesky (dezentral, RSS-fähig):**

Seit November 2024 migrieren viele deutsche Politiker zu Bluesky – insbesondere SPD und Grüne. Der Bundestag ist offiziell vertreten. Bluesky bietet native RSS-Feeds.

```yaml
bluesky:
  # RSS-Feed-Format: https://bsky.app/profile/{handle}/rss
  accounts:
    # Bundespolitik (relevant für Bundesgesetzgebung)
    - handle: "bundestag.bund.de"
      name: "Deutscher Bundestag"
      rss: "https://bsky.app/profile/bundestag.bund.de/rss"
      priorität: 1

    - handle: "lars-klingbeil.de"
      name: "Lars Klingbeil (SPD-Vorsitzender)"
      rss: "https://bsky.app/profile/lars-klingbeil.de/rss"
      priorität: 2

    - handle: "karinprien.bsky.social"
      name: "Karin Prien (BMBFSFJ)"
      rss: "https://bsky.app/profile/karinprien.bsky.social/rss"
      priorität: 1
      # Hinweis: Lisa Paus war BMFSFJ bis Mai 2025 (Ampel-Koalition)

    - handle: "saskiaesken.bsky.social"
      name: "Saskia Esken (SPD-Vorsitzende)"
      rss: "https://bsky.app/profile/saskiaesken.bsky.social/rss"
      priorität: 2

    # NGOs und Verbände
    - handle: "proasyl.bsky.social"
      name: "PRO ASYL"
      rss: "https://bsky.app/profile/proasyl.bsky.social/rss"
      priorität: 2

  # Hinweise zur Bluesky-Landschaft:
  # - CDU/CSU kaum vertreten (Ausnahme: wenige MdB)
  # - AfD nicht vertreten
  # - Hessische Landesregierung: bisher KEINE offiziellen Accounts
  # - Journalisten: zunehmend aktiv, insb. von linken/grünen Medien
```

**LinkedIn (Business-Netzwerk, API-basiert):**

71% der Bundesregierungsmitglieder sind auf LinkedIn aktiv. Wichtig für Policy-Diskurs und Fachkommunikation. Technisch aufwändiger als RSS-basierte Plattformen.

```yaml
linkedin:
  # LinkedIn erfordert OAuth 2.0 und API-Zugang
  # Kostenlose API: nur eigene Posts, begrenzte Suche
  # Sales Navigator API: umfangreicher, aber kostenpflichtig

  integration_optionen:
    # Option 1: LinkedIn Company Page RSS (inoffiziell, oft blockiert)
    rss_workaround:
      tool: "rss.app"  # Drittanbieter-Service
      kosten: "ab $9/Monat für 5 Feeds"

    # Option 2: LinkedIn API (offiziell)
    api:
      endpoint: "https://api.linkedin.com/v2/"
      auth: "OAuth 2.0"
      scope: "r_organization_social"  # Für Company Pages
      rate_limit: "100 Requests/Tag (kostenlos)"

    # Option 3: Web Scraping (ToS-Verstoß, nicht empfohlen)
    scraping: false

  # Prioritäre LinkedIn-Profile (manuell prüfen)
  profiles:
    - name: "Friedrich Merz"
      url: "linkedin.com/in/friedrich-merz"
      follower: "246.500"
      relevanz: "Bundeskanzler"

    - name: "Robert Habeck"
      url: "linkedin.com/in/robert-habeck"
      follower: "500.000+"
      relevanz: "Wirtschaftsminister"

    - name: "Bundesministerium für Arbeit und Soziales"
      url: "linkedin.com/company/bmas"
      relevanz: "Bundessozialpolitik"

  # Hinweis: Hessische Landesministerien haben meist KEINE
  # aktiven LinkedIn-Seiten. Fokus auf Bundesebene.

  empfehlung: "Phase 2 - Nach Basisintegration RSS/Bluesky"
```

**YouTube (Hessischer Landtag):**

```yaml
youtube:
  channels:
    - name: "Hessischer Landtag"
      channel_id: "UCxxxxxxxx"  # Kanal-ID ermitteln
      rss: "https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxxxxxx"
      content: "Plenarsitzungen, Ausschuss-Anhörungen"
      priorität: 2

  # Hinweis: Hessische Ministerien haben KEINE eigenen YouTube-Kanäle
  # Pressekonferenzen werden über Landtag-Kanal oder hessenschau übertragen
```

#### 1.4 Landtag-Dokumente
```yaml
landtag:
  drucksachen:
    url: "https://starweb.hessen.de/starweb/LIS/servlet.starweb"
    filter:
      - "Sozialausschuss"
      - "Sozialpolitik"
      - "Pflege"
      - "Kita"
      - "Migration"
    format: "PDF → Text (pdftotext)"

  termine:
    url: "https://hessischer-landtag.de/termine"
    filter: "Ausschuss für Arbeit und Soziales"
```

#### 1.5 Google Alerts (Ergänzende Breitenabdeckung)

Google Alerts ergänzt das eigene System um **~70% zusätzliche Quellenabdeckung**, insbesondere bei Nachrichtenagenturen, Regionalmedien und Fachportalen.

**Wichtig:** Google Alerts hat keine offizielle API, aber Alerts können als **RSS-Feed** statt E-Mail konfiguriert werden. Diese RSS-Feeds integrieren sich direkt in den bestehenden RSS-Scraper.

**Einrichtung:**
1. Alert unter google.com/alerts erstellen
2. Bei "Senden an" → **RSS-Feed** wählen (statt E-Mail)
3. Feed-URL kopieren und im Admin-Interface hinzufügen

**Empfohlene Alert-Konfiguration:**
```
# Kernthemen mit Hessen-Bezug
"Hessisches Ministerium" + (Soziales OR Pflege OR Kita OR Migration)
"Hessischer Landtag" + (Sozialausschuss OR Gesetz OR Anhörung)
"Landeshaushalt Hessen" + (Kürzung OR Soziales OR Pflege OR Kita)
"Freie Wohlfahrtspflege" + Hessen
(Pflegegesetz OR Kitagesetz OR HKJGB) + Hessen
"Liga der Freien Wohlfahrtspflege"
Sondervermögen + (Hessen OR Kommunen OR "soziale Infrastruktur")
Migrationsberatung + (Kürzung OR Finanzierung OR Bund)
Fachkräftemangel + (Pflege OR Kita OR Sozialarbeit) + Hessen
```

**Integration in sources.yaml:**
```yaml
google_alerts:
  - name: "Alert: Liga Hessen"
    url: "https://www.google.com/alerts/feeds/12345.../67890..."
    kategorie: "Google Alert"
    priorität: 1

  - name: "Alert: Pflegegesetz Hessen"
    url: "https://www.google.com/alerts/feeds/12345.../67891..."
    kategorie: "Google Alert"
    priorität: 1

  - name: "Alert: Kitagesetz Hessen"
    url: "https://www.google.com/alerts/feeds/12345.../67892..."
    kategorie: "Google Alert"
    priorität: 2
```

**Automatische Integration:**
```
Google Alerts (RSS)        Eigene Quellen (RSS/HTML/Social)
        │                              │
        └──────────┬───────────────────┘
                   ▼
           Unified RSS-Scraper
                   │
                   ▼
           Duplikat-Erkennung
           (bereits konsumiert?)
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
   🆕 NEU                 ✓ BEKANNT
   → Keyword-Filter       → Überspringen
   → LLM-Analyse
   → Dashboard-Notification
```

#### 1.6 Quellenmatrix: Eigenes System vs. Google Alerts

| Quellentyp | Eigenes System | Google Alerts |
|------------|----------------|---------------|
| **Nachrichtenagenturen (dpa, AFP, epd, KNA)** | ❌ Nein | ✅ Ja |
| **Hessische Leitmedien (HR, FAZ, FR, HNA)** | ✅ 7 Quellen | ✅ Ja |
| **Lokale Tageszeitungen** | ⚠️ Nur 3 | ✅ Hunderte |
| **Ministerien/Landtag (direkt)** | ✅ Tiefe Abdeckung | ⚠️ Verzögert |
| **Social Media (Mastodon, X, Bluesky)** | ✅ Echtzeit | ❌ Nein |
| **LinkedIn (Phase 2)** | ⚠️ API nötig | ❌ Nein |
| **Landtag-Drucksachen (PDF)** | ✅ Strukturiert | ❌ Nein |
| **Fachportale (Haufe, Beck, Juris)** | ❌ Nein | ✅ Ja |
| **Presseportale (presseportal.de, idw)** | ❌ Nein | ✅ Ja |
| **Bundesweite Medien mit Hessen-Bezug** | ❌ Nein | ✅ Ja |
| **Blogs & Magazine** | ❌ Nein | ✅ Ja |

**Nicht im eigenen System abgedeckte Quellen (via Google Alerts):**

*Regionale Medien:*
- Offenbach-Post, Hanauer Anzeiger, Wetterauer Zeitung
- Oberhessische Presse, Usinger Anzeiger, Kinzigtal-Nachrichten
- Lokale Wochenzeitungen

*Nachrichtenagenturen:*
- dpa-Landesdienst Hessen (Breaking News)
- epd (evangelischer Pressedienst – Diakonie-Themen)
- KNA (katholische Nachrichtenagentur – Caritas-Themen)

*Fachmedien:*
- Haufe Sozialwesen, Haufe Personal
- Beck-Aktuell (Sozialrecht)
- Ärzteblatt, Pflegewissenschaft

*Presseportale:*
- presseportal.de (Verbände, Ministerien, Unternehmen)
- idw-online.de (Wissenschafts-PR)

*Bundesweite Qualitätsmedien:*
- SPIEGEL, ZEIT, SZ, taz, Welt, Focus (bei Hessen-Bezug)

**Fazit:** Google Alerts für die Breite, eigenes System für die Tiefe und strukturierte LLM-Analyse.

---

### 2. Keyword-Filter (Stufe 1)

#### 2.1 Trigger-Keyword-Kategorien

```python
KEYWORDS = {
    # FINANZ-TRIGGER (höchste Priorität → 🔴)
    "finanz_kritisch": [
        "Kürzung", "Streichung", "Haushaltssperre", 
        "Finanzierungslücke", "Mittelreduzierung", "Kahlschlag",
        "Förderentzug", "Unterfinanzierung", "Etatkürzung",
        "Haushaltskonsolidierung", "Sparmaßnahmen"
    ],
    
    # REFORM-TRIGGER (→ 🟠)
    "reform": [
        "Gesetzesänderung", "Novelle", "Verordnung", 
        "Evaluierung", "Anhörung", "Regierungsentwurf",
        "Bundesratsentscheidung", "Gesetzentwurf", "Reform",
        "Richtlinie", "Landesgesetz"
    ],
    
    # STRUKTUR-TRIGGER (→ 🔴/🟠)
    "struktur": [
        "Schließung", "Abbau", "Personalreduzierung",
        "Kapazitätsengpass", "existenzbedrohend", "bedroht",
        "Insolvenz", "Trägerwechsel", "Standortaufgabe"
    ],
    
    # THEMEN-TRIGGER (→ 🟡/🟠)
    "themen_liga": [
        "Fachkräftemangel", "Pflegenotstand", "Betreuungsschlüssel",
        "Kitaplätze", "Migrationsberatung", "Eingliederungshilfe",
        "Wohlfahrtspflege", "Sozialwirtschaft", "Freie Träger",
        "Pflegeheim", "Altenhilfe", "Jugendhilfe", "Schuldnerberatung",
        "Obdachlosenhilfe", "Suchtberatung", "Flüchtlingsberatung"
    ],
    
    # AKTEUR-TRIGGER (→ 🟡)
    "akteure": [
        "Sozialministerium", "HMAIJS", "Heike Hofmann",
        "Diana Stolz", "Sozialausschuss", "Liga Hessen",
        "AWO", "Caritas", "Diakonie", "DRK", "Paritätischer",
        "Wohlfahrtsverband", "BAGFW"
    ],
    
    # GESETZGEBUNG (→ 🟠)
    "gesetze": [
        "HKJGB", "Pflegegesetz", "Kitagesetz", "BTHG",
        "SGB", "Sozialgesetzbuch", "Landeshaushalt",
        "Sondervermögen", "KiföG", "Pflegefinanzierung"
    ]
}

# Kombinationen erhöhen Priorität
PRIORITY_COMBINATIONS = [
    (["Kürzung", "Hessen"], "🔴"),
    (["Gesetz", "Anhörung"], "🟠"),
    (["Pflege", "Fachkräfte"], "🟠"),
    (["Kita", "Finanzierung"], "🟠"),
    (["Migration", "Beratung"], "🟡"),
]
```

#### 2.2 Filter-Logik

```python
def calculate_relevance_score(text: str, title: str) -> dict:
    """
    Berechnet Relevanz-Score basierend auf Keyword-Matches.
    Returns: {score: int, priority: str, matched_keywords: list, category: str}
    """
    score = 0
    matches = []
    
    combined_text = f"{title} {text}".lower()
    
    # Gewichtung nach Kategorie
    weights = {
        "finanz_kritisch": 10,
        "struktur": 8,
        "reform": 6,
        "gesetze": 5,
        "themen_liga": 4,
        "akteure": 3
    }
    
    for category, keywords in KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in combined_text:
                score += weights.get(category, 1)
                matches.append((keyword, category))
    
    # Priorität basierend auf Score
    if score >= 15:
        priority = "🔴"  # EILIG
    elif score >= 10:
        priority = "🟠"  # WICHTIG
    elif score >= 5:
        priority = "🟡"  # BEOBACHTEN
    elif score >= 2:
        priority = "🔵"  # INFORMATION
    else:
        priority = None  # Nicht relevant
    
    return {
        "score": score,
        "priority": priority,
        "matched_keywords": matches,
        "primary_category": matches[0][1] if matches else None
    }
```

---

### 3. LLM-Verarbeitung (Stufe 2)

#### 3.1 Provider-Konfiguration

```python
LLM_PROVIDERS = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "max_tokens": 500,
        "daily_limit": 1000,
        "priority": 1,
        "headers": {
            "Authorization": "Bearer ${OPENROUTER_API_KEY}",
            "HTTP-Referer": "https://liga-hessen.de",
            "X-Title": "Liga-Briefing-System"
        }
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.1-8b-instant",
        "max_tokens": 500,
        "daily_limit": 14400,
        "priority": 2,
        "headers": {
            "Authorization": "Bearer ${GROQ_API_KEY}"
        }
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "model": "devstral-small-2505",
        "max_tokens": 500,
        "daily_limit": 1000,  # Geschätzt
        "priority": 3,
        "headers": {
            "Authorization": "Bearer ${MISTRAL_API_KEY}"
        }
    }
}
```

#### 3.2 Analyse-Prompt

```python
ANALYSIS_PROMPT = """Du bist ein Analyst für die Liga der Freien Wohlfahrtspflege Hessen.
Analysiere den folgenden Nachrichtenartikel und erstelle eine strukturierte Bewertung.

KONTEXT:
Die Liga Hessen ist der Dachverband von 6 Wohlfahrtsverbänden (AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden).
Hauptthemen: Pflege, Kita, Migration, Eingliederungshilfe, Sozialfinanzierung.
Primärer politischer Kontakt: HMAIJS (Ministerin Heike Hofmann).

ARBEITSKREISE:
- AK 1: Grundsatz und Sozialpolitik
- AK 2: Migration und Flucht  
- AK 3: Gesundheit, Pflege und Senioren
- AK 4: Eingliederungshilfe
- AK 5: Kinder, Jugend, Frauen und Familie
- QAG Digitalisierung, QAG Klimaschutz, QAG Wohnen

ARTIKEL:
Titel: {title}
Quelle: {source}
Datum: {date}
Text: {text}

AUFGABE:
Erstelle eine JSON-Antwort mit exakt diesem Format:
{{
  "zusammenfassung": "2-3 Sätze, die den Kern für die Liga erfassen",
  "dringlichkeit": "🔴|🟠|🟡|🔵",
  "dringlichkeit_grund": "Kurze Begründung",
  "zustaendiger_ak": "AK 1|AK 2|AK 3|AK 4|AK 5|QAG ...|Geschäftsführung",
  "handlungsempfehlung": "Konkrete nächste Schritte in 1 Satz",
  "stakeholder": ["Liste relevanter Akteure"],
  "reaktionsfrist": "sofort|24h|1 Woche|2 Wochen|Beobachten",
  "liga_relevanz": 1-10
}}

DRINGLICHKEITSSTUFEN:
🔴 EILIG: Haushaltskürzungen, Gesetzeseinbringungen, akute Bedrohungen (<24h)
🟠 WICHTIG: Anhörungsfristen, Richtlinienentwürfe, Reformankündigungen (1 Woche)
🟡 BEOBACHTEN: Politische Aussagen, Parteipositionierungen, Trends (2 Wochen)
🔵 INFORMATION: Hintergrundberichte, allgemeine Entwicklungen (Zur Kenntnis)

Antworte NUR mit dem JSON-Objekt, ohne zusätzlichen Text."""
```

#### 3.3 Fallback-Logik

```python
async def analyze_with_fallback(article: dict) -> dict:
    """
    Versucht LLM-Analyse mit Fallback-Kette.
    """
    providers = sorted(
        LLM_PROVIDERS.items(), 
        key=lambda x: x[1]["priority"]
    )
    
    for provider_name, config in providers:
        try:
            result = await call_llm(
                provider=provider_name,
                config=config,
                prompt=ANALYSIS_PROMPT.format(**article)
            )
            return {
                "analysis": json.loads(result),
                "provider_used": provider_name,
                "success": True
            }
        except RateLimitError:
            logger.warning(f"{provider_name} rate limit erreicht, versuche nächsten...")
            continue
        except Exception as e:
            logger.error(f"{provider_name} Fehler: {e}")
            continue
    
    # Fallback: Nur Keyword-basierte Analyse
    return {
        "analysis": keyword_only_analysis(article),
        "provider_used": "keyword_fallback",
        "success": False
    }
```

---

### 4. Output-Generierung

#### 4.1 Briefing-Template

```markdown
# 📋 LIGA HESSEN – DAILY BRIEFING
**Datum:** {date}
**Erfasste Quellen:** {source_count} | **Analysierte Artikel:** {article_count}
**Generiert:** {timestamp}

---

## 🔴 EILMELDUNGEN (Reaktion erforderlich: <24h)

{#each urgent_items}
### {title}
**Quelle:** {source} | **Datum:** {item_date}

{summary}

| Zuständig | Handlungsempfehlung | Stakeholder |
|-----------|---------------------|-------------|
| {responsible_ak} | {action} | {stakeholders} |

🔗 [Zur Quelle]({url})

---
{/each}

## 🟠 WICHTIG (Diese Woche bearbeiten)

{#each important_items}
### {title}
**Quelle:** {source} | **Zuständig:** {responsible_ak}

{summary}

**Empfehlung:** {action}

---
{/each}

## 🟡 BEOBACHTEN (Strategische Relevanz)

{#each watch_items}
- **{title}** ({source}): {summary_short} → {responsible_ak}
{/each}

## 🔵 ZUR KENNTNISNAHME

{#each info_items}
- {title} ({source})
{/each}

---

## 📊 STATISTIK

| Kategorie | Anzahl |
|-----------|--------|
| Gesamt erfasst | {total_captured} |
| Nach Keyword-Filter | {after_keyword} |
| 🔴 Eilig | {urgent_count} |
| 🟠 Wichtig | {important_count} |
| 🟡 Beobachten | {watch_count} |
| 🔵 Information | {info_count} |

**LLM-Provider:** {provider_stats}
**Laufzeit:** {runtime}

---

## 📅 KOMMENDE FRISTEN

{#each upcoming_deadlines}
- **{date}:** {description} (AK {ak})
{/each}

---

*Automatisch generiert vom Liga-Briefing-System*
*Bei Fragen: briefing@liga-hessen.de*
```

#### 4.2 Delivery-Module

```yaml
delivery:
  email:
    enabled: true
    smtp_server: "smtp.liga-hessen.de"
    recipients:
      - "geschaeftsfuehrung@liga-hessen.de"
      - "presse@liga-hessen.de"
    send_time: "07:00"
    format: "html"  # Konvertiert Markdown → HTML
    
  webhook:
    enabled: false
    url: "https://hooks.slack.com/services/..."
    format: "slack_blocks"
    
  file:
    enabled: true
    output_dir: "/var/briefings/"
    filename_pattern: "briefing_{date}.md"
    retention_days: 90
```

---

### 5. Web-Interface

Das System bietet zwei Web-Oberflächen: ein **Admin-Interface** zur Quellenkonfiguration und ein **Dashboard** zur Anzeige aktueller Ergebnisse mit Echtzeit-Benachrichtigungen.

#### 5.1 Systemübersicht Web-Interface

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              WEB-INTERFACE                                   │
│                                                                              │
│  ┌─────────────────────────────┐    ┌─────────────────────────────────────┐ │
│  │      ADMIN-BEREICH          │    │          DASHBOARD                  │ │
│  │      /admin                 │    │          /                          │ │
│  │                             │    │                                     │ │
│  │  • Quellen verwalten        │    │  • Aktuelle Meldungen (Live)       │ │
│  │  • Keywords konfigurieren   │    │  • 🆕 Neue Items markiert          │ │
│  │  • LLM-Provider einstellen  │    │  • Filter nach Dringlichkeit       │ │
│  │  • E-Mail-Empfänger         │    │  • Filter nach AK                  │ │
│  │  • System-Status            │    │  • Volltextsuche                   │ │
│  │                             │    │  • "Als gelesen markieren"         │ │
│  └─────────────────────────────┘    └─────────────────────────────────────┘ │
│                                                                              │
│                              ┌─────────────────┐                            │
│                              │   REST API      │                            │
│                              │   /api/v1/...   │                            │
│                              └────────┬────────┘                            │
│                                       │                                     │
└───────────────────────────────────────┼─────────────────────────────────────┘
                                        │
                                        ▼
                              ┌─────────────────┐
                              │    Backend      │
                              │  (FastAPI)      │
                              └─────────────────┘
```

#### 5.2 Admin-Interface: Quellenkonfiguration

**Route:** `/admin/sources`

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  📡 QUELLEN-VERWALTUNG                                    [+ Neue Quelle]   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Filter: [Alle Typen ▼] [Alle Kategorien ▼] [Aktive ▼]      🔍 Suche...    │
│                                                                              │
├──────┬────────────────────┬─────────┬──────────────┬────────┬───────┬──────┤
│ Aktiv│ Name               │ Typ     │ Kategorie    │ Prio   │ Status│ Akt. │
├──────┼────────────────────┼─────────┼──────────────┼────────┼───────┼──────┤
│  ☑   │ hessenschau        │ RSS     │ Landespolitik│ ⭐⭐⭐  │ ✅ OK │ ✏️ 🗑 │
│  ☑   │ FAZ Rhein-Main     │ RSS     │ Regionales   │ ⭐⭐⭐  │ ✅ OK │ ✏️ 🗑 │
│  ☑   │ HMAIJS             │ HTML    │ Ministerium  │ ⭐⭐⭐  │ ✅ OK │ ✏️ 🗑 │
│  ☑   │ Alert: Liga Hessen │ G-Alert │ Google Alert │ ⭐⭐⭐  │ ✅ OK │ ✏️ 🗑 │
│  ☐   │ Offenbach-Post     │ RSS     │ Regional     │ ⭐⭐   │ ⚠️ 404│ ✏️ 🗑 │
│  ☑   │ @SozialHessen      │ Twitter │ Social Media │ ⭐⭐⭐  │ ✅ OK │ ✏️ 🗑 │
└──────┴────────────────────┴─────────┴──────────────┴────────┴───────┴──────┘
│                                                                              │
│  Letzte Aktualisierung: 01.01.2026 08:15    [🔄 Jetzt alle prüfen]         │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Quelle hinzufügen/bearbeiten Dialog:**

```yaml
# Formularfelder
quelle:
  name: "Neuer RSS-Feed"           # Pflicht
  typ: "rss|html|mastodon|twitter|bluesky|linkedin|youtube|google_alert|landtag"  # Dropdown
  url: "https://..."               # Pflicht
  kategorie: "Landespolitik|Regionales|Migration|..."    # Dropdown + Freitext
  priorität: 1-3                   # Slider
  aktiv: true/false                # Toggle

  # Typ-spezifisch (nur bei HTML)
  html_selektor: "article.news-item"

  # Typ-spezifisch (nur bei Twitter)
  nitter_instanz: "https://nitter.net"
```

#### 5.3 Dashboard: Aktuelle Ergebnisse

**Route:** `/` (Startseite)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  📋 LIGA BRIEFING DASHBOARD                          🔔 3 neue Meldungen   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  [🔴 Eilig (2)] [🟠 Wichtig (5)] [🟡 Beobachten (12)] [🔵 Info (24)]       │
│  [Alle AKs ▼]  [Heute ▼]  [Nur ungelesen ☑]              🔍 Suche...       │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  🆕 🔴 EILIG | vor 12 Min                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ Landeshaushalt: Kürzungen bei Migrationsberatung angekündigt        │   │
│  │ Quelle: hessenschau | AK 2 Migration                                │   │
│  │                                                                      │   │
│  │ Das Finanzministerium plant Einsparungen von 3,2 Mio. Euro bei...  │   │
│  │                                                                      │   │
│  │ 💡 Empfehlung: Sofortige Abstimmung mit BAGFW, Pressemitteilung... │   │
│  │                                                                      │   │
│  │ [🔗 Zur Quelle]  [✓ Als gelesen markieren]  [📧 Weiterleiten]      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  🆕 🔴 EILIG | vor 45 Min                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ Gesetzentwurf HKJGB-Novelle im Landtag eingebracht                  │   │
│  │ ...                                                                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ✓  🟠 WICHTIG | vor 2 Std                                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ Anhörungsfrist Pflegeverordnung endet am 15.01.                     │   │
│  │ (bereits gelesen)                                                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 5.4 Echtzeit-Benachrichtigungen

```python
# WebSocket für Live-Updates
@app.websocket("/ws/notifications")
async def websocket_notifications(websocket: WebSocket):
    await websocket.accept()
    while True:
        # Prüfe auf neue Items seit letzter Abfrage
        new_items = await get_new_unread_items(since=last_check)
        if new_items:
            await websocket.send_json({
                "type": "new_items",
                "count": len(new_items),
                "items": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "priority": item.priority,
                        "source": item.source_name,
                        "timestamp": item.published_at.isoformat()
                    }
                    for item in new_items
                ]
            })
        await asyncio.sleep(30)  # Alle 30 Sekunden prüfen
```

**Browser-Notification bei neuen 🔴 EILIG-Meldungen:**
```javascript
// Frontend: Push-Notification bei kritischen Meldungen
if (item.priority === "🔴" && Notification.permission === "granted") {
    new Notification("🔴 EILMELDUNG", {
        body: item.title,
        icon: "/icons/liga-logo.png",
        tag: item.id
    });
}
```

#### 5.5 REST API Endpoints

```yaml
# Quellen-Verwaltung
GET    /api/v1/sources              # Alle Quellen auflisten
POST   /api/v1/sources              # Neue Quelle hinzufügen
PUT    /api/v1/sources/{id}         # Quelle bearbeiten
DELETE /api/v1/sources/{id}         # Quelle löschen
POST   /api/v1/sources/{id}/test    # Quelle testen (Erreichbarkeit)

# Items/Meldungen
GET    /api/v1/items                # Alle Items (mit Filtern)
GET    /api/v1/items/{id}           # Einzelnes Item
PUT    /api/v1/items/{id}/read      # Als gelesen markieren
GET    /api/v1/items/unread/count   # Anzahl ungelesener Items
GET    /api/v1/items/new            # Neue Items seit Timestamp

# Briefings
GET    /api/v1/briefings            # Alle generierten Briefings
GET    /api/v1/briefings/{date}     # Briefing für Datum
POST   /api/v1/briefings/generate   # Manuell Briefing generieren

# System
GET    /api/v1/status               # System-Status
POST   /api/v1/scrape/run           # Manuell Scraping starten
GET    /api/v1/stats                # Statistiken
```

#### 5.6 Projektstruktur (erweitert)

```
liga-briefing-system/
├── frontend/                    # React/Vue Frontend
│   ├── src/
│   │   ├── components/
│   │   │   ├── Dashboard.tsx    # Hauptansicht
│   │   │   ├── ItemCard.tsx     # Einzelne Meldung
│   │   │   ├── SourceList.tsx   # Quellen-Tabelle
│   │   │   ├── SourceForm.tsx   # Quelle bearbeiten
│   │   │   └── Filters.tsx      # Filter-Leiste
│   │   ├── hooks/
│   │   │   └── useWebSocket.ts  # Live-Updates
│   │   └── App.tsx
│   └── package.json
│
├── backend/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI App
│   │   ├── routes/
│   │   │   ├── sources.py       # /api/v1/sources
│   │   │   ├── items.py         # /api/v1/items
│   │   │   └── briefings.py     # /api/v1/briefings
│   │   └── websocket.py         # WebSocket Handler
│   ├── scrapers/                # (wie bisher)
│   ├── processors/              # (wie bisher)
│   └── database/                # (wie bisher)
│
└── docker-compose.yml           # Frontend + Backend + DB
```

---

### 6. Datenbank-Schema

```sql
-- SQLite Schema für das Briefing-System

CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,  -- 'rss', 'html', 'mastodon', 'twitter', 'bluesky', 'linkedin', 'youtube', 'google_alert', 'landtag'
    url TEXT NOT NULL,
    priority INTEGER DEFAULT 2,
    category TEXT,
    last_checked DATETIME,
    last_status TEXT,           -- 'ok', 'error', 'timeout'
    last_error_message TEXT,    -- Fehlermeldung bei Problemen
    html_selector TEXT,         -- Nur für HTML-Scraper
    nitter_instance TEXT,       -- Nur für Twitter
    is_active BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE raw_items (
    id INTEGER PRIMARY KEY,
    source_id INTEGER REFERENCES sources(id),
    external_id TEXT,           -- GUID/URL für Deduplizierung
    title TEXT NOT NULL,
    content TEXT,
    url TEXT,
    published_at DATETIME,
    captured_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    -- NEU: Lese-Status für Dashboard
    is_read BOOLEAN DEFAULT 0,           -- Wurde als gelesen markiert?
    read_at DATETIME,                    -- Wann wurde es gelesen?
    read_by TEXT,                        -- Wer hat es gelesen? (optional)

    -- NEU: Für "Neu"-Markierung
    first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,  -- Wann erstmals erfasst?
    notified BOOLEAN DEFAULT 0,          -- Wurde Benachrichtigung gesendet?

    UNIQUE(source_id, external_id)
);

CREATE TABLE keyword_analysis (
    id INTEGER PRIMARY KEY,
    item_id INTEGER REFERENCES raw_items(id),
    relevance_score INTEGER,
    priority TEXT,              -- 🔴🟠🟡🔵 or NULL
    matched_keywords TEXT,      -- JSON array
    primary_category TEXT,
    analyzed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE llm_analysis (
    id INTEGER PRIMARY KEY,
    item_id INTEGER REFERENCES raw_items(id),
    provider TEXT,
    summary TEXT,
    urgency TEXT,
    responsible_ak TEXT,
    action_recommendation TEXT,
    stakeholders TEXT,          -- JSON array
    response_deadline TEXT,
    liga_relevance INTEGER,
    raw_response TEXT,          -- Full JSON for debugging
    analyzed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    tokens_used INTEGER
);

CREATE TABLE briefings (
    id INTEGER PRIMARY KEY,
    date DATE UNIQUE,
    markdown_content TEXT,
    html_content TEXT,
    stats TEXT,                 -- JSON
    generated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    delivered_at DATETIME
);

CREATE TABLE api_usage (
    id INTEGER PRIMARY KEY,
    provider TEXT,
    date DATE,
    requests_count INTEGER DEFAULT 0,
    tokens_used INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0,
    UNIQUE(provider, date)
);

-- Indizes für Performance
CREATE INDEX idx_raw_items_date ON raw_items(published_at);
CREATE INDEX idx_raw_items_unread ON raw_items(is_read, first_seen_at);  -- Für Dashboard
CREATE INDEX idx_raw_items_new ON raw_items(notified, first_seen_at);    -- Für Benachrichtigungen
CREATE INDEX idx_keyword_priority ON keyword_analysis(priority);
CREATE INDEX idx_llm_urgency ON llm_analysis(urgency);
CREATE INDEX idx_sources_active ON sources(is_active, type);
```

#### 6.1 Duplikat-Erkennung

Die Duplikat-Erkennung erfolgt zweistufig:

```python
async def check_duplicate(item: ScrapedItem) -> DuplicateCheckResult:
    """
    Prüft ob ein Item bereits bekannt ist.
    Returns: DuplicateCheckResult mit Status und ggf. existing_id
    """

    # Stufe 1: Exakte GUID/URL-Prüfung (schnell)
    existing = await db.execute(
        "SELECT id FROM raw_items WHERE external_id = ? AND source_id = ?",
        (item.guid, item.source_id)
    )
    if existing:
        return DuplicateCheckResult(is_duplicate=True, existing_id=existing.id)

    # Stufe 2: Titel-Ähnlichkeit (für Quellen ohne stabile GUIDs)
    similar = await db.execute("""
        SELECT id, title FROM raw_items
        WHERE source_id = ?
        AND published_at > datetime('now', '-7 days')
        AND title LIKE ?
    """, (item.source_id, f"%{item.title[:50]}%"))

    for candidate in similar:
        if calculate_similarity(item.title, candidate.title) > 0.85:
            return DuplicateCheckResult(
                is_duplicate=True,
                existing_id=candidate.id,
                match_type="title_similarity"
            )

    # Stufe 3: Content-Hash für identische Inhalte aus verschiedenen Quellen
    content_hash = hashlib.md5(item.content.encode()).hexdigest()
    cross_source = await db.execute(
        "SELECT id FROM raw_items WHERE content_hash = ?",
        (content_hash,)
    )
    if cross_source:
        return DuplicateCheckResult(
            is_duplicate=True,
            existing_id=cross_source.id,
            match_type="content_hash"
        )

    return DuplicateCheckResult(is_duplicate=False)
```

#### 6.2 Erwähnungen und Adressierungen

Das System trackt nicht nur Erwähnungen der Liga, sondern aller Stakeholder aus der Datenbank.

**Relevante Szenarien:**
| Szenario | Beispiel | Priorität |
|----------|----------|-----------|
| Liga direkt erwähnt | "@wohlfahrtspflegehessen wie steht ihr dazu?" | 🔴 |
| Mitgliedsverband erwähnt | "@drklvhessen @diakoniehessen gemeinsame Aktion" | 🟠 |
| Ministerin angesprochen | "@hofmann_heike Stellungnahme zur Kürzung?" | 🟠 |
| Stakeholder untereinander | "@SozialHessen antwortet @FR_de" | 🟡 |
| Journalist fragt Ministerium | "@haborgtMdL an @SozialHessen: Wann kommt...?" | 🟠 |

**Datenmodell für Erwähnungen:**

```python
@dataclass
class MentionInfo:
    """Tracking von Erwähnungen und Adressierungen"""

    # === WER WIRD ERWÄHNT? ===
    mentioned_handles: list[str]         # ["@SozialHessen", "@drklvhessen"]
    mentioned_stakeholder_ids: list[int] # [12, 45] - FK zu stakeholders
    mentioned_categories: list[str]      # ["ministerium", "liga_mitglied", "presse"]

    # Liga-spezifisch
    mentions_liga_direct: bool           # @wohlfahrtspflegehessen
    mentions_liga_member: bool           # AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden
    mentions_any_tracked: bool           # Irgendein Stakeholder aus DB

    # === KONVERSATIONSKONTEXT ===
    is_reply: bool                       # Antwort auf anderen Post?
    is_reply_to_stakeholder: bool        # Antwort auf Stakeholder-Post?
    reply_to_id: str | None
    reply_to_handle: str | None
    reply_to_stakeholder_id: int | None

    thread_id: str | None
    thread_participants: list[str]       # Alle Handles im Thread

    # === ART DER ANSPRACHE ===
    is_direct_question: bool             # Frage an jemanden?
    is_criticism: bool                   # Kritik/Angriff?
    is_praise: bool                      # Lob/Zustimmung?
    is_request: bool                     # Forderung/Bitte?
    is_announcement: bool                # Ankündigung an jemanden?
    requires_response: bool              # Erwartet Antwort?

    # === ABSENDER-INFO ===
    sender_handle: str
    sender_stakeholder_id: int | None    # Falls Absender in Stakeholder-DB
    sender_category: str | None          # "politiker", "journalist", "verband", "bürger"
    sender_party: str | None             # "CDU", "SPD", "Grüne"...
    sender_org: str | None               # "HMAIJS", "FR", "DGB"...
```

**Stakeholder-Tabelle (erweitert):**

```sql
CREATE TABLE stakeholders (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,                  -- "Heike Hofmann"
    organization TEXT,                   -- "HMAIJS"
    role TEXT,                           -- "Ministerin"
    category TEXT NOT NULL,              -- ministerium|landtag|presse|verband|liga_mitglied
    party TEXT,                          -- CDU|SPD|Grüne|...

    -- Social Media Handles (für Matching)
    twitter_handle TEXT,                 -- "hofmann_heike"
    mastodon_handle TEXT,                -- "@hofmann@social.hessen.de"
    instagram_handle TEXT,
    bluesky_handle TEXT,                 -- "saskiaesken.bsky.social"
    linkedin_url TEXT,                   -- "linkedin.com/in/friedrich-merz"

    -- Kontakt
    email TEXT,
    phone TEXT,
    website TEXT,

    -- Relevanz für Liga
    relevance_score INTEGER DEFAULT 5,   -- 1-10
    primary_ak TEXT,                     -- Welcher AK ist zuständig?
    notes TEXT,

    is_active BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME
);

-- Index für schnelles Handle-Matching
CREATE INDEX idx_stakeholders_twitter ON stakeholders(twitter_handle);
CREATE INDEX idx_stakeholders_mastodon ON stakeholders(mastodon_handle);
CREATE INDEX idx_stakeholders_bluesky ON stakeholders(bluesky_handle);
CREATE INDEX idx_stakeholders_category ON stakeholders(category);
```

**Stakeholder-Kategorien:**
```python
STAKEHOLDER_CATEGORIES = {
    "liga":           "Liga Hessen (Dachverband)",
    "liga_mitglied":  "Mitgliedsverbände (AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden)",
    "ministerium":    "Hessische Ministerien",
    "minister":       "Minister/innen persönlich",
    "staatssekretaer": "Staatssekretär/innen",
    "landtag":        "Hessischer Landtag (Institution)",
    "mdl":            "Landtagsabgeordnete",
    "fraktion":       "Landtagsfraktionen",
    "kommune":        "Kommunale Spitzenverbände",
    "bund":           "Bundesebene (Ministerien, BAGFW)",
    "mdb":            "Bundestagsabgeordnete",
    "presse":         "Medien und Journalisten",
    "gewerkschaft":   "Gewerkschaften",
    "arbeitgeber":    "Arbeitgeberverbände",
    "kirche":         "Kirchen und Religionsgemeinschaften",
    "ngo":            "NGOs und Menschenrechtsorganisationen",
    "fachverband":    "Fachverbände und -organisationen",
    "partner":        "Sonstige Partner",
}
```

**Stakeholder-Seed-Daten (Initial-Import):**

```sql
-- ============================================================================
-- KATEGORIE 1: LIGA HESSEN UND MITGLIEDSVERBÄNDE
-- ============================================================================

INSERT INTO stakeholders (name, organization, role, category, twitter_handle, instagram_handle, mastodon_handle, website, email, phone, relevance_score, primary_ak) VALUES
-- Liga Dachverband
('Liga der Freien Wohlfahrtspflege Hessen', 'Liga Hessen', 'Dachverband', 'liga', NULL, 'wohlfahrtspflegehessen', NULL, 'liga-hessen.de', 'info@liga-hessen.de', '0611/3081434', 10, 'Geschäftsführung'),
('Alina Loeb', 'Liga Hessen', 'Presse', 'liga', NULL, NULL, NULL, 'liga-hessen.de', NULL, '0611/4504166-21', 8, 'Geschäftsführung'),  -- Durchwahl Presse

-- AWO
('AWO Bezirksverband Nordhessen', 'AWO', 'Bezirksverband', 'liga_mitglied', NULL, NULL, NULL, 'awo-nordhessen.de', 'sigrid.wieder@awo-nordhessen.de', NULL, 8, 'AK 1'),
('Michael Schmidt', 'AWO Nordhessen', 'Geschäftsführer, Liga-Vorsitzender 2024', 'liga_mitglied', NULL, NULL, NULL, 'awo-nordhessen.de', NULL, NULL, 9, 'AK 1'),
('AWO Bezirksverband Hessen-Süd', 'AWO', 'Bezirksverband', 'liga_mitglied', NULL, NULL, NULL, 'awo-hs.org', 's-magnus@awo-hessensued.de', NULL, 8, 'AK 1'),
('Thomas Przibilla', 'AWO Hessen-Süd', 'Geschäftsführer', 'liga_mitglied', NULL, NULL, NULL, 'awo-hs.org', NULL, NULL, 7, 'AK 1'),

-- Caritas
('Caritasverband Diözese Fulda', 'Caritas', 'Diözesanverband', 'liga_mitglied', NULL, 'caritas.bistum.fulda', NULL, 'dicvfulda.caritas.de', NULL, NULL, 8, 'AK 1'),
('Dr. Markus Juch', 'Caritas Fulda', 'Direktor, Liga-Stellvertreter', 'liga_mitglied', NULL, NULL, NULL, 'dicvfulda.caritas.de', NULL, NULL, 9, 'AK 1'),
('Caritasverband Diözese Limburg', 'Caritas', 'Diözesanverband', 'liga_mitglied', NULL, 'caritasbezirklimburg', NULL, 'dicv-limburg.de', NULL, NULL, 8, 'AK 1'),
('Jörg Klärner', 'Caritas Limburg', 'Direktor', 'liga_mitglied', NULL, NULL, NULL, 'dicv-limburg.de', NULL, NULL, 7, 'AK 1'),
('Caritasverband Diözese Mainz', 'Caritas', 'Diözesanverband', 'liga_mitglied', NULL, NULL, NULL, 'caritas-bistum-mainz.de', 't.greitens@caritas-mainz.de', NULL, 8, 'AK 1'),

-- Diakonie
('Diakonie Hessen', 'Diakonie', 'Landesverband', 'liga_mitglied', NULL, 'diakoniehessen', NULL, 'diakonie-hessen.de', NULL, '069/7947-6404', 9, 'AK 1'),
('Carsten Tag', 'Diakonie Hessen', 'Vorstandsvorsitzender, Liga-Stellvertreter', 'liga_mitglied', NULL, NULL, NULL, 'diakonie-hessen.de', NULL, NULL, 9, 'AK 1'),
('Arno F. Kehrer', 'Diakonie Hessen', 'Pressesprecher', 'liga_mitglied', NULL, NULL, NULL, 'diakonie-hessen.de', NULL, '069/7947-6404', 7, 'AK 1'),

-- DRK
('DRK Landesverband Hessen', 'DRK', 'Landesverband', 'liga_mitglied', 'DrkHessen', 'drklvhessen', NULL, 'drk-hessen.de', NULL, '0611-7909-527', 9, 'AK 1'),
('Nils Möller', 'DRK Hessen', 'Landesgeschäftsführer', 'liga_mitglied', NULL, NULL, NULL, 'drk-hessen.de', NULL, NULL, 8, 'AK 1'),
('Gisela Prellwitz', 'DRK Hessen', 'Presse', 'liga_mitglied', NULL, NULL, NULL, 'drk-hessen.de', NULL, '0611-7909-527', 6, 'AK 1'),

-- Paritätischer
('Der Paritätische Hessen', 'Paritätischer', 'Landesverband', 'liga_mitglied', 'ParitaetHessen', 'paritaethessen', NULL, 'paritaet-hessen.org', NULL, NULL, 9, 'AK 1'),
('Dr. Yasmin Alinaghi', 'Paritätischer Hessen', 'Landesgeschäftsführerin', 'liga_mitglied', NULL, NULL, NULL, 'paritaet-hessen.org', NULL, NULL, 8, 'AK 1'),
('Barbara Helfrich', 'Paritätischer Hessen', 'Pressesprecherin', 'liga_mitglied', NULL, NULL, NULL, 'paritaet-hessen.org', NULL, NULL, 6, 'AK 1'),

-- Jüdische Gemeinden
('Landesverband Jüdische Gemeinden Hessen', 'Jüdische Gemeinden', 'Landesverband', 'liga_mitglied', NULL, NULL, NULL, 'lvjgh.de', NULL, '069/444049', 8, 'AK 1'),
('Daniel Neumann', 'Jüdische Gemeinden Hessen', 'Direktor', 'liga_mitglied', NULL, NULL, NULL, 'lvjgh.de', NULL, NULL, 8, 'AK 1');

-- ============================================================================
-- KATEGORIE 2: HESSISCHE LANDESREGIERUNG UND MINISTERIEN
-- ============================================================================

INSERT INTO stakeholders (name, organization, role, category, party, twitter_handle, instagram_handle, mastodon_handle, website, relevance_score, primary_ak) VALUES
-- Staatskanzlei
('Hessische Staatskanzlei', 'Landesregierung', 'Staatskanzlei', 'ministerium', NULL, 'RegHessen', 'regierunghessen', 'landesregierung@social.hessen.de', 'staatskanzlei.hessen.de', 9, 'Geschäftsführung'),
('Boris Rhein', 'Landesregierung', 'Ministerpräsident', 'minister', 'CDU', 'boris_rhein', 'boris.rhein', 'borisrhein@social.hessen.de', NULL, 10, 'Geschäftsführung'),
('Tobias Rösmann', 'Staatskanzlei', 'Regierungssprecher', 'ministerium', NULL, NULL, NULL, NULL, 'staatskanzlei.hessen.de', 7, 'Geschäftsführung'),

-- HMAIJS (Sozialministerium) - Wichtigster Kontakt!
('HMAIJS', 'Sozialministerium', 'Ministerium für Arbeit, Integration, Jugend und Soziales', 'ministerium', NULL, 'SozialHessen', 'sozialhessen', NULL, 'soziales.hessen.de', 10, 'AK 1'),
('Heike Hofmann', 'HMAIJS', 'Ministerin', 'minister', 'SPD', 'hofmann_heike', 'heike_hofmann', NULL, 'soziales.hessen.de', 10, 'Geschäftsführung'),
('Manuela Strube', 'HMAIJS', 'Staatssekretärin', 'staatssekretaer', 'SPD', NULL, NULL, NULL, 'soziales.hessen.de', 8, 'AK 1'),
('Katrin Hechler', 'HMAIJS', 'Staatssekretärin', 'staatssekretaer', 'SPD', NULL, NULL, NULL, 'soziales.hessen.de', 8, 'AK 2'),

-- Gesundheitsministerium
('Ministerium für Familie, Gesundheit und Pflege', 'Gesundheitsministerium', 'Ministerium', 'ministerium', NULL, NULL, NULL, NULL, 'familie.hessen.de', 9, 'AK 3'),
('Diana Stolz', 'Gesundheitsministerium', 'Ministerin', 'minister', 'CDU', NULL, NULL, NULL, 'familie.hessen.de', 9, 'AK 3'),
('Dr. Sonja Optendrenk', 'Gesundheitsministerium', 'Staatssekretärin', 'staatssekretaer', 'CDU', NULL, NULL, NULL, 'familie.hessen.de', 7, 'AK 3'),

-- Finanzministerium
('Hessisches Finanzministerium', 'Finanzministerium', 'Ministerium', 'ministerium', NULL, 'finanzenhessen', 'finanzenhessen', NULL, 'finanzen.hessen.de', 8, 'AK 1'),
('Prof. Dr. R. Alexander Lorz', 'Finanzministerium', 'Minister', 'minister', 'CDU', NULL, NULL, NULL, 'finanzen.hessen.de', 8, 'AK 1'),

-- Kultusministerium (Kita, Schulsozialarbeit → AK 5)
('Hessisches Kultusministerium', 'Kultusministerium', 'Ministerium', 'ministerium', NULL, 'SchuleHessen', 'schulehessen', NULL, 'kultusministerium.hessen.de', 7, 'AK 5'),
('Armin Schwarz', 'Kultusministerium', 'Minister', 'minister', 'CDU', NULL, 'armin_schwarz_cdu', NULL, 'kultusministerium.hessen.de', 7, 'AK 5'),

-- Innenministerium (Migration, Flüchtlinge → AK 2)
-- Ministerium: @innen.hessen (Instagram), kein Twitter
-- Minister Roman Poseck: Keine persönlichen Social-Media-Accounts gefunden
('Hessisches Ministerium des Innern, für Sicherheit und Heimatschutz', 'Innenministerium', 'Ministerium', 'ministerium', NULL, NULL, 'innen.hessen', NULL, 'innen.hessen.de', 8, 'AK 2'),
('Roman Poseck', 'Innenministerium', 'Minister', 'minister', 'CDU', NULL, NULL, NULL, 'innen.hessen.de', 8, 'AK 2'),

-- Wirtschaftsministerium (Fachkräfte, Arbeitsmarkt, Wohnen → AK 1, QAG Wohnen)
-- Ministerium: @WirtschaftHE (Twitter), @wirtschafthessen (Insta), @wirtschafthessen@social.hessen.de (Mastodon)
-- Minister Kaweh Mansoori: @El_KaWeh_ (Twitter), @kawehmansoori + @kaweh.mansoori.minister (Instagram)
('Hessisches Ministerium für Wirtschaft, Energie, Verkehr, Wohnen und ländlichen Raum', 'Wirtschaftsministerium', 'Ministerium', 'ministerium', NULL, 'WirtschaftHE', 'wirtschafthessen', 'wirtschafthessen@social.hessen.de', 'wirtschaft.hessen.de', 8, 'AK 1'),
('Kaweh Mansoori', 'Wirtschaftsministerium', 'Minister, stellv. MP', 'minister', 'SPD', 'El_KaWeh_', 'kawehmansoori', NULL, 'kaweh-mansoori.de', 9, 'AK 1'),

-- Justizministerium (Resozialisierung, Straffälligenhilfe → AK 1)
-- Ministerium: @Justiz_Hessen (Twitter), @justiz__hessen (Instagram - doppelter Unterstrich!)
-- Minister Christian Heinz: Keine persönlichen Accounts gefunden
('Hessisches Ministerium der Justiz und für den Rechtsstaat', 'Justizministerium', 'Ministerium', 'ministerium', NULL, 'Justiz_Hessen', 'justiz__hessen', NULL, 'justizministerium.hessen.de', 5, 'AK 1'),
('Christian Heinz', 'Justizministerium', 'Minister', 'minister', 'CDU', NULL, NULL, NULL, 'justizministerium.hessen.de', 5, 'AK 1'),

-- Umweltministerium (Klimaschutz in Sozialeinrichtungen → QAG Klima)
-- Ministerium: @umwelthessen (Instagram)
-- Minister Ingmar Jung: @ingmarjung (Instagram, 3.757 Follower), Twitter unbekannt
('Hessisches Ministerium für Landwirtschaft und Umwelt, Weinbau, Forsten, Jagd und Heimat', 'Umweltministerium', 'Ministerium', 'ministerium', NULL, NULL, 'umwelthessen', NULL, 'landwirtschaft.hessen.de', 6, 'QAG Klimaschutz'),
('Ingmar Jung', 'Umweltministerium', 'Minister', 'minister', 'CDU', NULL, 'ingmarjung', NULL, 'landwirtschaft.hessen.de', 6, 'QAG Klimaschutz'),

-- Digitalministerium (Digitalisierung Sozialwirtschaft → QAG Digitalisierung)
-- Ministerin Kristina Sinemus: @KSinemus (Twitter, 1.411 Follower)
('Hessisches Ministerium für Digitalisierung und Innovation', 'Digitalministerium', 'Ministerium', 'ministerium', NULL, 'DigitalHessen', 'digitalhessen', NULL, 'digitales.hessen.de', 6, 'QAG Digitalisierung'),
('Kristina Sinemus', 'Digitalministerium', 'Ministerin', 'minister', 'CDU', 'KSinemus', NULL, NULL, 'digitales.hessen.de', 6, 'QAG Digitalisierung'),

-- Wissenschaftsministerium (Forschung Sozialwissenschaft, Pflegewissenschaft → AK 3)
-- Minister Timon Gremmels: @Timon_Gremmels (Twitter), @gremmels (Instagram)
-- Hinweis: SPD Hessen hat Twitter verlassen, Account evtl. inaktiv
('Hessisches Ministerium für Wissenschaft und Forschung, Kunst und Kultur', 'Wissenschaftsministerium', 'Ministerium', 'ministerium', NULL, NULL, 'wissenschafthessen', NULL, 'wissenschaft.hessen.de', 5, 'AK 3'),
('Timon Gremmels', 'Wissenschaftsministerium', 'Minister', 'minister', 'SPD', 'Timon_Gremmels', 'gremmels', NULL, 'timon-gremmels.de', 5, 'AK 3'),

-- Europaminister (EU-Förderprogramme, Bundesrat → AK 1)
-- Minister Manfred Pentz: @Ma_Pentz (Twitter), @manfredpentz (Instagram, 5.113 Follower)
('Hessisches Ministerium für Bundes- und Europaangelegenheiten', 'Europaminister', 'Ministerium', 'ministerium', NULL, NULL, NULL, NULL, 'staatskanzlei.hessen.de', 5, 'AK 1'),
('Manfred Pentz', 'Europaminister', 'Minister', 'minister', 'CDU', 'Ma_Pentz', 'manfredpentz', NULL, 'manfred-pentz.de', 5, 'AK 1');

-- ============================================================================
-- KATEGORIE 3: HESSISCHER LANDTAG
-- ============================================================================

INSERT INTO stakeholders (name, organization, role, category, party, twitter_handle, instagram_handle, mastodon_handle, website, email, relevance_score, primary_ak) VALUES
-- Institution
('Hessischer Landtag', 'Landtag', 'Parlament', 'landtag', NULL, 'landtag_hessen', 'hessischerlandtag', 'HessischerLandtag@social.bund.de', 'hessischer-landtag.de', NULL, 10, 'AK 1'),
('Astrid Wallmann', 'Landtag', 'Landtagspräsidentin', 'landtag', 'CDU', NULL, NULL, NULL, 'astrid-wallmann.de', NULL, 8, 'Geschäftsführung'),

-- Sozialausschuss
('Sabine Bächle-Scholz', 'Landtag', 'Vorsitzende Sozialausschuss', 'mdl', 'CDU', NULL, NULL, NULL, 'hessischer-landtag.de', NULL, 9, 'AK 1'),
('Turgut Yüksel', 'Landtag', 'Stellv. Vorsitzender Sozialausschuss', 'mdl', 'SPD', NULL, NULL, NULL, 'hessischer-landtag.de', NULL, 8, 'AK 1'),

-- Sozialpolitische Sprecher
('Max Schad', 'CDU-Fraktion', 'Sprecher Sozialpolitik', 'mdl', 'CDU', NULL, NULL, NULL, 'hessischer-landtag.de/abgeordnete/max-schad', NULL, 8, 'AK 1'),
('Nadine Gersberg', 'SPD-Fraktion', 'Sprecherin Sozialpolitik', 'mdl', 'SPD', NULL, NULL, NULL, 'nadine-gersberg.de', NULL, 8, 'AK 1'),
('Marcus Bocklet', 'Grüne-Fraktion', 'Sprecher Sozialpolitik', 'mdl', 'Grüne', NULL, NULL, NULL, 'hessischer-landtag.de', 'm.bocklet@ltg.hessen.de', 8, 'AK 1'),
('Volker Richter', 'AfD-Fraktion', 'Sprecher Sozialpolitik', 'mdl', 'AfD', NULL, NULL, NULL, 'afd-fraktion-hessen.de', NULL, 6, 'AK 1'),
('Stefan Naas', 'FDP-Fraktion', 'Sprecher Sozialpolitik', 'mdl', 'FDP', NULL, NULL, NULL, 'fdp-fraktion-hessen.de', NULL, 7, 'AK 1'),

-- Fraktionen
('CDU-Fraktion Hessen', 'CDU', 'Landtagsfraktion', 'fraktion', 'CDU', NULL, NULL, NULL, 'cdu-fraktion-hessen.de', NULL, 7, 'AK 1'),
('SPD-Fraktion Hessen', 'SPD', 'Landtagsfraktion', 'fraktion', 'SPD', NULL, 'spdhessen', 'spd@hessen.social', 'spd-fraktion-hessen.de', NULL, 7, 'AK 1'),
('Grüne-Fraktion Hessen', 'Grüne', 'Landtagsfraktion', 'fraktion', 'Grüne', NULL, NULL, NULL, 'gruene-hessen.de/landtag', NULL, 7, 'AK 1'),
('AfD-Fraktion Hessen', 'AfD', 'Landtagsfraktion', 'fraktion', 'AfD', NULL, NULL, NULL, 'afd-fraktion-hessen.de', NULL, 5, 'AK 1'),
('FDP-Fraktion Hessen', 'FDP', 'Landtagsfraktion', 'fraktion', 'FDP', NULL, NULL, NULL, 'fdp-fraktion-hessen.de', NULL, 6, 'AK 1');

-- ============================================================================
-- KATEGORIE 4: KOMMUNALE SPITZENVERBÄNDE
-- ============================================================================

INSERT INTO stakeholders (name, organization, role, category, website, phone, relevance_score, primary_ak) VALUES
('Hessischer Städtetag', 'Kommunale Spitzenverbände', 'Städtetag', 'kommune', 'hess-staedtetag.de', NULL, 8, 'AK 1'),
('Gert-Uwe Mende', 'Hessischer Städtetag', 'Präsident (OB Wiesbaden)', 'kommune', NULL, NULL, 7, 'AK 1'),
('Dr. Jürgen Dieter', 'Hessischer Städtetag', 'Direktor', 'kommune', 'hess-staedtetag.de', NULL, 7, 'AK 1'),

('Hessischer Landkreistag', 'Kommunale Spitzenverbände', 'Landkreistag', 'kommune', 'hlt.de', NULL, 8, 'AK 1'),
('Anita Schneider', 'Hessischer Landkreistag', 'Präsidentin (LK Gießen)', 'kommune', NULL, NULL, 7, 'AK 1'),
('Tim Ruder', 'Hessischer Landkreistag', 'Direktor', 'kommune', 'hlt.de', NULL, 7, 'AK 1'),

('Hessischer Städte- und Gemeindebund', 'Kommunale Spitzenverbände', 'HSGB', 'kommune', 'hsgb.de', '06108-6001-21', 7, 'AK 1'),
('Dr. David Rauber', 'HSGB', 'Geschäftsführer, Sprecher', 'kommune', 'hsgb.de', NULL, 7, 'AK 1');

-- ============================================================================
-- KATEGORIE 5: BUNDESEBENE
-- ============================================================================

INSERT INTO stakeholders (name, organization, role, category, party, twitter_handle, instagram_handle, website, relevance_score, primary_ak) VALUES
('BAGFW', 'Bundesarbeitsgemeinschaft der Freien Wohlfahrtspflege', 'Bundesverband', 'bund', NULL, NULL, NULL, 'bagfw.de', 8, 'AK 1'),
('Evelin Schulz', 'BAGFW', 'Geschäftsführerin', 'bund', NULL, NULL, NULL, 'bagfw.de', 7, 'AK 1'),
('Michael Groß', 'BAGFW', 'Präsident (AWO)', 'bund', NULL, NULL, NULL, 'bagfw.de', 7, 'AK 1'),

('BMAS', 'Bundesministerium für Arbeit und Soziales', 'Bundesministerium', 'bund', NULL, 'bmas_bund', 'bmas_bund', 'bmas.de', 9, 'AK 1'),
('BMFSFJ', 'Bundesministerium für Familie, Senioren, Frauen und Jugend', 'Bundesministerium', 'bund', NULL, 'BMFSFJ', 'bmbfsfj', 'bmfsfj.de', 8, 'AK 5'),

('Jan Feser', 'Bundestag', 'MdB, Ausschuss Arbeit und Soziales', 'mdb', 'AfD', NULL, NULL, 'bundestag.de', 5, 'AK 1');

-- ============================================================================
-- KATEGORIE 6: MEDIEN HESSEN
-- ============================================================================

INSERT INTO stakeholders (name, organization, role, category, twitter_handle, instagram_handle, website, email, relevance_score, primary_ak) VALUES
-- Öffentlich-rechtlich
('Hessischer Rundfunk', 'hr', 'Öffentlich-rechtlich', 'presse', 'hrPresse', 'hessischerrundfunk', 'hr.de', 'pressedesk@hr.de', 8, NULL),
('hessenschau', 'hr', 'Nachrichtenportal', 'presse', 'hessenschaude', 'hessenschau', 'hessenschau.de', NULL, 9, NULL),
('ZDF Landesstudio Hessen', 'ZDF', 'Landesstudio', 'presse', NULL, NULL, 'zdf.de', NULL, 6, NULL),

-- Tageszeitungen
('Frankfurter Rundschau', 'FR', 'Tageszeitung', 'presse', 'FR_de', NULL, 'fr.de', NULL, 8, NULL),
('Peter Hanack', 'Frankfurter Rundschau', 'Redakteur Sozialpolitik', 'presse', NULL, NULL, 'fr.de', NULL, 7, NULL),
('FAZ Rhein-Main', 'FAZ', 'Regionalausgabe', 'presse', 'FAZ_NET', NULL, 'faz.net', 'wiesbaden@faz.de', 8, NULL),
('HNA', 'HNA', 'Tageszeitung Nordhessen', 'presse', 'HNA_online', NULL, 'hna.de', NULL, 6, NULL),
('Fuldaer Zeitung', 'FZ', 'Tageszeitung', 'presse', 'fuldaerzeitung', NULL, 'fuldaerzeitung.de', 'redaktion@fuldaerzeitung.de', 5, NULL),

-- Fachmedien
('Wohlfahrt Intern', 'Röthig Medien', 'Fachmedium', 'presse', NULL, NULL, 'wohlfahrtintern.de', NULL, 6, NULL),
('CAREkonkret', 'Vincentz Network', 'Fachmedium Pflege', 'presse', NULL, NULL, 'carekonkret.net', 'service@vincentz.net', 6, 'AK 3'),

-- ============================================================================
-- JOURNALISTEN MIT SOCIAL-MEDIA-PRÄSENZ (Landespolitik Hessen)
-- ============================================================================
-- Diese Journalisten berichten regelmäßig über hessische Landespolitik und
-- breaken oft Nachrichten zuerst auf Twitter/X bevor sie in der Zeitung erscheinen.

-- Frankfurter Rundschau
('Pitt von Bebenburg', 'Frankfurter Rundschau', 'Chefreporter, ehem. Hessen-Korrespondent', 'presse', 'PvBebenburg', NULL, 'fr.de', NULL, 9, NULL),
-- @PvBebenburg: "Hessen-Korrespondent der FR. Neugierig, informiert, meinungsstark."
-- Hessischer Journalistenpreis 2014, 35 Jahre FR, Buchautor

('Peter Hanack', 'Frankfurter Rundschau', 'Redakteur Sozialpolitik/Bildung', 'presse', NULL, NULL, 'fr.de', 'peter.hanack@fr.de', 8, 'AK 1'),
-- Schwerpunkt: Bildungssystem, Soziales, Corona-Berichterstattung
-- Hessischer Journalistenpreis 2. Preis 2021

-- FAZ Rhein-Main (Landespressekonferenz-Vorstand)
('Dr. Ewald Hetrodt', 'FAZ', 'Parlamentskorrespondent, LPK-Sprecher', 'presse', NULL, NULL, 'faz.net', 'e.hetrodt@faz.de', 9, NULL),
-- Seit 2009 FAZ-Korrespondent in Wiesbaden, seit 2023 LPK-Sprecher

('Timo Steppat', 'FAZ', 'Korrespondent Wiesbaden', 'presse', NULL, NULL, 'faz.net', 't.steppat@faz.de', 7, NULL),
('Mechthild Harting', 'FAZ', 'Redakteurin Rhein-Main (Planung, Umwelt)', 'presse', NULL, NULL, 'faz.net', 'm.harting@faz.de', 6, 'QAG Klimaschutz'),
('Ralf Euler', 'FAZ', 'Redakteur Sonntagszeitung/Rhein-Main', 'presse', NULL, NULL, 'faz.net', 'r.euler@faz.de', 6, NULL),

-- Hessischer Rundfunk / hessenschau
('Simone Behse', 'Hessischer Rundfunk', 'Landtagskorrespondentin, LPK-Vorstand', 'presse', NULL, NULL, 'hr.de', NULL, 9, NULL),
-- Seit April 2020 Landtagskorrespondentin, LPK-Vorstand seit 2020

('Jörn Perske', 'hessenschau', 'Online-Redakteur, Social Media', 'presse', NULL, NULL, 'hessenschau.de', NULL, 6, NULL),
-- Seit August 2020 beim HR, primär Osthessen

-- OSTHESSEN|NEWS (Digitale Medien, LPK-Vorstand)
('Christian P. Stadtfeld', 'OSTHESSEN|NEWS', 'Chefredakteur, GF, LPK-Vorstand', 'presse', NULL, NULL, 'osthessen-news.de', NULL, 7, NULL);
-- Seit 2023 im LPK-Vorstand, vertritt digitale Medien

-- Hinweis: Viele hessische Journalisten haben Twitter/X verlassen oder sind inaktiv:
-- - Hessischer Rundfunk verlässt X nach der Bundestagswahl 2025
-- - SPD Hessen hat Twitter-Accounts gelöscht
-- - Einige FAZ-Redakteure nutzen primär LinkedIn statt Twitter

-- ============================================================================
-- KATEGORIE 7: BÜNDNISPARTNER UND NETZWERKE
-- ============================================================================

INSERT INTO stakeholders (name, organization, role, category, twitter_handle, instagram_handle, website, relevance_score, primary_ak) VALUES
-- Gewerkschaften/Arbeitgeber
('DGB Hessen-Thüringen', 'DGB', 'Gewerkschaftsbund', 'gewerkschaft', NULL, NULL, 'hessen-thueringen.dgb.de', 7, 'AK 1'),
('Michael Rudolph', 'DGB Hessen-Thüringen', 'Vorsitzender', 'gewerkschaft', NULL, NULL, 'hessen-thueringen.dgb.de', 6, 'AK 1'),
('VhU', 'Vereinigung hessischer Unternehmerverbände', 'Arbeitgeberverband', 'arbeitgeber', NULL, NULL, 'vhu.de', 6, 'AK 1'),
('IHK Frankfurt', 'IHK', 'Industrie- und Handelskammer', 'arbeitgeber', NULL, NULL, 'frankfurt-main.ihk.de', 5, NULL),

-- Kirchen
('EKHN', 'Evangelische Kirche Hessen-Nassau', 'Landeskirche', 'kirche', NULL, NULL, 'ekhn.de', 7, 'AK 1'),
('EKKW', 'Evangelische Kirche Kurhessen-Waldeck', 'Landeskirche', 'kirche', NULL, NULL, 'ekkw.de', 6, 'AK 1'),
('Bistum Limburg', 'Katholische Kirche', 'Bistum', 'kirche', NULL, 'bistumlimburg', 'bistumlimburg.de', 7, 'AK 1'),
('Dr. Georg Bätzing', 'Bistum Limburg', 'Bischof, DBK-Vorsitzender', 'kirche', NULL, NULL, 'bistumlimburg.de', 7, 'AK 1'),
('Bistum Mainz', 'Katholische Kirche', 'Bistum', 'kirche', NULL, NULL, 'bistummainz.de', 6, 'AK 1'),
('Bistum Fulda', 'Katholische Kirche', 'Bistum', 'kirche', NULL, NULL, 'bistum-fulda.de', 6, 'AK 1'),

-- NGOs
('PRO ASYL', 'PRO ASYL', 'Menschenrechtsorganisation', 'ngo', 'ProAsyl', 'proasyl', 'proasyl.de', 8, 'AK 2'),
('Amnesty International Deutschland', 'Amnesty', 'Menschenrechtsorganisation', 'ngo', 'amnesty_de', NULL, 'amnesty.de', 6, 'AK 2'),

-- Partner
('LOTTO Hessen', 'LOTTO', 'Förderer Sozialpreis', 'partner', 'LOTTOHes_Presse', NULL, 'lotto-hessen.de', 5, NULL),
('Aktion Mensch', 'Aktion Mensch', 'Förderorganisation', 'partner', NULL, NULL, 'aktion-mensch.de', 6, 'AK 4');

-- ============================================================================
-- KATEGORIE 8: FACHORGANISATIONEN
-- ============================================================================

INSERT INTO stakeholders (name, organization, role, category, twitter_handle, website, phone, relevance_score, primary_ak) VALUES
-- Sucht/Gesundheit
('Hessische Landesstelle für Suchtfragen', 'HLS', 'Fachverband', 'fachverband', 'HLS_Frankfurt', 'hls-online.org', NULL, 7, 'AK 3'),

-- Senioren
('Landesseniorenvertretung Hessen', 'LSVH', 'Interessenvertretung', 'fachverband', NULL, 'landesseniorenvertretung.hessen.de', '0611-9887119', 6, 'AK 3'),

-- Behindertenhilfe
('Landesbehindertenrat Hessen', 'LBR', 'Interessenvertretung', 'fachverband', NULL, 'lbrhessen.com', NULL, 7, 'AK 4'),
('Lebenshilfe Landesverband Hessen', 'Lebenshilfe', 'Fachverband', 'fachverband', NULL, 'lebenshilfe-hessen.de', NULL, 7, 'AK 4'),
('Blinden- und Sehbehindertenbund Hessen', 'BSBH', 'Fachverband', 'fachverband', NULL, 'bsbh.org', NULL, 5, 'AK 4'),

-- Migration
('Hessischer Flüchtlingsrat', 'Flüchtlingsrat', 'Fachverband', 'fachverband', NULL, 'fr-hessen.de', '069-976 987 10', 8, 'AK 2'),

-- Kinder/Jugend
('LAG KitaEltern Hessen', 'Elternvertretung', 'Interessenvertretung', 'fachverband', NULL, 'kita-eltern-hessen.de', NULL, 6, 'AK 5'),
('Landeselternbeirat Hessen', 'LEB', 'Schulelternvertretung', 'fachverband', NULL, 'leb-hessen.de', NULL, 5, 'AK 5'),
('Kinderschutzbund LV Hessen', 'DKSB', 'Kinderschutz', 'fachverband', NULL, 'kinderschutzbund-hessen.de', NULL, 7, 'AK 5'),

-- Armut/Soziales
('LAG Schuldnerberatung Hessen', 'Schuldnerberatung', 'Fachverband', 'fachverband', NULL, 'schuldnerberatung-hessen.de', NULL, 6, 'AK 1');
```

**Items-Tabelle (Mention-Felder):**

```sql
-- Zur items-Tabelle hinzufügen:

    -- Erwähnungen (wer wird genannt?)
    mentioned_handles TEXT,              -- JSON: ["@SozialHessen", "@drklvhessen"]
    mentioned_stakeholder_ids TEXT,      -- JSON: [12, 45]
    mentioned_categories TEXT,           -- JSON: ["ministerium", "liga_mitglied"]
    mentions_liga_direct BOOLEAN DEFAULT 0,
    mentions_liga_member BOOLEAN DEFAULT 0,
    mentions_any_stakeholder BOOLEAN DEFAULT 0,

    -- Konversation
    is_reply BOOLEAN DEFAULT 0,
    is_reply_to_stakeholder BOOLEAN DEFAULT 0,
    reply_to_id TEXT,
    reply_to_handle TEXT,
    reply_to_stakeholder_id INTEGER REFERENCES stakeholders(id),
    thread_id TEXT,
    thread_participants TEXT,            -- JSON array

    -- Art der Ansprache
    is_direct_question BOOLEAN DEFAULT 0,
    is_criticism BOOLEAN DEFAULT 0,
    is_request BOOLEAN DEFAULT 0,
    requires_response BOOLEAN DEFAULT 0,

    -- Absender
    sender_handle TEXT,
    sender_stakeholder_id INTEGER REFERENCES stakeholders(id),
    sender_category TEXT,
    sender_party TEXT,
    sender_org TEXT,
```

**Mention-Erkennung:**

```python
async def detect_mentions(item: RawItem, content: str) -> MentionInfo:
    """
    Erkennt Erwähnungen von Stakeholdern im Content.
    """
    mentions = MentionInfo()

    # Alle @handles extrahieren
    handles = re.findall(r'@(\w+)', content)
    mentions.mentioned_handles = handles

    # Gegen Stakeholder-DB matchen
    for handle in handles:
        stakeholder = await db.execute(
            """SELECT id, category, name, organization
               FROM stakeholders
               WHERE twitter_handle = ?
                  OR mastodon_handle LIKE ?
                  OR instagram_handle = ?""",
            (handle, f"%{handle}%", handle)
        )
        if stakeholder:
            mentions.mentioned_stakeholder_ids.append(stakeholder.id)
            mentions.mentioned_categories.append(stakeholder.category)

            # Liga-Check
            if stakeholder.category == "liga":
                mentions.mentions_liga_direct = True
            elif stakeholder.category == "liga_mitglied":
                mentions.mentions_liga_member = True

    mentions.mentions_any_stakeholder = len(mentions.mentioned_stakeholder_ids) > 0

    # Absender prüfen
    sender = await db.execute(
        "SELECT * FROM stakeholders WHERE twitter_handle = ?",
        (item.author_handle,)
    )
    if sender:
        mentions.sender_stakeholder_id = sender.id
        mentions.sender_category = sender.category
        mentions.sender_party = sender.party
        mentions.sender_org = sender.organization

    # Ansprache-Typ erkennen (einfache Heuristik)
    if "?" in content and mentions.mentions_any_stakeholder:
        mentions.is_direct_question = True
        mentions.requires_response = True

    return mentions
```

**Prioritäts-Boost durch Mentions:**

```python
def calculate_priority_with_mentions(item: CommunicationItem) -> str:
    score = item.relevance_score

    # === MENTION-BOOSTS ===
    if item.mentions_liga_direct:
        score += 25                      # Direkte Liga-Erwähnung
    if item.mentions_liga_member:
        score += 15                      # Mitgliedsverband erwähnt
    if item.is_reply_to_stakeholder:
        score += 10                      # Antwort auf Stakeholder
    if item.requires_response:
        score += 15                      # Erwartet Antwort
    if item.is_direct_question:
        score += 10
    if item.is_criticism:
        score += 10                      # Kritik beobachten

    # === ABSENDER-BOOSTS ===
    sender_boosts = {
        "minister": 30,
        "staatssekretaer": 25,
        "mdl": 20,
        "journalist": 20,
        "verband_leitung": 15,
        "kommune": 10,
    }
    score += sender_boosts.get(item.sender_category, 0)

    # === STAKEHOLDER-INTERAKTION ===
    # Wenn zwei wichtige Stakeholder interagieren
    if (item.sender_stakeholder_id and
        item.is_reply_to_stakeholder and
        item.reply_to_stakeholder_id):
        score += 20                      # Stakeholder-Dialog

    # Priorität
    if score >= 50 or item.mentions_liga_direct:
        return "🔴"
    elif score >= 35:
        return "🟠"
    elif score >= 20:
        return "🟡"
    elif score >= 10:
        return "🔵"
    return None
```

#### 6.3 "Neu"-Markierung und Benachrichtigungen

```python
async def process_new_item(item: RawItem):
    """
    Verarbeitet ein neues Item und löst Benachrichtigungen aus.
    """
    # Mentions erkennen
    mentions = await detect_mentions(item, item.content)
    item.update_from_mentions(mentions)

    # Item als "neu" markieren
    item.first_seen_at = datetime.now()
    item.is_read = False
    item.notified = False

    # Keyword-Analyse durchführen
    analysis = calculate_relevance_score(item.content, item.title)

    # Priorität MIT Mention-Boost berechnen
    priority = calculate_priority_with_mentions(item)

    # Bei hoher Priorität oder Liga-Erwähnung: Sofort-Benachrichtigung
    if priority in ["🔴", "🟠"] or item.mentions_liga_direct:
        await send_notification(
            type="new_high_priority",
            item=item,
            priority=priority,
            reason="liga_mentioned" if item.mentions_liga_direct else "high_score"
        )
        item.notified = True

    await db.save(item)

    # WebSocket-Broadcast an alle verbundenen Clients
    await broadcast_to_websockets({
        "type": "new_item",
        "item": item.to_dict(),
        "priority": priority,
        "mentions_liga": item.mentions_liga_direct,
        "stakeholder_interaction": item.is_reply_to_stakeholder
    })
```

---

### 7. Projektstruktur

```
liga-briefing-system/
├── config/
│   ├── sources.yaml          # Alle Datenquellen
│   ├── keywords.yaml         # Trigger-Keywords
│   ├── llm_providers.yaml    # API-Konfiguration
│   └── delivery.yaml         # E-Mail/Webhook-Config
│
├── scrapers/
│   ├── __init__.py
│   ├── base.py               # Abstrakte Basisklasse
│   ├── rss_scraper.py        # RSS-Feed-Parser
│   ├── html_scraper.py       # BeautifulSoup-Scraper
│   ├── mastodon_scraper.py   # Mastodon.py Integration
│   ├── twitter_scraper.py    # Nitter-Proxy
│   ├── bluesky_scraper.py    # Bluesky RSS-Feeds
│   ├── linkedin_scraper.py   # LinkedIn API (Phase 2)
│   ├── youtube_scraper.py    # YouTube RSS-Feeds
│   └── landtag_scraper.py    # PDF + HTML
│
├── processors/
│   ├── __init__.py
│   ├── keyword_filter.py     # Stufe 1: Keyword-Matching
│   ├── llm_analyzer.py       # Stufe 2: LLM-Analyse
│   └── deduplicator.py       # Duplikat-Erkennung
│
├── output/
│   ├── __init__.py
│   ├── briefing_generator.py # Markdown-Generierung
│   ├── email_sender.py       # SMTP-Versand
│   └── webhook_sender.py     # Slack/Teams
│
├── database/
│   ├── __init__.py
│   ├── models.py             # SQLAlchemy Models
│   └── migrations/           # Alembic Migrations
│
├── utils/
│   ├── __init__.py
│   ├── logger.py
│   └── rate_limiter.py
│
├── main.py                   # Orchestrierung
├── scheduler.py              # APScheduler/Cron
├── requirements.txt
├── .env.example
└── README.md
```

---

### 8. Zeitplan und Automatisierung

```python
# scheduler.py - APScheduler Konfiguration

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler()

# Datenerfassung: Alle 2 Stunden (6:00-22:00)
scheduler.add_job(
    run_all_scrapers,
    CronTrigger(hour='6-22/2', minute=0),
    id='scraping'
)

# Keyword-Analyse: Nach jedem Scraping-Lauf
scheduler.add_job(
    run_keyword_analysis,
    CronTrigger(hour='6-22/2', minute=15),
    id='keyword_analysis'
)

# LLM-Analyse: 2x täglich (Morgen + Nachmittag)
scheduler.add_job(
    run_llm_analysis,
    CronTrigger(hour='6,14', minute=30),
    id='llm_analysis'
)

# Briefing-Generierung: Täglich 7:00
scheduler.add_job(
    generate_and_send_briefing,
    CronTrigger(hour=7, minute=0),
    id='daily_briefing'
)

# Wöchentliche Zusammenfassung: Freitag 16:00
scheduler.add_job(
    generate_weekly_summary,
    CronTrigger(day_of_week='fri', hour=16, minute=0),
    id='weekly_summary'
)
```

---

### 9. Kosten und Ressourcen

#### Geschätzte tägliche Nutzung:
| Komponente | Volumen | Kosten |
|------------|---------|--------|
| RSS-Feeds | ~50 Artikel | $0 |
| HTML-Scraping | ~30 Artikel | $0 |
| Social Media | ~40 Posts | $0 |
| Landtag-Dokumente | ~5 Dokumente | $0 |
| **Gesamt erfasst** | **~125 Items** | **$0** |
| Nach Keyword-Filter | ~40 Items (30%) | $0 |
| OpenRouter (Llama 3.3 70B) | ~40 Requests | $0 |
| Groq (Backup) | ~10 Requests | $0 |
| **Monatliche Kosten** | | **$0** |

#### Einmalige Kosten:
| Posten | Kosten |
|--------|--------|
| OpenRouter Account-Aktivierung | $10 (einmalig) |
| Server (optional, wenn nicht lokal) | ~$5-10/Monat |

---

### 10. Nächste Schritte

1. **Sofort:** API-Keys besorgen (OpenRouter, Groq, Mistral)
2. **Tag 1-2:** Basis-Scraper implementieren (RSS + HTML)
3. **Tag 3:** Keyword-Filter implementieren
4. **Tag 4-5:** LLM-Integration mit Fallback
5. **Tag 6:** Output-Generierung + E-Mail
6. **Tag 7:** Testing + Deployment

---

*Architektur-Dokument v1.0 – Liga-Briefing-System*
*Erstellt: Januar 2026*
