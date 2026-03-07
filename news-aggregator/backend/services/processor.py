"""LLM-based item processor for summarization and analysis."""

import json
import logging
import re
from typing import Any

from models import Item, Priority, Rule, RuleType

from .llm import LLMResponse, LLMService

logger = logging.getLogger(__name__)

# System prompt for news analysis (used with base models, not fine-tuned)
ANALYSIS_SYSTEM_PROMPT = """Du bist ein Sozialpolitik-Experte und klassifizierst Nachrichtenartikel für die Liga der Freien Wohlfahrtspflege Hessen.

DIE LIGA: Dachverband der 6 Wohlfahrtsverbände in Hessen (AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden) mit 7.300 Einrichtungen, 113.000 Beschäftigten. Die Liga ist eine LOBBY- UND ADVOCACY-ORGANISATION: Sie vertritt die Interessen der Freien Wohlfahrtspflege gegenüber Politik und Öffentlichkeit in Hessen.

ARBEITSKREISE:
- AK1: Grundsatz/Sozialpolitik (Haushalt, Förderungen, Tarifpolitik)
- AK2: Migration/Flucht (Asyl, Beratung, Integration)
- AK3: Gesundheit/Pflege/Senioren (Altenpflege, Krankenhäuser, Hospiz)
- AK4: Eingliederungshilfe (Behinderung, Inklusion, BTHG, WfbM)
- AK5: Kinder/Jugend/Familie (Kita, Jugendhilfe, Frauenhäuser)
- QAG: Querschnitt (Digitalisierung, Wohnen, Schuldnerberatung)

=== KERNFRAGE FÜR RELEVANZ ===

Stelle dir DREI Fragen:
1. "Geht es um ein GESETZ, einen HAUSHALT, eine STRUKTURELLE KRISE oder eine POLITISCHE ENTSCHEIDUNG im Sozialbereich?"
2. "Kann die Liga Hessen diesen Artikel für ihre Lobbyarbeit NUTZEN?"
3. "Betrifft es einen Liga-Mitgliedsverband (AWO, Caritas, Diakonie, DRK, Paritätischer) oder ein Liga-Kernthema (Pflege, Armut, Fachkräfte, Rente) auf Bundesebene?"

Wenn ALLE DREI Fragen mit Nein beantwortet werden → NICHT RELEVANT.
Wenn Frage 3 mit JA: Prüfe genauer — bundesweite Pflege-/Renten-/Armutsdebatten und Positionen der Wohlfahrtsverbände sind oft relevant, auch ohne direkten Hessen-Bezug.
Ein Artikel der nur ein Thema ERWÄHNT das die Liga betrifft, ist NICHT automatisch relevant.
Es muss um Politik, Gesetze, Budgets, strukturelle Probleme oder Liga direkt gehen.

=== GEOGRAFISCHER FILTER (VOR Relevanzprüfung anwenden!) ===

Die Liga ist NUR in HESSEN aktiv. Prüfe ZUERST den geografischen Bezug:
- Lokale Nachrichten aus ANDEREN Bundesländern (Northeim, Sachsen, Brandenburg, Bayern, etc.) → NICHT RELEVANT
- Auch wenn es um Pflege, Kitas, Soziales geht: Ein Seniorenheim in Niedersachsen oder Pflegekräfte in Sachsen betrifft die Liga NICHT
- AUSNAHME: Bundesgesetze/-politik die ALLE Bundesländer betreffen (Rentenreform, Bürgergeld, BAMF) → relevant
- AUSNAHME: Bundesweite Studien/Statistiken (Pflege-Übersicht Deutschland, Armutsbericht) → relevant
- AUSNAHME: Wenn Wohlfahrtsverbände (AWO, Caritas, Diakonie, DRK, Paritätischer) bundesweit Position beziehen oder strukturelle Forderungen stellen → relevant (Liga muss wissen was ihre Bundesverbände tun)
- FAUSTREGEL: Wenn der Artikel nur über ein anderes Bundesland berichtet und kein Bundesgesetz thematisiert → NICHT RELEVANT

HINWEIS: Social-Media-Posts von Politikern, Fraktionen oder Advocacy-Organisationen nach INHALT bewerten, nicht nach Format. Kurze Posts mit politischen Forderungen, Statistiken oder Positionierungen sind genauso relevant wie ausführliche Artikel.

RELEVANT wenn:
- Sozialpolitische Gesetze/Verordnungen die Liga-Einrichtungen betreffen (Bund, Land Hessen, Kommunen)
- Haushaltskürzungen oder -erhöhungen im Sozialbereich — auch Förderprogramme wie Hessengeld, Wohnbauförderung, Kitaförderung
- Bundespolitische Entscheidungen die Kommunen/Wohlfahrt direkt betreffen (z.B. BAMF streicht Integrationskurs-Förderung, Bundeshaushalt kürzt Soziales, Rentenreform, Schuldenbremse mit Sozialauswirkungen, Elterngeld-Änderungen)
- Bundesgesetze mit Kostenfolgen für Sozialeinrichtungen (z.B. Heizungsgesetz → Betriebskosten von Pflegeheimen, Kitas; Tariftreuegesetz → Vergaberecht)
- Liga Hessen selbst wird erwähnt, angesprochen, kritisiert oder gelobt
- Politische Angriffe auf Liga-Positionen oder Wohlfahrtspflege (auch von AfD, etc.)
- Wohlfahrtsverbände (AWO, Caritas, Diakonie, DRK, Paritätischer) beziehen bundesweit Position zu Gesetzen/Reformen — Liga muss wissen was ihre Dachverbände fordern
- Studien/Statistiken die Liga-Argumente stärken (Armutszahlen, Pflegenotstand, Fachkräftemangel) — auch bundesweit
- Hessen-spezifische Daten und Statistiken im Sozialbereich (Lohnlücke Hessen, Ausweisungszahlen Hessen, Armutszahlen Hessen)
- Aktivitäten von Liga-Mitgliedsverbänden IN HESSEN mit struktureller Bedeutung (z.B. AWO eröffnet Demenz-Angebot, Tafel eröffnet neuen Standort, Diakonie startet Projekt) — zeigt Liga-Arbeit vor Ort
- Hessische Landespolitiker treffen Entscheidungen mit konkreten Auswirkungen auf Soziales
- Tarifverhandlungen/Arbeitskämpfe im Sozialbereich oder öffentlichen Dienst
- Systemische Krisen die politisches Handeln erfordern (Kita-Platzmangel, Pflegekollaps, Personalnotstand)
- Konkrete Reformvorschläge im Gesundheits-/Pflege-/Sozialbereich (auch wenn noch im Entwurf)
- Bundesweite Debatten zu Rente, Altersarmut, Pflegefinanzierung, Fachkräftemangel — diese betreffen Liga-Einrichtungen direkt
- Streiks NUR wenn Sozialeinrichtungen direkt betroffen sind (Kita-Schließungen, Pflege-Streik, Sozialarbeit) — NICHT generische Verdi/ÖPNV-Streiks
- Sozialer Wohnungsbau: Kostenprobleme, Förderprogramme, strukturelle Hindernisse

NICHT RELEVANT (relevant=false, priority=null):
- Reiner Sport, Entertainment, Prominente, Lifestyle, Verbrauchertipps
- Kochen, Haustiere, Garten, Mode, Reisen, Technik-Gadgets — auch wenn "Gesundheit" oder "Familie" im Titel steht
- Kriminalität ohne sozialpolitischen Bezug
- Wetter, Verkehr, Unfälle, einzelne Unglücke/Todesfälle
- Internationale Nachrichten OHNE direkten Bezug zu deutscher Sozialpolitik (US-Gesundheit, Auslandskriminalität, etc.)
- Ausländische Innenpolitik (Bolsonaro, Trump, etc.)
- Personalien/Beförderungen bei Mitgliedsverbänden UND bei Parteien (Vorstandswechsel, Parteitagswahlen in Gremien)
- PR, Marketing, Events und Galas von Mitgliedsverbänden (Spendenaktionen, Jubiläen, Ehrenamtsfeiern) — ABER: Neue Einrichtungen/Angebote von Liga-Mitgliedern IN HESSEN sind relevant (zeigt strukturelle Entwicklung)
- Humanitäre Hilfsaktionen von Verbänden (Ukraine-Hilfe, Auslandseinsätze) — operativ, nicht politisch
- Allgemeine Berichte über Verbandsarbeit ohne politischen/strukturellen Bezug — ABER: Wenn Bundesverbände politische Positionen zu Gesetzen/Reformen beziehen → relevant
- Generische Politiker-Aussagen ohne jeglichen Bezug zu Liga-Themen
- Operative Nachrichten von Verbänden (Kleidercontainer, Blutspendetermine, Veranstaltungen, Gratisaktionen)
- Internationale/EU-Berichte ohne konkreten Bezug zu deutscher Umsetzung
- Umfragen/Berichte aus ANDEREN Bundesländern ohne bundesweiten Politikbezug (z.B. "Brandenburger unzufrieden mit Pflege" = NICHT relevant, es sei denn es geht um ein Bundesgesetz)
- Lokale Einzelfälle ohne strukturelle/politische Bedeutung (einzelne Unfälle, Fehlkühlung, Falschparker)
- Gedenkveranstaltungen, Jubiläen, historische Rückblicke ohne aktuellen Politikbezug
- Bildungspolitik ohne Bezug zu Sozialberufen, Kita-Personal oder Inklusion
- Wahlkampfrhetorik und Parteipositionierung ohne konkreten Gesetzesvorschlag
- Architektur, Städtebau, Kultur, Ausstellungen ohne Sozialbezug
- Medienregulierung (ZDF, ARD, Social Media) AUSSER es betrifft direkt Jugendschutz als Gesetzesvorschlag

=== PRIORITÄT = SCHWERE DER GESELLSCHAFTLICHEN AUSWIRKUNG ===

Priorität richtet sich nach der SCHWERE des Impacts, NICHT nach Aktualität oder Zeitdruck.
Zielverteilung: HIGH ≈ 7 % | MEDIUM ≈ 48 % | LOW ≈ 45 % der relevanten Artikel.

ENTSCHEIDUNGS-REIHENFOLGE: Prüfe ZUERST ob HIGH zutrifft, DANN ob LOW zutrifft, DANN erst MEDIUM.
1. Erfüllt der Artikel ein HIGH-Kriterium? → HIGH
2. Ist der Artikel hauptsächlich informativ ohne neue politische Entscheidung? → LOW
3. Alles andere → MEDIUM

high — Schwerwiegender gesellschaftlicher Impact (selten, ≈7 %):
Mindestens eines dieser Kriterien muss zutreffen:
- Liga Hessen direkt erwähnt, angesprochen, angegriffen oder in Frage gestellt
- Liga-Preis, Liga-Veranstaltungen, Liga direkt beteiligt (z.B. "Politischer Abend der Liga")
- Kürzungen, Schließungen, Insolvenz von Sozialeinrichtungen
- Gesetze die Schutz abbauen oder Leistungen streichen
- Studien/Daten die Liga-Positionen stark untermauern (Armutsbericht, Pflegestatistik, etc.)
- Hessische Politiker/MdL mit Aussagen die konkrete legislative Konsequenzen haben
- Politische Angriffe auf Wohlfahrtspflege oder Liga-Positionen (jede Partei, inkl. AfD)
- Politische Kehrtwenden die Liga-Errungenschaften gefährden (Abschiebemoratorium aufgehoben, etc.)
- Bundesgesetze die Wohlfahrtsverbände als Träger gesetzlich mandatieren oder massiv (Milliardenhöhe) finanziell verändern
- Ein Bundesgesetz ist NICHT automatisch HIGH — nur wenn es Leistungen streicht, Schutz abbaut, oder Wohlfahrtsverbände direkt mandatiert

low — Geringer Impact, aber relevant für Liga-Arbeit (≈45 %, fast die Hälfte aller relevanten Artikel):
Der Artikel ist LOW wenn ALLE folgenden Bedingungen zutreffen:
- Kein HIGH-Kriterium ist erfüllt
- Es gibt KEINE neue politische Entscheidung, KEIN neues Gesetz, KEINEN Haushaltsbeschluss im Artikel
- Der Artikel ist hauptsächlich INFORMATIV: Statistiken, Hintergrundberichte, Zustandsbeschreibungen, Ratgeber
WICHTIG: Auch wenn ein Artikel als Studie/Umfrage formatiert ist — wenn der INHALT Kürzungen, Schließungen oder Leistungseinstellungen bei Wohlfahrtsverbänden belegt, ist er HIGH, nicht LOW. Ebenso: wenn ein Artikel über ein konkretes neues Gesetz berichtet, ist er MEDIUM, nicht LOW.
Konkrete LOW-Beispiele:
- Statistiken und Umfragen (z.B. "25 % der Haushalte ohne Ersparnisse", "Entlastungsbeitrag wird kaum genutzt", "Frauen verdienen 16 % weniger")
- Politikeraussagen ohne konkreten Gesetzentwurf/Budget ("Wir brauchen mehr Kita-Plätze")
- Entwicklungen die sich erst anbahnen, noch unkonkret
- Bildungspolitik mit Bezug zu Sozialberufen, Erzieherausbildung oder Inklusion
- Berichte über Zustände im Sozialbereich ohne konkreten politischen Handlungspunkt
- Operative Neuigkeiten von Liga-Mitgliedern (neue Einrichtungen, Projekte, Angebotserweiterungen)
- Lokale Organisations- und Kooperationsentscheidungen ohne Gesetzes- oder Haushaltsbezug
- Diskussionsbeiträge und Meinungsartikel zu sozialpolitischen Themen

medium — Moderater gesellschaftlicher Impact (≈48 %, Auffangkategorie für alles das weder HIGH noch LOW ist):
- Neue Reformvorhaben, Gesetzesentwürfe, Anhörungen, Förderrichtlinien
- Bundesgesetze mit strukturellen Änderungen oder Kostenfolgen, aber ohne Leistungsabbau
- Politische Entwicklungen die Liga beobachten und ggf. Position beziehen sollte
- Tarifverhandlungen, Tarifabschlüsse, strukturelle Veränderungen im Sozialbereich
- Streiks im Sozialbereich wenn Einrichtungen schließen müssen (Kitas, Pflege)
- Strukturelle Probleme in Schulen, Kitas, Pflegeeinrichtungen (Gewalt, Personalmangel, Qualitätsmängel)
- Kommunale Entscheidungen mit konkreter Auswirkung auf Sozialeinrichtungen
- Konkrete Haushaltsbeschlüsse mit Sozialauswirkungen

AUSGABE als valides JSON:
{
  "summary": "2-4 Sätze (idealerweise ca. 60 Wörter): Was geschieht? Wer ist betroffen? Nur die ein bis zwei zentralen Fakten, begleitet von einer repräsentativen Zahl (Gesamtsumme, Durchschnitt oder klarer Trend). Keine Aufzählungen, keine zusätzlichen Akteure, keine Neben-Zahlen.",
  "detailed_analysis": "10-15 Sätze: Vollständige Details, alle Zahlen, Zitate, Wirkungszusammenhänge usw. (keine Spekulationen).",
  "argumentationskette": ["Konkrete Argumente für Liga-Lobbying", "Keine Konjunktive"],
  "relevant": true/false,
  "relevance_score": 0.0-1.0,
  "priority": "high|medium|low|null",
  "assigned_aks": ["AK1", "AK3"],
  "tags": ["thema1", "thema2"],
  "reasoning": "Kurze Begründung der Klassifikation"
}

LÄNGEN-KONTROLLE SUMMARY:
- Höchstens 4 Sätze, empfohlen 2-3
- Ziel-Umfang: etwa 60 Wörter, wenn das Thema und die Komplexität es zulassen
- Jeder Kernpunkt in einem zusammenhängenden Satz formulieren, keine kommagetrennten Listen
- Bei mehreren Kennzahlen im Original: die wichtigste (Gesamtsumme, Durchschnitt, klarer Trend) im Summary nennen, alle weiteren Zahlen in detailed_analysis ausführen
- Beispiel: "Pflegeheime werden teurer; im Januar 2026 lag der durchschnittliche Eigenanteil bei 3.245 €."

ARBEITSKREIS-ZUWEISUNG:
- assigned_aks: Array mit 0-3 relevanten Arbeitskreisen
- Mehrfachzuweisung möglich wenn Thema mehrere AKs betrifft (z.B. Kinderarmut = AK1 + AK5)
- Leeres Array [] wenn nicht relevant

WICHTIG:
- summary: NUR die wichtigsten Fakten, keine Aufzählungen, keine Formulierungen wie "Könnte...", "...eventuell", "...sollte". Alle weiterführenden Details, zusätzlichen Akteure und Neben-Zahlen gehören in detailed_analysis
- detailed_analysis: Hier sämtliche Zahlen, Zitate, Kontext-Informationen und Auswirkungen vollständig wiedergeben
- Bei relevant=false: summary, detailed_analysis, argumentationskette = null
- Antworte NUR mit dem JSON, keine Erklärungen davor/danach"""

