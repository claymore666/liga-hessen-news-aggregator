"""
Topic taxonomy for LLM-based news classification.

Canonical topic list for the Liga der Freien Wohlfahrtspflege Hessen news aggregator.
The LLM must assign exactly ONE topic from this list per item, or "Sonstiges" with a suggestion.
"""

# Canonical topic taxonomy - flat list, German only, welfare/social policy perspective
# Organized by rough AK alignment but stored as flat list
TOPIC_TAXONOMY: list[str] = [
    # AK1: Grundsatz/Sozialpolitik
    "Sozialpolitik",
    "Haushalt und Finanzen",
    "Steuerpolitik",
    "Sozialleistungen",
    "Bürokratieabbau",
    "Vergaberecht",
    "Ehrenamt",
    "Wohlfahrtsverbände",
    "Tarifpolitik",

    # AK2: Migration/Flucht
    "Migration und Flucht",
    "Asylpolitik",
    "Integration",
    "Abschiebung",

    # AK3: Gesundheit/Pflege/Senioren
    "Pflege",
    "Pflegefinanzierung",
    "Pflegepersonal",
    "Gesundheitsversorgung",
    "Krankenhausreform",
    "Psychische Gesundheit",
    "Sucht und Prävention",
    "Senioren und Alter",
    "Demenz",
    "Hospiz und Palliativ",

    # AK4: Eingliederungshilfe
    "Behinderung und Inklusion",
    "Barrierefreiheit",
    "Eingliederungshilfe",

    # AK5: Kinder/Jugend/Familie
    "Kita und Kinderbetreuung",
    "Kinder- und Jugendhilfe",
    "Kinderschutz",
    "Familienpolitik",
    "Kinderarmut",

    # QAG: Querschnitt
    "Digitalisierung",
    "Wohnen und Wohnungsnot",
    "Armut und Existenzsicherung",
    "Obdachlosigkeit",
    "Schuldnerberatung",

    # Übergreifend
    "Fachkräftemangel",
    "Arbeitsmarkt",
    "Bildung und Ausbildung",
    "Gleichstellung",
    "Gewalt und Gewaltschutz",
    "Demokratie und Extremismus",
    "Menschenrechte",
    "Humanitäre Hilfe",
    "Klimaschutz und Soziales",
    "Recht und Gesetzgebung",
]

# Set for O(1) lookup (case-insensitive)
TOPIC_TAXONOMY_SET: set[str] = {t.lower() for t in TOPIC_TAXONOMY}

SONSTIGES = "Sonstiges"


def validate_topic(topic: str) -> tuple[str, str | None]:
    """Validate a topic against the taxonomy.

    Returns:
        Tuple of (canonical_topic, suggestion).
        - If topic matches taxonomy: (matched_topic, None)
        - If topic is Sonstiges or unknown: ("Sonstiges", original_topic_or_suggestion)
    """
    if not topic or not topic.strip():
        return SONSTIGES, None

    topic_clean = topic.strip()

    # Check exact match (case-insensitive)
    if topic_clean.lower() in TOPIC_TAXONOMY_SET:
        # Return the canonical casing
        for t in TOPIC_TAXONOMY:
            if t.lower() == topic_clean.lower():
                return t, None
        return topic_clean, None

    # If model said Sonstiges, that's fine
    if topic_clean.lower() == "sonstiges":
        return SONSTIGES, None

    # Unknown topic - store as Sonstiges with suggestion
    return SONSTIGES, topic_clean
