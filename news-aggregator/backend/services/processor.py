"""LLM-based item processor for summarization and analysis."""

import json
import logging
import re
from typing import Any

from models import Item, Priority, Rule, RuleType

from .llm import LLMResponse, LLMService

logger = logging.getLogger(__name__)

# System prompt for news analysis
SYSTEM_PROMPT = """Du bist ein Nachrichtenanalyse-Assistent für die Liga der Freien Wohlfahrtspflege Hessen.
Deine Aufgabe ist es:
1. Nachrichtenartikel zusammenzufassen (2-3 Sätze auf Deutsch)
2. Die Relevanz für die hessische Sozialpolitik zu bewerten
3. Die Dringlichkeit einzuschätzen

Antworte IMMER im gültigen JSON-Format.

Kontext: Die Liga vertritt Wohlfahrtsverbände (AWO, Caritas, Diakonie, DRK, Paritätischer, Jüdische Gemeinden)
und befasst sich mit: Pflege, Kita, Migration, Eingliederungshilfe, Sozialfinanzierung.

Arbeitskreise:
- AK 1: Grundsatz und Sozialpolitik
- AK 2: Migration und Flucht
- AK 3: Gesundheit, Pflege und Senioren
- AK 4: Eingliederungshilfe
- AK 5: Kinder, Jugend, Frauen und Familie
- QAG: Digitalisierung, Klimaschutz, Wohnen"""

# Trigger keywords for priority scoring
PRIORITY_KEYWORDS = {
    "critical": {
        "weight": 40,
        "keywords": [
            "kürzung", "streichung", "haushaltssperre", "finanzierungslücke",
            "kahlschlag", "förderentzug", "nothaushalt", "haushaltskrise",
        ],
    },
    "high": {
        "weight": 25,
        "keywords": [
            "schließung", "abbau", "existenzbedrohend", "insolvenz",
            "personalreduzierung", "stellenabbau", "einschnitte",
        ],
    },
    "reform": {
        "weight": 15,
        "keywords": [
            "gesetzesänderung", "novelle", "anhörung", "regierungsentwurf",
            "bundesratsentscheidung", "gesetzgebung", "reform",
        ],
    },
    "topic": {
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

INHALT: {item.content[:3000]}

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
            - priority: str (critical/high/medium/low/null)
            - assigned_ak: str | None
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
Inhalt: {item.content[:3000]}
Quelle: {source_name}
Datum: {date_str}"""

        try:
            # Don't send system prompt - it's baked into the fine-tuned model
            response = await self.llm.complete(
                prompt,
                temperature=0.1,
                max_tokens=800,  # Increased for detailed_analysis field
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

        # Determine priority from score
        if total_score >= 90:
            priority = Priority.CRITICAL
        elif total_score >= 70:
            priority = Priority.HIGH
        elif total_score >= 40:
            priority = Priority.MEDIUM
        else:
            priority = Priority.LOW

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

        try:
            # Try direct JSON parse
            result = json.loads(text)
            if isinstance(result, dict):
                return result
            # Not a dict (e.g., array) - fall through to default
        except json.JSONDecodeError:
            pass

        # Try to find JSON object in text (handles nested braces)
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
                            return json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback to default
        logger.warning(f"Could not parse LLM response as JSON: {text[:100]}")
        return self._default_analysis(text[:500])

    def _default_analysis(self, summary: str = "") -> dict[str, Any]:
        """Return default analysis when LLM fails."""
        return {
            "summary": summary,
            "relevant": False,
            "relevance_score": 0.0,
            "priority": "low",
            "assigned_ak": None,
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
    except Exception:
        pass  # Fall back to env if DB check fails

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