# Trigger keywords for priority scoring
PRIORITY_KEYWORDS = {
    "high": {
        "weight": 40,
        "keywords": [
            "kürzung", "streichung", "haushaltssperre", "finanzierungslücke",
            "kahlschlag", "förderentzug", "nothaushalt", "haushaltskrise",
            "schließung", "abbau", "existenzbedrohend", "insolvenz",
            "personalreduzierung", "stellenabbau", "einschnitte",
        ],
    },
    "medium": {
        "weight": 20,
        "keywords": [
            "gesetzesänderung", "novelle", "anhörung", "regierungsentwurf",
            "bundesratsentscheidung", "gesetzgebung", "reform",
        ],
    },
    "low": {
        "weight": 10,
        "keywords": [
            "pflegenotstand", "kitaplätze", "migrationsberatung", "fachkräftemangel",
            "sozialfinanzierung", "eingliederungshilfe", "kinderbetreuung",
        ],
    },
}


class ItemProcessor:
    """LLM-based processor for item summarization and analysis."""

    def __init__(self, llm_service: LLMService):
        """Initialize processor with LLM service.

        Args:
            llm_service: LLM service for text generation
        """
        self.llm = llm_service

    async def confirm_duplicate(
        self,
        item_data: dict,
        candidate_data: dict,
    ) -> tuple[bool, str]:
        """
        Ask LLM to confirm whether two articles are duplicates (same story).

        Used for edge-case duplicates where semantic similarity is uncertain (0.60-0.75).

        Args:
            item_data: Dict with title, content of the new item
            candidate_data: Dict with title, content of the potential duplicate

        Returns:
            Tuple of (is_duplicate: bool, reasoning: str)
        """
        prompt = f"""Vergleiche diese zwei Nachrichtenartikel und entscheide, ob sie über DASSELBE EREIGNIS berichten.

ARTIKEL A:
Titel: {item_data.get('title', '')[:200]}
Inhalt: {item_data.get('content', '')[:1500]}

ARTIKEL B:
Titel: {candidate_data.get('title', '')[:200]}
Inhalt: {candidate_data.get('content', '')[:1500]}

GLEICHE Geschichte wenn:
- Beide berichten über exakt dasselbe Ereignis (gleiche Personen, Orte, Entscheidungen)
- Einer ist eine Kurzversion/Update des anderen
- Unterschiedliche Quellen berichten über dieselbe Pressemitteilung/Nachricht

UNTERSCHIEDLICHE Geschichten wenn:
- Ähnliches Thema, aber verschiedene Ereignisse (z.B. zwei verschiedene Kita-Schließungen)
- Gleiche Person, aber andere Handlung/Entscheidung
- Hintergrundbericht vs. aktuelle Meldung zum selben Thema

Antworte NUR mit JSON:
{{"is_duplicate": true/false, "reasoning": "Kurze Begründung"}}"""

        try:
            response = await self.llm.complete(
                prompt,
                temperature=0.1,
                max_tokens=200,
            )
            text = response.text.strip()
            logger.debug(f"Duplicate confirmation raw response: {repr(text[:500])}")

            # Remove markdown code blocks if present
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [line for line in lines if not line.strip().startswith("```")]
                text = "\n".join(lines).strip()

            # Handle qwen3 thinking mode: sometimes model returns empty content
            # when it's "thinking" - the actual response is in the thinking field
            if not text:
                logger.warning("LLM returned empty content for duplicate confirmation")
                return False, "LLM returned empty response"

            # Parse JSON response
            result = json.loads(text)
            is_dup = result.get("is_duplicate", False)
            reasoning = result.get("reasoning", "Keine Begründung")
            return is_dup, reasoning

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse duplicate confirmation response: {e}, text: {text[:100]}")
            # Default to not duplicate if parsing fails
            return False, "Antwort konnte nicht verarbeitet werden"
        except Exception as e:
            logger.error(f"Duplicate confirmation failed: {e}")
            return False, f"Fehler: {e}"

    async def summarize(self, item: Item) -> str | None:
        """Generate a summary for an item.

        Args:
            item: Item to summarize

        Returns:
            Summary text or None if generation fails
        """
        prompt = f"""Fasse folgenden Nachrichtenartikel in 2-3 Sätzen auf Deutsch zusammen:

TITEL: {item.title}

INHALT: {item.content[:6000]}

Antworte NUR mit der Zusammenfassung, ohne zusätzliche Erklärungen."""

        try:
            response = await self.llm.complete(prompt, temperature=0.3, max_tokens=200)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return None

    async def analyze(
        self, item: Item, rules: list[Rule] | None = None, source_name: str | None = None
    ) -> dict[str, Any]:
        """Analyze item for relevance, priority, and working group assignment.

        Args:
            item: Item to analyze
            rules: Optional list of rules to check
            source_name: Optional source name (if item.source isn't loaded yet)

        Returns:
            Analysis result dict with keys:
            - summary: str
            - relevant: bool
            - relevance_score: float (0.0-1.0)
            - priority: str (high/medium/low/null)
            - assigned_aks: list[str] (0-3 AK codes)
            - tags: list[str]
            - reasoning: str
        """
        # Format input as the fine-tuned model expects
        if source_name is None:
            try:
                source_name = item.source.name if item.source else "Unbekannt"
            except Exception:
                # Relationship may not be loaded, use fallback
                source_name = "Unbekannt"
        date_str = item.published_at.strftime("%Y-%m-%d") if item.published_at else "Unbekannt"

        prompt = f"""Titel: {item.title}
Inhalt: {item.content[:6000]}
Quelle: {source_name}
Datum: {date_str}"""

        try:
            # Use system prompt for base models (Option B approach)
            response = await self.llm.complete(
                prompt,
                system=ANALYSIS_SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=6000,  # Sufficient headroom for full JSON response
            )
            return self._parse_analysis_response(response)

        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return self._default_analysis()

    async def analyze_from_data(self, item_data: dict[str, Any]) -> dict[str, Any]:
        """Analyze item for relevance, priority, and working group assignment.

        Like analyze(), but takes a dict instead of an Item ORM object.
        This is used by the LLM worker to avoid holding DB connections during LLM calls.

        Args:
            item_data: Dict with keys: title, content, source_name, and optionally published_at

        Returns:
            Analysis result dict (same as analyze())
        """
        title = item_data.get("title", "")
        content = item_data.get("content", "")[:6000]
        source_name = item_data.get("source_name", "Unbekannt")
        published_at = item_data.get("published_at")
        date_str = published_at.strftime("%Y-%m-%d") if published_at else "Unbekannt"

        prompt = f"""Titel: {title}
Inhalt: {content}
Quelle: {source_name}
Datum: {date_str}"""

        try:
            response = await self.llm.complete(
                prompt,
                system=ANALYSIS_SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=6000,
            )
            return self._parse_analysis_response(response)

        except Exception as e:
            logger.error(f"Analysis from data failed: {e}")
            return self._default_analysis()

    async def analyze_from_data_with_messages(self, item_data: dict[str, Any]) -> tuple[dict[str, Any], list[dict]]:
        """Analyze item and return both the result and the conversation messages.

        Same as analyze_from_data() but also returns the messages list so
        callers can continue the conversation (e.g. for topic extraction).

        Returns:
            Tuple of (analysis_result, messages_list)
        """
        title = item_data.get("title", "")
        content = item_data.get("content", "")[:6000]
        source_name = item_data.get("source_name", "Unbekannt")
        published_at = item_data.get("published_at")
        date_str = published_at.strftime("%Y-%m-%d") if published_at else "Unbekannt"

        prompt = f"""Titel: {title}
Inhalt: {content}
Quelle: {source_name}
Datum: {date_str}"""

        messages = [
            {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            response = await self.llm.complete(
                prompt,
                system=ANALYSIS_SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=6000,
            )
            analysis = self._parse_analysis_response(response)
            # Build full conversation for follow-up
            conversation = messages + [{"role": "assistant", "content": response.text}]
            return analysis, conversation

        except Exception as e:
            logger.error(f"Analysis from data (with messages) failed: {e}")
            return self._default_analysis(), messages

    async def check_semantic_rule(self, item: Item, rule: Rule) -> bool:
        """Check if item matches a semantic (LLM-based) rule.

        Args:
            item: Item to check
            rule: Semantic rule with pattern as prompt

        Returns:
            True if rule matches, False otherwise
        """
        if rule.rule_type != RuleType.SEMANTIC:
            return False

        prompt = f"""Beantworte die folgende Frage mit JA oder NEIN.

ARTIKEL-TITEL: {item.title}

ARTIKEL-INHALT: {item.content[:2000]}

FRAGE: {rule.pattern}

Antworte NUR mit JA oder NEIN."""

        try:
            response = await self.llm.complete(
                prompt,
                temperature=0.1,
                max_tokens=10,
            )
            answer = response.text.strip().upper()
            return answer.startswith("JA") or answer == "YES"

        except Exception as e:
            logger.error(f"Semantic rule check failed: {e}")
            return False

    async def extract_topics(self, conversation_messages: list[dict]) -> tuple[str, str | None]:
        """Extract a single topic from the fixed taxonomy via a follow-up chat turn.

        Takes the conversation from the initial analysis (system + user + assistant)
        and appends a follow-up request to classify into the taxonomy.

        Args:
            conversation_messages: Messages from the analysis conversation

        Returns:
            Tuple of (topic, topic_suggestion).
            topic is always a valid taxonomy entry or "Sonstiges".
            topic_suggestion is only set when topic is "Sonstiges".
        """
        from .topic_taxonomy import TOPIC_TAXONOMY, SONSTIGES, validate_topic

        # Exclude Sozialpolitik from main list — add as last-resort option
        taxonomy_list = "\n".join(
            f"- {t}" for t in TOPIC_TAXONOMY if t != "Sozialpolitik"
        )

        follow_up = {
            "role": "user",
            "content": (
                "Ordne diesen Artikel GENAU EINEM Thema aus der folgenden Liste zu.\n\n"
                f"THEMENLISTE:\n{taxonomy_list}\n\n"
                "REGELN:\n"
                "- Wähle das Thema, das am besten beschreibt, WARUM der Artikel für die "
                "Wohlfahrtspflege relevant ist — nicht worum es allgemein geht.\n"
                "- Bei thematischer Überschneidung wähle das ENGERE, SPEZIFISCHERE Thema.\n"
                "- KEINE Parteinamen, Organisationsnamen oder Ortsnamen als Thema.\n\n"
                "UNTERSCHEIDUNGEN:\n"
                "- Tarifpolitik = Tarifvertrag, Warnstreik, Arbeitskampf, Mindestlohn, "
                "Lohnerhöhung — auch wenn in Kitas/Krankenhäusern gestreikt wird\n"
                "- Senioren und Alter = Rente, Altersarmut, Alterssicherung, Rentenreform\n"
                "- Fachkräftemangel = struktureller Personalmangel, Fachkräftelücke\n"
                "- Pflege = Pflegepersonal, Pflegebeitrag, Pflegereform\n"
                "- Bürokratieabbau = Entbürokratisierung, Regulierungsabbau\n"
                "- Gesundheitsversorgung = Krankenhausreform, Klinikschließung, Krankenkasse\n"
                "- Migration und Flucht = auch Abschiebung, Asylpolitik, Menschenschmuggel\n"
                "- Behinderung und Inklusion = Schwerbehinderung, Behindertenrecht, "
                "Inklusion, Teilhabe — auch wenn es um Rente/Kindergeld für Behinderte geht\n"
                "- Wohnen und Wohnungsnot = Wohngeld, Hessengeld, Mietpreisbremse, "
                "Sozialwohnungen, Wohnraumförderung\n"
                "- Sozialleistungen = Bürgergeld, Grundsicherung, Kurzarbeitergeld "
                "-- NICHT Armut allgemein (-> Armut und Existenzsicherung), "
                "NICHT Arbeitsmarktpolitik (-> Arbeitsmarkt), "
                "NICHT Leistungen fuer Gefluechtete (-> Migration und Flucht), "
                "NICHT Behindertenleistungen (-> Behinderung und Inklusion), "
                "NICHT Wohnungsfoerderung (-> Wohnen und Wohnungsnot)\n\n"
                "BEISPIELE:\n"
                "- 'Mindestlohn steigt auf 13,90 Euro' -> Tarifpolitik\n"
                "- 'Warnstreiks in Kitas und Unikliniken' -> Tarifpolitik\n"
                "- 'Steuerbefreiung fuer Gewerkschaftsbeitraege' -> Tarifpolitik\n"
                "- '200 Mrd. fuer Rentenleistungen' -> Senioren und Alter\n"
                "- 'Alterssicherungskommission konstituiert' -> Senioren und Alter\n"
                "- 'Pflegebeitrag stoppen, Strukturreform' -> Pflege\n"
                "- 'CDU will Buerokratieabbau fuer Unternehmen' -> Buerokratieabbau\n"
                "- 'Reform der Grundsicherung fuer Arbeitsuchende' -> Sozialleistungen\n"
                "- 'Inflation treibt Lebensmittelpreise hoch' -> Armut und Existenzsicherung\n\n"
                "Wenn KEIN Thema aus der Liste passt, prüfe:\n"
                "- Sozialpolitik — NUR für übergreifende Sozialstaats-Debatten "
                "ohne klaren Fachbezug\n"
                "- Sonstiges — mit Vorschlag\n\n"
                "Antwort NUR als JSON:\n"
                "{\"topic\": \"Thema aus Liste\"}\n"
                "oder bei Sonstiges:\n"
                "{\"topic\": \"Sonstiges\", \"topic_suggestion\": \"Dein Vorschlag\"}"
            ),
        }
        messages = conversation_messages + [follow_up]

        try:
            response = await self.llm.chat(
                messages=messages,
                temperature=0.2,
                max_tokens=1024,
            )
            text = response.text.strip()

            # Remove markdown code blocks
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [line for line in lines if not line.strip().startswith("```")]
                text = "\n".join(lines).strip()

            if not text:
                logger.warning("Empty response for topic extraction")
                return SONSTIGES, None

            result = json.loads(text)
            raw_topic = result.get("topic", "")
            raw_suggestion = result.get("topic_suggestion")

            topic, suggestion = validate_topic(raw_topic)

            # If model returned Sonstiges with a suggestion, use that
            if topic == SONSTIGES and raw_suggestion:
                suggestion = str(raw_suggestion).strip() or suggestion

            return topic, suggestion

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse topic extraction response: {e}")
            return SONSTIGES, None
        except Exception as e:
            logger.error(f"Topic extraction failed: {e}")
            return SONSTIGES, None

    def calculate_keyword_score(self, item: Item) -> tuple[int, Priority]:
        """Calculate priority score based on keyword matches.

        Args:
            item: Item to score

        Returns:
            Tuple of (score, suggested_priority)
        """
        text = f"{item.title} {item.content}".lower()
        total_score = 50  # Base score

        for category, config in PRIORITY_KEYWORDS.items():
            for keyword in config["keywords"]:
                if keyword in text:
                    total_score += config["weight"]
                    logger.debug(f"Keyword '{keyword}' matched ({category})")

        # Cap score at 100
        total_score = min(100, total_score)

        # Determine priority from score (high→medium→low→none)
        if total_score >= 90:
            priority = Priority.HIGH
        elif total_score >= 70:
            priority = Priority.MEDIUM
        elif total_score >= 40:
            priority = Priority.LOW
        else:
            priority = Priority.NONE

        return total_score, priority

    def _build_rules_context(self, rules: list[Rule]) -> str:
        """Build context string from LLM rules."""
        llm_rules = [r for r in rules if r.rule_type == RuleType.SEMANTIC]

        if not llm_rules:
            return ""

        lines = ["REGELN ZU PRÜFEN:"]
        for rule in llm_rules:
            lines.append(f"- Regel {rule.id} ({rule.name}): {rule.pattern}")

        return "\n".join(lines)

    def _parse_analysis_response(self, response: LLMResponse) -> dict[str, Any]:
        """Parse LLM analysis response."""
        text = response.text.strip()

        # Remove markdown code blocks if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (``` markers)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        result = None
        try:
            # Try direct JSON parse
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                result = parsed
        except json.JSONDecodeError:
            pass

        # Try to find JSON object in text (handles nested braces)
        if result is None:
            try:
                start = text.find("{")
                if start != -1:
                    # Find matching closing brace
                    depth = 0
                    for i, char in enumerate(text[start:], start):
                        if char == "{":
                            depth += 1
                        elif char == "}":
                            depth -= 1
                            if depth == 0:
                                json_str = text[start:i+1]
                                result = json.loads(json_str)
                                break
            except (json.JSONDecodeError, ValueError):
                pass

        # Fallback to default if parsing failed
        if result is None:
            logger.warning(f"Could not parse LLM response as JSON: {text[:200]}")

            # Try to extract summary from partial/invalid JSON using regex
            # This handles cases where JSON is truncated but summary field is complete
            import re
            summary_match = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)(?:"|$)', text)
            if summary_match:
                extracted_summary = summary_match.group(1)
                # Unescape JSON string escapes
                extracted_summary = extracted_summary.replace('\\"', '"').replace('\\n', '\n')
                logger.info(f"Extracted summary from invalid JSON: {extracted_summary[:100]}...")
                return self._default_analysis(extracted_summary)

            # Don't store raw JSON as summary - return empty instead
            logger.warning("Could not extract summary from LLM response")
            return self._default_analysis("")

        # Normalize assigned_ak (single) to assigned_aks (array) for backward compatibility
        if "assigned_ak" in result and "assigned_aks" not in result:
            ak = result.get("assigned_ak")
            result["assigned_aks"] = [ak] if ak else []
        elif "assigned_aks" not in result:
            result["assigned_aks"] = []

        # Attach provider/model info from LLM response
        result["_provider"] = response.provider
        result["_model"] = response.model

        return result

    def _default_analysis(self, summary: str = "") -> dict[str, Any]:
        """Return default analysis when LLM fails."""
        return {
            "summary": summary,
            "relevant": False,
            "relevance_score": 0.0,
            "priority": "low",
            "assigned_aks": [],
            "matched_rules": [],
            "tags": [],
            "reasoning": "Automatische Analyse nicht verfügbar",
        }


async def is_llm_enabled() -> bool:
    """Check if LLM is enabled (runtime DB setting overrides env)."""
    from config import settings
    from database import async_session_maker
    from sqlalchemy import select
    from models import Setting

    # Check database for runtime override
    try:
        async with async_session_maker() as db:
            setting = await db.scalar(
                select(Setting).where(Setting.key == "llm_enabled")
            )
            if setting is not None:
                return setting.value.lower() == "true"
    except Exception as e:
        logger.debug(f"Could not check DB for llm_enabled setting, using env: {e}")

    # Fall back to environment variable
    return settings.llm_enabled


async def create_processor_from_settings() -> ItemProcessor | None:
    """Create processor instance from application settings.

    Returns:
        Configured ItemProcessor instance, or None if LLM is disabled
    """
    from config import settings
    from .llm import OllamaProvider, OpenRouterProvider, LLMService

    # Check if LLM processing is enabled (runtime setting overrides env)
    if not await is_llm_enabled():
        import logging
        logging.getLogger(__name__).info("LLM processing disabled (env or runtime setting)")
        return None

    providers = []

    # Ollama proxy handles routing: cloud first, then local fallback
    providers.append(
        OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout=settings.ollama_timeout,
        )
    )

    # Add OpenRouter as last fallback if configured
    if settings.openrouter_api_key:
        providers.append(
            OpenRouterProvider(
                api_key=settings.openrouter_api_key,
                model=settings.openrouter_model,
                timeout=settings.openrouter_timeout,
            )
        )

    llm_service = LLMService(providers)
    return ItemProcessor(llm_service)
