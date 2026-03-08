"""
Geographic and structural feature extraction for Liga Hessen classifier.

Provides hand-crafted features that complement embeddings:
- Geographic signals (Hessen vs other Bundesländer)
- Source categorization (regional, political, national, Bundesverband)
- Content structure (length, social media, topic keywords)

Used by both training (train_embedding_classifier.py) and
inference (classifier.py) to ensure consistency.
"""

import re

import numpy as np


# ============================================================================
# Keyword lists
# ============================================================================

# Hessen cities and regions (case-insensitive matching)
HESSEN_KEYWORDS = {
    # State name
    "hessen", "hessisch", "hessische", "hessischen", "hessischer",
    # Major cities
    "frankfurt", "wiesbaden", "kassel", "darmstadt", "offenbach",
    "hanau", "gießen", "giessen", "marburg", "fulda", "bad homburg",
    "rüsselsheim", "wetzlar", "oberursel", "bad vilbel", "rodgau",
    "dreieich", "bensheim", "langen", "neu-isenburg", "lampertheim",
    "viernheim", "dietzenbach", "hofheim", "kelkheim", "bad nauheim",
    "friedberg", "butzbach", "limburg",
    # Regions
    "rhein-main", "nordhessen", "mittelhessen", "südhessen",
    "wetterau", "odenwald", "bergstraße", "taunus", "vogelsberg",
    "schwalm-eder", "waldeck-frankenberg", "main-kinzig",
    "main-taunus", "hochtaunus", "rheingau",
    # Institutions
    "landtag hessen", "landesregierung hessen",
}

# Per-Bundesland keyword dictionaries (for individual state detection)
BUNDESLAND_KEYWORDS = {
    "bayern": {"bayern", "bayerisch", "bayerische", "münchen", "nürnberg", "augsburg", "regensburg"},
    "baden_wuerttemberg": {"baden-württemberg", "stuttgart", "karlsruhe", "freiburg", "mannheim", "heidelberg"},
    "nordrhein_westfalen": {"nordrhein-westfalen", "nrw", "düsseldorf", "köln", "dortmund", "essen", "duisburg", "bochum"},
    "niedersachsen": {"niedersachsen", "hannover", "braunschweig", "osnabrück", "oldenburg", "göttingen"},
    "sachsen": {"sachsen", "sächsisch", "dresden", "leipzig", "chemnitz"},
    "rheinland_pfalz": {"rheinland-pfalz", "mainz", "koblenz", "trier", "ludwigshafen", "kaiserslautern"},
    "schleswig_holstein": {"schleswig-holstein", "kiel", "lübeck", "flensburg"},
    "brandenburg": {"brandenburg", "potsdam", "cottbus"},
    "thueringen": {"thüringen", "erfurt", "jena", "weimar", "gera"},
    "sachsen_anhalt": {"sachsen-anhalt", "magdeburg", "halle"},
    "mecklenburg_vorpommern": {"mecklenburg-vorpommern", "rostock", "schwerin", "greifswald"},
    "saarland": {"saarland", "saarbrücken"},
    "berlin": {"berlin"},
    "hamburg": {"hamburg"},
    "bremen": {"bremen", "bremerhaven"},
}

# Flat set of all other Bundesländer keywords (for geo_ratio calculation)
OTHER_BUNDESLAENDER = set()
for _kws in BUNDESLAND_KEYWORDS.values():
    OTHER_BUNDESLAENDER |= _kws

# Source classification (based on actual production sources)
SOURCE_HESSEN_REGIONAL = {
    "faz", "frankfurter allgemeine",
    "frankfurter rundschau", "fr.de",
    "hessenschau",
    "hna",
    "fuldaer zeitung",
    "wiesbadener kurier",
    "hr presse", "hessischer rundfunk",
    "zdf landesstudio",
    "op-online",  # Offenbach Post
    "giessener anzeiger",
    "echo online",  # Darmstädter Echo
}

SOURCE_HESSEN_POLITICS = {
    "fraktion hessen",
    "boris rhein", "astrid wallmann",
    "landesregierung hessen", "hessischer landtag",
    "sozialministerium", "hmaijs",
    "kultusministerium hessen", "finanzministerium hessen",
    "christoph sippel", "vanessa gronemann",
    "nina eisenhardt", "martina feldmayer",
    "torsten leveringhaus", "ines claus",
    "cdu hessen", "spd hessen", "fdp hessen",
    "grüne fraktion hessen", "afd fraktion hessen",
    "die linke hessen", "bsw hessen",
    "hls (suchtfragen)",
    "lebenshilfe hessen",
    "drk hessen", "vhu hessen",
    "dgb hessen",
    "ihk frankfurt",
}

