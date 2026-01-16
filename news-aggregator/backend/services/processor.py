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

DIE LIGA: Dachverband der 6 Wohlfahrtsverbände in Hessen (AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden) mit 7.300 Einrichtungen, 113.000 Beschäftigten.

ARBEITSKREISE:
- AK1: Grundsatz/Sozialpolitik (Haushalt, Förderungen, Tarifpolitik)
- AK2: Migration/Flucht (Asyl, Beratung, Integration)
- AK3: Gesundheit/Pflege/Senioren (Altenpflege, Krankenhäuser, Hospiz)
- AK4: Eingliederungshilfe (Behinderung, Inklusion, BTHG, WfbM)
- AK5: Kinder/Jugend/Familie (Kita, Jugendhilfe, Frauenhäuser)
- QAG: Querschnitt (Digitalisierung, Wohnen, Schuldnerberatung)

PRIORITÄTEN:
- high: Sofortige Reaktion nötig - Kürzungen, Schließungen, Gesetzesentwürfe mit Frist
- medium: Zeitnah (1-2 Wochen) - Anhörungen, Reformen, Förderrichtlinien
- low: Beobachten/Zur Kenntnis - Politische Debatten, Studien, Hintergrundberichte

RELEVANT wenn: Wohlfahrtsverbände, soziale Einrichtungen, Sozialpolitik in Deutschland/Hessen, Haushalt/Kürzungen, Pflege, Kita, Migration in DE, Behinderung, Armut, Fachkräftemangel im Sozialbereich.
NICHT RELEVANT (relevant=false, priority=null):
- Reiner Sport, Entertainment, Prominente
- Kriminalität ohne Sozialbezug
- Wetter, Verkehr, Unfälle
- Internationale Politik (USA, Brasilien, etc.) OHNE direkten Bezug zu deutscher Sozialpolitik
- Ausländische Innenpolitik (Bolsonaro, Trump, etc.) ist NICHT relevant für die Liga

AUSGABE als valides JSON:
{
  "summary": "4-8 Sätze: Was passiert? Wer betroffen? Kernpunkte? NUR FAKTEN aus dem Artikel.",
  "detailed_analysis": "10-15 Sätze: Alle Details, Zahlen, Zitate, Auswirkungen. KEINE Spekulation über Liga!",
  "argumentationskette": ["Konkrete Argumente für Liga-Lobbying", "Keine Konjunktive"],
  "relevant": true/false,
  "relevance_score": 0.0-1.0,
  "priority": "high|medium|low|null",
  "assigned_aks": ["AK1", "AK3"],
  "tags": ["thema1", "thema2"],
  "reasoning": "Kurze Begründung der Klassifikation"
}

ARBEITSKREIS-ZUWEISUNG:
- assigned_aks: Array mit 0-3 relevanten Arbeitskreisen
- Mehrfachzuweisung möglich wenn Thema mehrere AKs betrifft (z.B. Kinderarmut = AK1 + AK5)
- Leeres Array [] wenn nicht relevant

WICHTIG:
- summary/detailed_analysis: NUR Fakten aus dem Artikel, KEINE "Liga dürfte...", "Wohlfahrtsverbände könnten..."
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

    # Add Ollama as primary provider
    providers.append(
        OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout=settings.ollama_timeout,
        )
    )

    # Add OpenRouter as fallback if configured
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
