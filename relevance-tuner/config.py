#!/usr/bin/env python3
"""
Central Configuration for Liga Hessen Relevance Tuner

All shared settings, paths, and constants are defined here.
Environment variables can override defaults.
"""

import os
from pathlib import Path

# ============================================================================
# Paths
# ============================================================================

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data" / "final"
MODELS_DIR = PROJECT_ROOT / "models"

# ============================================================================
# Embedding Model (Ollama)
# ============================================================================

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text:137m-v1.5-fp16")
EMBEDDING_NUM_CTX = int(os.environ.get("EMBEDDING_NUM_CTX", 8192))
EMBEDDING_DIMS = 768  # nomic-embed-text produces 768-dim vectors
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# Chunking settings for long texts (nomic handles ~11,937 chars max)
EMBEDDING_CHUNK_SIZE = int(os.environ.get("EMBEDDING_CHUNK_SIZE", 10000))
EMBEDDING_CHUNK_OVERLAP = int(os.environ.get("EMBEDDING_CHUNK_OVERLAP", 500))

# ============================================================================
# TF-IDF Settings (for TF-IDF approaches)
# ============================================================================

TFIDF_MAX_FEATURES = int(os.environ.get("TFIDF_MAX_FEATURES", 3000))
TFIDF_NGRAM_RANGE = (1, 2)
TFIDF_MIN_DF = 2
TFIDF_MAX_DF = float(os.environ.get("TFIDF_MAX_DF", 0.9))
TFIDF_SUBLINEAR_TF = True

# ============================================================================
# Classifier Settings
# ============================================================================

RANDOM_SEED = 42

# LogisticRegression defaults
LR_MAX_ITER = 1000
LR_C = 1.0
LR_SOLVER = "lbfgs"

# RandomForest defaults
RF_N_ESTIMATORS = 200
RF_MAX_DEPTH = 15

# ============================================================================
# Classification Labels
# ============================================================================

PRIORITY_LEVELS = ["low", "medium", "high", "critical"]
AK_CLASSES = ["AK1", "AK2", "AK3", "AK4", "AK5", "QAG"]

# AK Definitions (for reference)
AK_DEFINITIONS = {
    "AK1": "Grundsatz und Sozialpolitik (general social policy)",
    "AK2": "Migration und Flucht (migration, refugees, asylum)",
    "AK3": "Gesundheit, Pflege und Senioren (health, care, elderly)",
    "AK4": "Eingliederungshilfe (disability inclusion)",
    "AK5": "Kinder, Jugend, Frauen und Familie (children, youth, families)",
    "QAG": "Querschnitt - Digitalisierung, Klimaschutz, Wohnen (cross-cutting)",
}

# ============================================================================
# Keywords (consolidated from all scripts)
# ============================================================================

# Keywords per AK for feature engineering
AK_KEYWORDS = {
    "AK1": [  # Grundsatz und Sozialpolitik
        "sozialpolitik", "sozialstaat", "wohlfahrt", "gemeinnützig", "ehrenamt",
        "bürgerengagement", "zivilgesellschaft", "soziale arbeit", "träger",
        "verband", "freie wohlfahrtspflege", "sozialgesetzgebung", "grundsatz",
        "reform", "haushalt", "finanzierung", "förderung", "landesregierung",
    ],
    "AK2": [  # Migration und Flucht
        "migration", "flucht", "flüchtling", "asyl", "geflüchtete", "zuwanderung",
        "integration", "migranten", "einwanderung", "abschiebung", "aufenthalt",
        "aufnahme", "unterbringung", "erstaufnahme", "sprachkurs", "integrationskurs",
        "asylbewerber", "asylverfahren", "duldung", "bleiberecht", "einbürgerung",
    ],
    "AK3": [  # Gesundheit, Pflege und Senioren
        "pflege", "pflegekraft", "altenpflege", "pflegeheim", "krankenhaus",
        "gesundheit", "demenz", "senioren", "häusliche pflege", "pflegedienst",
        "pflegeversicherung", "pflegestufe", "pflegegrad", "pflegebedürftig",
        "kranken", "patient", "altersheim", "hospiz", "palliativ", "therapie",
    ],
    "AK4": [  # Eingliederungshilfe
        "eingliederungshilfe", "behinderung", "behindert", "inklusion", "teilhabe",
        "werkstatt", "barrierefreiheit", "bthg", "bundesteilhabegesetz",
        "schwerbehindert", "teilhabeplan", "persönliches budget", "wfbm",
        "assistenz", "förderung", "selbstbestimmung", "un-brk",
    ],
    "AK5": [  # Kinder, Jugend, Frauen und Familie
        "kita", "kindergarten", "kinderbetreuung", "jugend", "jugendhilfe",
        "familie", "familienberatung", "schwangerschaft", "kinderschutz",
        "erziehung", "eltern", "alleinerziehend", "jugendamt", "kindertagesstätte",
        "krippe", "hort", "schulkind", "frauenhaus", "beratungsstelle",
        "schwangerschaftskonflikt", "frühe hilfen", "frauenberatung",
    ],
    "QAG": [  # Querschnitt
        "digitalisierung", "klimaschutz", "wohnen", "wohnungsnot", "obdachlos",
        "wohnungslos", "sozialraum", "nachbarschaft", "quartier", "energiearmut",
        "nachhaltigkeit", "mobilität", "ländlicher raum", "stadtentwicklung",
        "armut", "armutsbericht", "existenzsicherung", "grundsicherung",
    ],
}

# Liga organizations (indicates relevance)
LIGA_KEYWORDS = [
    "liga", "wohlfahrt", "awo", "caritas", "diakonie", "drk", "paritätisch",
    "wohlfahrtsverband", "spitzenverband", "freie wohlfahrtspflege",
]

# Hessen-specific terms
HESSEN_KEYWORDS = [
    "hessen", "hessisch", "landesregierung", "landtag", "wiesbaden", "frankfurt",
    "darmstadt", "kassel", "offenbach", "fulda", "gießen", "marburg",
]

# Irrelevant topics (sports, entertainment, etc.)
IRRELEVANT_KEYWORDS = [
    "fußball", "bundesliga", "champions", "sport", "tennis", "olympia",
    "mannschaft", "trainer", "spieler", "tor", "sieg", "niederlage",
    "kino", "film", "konzert", "festival", "unterhaltung",
]

# Urgent priority indicators
URGENT_KEYWORDS = [
    "sofort", "dringend", "eilig", "frist", "deadline", "morgen",
    "heute", "akut", "notfall", "krise", "warnung",
]