SOURCE_NATIONAL = {
    "google alerts",
    "tagesschau",
    "bmas", "arbeitsministerium",
    "bmbfsfj", "familienministerium",
    "rki",
    "destatis",
    "bertelsmann stiftung",
    "diw berlin", "diw",
    "wzb",
    "ifo institut", "ifo",
    "iab",
    "iw köln",
    "iza",
    "zew",
    "junge welt",
}

SOURCE_BUNDESVERBAND = {
    "bagfw", "bundesarbeitsgemeinschaft",
    "caritas deutschland",
    "der paritätische",
    "pro asyl",
    "amnesty international",
    "bistum", "diözese",
    "diakonie deutschland",
}

SOURCE_EUROSTAT = {
    "eurostat", "cedefop", "fra",
}

# Social policy topic keywords (general)
SOCIAL_KEYWORDS = {
    "pflege", "altenpflege", "pflegeheim", "pflegekraft",
    "kita", "kindergarten", "kinderbetreuung",
    "sozial", "sozialpolitik", "sozialarbeit", "sozialleistung",
    "wohlfahrt", "wohlfahrtspflege",
    "armut", "armutsrisiko", "kinderarmut",
    "migration", "flucht", "flüchtling", "asyl", "geflüchtete",
    "inklusion", "behinderung", "teilhabe",
    "obdachlos", "wohnungslos",
    "sucht", "drogenberatung",
    "schuldnerberatung", "existenzsicherung",
}

# Per-AK topic keywords (data-mined from training set + domain knowledge)
AK_KEYWORDS = {
    "ak1": {  # Grundsatz und Sozialpolitik
        "sozialstaat", "sozialpolitik", "grundsicherung", "schuldenbremse",
        "sondervermögen", "teilzeit", "arbeitszeit", "arbeitszeiten",
        "arbeitnehmer", "streik", "nahverkehr", "einsamkeit",
        "heizungsgesetz", "spitzensteuersatz", "mehrwertsteuer",
        "renteneintrittsalter", "wohnkosten", "bürgergeld", "sozialhilfe",
        "wohlfahrtspflege", "wohlfahrtsverband", "tarifpolitik",
        "wohnungslos", "obdachlos", "schuldnerberatung", "überschuldung",
        "suchtberatung", "suchthilfe", "arbeitsmarktpolitik",
        "gemeinnützigkeit", "ehrenamt",
    },
    "ak2": {  # Migration und Flucht
        "abschiebung", "abgeschoben", "asyl", "asylverfahren",
        "migranten", "migration", "flüchtling", "flüchtlingsrat",
        "geflüchtete", "integrationskurs", "aufenthaltstitel",
        "ausländerbehörde", "ausländer", "remigration",
        "drogenhilfe", "suchthilfezentrum", "crack",
        "erstaufnahme", "bleiberecht", "herkunftsstaaten",
    },
    "ak3": {  # Gesundheit, Pflege und Senioren
        "pflegebudget", "pflegedienst", "pflegeheim", "pflegeversicherung",
        "pflegereform", "pflegekraft", "pflegeausbildung", "pflegeberuf",
        "pflegegrad", "pflegegeld", "häusliche", "altenpflege",
        "blutspende", "blutspenden", "blutgruppe",
        "patient", "patienten", "patientinnen",
        "klinik", "krankenhaus", "notaufnahme", "reha",
        "demenz", "hospiz", "palliativ", "ärzte",
        "eigenanteil", "entlastungsbetrag", "tagespflege",
        "kurzzeitpflege", "seniorenarbeit",
    },
    "ak4": {  # Eingliederungshilfe
        "inklusion", "behinderung", "behinderte", "behindertenrechtskonvention",
        "bundesteilhabegesetz", "eingliederungshilfe",
        "barrierefreiheit", "barrierefrei", "teilhabe",
        "werkstatt", "werkstätten", "wohnraumhilfe",
        "förderbescheid", "meldeadresse",
    },
    "ak5": {  # Kinder, Jugend, Frauen und Familie
        "kita", "kindergarten", "kinderbetreuung", "kindertagesstätte",
        "frauenhaus", "frauenhäuser", "belästigung", "gewaltschutz",
        "jugendgefährdend", "altersverifikation", "sozialindex",
        "schulsozialarbeit", "jugendhilfe", "jugendarbeit",
        "familienberatung", "familienbildung", "kinderarmut",
        "medienbildung", "plattformen", "algorithmen",
        "erzieher", "erzieherin",
    },
    "qag": {  # Querschnitt: Digitalisierung, Klimaschutz, Wohnen
        "digitalisierung", "technologien", "bürgerportal",
        "bezahlbarem", "eigentumswohnung", "sozialpreis",
        "klimaschutz", "energetische sanierung",
        "wohnungsbau", "wohnraumförderung",
    },
}


# ============================================================================
# Pre-compiled regex patterns (avoid recompiling per keyword per call)
# ============================================================================

def _compile_substring_re(keywords: set) -> re.Pattern:
    """Compile a regex that matches any keyword as a substring (case-insensitive)."""
    # Sort by length descending so longer keywords match first (greedy)
    sorted_kws = sorted(keywords, key=len, reverse=True)
    return re.compile('|'.join(re.escape(kw) for kw in sorted_kws), re.IGNORECASE)

def _compile_word_boundary_re(keywords: set) -> re.Pattern:
    """Compile a regex that matches any keyword with word boundaries (case-insensitive)."""
    sorted_kws = sorted(keywords, key=len, reverse=True)
    return re.compile(r'\b(?:' + '|'.join(re.escape(kw) for kw in sorted_kws) + r')\b', re.IGNORECASE)

_HESSEN_RE = _compile_substring_re(HESSEN_KEYWORDS)
_OTHER_BUNDESLAENDER_RE = _compile_word_boundary_re(OTHER_BUNDESLAENDER)
_SOCIAL_RE = _compile_substring_re(SOCIAL_KEYWORDS)

# Per-Bundesland patterns (word boundary)
_BUNDESLAND_RES = {
    key: _compile_word_boundary_re(kws)
    for key, kws in BUNDESLAND_KEYWORDS.items()
}

# Per-AK topic patterns (substring)
_AK_RES = {
    key: _compile_substring_re(kws)
    for key, kws in AK_KEYWORDS.items()
}


# ============================================================================
# Feature extraction
# ============================================================================

def _normalize(text: str) -> str:
    """Lowercase and strip for keyword matching."""
    return text.lower().strip()


def _count_keyword_hits(text: str, keywords: set, word_boundary: bool = False) -> int:
    """Count how many keywords appear in text.

    Args:
        word_boundary: If True, use regex word boundaries to avoid
            substring matches (e.g., "essen" in "Hessen").
    """
    text_lower = _normalize(text)
    if word_boundary:
        return sum(
            1 for kw in keywords
            if re.search(r'\b' + re.escape(kw) + r'\b', text_lower)
        )
    return sum(1 for kw in keywords if kw in text_lower)


def _source_matches(source: str, category: set) -> bool:
    """Check if source matches any pattern in category."""
    source_lower = _normalize(source)
    return any(pat in source_lower for pat in category)


def extract_features(title: str, content: str, source: str = "") -> np.ndarray:
    """
    Extract geographic and structural features from a news item.

    Args:
        title: Article title
        content: Article content/body
        source: Source name (e.g., "FAZ (Frankfurter Allgemeine)")

    Returns:
        np.ndarray of shape (37,) with feature values
    """
    full_text = f"{title} {content}"
    text_lower = _normalize(full_text)

    # Geographic features (using pre-compiled patterns)
    hessen_hits = len(_HESSEN_RE.findall(full_text))
    other_hits = len(_OTHER_BUNDESLAENDER_RE.findall(full_text))

    has_hessen = float(hessen_hits > 0)
    title_has_hessen = float(bool(_HESSEN_RE.search(title)))
    geo_ratio = hessen_hits / (hessen_hits + other_hits + 1)  # 0-1 range

    # Per-Bundesland detection (15 other states, word-boundary matching)
    state_features = []
    for state_key in BUNDESLAND_KEYWORDS:
        state_features.append(
            float(bool(_BUNDESLAND_RES[state_key].search(full_text)))
        )

    # Bundesweit/federal signal
    has_bundesweit = float(any(
        kw in text_lower for kw in
        ("bundesweit", "deutschlandweit", "bundesregierung", "bundestag")
    ))

    # Source classification
    source_hessen_regional = float(_source_matches(source, SOURCE_HESSEN_REGIONAL))
    source_hessen_politics = float(_source_matches(source, SOURCE_HESSEN_POLITICS))
    source_national = float(_source_matches(source, SOURCE_NATIONAL))
    source_bundesverband = float(_source_matches(source, SOURCE_BUNDESVERBAND))
    is_eurostat = float(_source_matches(source, SOURCE_EUROSTAT))

    # Content structure
    content_len = len(content)
    if content_len < 200:
        length_bucket = 0.0
    elif content_len < 1000:
        length_bucket = 1.0
    elif content_len < 5000:
        length_bucket = 2.0
    else:
        length_bucket = 3.0

    is_social_media = float(bool(re.search(
        r"(instagram|twitter|x\.com|tiktok|facebook|linkedin)\b",
        text_lower,
    )))

    # Social policy topic signal (using pre-compiled pattern)
    has_social_keywords = float(bool(_SOCIAL_RE.search(full_text)))

    # Per-AK topic keyword hits (count, not just boolean — intensity matters)
    ak_features = []
    for ak_key in AK_KEYWORDS:
        hits = len(_AK_RES[ak_key].findall(full_text))
        ak_features.append(float(hits))

    # Source-aware combo features (interaction terms for FP reduction)
    # National source + no Hessen mention = strong irrelevance signal
    national_source_no_hessen = float(source_national and not has_hessen)
    # Social keywords + no Hessen = likely Pflege from other state
    social_keywords_no_hessen = float(has_social_keywords and not has_hessen)
    # Google Alerts + another state detected + no Hessen
    any_other_state = float(any(sf > 0 for sf in state_features))
    is_google_alerts = float("google alerts" in _normalize(source))
    google_alerts_other_state = float(is_google_alerts and any_other_state and not has_hessen)
    # Hessen source + social topic = strong relevance signal
    hessen_source_social_topic = float(
        (source_hessen_regional or source_hessen_politics) and has_social_keywords
    )

    return np.array([
        has_hessen,              # 0: Hessen mentioned anywhere
        title_has_hessen,        # 1: Hessen in title (strong signal)
        geo_ratio,               # 2: Hessen / (Hessen + Other + 1)
        *state_features,         # 3-17: Per-Bundesland booleans (15 states)
        has_bundesweit,          # 18: Federal-level keywords
        source_hessen_regional,  # 19: Regional Hessen media
        source_hessen_politics,  # 20: Hessen political source
        source_national,         # 21: National/federal source
        source_bundesverband,    # 22: Bundesverband source
        is_eurostat,             # 23: EU stats source
        length_bucket,           # 24: Content length bucket (0-3)
        is_social_media,         # 25: Social media content
        has_social_keywords,     # 26: Social policy keywords present
        *ak_features,            # 27-32: Per-AK keyword hit counts (6 AKs)
        # Combo features (v4)
        national_source_no_hessen,   # 33: National source, no Hessen → irrelevant
        social_keywords_no_hessen,   # 34: Social keywords, no Hessen → other state
        google_alerts_other_state,   # 35: Google Alerts + other state, no Hessen
        hessen_source_social_topic,  # 36: Hessen source + social topic → relevant
    ], dtype=np.float64)


FEATURE_NAMES = [
    "has_hessen",
    "title_has_hessen",
    "geo_ratio",
    # Per-Bundesland features
    *[f"state_{key}" for key in BUNDESLAND_KEYWORDS],
    "has_bundesweit",
    "source_hessen_regional",
    "source_hessen_politics",
    "source_national",
    "source_bundesverband",
    "is_eurostat",
    "length_bucket",
    "is_social_media",
    "has_social_keywords",
    # Per-AK topic features
    *[f"ak_{key}_hits" for key in AK_KEYWORDS],
    # Combo features (v4)
    "national_source_no_hessen",
    "social_keywords_no_hessen",
    "google_alerts_other_state",
    "hessen_source_social_topic",
]

FEATURE_VERSION = 4  # Added source-aware combo features
NUM_FEATURES = len(FEATURE_NAMES)


def extract_features_batch(
    titles: list[str],
    contents: list[str],
    sources: list[str],
) -> np.ndarray:
    """
    Extract features for a batch of items.

    Returns:
        np.ndarray of shape (n_items, NUM_FEATURES)
    """
    return np.array([
        extract_features(t, c, s)
        for t, c, s in zip(titles, contents, sources)
    ])
