"""Backfill topic taxonomy for existing items.

Two-phase approach:
1. Fast mapping: Map existing LLM tags to new taxonomy (no LLM needed)
2. LLM backfill: For items that couldn't be mapped, use LLM follow-up

Run inside the backend container:
    python scripts/backfill_topics.py [--llm] [--days N]

Options:
    --llm    Also run LLM-based backfill for unmapped items (requires Ollama)
    --days N Number of days to look back (default: 90)
"""

import asyncio
import json
import logging
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# Mapping from old free-text tags to new canonical taxonomy topics.
# Case-insensitive matching. First match wins.
TAG_TO_TOPIC: dict[str, str] = {
    # AK1: Grundsatz/Sozialpolitik
    "sozialpolitik": "Sozialpolitik",
    "sozialstaat": "Sozialpolitik",
    "sozialleistungen": "Sozialleistungen",
    "sozialhilfe": "Sozialleistungen",
    "grundsicherung": "Sozialleistungen",
    "bürgergeld": "Sozialleistungen",
    "bürgergeld-reform": "Sozialleistungen",
    "sanktionen": "Sozialleistungen",
    "haushalt": "Haushalt und Finanzen",
    "finanzierung": "Haushalt und Finanzen",
    "förderung": "Haushalt und Finanzen",
    "kürzungen": "Haushalt und Finanzen",
    "kommunalfinanzierung": "Haushalt und Finanzen",
    "länderfinanzausgleich": "Haushalt und Finanzen",
    "investitionen": "Haushalt und Finanzen",
    "steuerpolitik": "Steuerpolitik",
    "steuerreform": "Steuerpolitik",
    "erbschaftsteuer": "Steuerpolitik",
    "erbschaftssteuer": "Steuerpolitik",
    "bürokratieabbau": "Bürokratieabbau",
    "bürokratie": "Bürokratieabbau",
    "entbürokratisierung": "Bürokratieabbau",
    "vergaberecht": "Vergaberecht",
    "tariftreue": "Vergaberecht",
    "ehrenamt": "Ehrenamt",
    "wohlfahrtsverbände": "Wohlfahrtsverbände",
    "tarifpolitik": "Tarifpolitik",
    "tarifverhandlungen": "Tarifpolitik",
    "tarifvertrag": "Tarifpolitik",
    "tariflöhne": "Tarifpolitik",

    # AK2: Migration/Flucht
    "migration": "Migration und Flucht",
    "migration/flucht": "Migration und Flucht",
    "flucht": "Migration und Flucht",
    "flüchtlinge": "Migration und Flucht",
    "migrationswende": "Migration und Flucht",
    "asyl": "Asylpolitik",
    "asylpolitik": "Asylpolitik",
    "asylrecht": "Asylpolitik",
    "asylverfahren": "Asylpolitik",
    "asylbewerber": "Asylpolitik",
    "integration": "Integration",
    "abschiebungen": "Abschiebung",
    "abschiebung": "Abschiebung",

    # AK3: Gesundheit/Pflege/Senioren
    "pflege": "Pflege",
    "altenpflege": "Pflege",
    "pflegebudget": "Pflegefinanzierung",
    "pflegekosten": "Pflegefinanzierung",
    "pflegeversicherung": "Pflegefinanzierung",
    "pflegegeld": "Pflegefinanzierung",
    "pflegekräftemangel": "Pflegepersonal",
    "pflegekräfte": "Pflegepersonal",
    "personalmangel": "Pflegepersonal",
    "gesundheit": "Gesundheitsversorgung",
    "gesundheitsversorgung": "Gesundheitsversorgung",
    "gesundheitspolitik": "Gesundheitsversorgung",
    "gesundheitsreform": "Gesundheitsversorgung",
    "gesundheitswesen": "Gesundheitsversorgung",
    "notfallversorgung": "Gesundheitsversorgung",
    "primärversorgung": "Gesundheitsversorgung",
    "krankenhäuser": "Krankenhausreform",
    "krankenhausreform": "Krankenhausreform",
    "krankenhaus": "Krankenhausreform",
    "kliniken": "Krankenhausreform",
    "psychische gesundheit": "Psychische Gesundheit",
    "sucht": "Sucht und Prävention",
    "suchthilfe": "Sucht und Prävention",
    "alkohol": "Sucht und Prävention",
    "senioren": "Senioren und Alter",
    "demenz": "Demenz",

    # AK4: Eingliederungshilfe
    "behinderung": "Behinderung und Inklusion",
    "inklusion": "Behinderung und Inklusion",
    "barrierefreiheit": "Barrierefreiheit",
    "eingliederungshilfe": "Eingliederungshilfe",

    # AK5: Kinder/Jugend/Familie
    "kita": "Kita und Kinderbetreuung",
    "kitas": "Kita und Kinderbetreuung",
    "frühkindliche bildung": "Kita und Kinderbetreuung",
    "kindertagesbetreuung": "Kita und Kinderbetreuung",
    "jugend": "Kinder- und Jugendhilfe",
    "jugendhilfe": "Kinder- und Jugendhilfe",
    "kinder/jugend": "Kinder- und Jugendhilfe",
    "kinder/jugend/familie": "Kinder- und Jugendhilfe",
    "kinder": "Kinder- und Jugendhilfe",
    "kinderrechte": "Kinder- und Jugendhilfe",
    "kinderschutz": "Kinderschutz",
    "familienpolitik": "Familienpolitik",
    "kinderarmut": "Kinderarmut",

    # QAG: Querschnitt
    "digitalisierung": "Digitalisierung",
    "ki": "Digitalisierung",
    "technologie": "Digitalisierung",
    "wohnen": "Wohnen und Wohnungsnot",
    "wohnungsnot": "Wohnen und Wohnungsnot",
    "wohnungspolitik": "Wohnen und Wohnungsnot",
    "sozialwohnungen": "Wohnen und Wohnungsnot",
    "sozialwohnung": "Wohnen und Wohnungsnot",
    "wohnungsbau": "Wohnen und Wohnungsnot",
    "armut": "Armut und Existenzsicherung",
    "obdachlosigkeit": "Obdachlosigkeit",

    # Übergreifend
    "fachkräftemangel": "Fachkräftemangel",
    "fachkräfte": "Fachkräftemangel",
    "arbeitsmarkt": "Arbeitsmarkt",
    "arbeitslosigkeit": "Arbeitsmarkt",
    "arbeitsrecht": "Arbeitsmarkt",
    "bildung": "Bildung und Ausbildung",
    "bildungspolitik": "Bildung und Ausbildung",
    "ausbildung": "Bildung und Ausbildung",
    "weiterbildung": "Bildung und Ausbildung",
    "bildungsurlaub": "Bildung und Ausbildung",
    "schulpolitik": "Bildung und Ausbildung",
    "gleichstellung": "Gleichstellung",
    "gewalt": "Gewalt und Gewaltschutz",
    "gewalt gegen frauen": "Gewalt und Gewaltschutz",
    "gewaltprävention": "Gewalt und Gewaltschutz",
    "demokratie": "Demokratie und Extremismus",
    "rechtsextremismus": "Demokratie und Extremismus",
    "extremismus": "Demokratie und Extremismus",
    "linksextremismus": "Demokratie und Extremismus",
    "antisemitismus": "Demokratie und Extremismus",
    "rassismus": "Demokratie und Extremismus",
    "menschenrechte": "Menschenrechte",
    "humanitäre hilfe": "Humanitäre Hilfe",
    "klimaschutz": "Klimaschutz und Soziales",
    "energiewende": "Klimaschutz und Soziales",
    "gesetzgebung": "Recht und Gesetzgebung",
    "rechtshilfe": "Recht und Gesetzgebung",
    "rente": "Sozialleistungen",
    "rentenreform": "Sozialleistungen",
    "pflegereform": "Pflegefinanzierung",
    "pflegekrisen": "Pflege",
    "nachbarschaftshilfe": "Ehrenamt",
    "drk": "Wohlfahrtsverbände",
    "caritas": "Wohlfahrtsverbände",
    "diakonie": "Wohlfahrtsverbände",
    "kindeswohl": "Kinderschutz",
    "gesundheitskosten": "Gesundheitsversorgung",
    "gesundheit/pflege": "Gesundheitsversorgung",
    "gesundheit/pflege/senioren": "Gesundheitsversorgung",
    "krankenhaus-einschnitte": "Krankenhausreform",
    "sparpaket": "Haushalt und Finanzen",
    "kündigungswelle": "Arbeitsmarkt",
    "wirtschaft": "Haushalt und Finanzen",
    "sozialprojekte": "Sozialpolitik",
    "gemeinschaftsprojekte": "Sozialpolitik",
    "organisationsstruktur": "Wohlfahrtsverbände",
    "kirchensteuern": "Haushalt und Finanzen",
    "recht/verwaltung": "Recht und Gesetzgebung",
    "klinikclowns": "Gesundheitsversorgung",
    "drogenhandel": "Sucht und Prävention",
    "rentenberatung": "Sozialleistungen",
    "integrative pflege": "Pflege",
    "naturheilkunde": "Gesundheitsversorgung",
    "patientenversorgung": "Gesundheitsversorgung",
    "pflegekrise": "Pflege",
    "demografie": "Senioren und Alter",
    "dorfentwicklung": "Sozialpolitik",
    "tagespflege": "Pflege",
    "geschlechtergerechtigkeit": "Gleichstellung",
    "löhne": "Tarifpolitik",
    "alkoholpolitik": "Sucht und Prävention",
    "jugendschutz": "Kinder- und Jugendhilfe",
    "prävention": "Gesundheitsversorgung",
    "pflegereform 2026": "Pflegefinanzierung",
    "landespflegeplan": "Pflege",
    "pflegeinfrastruktur": "Pflege",
    "kommunalfinanzen": "Haushalt und Finanzen",
    "sondervermögen": "Haushalt und Finanzen",
    "kommunaler finanzausgleich": "Haushalt und Finanzen",
    "wohnungsmarkt": "Wohnen und Wohnungsnot",
    "leerstandsgesetz": "Wohnen und Wohnungsnot",
    "social media": "Digitalisierung",
    "mental health": "Psychische Gesundheit",
    "mentalgesundheit": "Psychische Gesundheit",
    "youth": "Kinder- und Jugendhilfe",
    "webinar": "Digitalisierung",

    # Additional production mappings
    "politik": "Sozialpolitik",
    "statistik": "Sozialpolitik",
    "daten": "Sozialpolitik",
    "data": "Sozialpolitik",
    "reform": "Sozialpolitik",
    "reformen": "Sozialpolitik",
    "sozialstaatsreform": "Sozialpolitik",
    "reformagenda": "Sozialpolitik",
    "sozialrecht": "Recht und Gesetzgebung",
    "soziale gerechtigkeit": "Sozialpolitik",
    "sozialpolitik hessen": "Sozialpolitik",
    "infrastruktur": "Sozialpolitik",
    "liga hessen": "Wohlfahrtsverbände",
    "kosten": "Haushalt und Finanzen",
    "finanzpolitik": "Haushalt und Finanzen",
    "finanzen": "Haushalt und Finanzen",
    "finanzausgleich": "Haushalt und Finanzen",
    "kommunaler finanzausgleich": "Haushalt und Finanzen",
    "kommunalpolitik": "Haushalt und Finanzen",
    "kommunen": "Haushalt und Finanzen",
    "schuldenbremse": "Haushalt und Finanzen",
    "landeshaushalt": "Haushalt und Finanzen",
    "finanzreform": "Haushalt und Finanzen",
    "schulden": "Haushalt und Finanzen",
    "eigenanteil": "Pflegefinanzierung",
    "eigenanteile": "Pflegefinanzierung",
    "eigenbeteiligung": "Pflegefinanzierung",
    "eigenbeteiligungen": "Pflegefinanzierung",
    "heimkosten": "Pflegefinanzierung",
    "pflegekasse": "Pflegefinanzierung",
    "pflegefinanzierung": "Pflegefinanzierung",
    "pflegebudgets": "Pflegefinanzierung",
    "hilfe zur pflege": "Pflegefinanzierung",
    "pflegegrad": "Pflegefinanzierung",
    "pflegegeld": "Pflegefinanzierung",
    "pflegeheim": "Pflege",
    "pflegeheime": "Pflege",
    "pflegebedürftige": "Pflege",
    "pflegepersonal": "Pflegepersonal",
    "ambulante pflege": "Pflege",
    "heimbewohner": "Pflege",
    "seniorenheime": "Pflege",
    "altenhilfe": "Pflege",
    "klinik": "Krankenhausreform",
    "krankenversorgung": "Gesundheitsversorgung",
    "krankenkassen": "Gesundheitsversorgung",
    "krankenversicherung": "Gesundheitsversorgung",
    "krankenstand": "Gesundheitsversorgung",
    "apotheken": "Gesundheitsversorgung",
    "apothekenreform": "Gesundheitsversorgung",
    "onkologie": "Gesundheitsversorgung",
    "krebs": "Gesundheitsversorgung",
    "rettungsdienst": "Gesundheitsversorgung",
    "grippe": "Gesundheitsversorgung",
    "rsv": "Gesundheitsversorgung",
    "infektionskrankheiten": "Gesundheitsversorgung",
    "gesundheitssystem": "Gesundheitsversorgung",
    "gesundheitsschutz": "Gesundheitsversorgung",
    "therapie": "Gesundheitsversorgung",
    "hausarzt": "Gesundheitsversorgung",
    "roboter in pflege": "Pflege",
    "roboter": "Pflege",
    "pflegepolitik": "Pflege",
    "pflegequalität": "Pflege",
    "pflegelotsen": "Pflege",
    "pflegeeinrichtungen": "Pflege",
    "pflegeausbildung": "Pflegepersonal",
    "personalausstattung": "Pflegepersonal",
    "personalnot": "Pflegepersonal",
    "personalbemessung": "Pflegepersonal",
    "gewalt in pflege": "Pflege",
    "häusliche pflege": "Pflege",
    "verhinderungspflege": "Pflege",
    "suchtprävention": "Sucht und Prävention",
    "suchtberatung": "Sucht und Prävention",
    "cannabis": "Sucht und Prävention",
    "kinderbetreuung": "Kita und Kinderbetreuung",
    "kindergarten": "Kita und Kinderbetreuung",
    "kinderzuschlag": "Kinderarmut",
    "kindesmissbrauch": "Kinderschutz",
    "kinderpornografie": "Kinderschutz",
    "kinderarbeit": "Kinderschutz",
    "jugendarbeit": "Kinder- und Jugendhilfe",
    "jugendpolitik": "Kinder- und Jugendhilfe",
    "jugendbeteiligung": "Kinder- und Jugendhilfe",
    "jugendradikalisierung": "Kinder- und Jugendhilfe",
    "jugendarbeitslosigkeit": "Kinder- und Jugendhilfe",
    "familie": "Familienpolitik",
    "familien": "Familienpolitik",
    "familienförderung": "Familienpolitik",
    "adoption": "Familienpolitik",
    "blutspende": "Gesundheitsversorgung",
    "blutspenden": "Gesundheitsversorgung",
    "organspende": "Gesundheitsversorgung",
    "wohnungslosigkeit": "Obdachlosigkeit",
    "wohnungssuche": "Wohnen und Wohnungsnot",
    "wohnungsmangel": "Wohnen und Wohnungsnot",
    "wohnungsknappheit": "Wohnen und Wohnungsnot",
    "wohnraum": "Wohnen und Wohnungsnot",
    "wohngeld": "Wohnen und Wohnungsnot",
    "mietpreise": "Wohnen und Wohnungsnot",
    "mieten": "Wohnen und Wohnungsnot",
    "mieterschutz": "Wohnen und Wohnungsnot",
    "sozialer wohnungsbau": "Wohnen und Wohnungsnot",
    "bauen": "Wohnen und Wohnungsnot",
    "baukosten": "Wohnen und Wohnungsnot",
    "bauordnung": "Wohnen und Wohnungsnot",
    "bauvorhaben": "Wohnen und Wohnungsnot",
    "arbeitsbedingungen": "Arbeitsmarkt",
    "arbeitszeit": "Arbeitsmarkt",
    "arbeitszeiten": "Arbeitsmarkt",
    "arbeitszeitgesetz": "Arbeitsmarkt",
    "teilzeit": "Arbeitsmarkt",
    "teilzeitrecht": "Arbeitsmarkt",
    "teilzeitanspruch": "Arbeitsmarkt",
    "teilzeitarbeit": "Arbeitsmarkt",
    "beschäftigung": "Arbeitsmarkt",
    "arbeitsvermittlung": "Arbeitsmarkt",
    "arbeitsmarktintegration": "Arbeitsmarkt",
    "arbeit": "Arbeitsmarkt",
    "gehalt": "Tarifpolitik",
    "mindestlohn": "Tarifpolitik",
    "lohndumping": "Tarifpolitik",
    "besoldung": "Tarifpolitik",
    "tvöd": "Tarifpolitik",
    "streik": "Tarifpolitik",
    "warnstreik": "Tarifpolitik",
    "gewerkschaften": "Tarifpolitik",
    "gewerkschaft": "Tarifpolitik",
    "schule": "Bildung und Ausbildung",
    "schulen": "Bildung und Ausbildung",
    "schulsystem": "Bildung und Ausbildung",
    "schulbau": "Bildung und Ausbildung",
    "fortbildung": "Bildung und Ausbildung",
    "qualifizierung": "Bildung und Ausbildung",
    "berufsorientierung": "Bildung und Ausbildung",
    "lehrkräftemangel": "Fachkräftemangel",
    "arbeitskräftemangel": "Fachkräftemangel",
    "fachkräftesicherung": "Fachkräftemangel",
    "internationale fachkräfte": "Fachkräftemangel",
    "diskriminierung": "Gleichstellung",
    "sexualisierte gewalt": "Gewalt und Gewaltschutz",
    "gewaltschutzgesetz": "Gewalt und Gewaltschutz",
    "missbrauch": "Gewalt und Gewaltschutz",
    "frauen": "Gleichstellung",
    "care-arbeit": "Gleichstellung",
    "radikalisierung": "Demokratie und Extremismus",
    "verfassungsschutz": "Demokratie und Extremismus",
    "verfassungsrecht": "Recht und Gesetzgebung",
    "verfassung": "Recht und Gesetzgebung",
    "verfassungsgericht": "Recht und Gesetzgebung",
    "gesetzentwurf": "Recht und Gesetzgebung",
    "rechtsstreit": "Recht und Gesetzgebung",
    "rechtsstaatlichkeit": "Recht und Gesetzgebung",
    "recht": "Recht und Gesetzgebung",
    "klimawandel": "Klimaschutz und Soziales",
    "klimapolitik": "Klimaschutz und Soziales",
    "energiepolitik": "Klimaschutz und Soziales",
    "energiearmut": "Klimaschutz und Soziales",
    "umwelt": "Klimaschutz und Soziales",
    "nachhaltigkeit": "Klimaschutz und Soziales",
    "naturschutz": "Klimaschutz und Soziales",
    "hospiz": "Hospiz und Palliativ",
    "altersvorsorge": "Sozialleistungen",
    "renten": "Sozialleistungen",
    "pensionen": "Sozialleistungen",
    "rentenpolitik": "Sozialleistungen",
    "sozialversicherung": "Sozialleistungen",
    "sozialabgaben": "Sozialleistungen",
    "kindergeld": "Sozialleistungen",
    "wohngeld": "Sozialleistungen",
    "altersarmut": "Armut und Existenzsicherung",
    "armutsrisiko": "Armut und Existenzsicherung",
    "lebenshaltungskosten": "Armut und Existenzsicherung",
    "lebensmittelkosten": "Armut und Existenzsicherung",
    "lebensmittelpreise": "Armut und Existenzsicherung",
    "tafeln": "Armut und Existenzsicherung",
    "soziale teilhabe": "Armut und Existenzsicherung",
    "teilhabe": "Armut und Existenzsicherung",
    "datenschutz": "Digitalisierung",
    "innovation": "Digitalisierung",
    "sprachförderung": "Integration",
    "beratung": "Sozialpolitik",
    "gemeinwesenarbeit": "Sozialpolitik",
    "sozialdienst": "Sozialpolitik",
    "sozialarbeit": "Sozialpolitik",
    "verwaltung": "Bürokratieabbau",
    "verwaltungsreform": "Bürokratieabbau",
    "verwaltungsmodernisierung": "Bürokratieabbau",
    "spenden": "Wohlfahrtsverbände",
    "stiftung": "Wohlfahrtsverbände",
    "jüdische gemeinden": "Wohlfahrtsverbände",
    "transparenz": "Sozialpolitik",
    "katastrophenschutz": "Sozialpolitik",
    "kritische infrastruktur": "Sozialpolitik",
    "kritis-dachgesetz": "Sozialpolitik",
    "deportation": "Abschiebung",

    # Noise tags - map to closest welfare topic
    "sicherheit": "Sozialpolitik",
    "kommunalwahl": "Sozialpolitik",
    "kommunalwahlen": "Sozialpolitik",
    "wahlrecht": "Sozialpolitik",
    "wahlkampf": "Sozialpolitik",
    "wahl": "Sozialpolitik",
    "parteien": "Sozialpolitik",
    "koalition": "Sozialpolitik",
    "koalitionsstreit": "Sozialpolitik",
    "koalitionsvertrag": "Sozialpolitik",
    "spd": "Sozialpolitik",
    "cdu": "Sozialpolitik",
    "afd": "Demokratie und Extremismus",
    "csu": "Sozialpolitik",
    "hessen": "Sozialpolitik",
    "nrw": "Sozialpolitik",
    "brandenburg": "Sozialpolitik",
    "frankfurt": "Sozialpolitik",
    "verkehr": "Sozialpolitik",
    "verkehrspolitik": "Sozialpolitik",
    "öpnv": "Sozialpolitik",
    "nahverkehr": "Sozialpolitik",
    "mobilität": "Sozialpolitik",
    "kriminalität": "Sozialpolitik",
    "polizei": "Sozialpolitik",
    "kultur": "Sozialpolitik",
    "kulturpolitik": "Sozialpolitik",
    "sport": "Sozialpolitik",
    "wetter": "Sozialpolitik",
    "iran": "Humanitäre Hilfe",
    "ukraine": "Humanitäre Hilfe",
    "usa": "Sozialpolitik",
    "internationale politik": "Humanitäre Hilfe",
    "international politics": "Humanitäre Hilfe",
    "eu-politik": "Sozialpolitik",
    "europapolitik": "Sozialpolitik",
    "eurostat": "Sozialpolitik",
    "eu-daten": "Sozialpolitik",
    "eu-lfs": "Sozialpolitik",
    "bundeswehr": "Sozialpolitik",
    "wehrdienst": "Sozialpolitik",
    "corona": "Gesundheitsversorgung",
    "landwirtschaft": "Sozialpolitik",
    "tierhaltung": "Sozialpolitik",
    "tierschutz": "Sozialpolitik",
    "forschung": "Sozialpolitik",
    "wissenschaft": "Sozialpolitik",
    "medien": "Sozialpolitik",
    "brand": "Sozialpolitik",
    "unfall": "Sozialpolitik",
    "geschichte": "Sozialpolitik",
    "holocaust": "Menschenrechte",
    "nationalsozialismus": "Menschenrechte",
    "erinnerungskultur": "Menschenrechte",
    "gedenken": "Menschenrechte",
    "ddr-geschichte": "Menschenrechte",
    "protest": "Sozialpolitik",
    "proteste": "Sozialpolitik",
    "kritik": "Sozialpolitik",
    "ethik": "Menschenrechte",
    "kirche": "Wohlfahrtsverbände",
    "tabaksteuer": "Steuerpolitik",
    "schwarzarbeit": "Arbeitsmarkt",
    "stadtentwicklung": "Wohnen und Wohnungsnot",
    "stadtplanung": "Wohnen und Wohnungsnot",
    "städtebau": "Wohnen und Wohnungsnot",
    "landesregierung": "Sozialpolitik",
    "landtag": "Sozialpolitik",
    "landespolitik": "Sozialpolitik",
    "bundestag": "Sozialpolitik",
    "bundesländer": "Sozialpolitik",
    "föderalismus": "Sozialpolitik",
    "föderalismusreform": "Sozialpolitik",
    "anerkennung": "Integration",
    "freiwilligenarbeit": "Ehrenamt",
    "sgb xi": "Pflege",
    "bezahlkarte": "Asylpolitik",
    "arbeitsverweigerer-regelung": "Sozialleistungen",
    "spd-reform": "Sozialpolitik",
    "eu-asylreform": "Asylpolitik",
    "geas-reform": "Asylpolitik",
    "unbegleitete minderjährige": "Migration und Flucht",
    "minderjährige": "Kinder- und Jugendhilfe",
    "angehörige": "Pflege",
    "vorsitzwechsel": "Wohlfahrtsverbände",
    "pfadfinder": "Ehrenamt",
    "tourismus": "Sozialpolitik",
    "tourism": "Sozialpolitik",
    "hicp": "Sozialpolitik",
    "gesundheitsstatistik": "Gesundheitsversorgung",
    "arbeitsmoral": "Arbeitsmarkt",
    "krankmeldung": "Arbeitsmarkt",
    "lebensmittel": "Armut und Existenzsicherung",
    "hochwasserschutz": "Sozialpolitik",
    "stromausfall": "Sozialpolitik",
    "energieeffizienz": "Klimaschutz und Soziales",
    "sanierung": "Haushalt und Finanzen",
    "handel": "Sozialpolitik",
    "industrie": "Arbeitsmarkt",
    "mittelstand": "Arbeitsmarkt",
    "handwerk": "Arbeitsmarkt",
    "start-ups": "Arbeitsmarkt",
    "studie": "Sozialpolitik",
    "ungleichheit": "Armut und Existenzsicherung",
    "soziale ungleichheit": "Armut und Existenzsicherung",
    "vermögen": "Steuerpolitik",
    "generationengerechtigkeit": "Sozialpolitik",
    "resilienz": "Sozialpolitik",
    "cybersecurity": "Digitalisierung",
    "rechtsschutz": "Recht und Gesetzgebung",
    "justiz": "Recht und Gesetzgebung",
    "justizvollzug": "Recht und Gesetzgebung",
    "petition": "Sozialpolitik",
    "ngos": "Wohlfahrtsverbände",
    "verbraucherschutz": "Sozialpolitik",
    "soforthilfe": "Humanitäre Hilfe",
    "krisenhilfe": "Humanitäre Hilfe",
    "krisenmanagement": "Sozialpolitik",
    "krisenvorsorge": "Sozialpolitik",
    "regulierung": "Recht und Gesetzgebung",
    "tabaksteuer": "Steuerpolitik",
    "mehrwertsteuer": "Steuerpolitik",
    "grundsteuer": "Steuerpolitik",
    "psychische erkrankungen": "Psychische Gesundheit",
    "psychologie": "Psychische Gesundheit",
    "genetik": "Gesundheitsversorgung",
    "medizin": "Gesundheitsversorgung",
    "advanced practice nurse": "Pflegepersonal",
    "geburtshilfe": "Gesundheitsversorgung",
    "hochschulen": "Bildung und Ausbildung",
    "hochschulfinanzierung": "Bildung und Ausbildung",
    "hochschulpolitik": "Bildung und Ausbildung",
    "startchancen-programm": "Bildung und Ausbildung",
    "mathematikunterricht": "Bildung und Ausbildung",
    "elternvertretung": "Kita und Kinderbetreuung",
    "personalkosten": "Haushalt und Finanzen",
    "asylbewerberleistungsgesetz": "Asylpolitik",
    "flexibilisierung": "Arbeitsmarkt",
    "flexibilität": "Arbeitsmarkt",
    "arbeitnehmerrechte": "Arbeitsmarkt",
    "arbeitsbelastung": "Arbeitsmarkt",
    "kündigungsschutz": "Arbeitsmarkt",
    "tarifverträge": "Tarifpolitik",
    "tarifbindung": "Tarifpolitik",
    "vergabegesetz": "Vergaberecht",
    "öffentliche aufträge": "Vergaberecht",
    "öffentlicher dienst": "Tarifpolitik",
    "beamtenbesoldung": "Tarifpolitik",
    "beamte": "Tarifpolitik",
    "verbeamtung": "Tarifpolitik",
    "bamf": "Asylpolitik",
    "migrationspolitik": "Migration und Flucht",
    "eu-blauen-karte": "Migration und Flucht",
    "regionale unterschiede": "Sozialpolitik",
    "regionale entwicklung": "Sozialpolitik",
    "regionalität": "Sozialpolitik",
    "ländlicher raum": "Sozialpolitik",
    "stadtpolitik": "Sozialpolitik",
    "außenpolitik": "Humanitäre Hilfe",
    "internationale zusammenarbeit": "Humanitäre Hilfe",
    "taliban": "Humanitäre Hilfe",
    "boykott": "Sozialpolitik",
    "fußball": "Sozialpolitik",
    "freibad": "Sozialpolitik",
    "niederlande": "Sozialpolitik",
    "bavaria": "Sozialpolitik",
    "berlin": "Sozialpolitik",
    "schweiz": "Sozialpolitik",
    "indien": "Humanitäre Hilfe",
    "familienunternehmen": "Arbeitsmarkt",
    "agenturnummer": "Sozialpolitik",
    "agg": "Gleichstellung",
    "gastronomie": "Arbeitsmarkt",
    "heizungsgesetz": "Klimaschutz und Soziales",
    "wirtschaftsdaten": "Sozialpolitik",
    "arbeitsmoral": "Arbeitsmarkt",
    "demographics": "Sozialpolitik",
    "statistics": "Sozialpolitik",
    "gesundheitsstatistik": "Gesundheitsversorgung",
    "eurostat-daten": "Sozialpolitik",
    "wirtschaftspolitik": "Sozialpolitik",
    "arbeitsunfälle": "Arbeitsmarkt",
    "schließung": "Haushalt und Finanzen",
    "betreuung": "Kinder- und Jugendhilfe",
    "unternehmen": "Arbeitsmarkt",
    "sicherheitspolitik": "Sozialpolitik",
    "vergabe": "Vergaberecht",
    "feuerwehr": "Ehrenamt",
    "ernährung": "Gesundheitsversorgung",
    "insolvenz": "Haushalt und Finanzen",
    "inflation": "Haushalt und Finanzen",
    "kostensteigerung": "Haushalt und Finanzen",
}


def map_tags_to_topic(tags: list[str]) -> str | None:
    """Map a list of old tags to a single canonical topic.

    Returns the first matching topic, or None if no mapping found.
    """
    for tag in tags:
        tag_lower = tag.strip().lower()
        if tag_lower in TAG_TO_TOPIC:
            return TAG_TO_TOPIC[tag_lower]
    return None


async def backfill_from_tags(days: int):
    """Phase 1: Map existing tags to taxonomy without LLM."""
    from database import async_session_maker
    from models import Item
    from sqlalchemy import select, text as sql_text
    from sqlalchemy.orm.attributes import flag_modified

    async with async_session_maker() as db:
        query = (
            select(Item)
            .where(
                Item.published_at >= sql_text(f"CURRENT_DATE - INTERVAL '{days} days'"),
                Item.similar_to_id.is_(None),
                Item.priority != "none",
            )
            .order_by(Item.published_at.desc())
        )
        result = await db.execute(query)
        items = result.scalars().all()

    logger.info(f"Found {len(items)} items to process")

    mapped = 0
    already_has_topic = 0
    unmapped = 0
    unmapped_tags = {}

    for item in items:
        llm = (item.metadata_ or {}).get("llm_analysis")
        if not llm:
            unmapped += 1
            continue

        # Skip if already has new topic field
        if llm.get("topic"):
            already_has_topic += 1
            continue

        tags = llm.get("tags", [])
        topic = map_tags_to_topic(tags)

        if topic:
            async with async_session_maker() as db:
                result = await db.execute(select(Item).where(Item.id == item.id))
                db_item = result.scalar_one()
                db_item.metadata_["llm_analysis"]["topic"] = topic
                db_item.metadata_["llm_analysis"]["topic_suggestion"] = None
                flag_modified(db_item, "metadata_")
                await db.commit()
            mapped += 1
            if mapped % 50 == 0:
                logger.info(f"  Mapped {mapped} items...")
        else:
            unmapped += 1
            for tag in tags:
                tag_lower = tag.strip().lower()
                unmapped_tags[tag_lower] = unmapped_tags.get(tag_lower, 0) + 1

    logger.info(f"Phase 1 complete: {mapped} mapped, {already_has_topic} already had topic, {unmapped} unmapped")

    if unmapped_tags:
        top_unmapped = sorted(unmapped_tags.items(), key=lambda x: -x[1])[:20]
        logger.info("Top unmapped tags:")
        for tag, count in top_unmapped:
            logger.info(f"  {tag}: {count}")

    return unmapped


async def backfill_with_llm(days: int):
    """Phase 2: Use LLM for items that couldn't be tag-mapped."""
    from database import async_session_maker
    from models import Item, Channel, Source
    from services.processor import create_processor_from_settings, ANALYSIS_SYSTEM_PROMPT
    from sqlalchemy import select, text as sql_text
    from sqlalchemy.orm import selectinload
    from sqlalchemy.orm.attributes import flag_modified

    processor = await create_processor_from_settings()
    if not processor:
        logger.error("LLM processor not available (is Ollama running?)")
        return

    async with async_session_maker() as db:
        query = (
            select(Item.id)
            .where(
                Item.published_at >= sql_text(f"CURRENT_DATE - INTERVAL '{days} days'"),
                Item.similar_to_id.is_(None),
                Item.priority != "none",
            )
            .order_by(Item.published_at.desc())
        )
        result = await db.execute(query)
        all_ids = [row[0] for row in result.fetchall()]

    logger.info(f"Phase 2: Checking {len(all_ids)} items for missing topics...")

    processed = 0
    skipped = 0
    errors = 0

    for item_id in all_ids:
        async with async_session_maker() as db:
            result = await db.execute(
                select(Item)
                .where(Item.id == item_id)
                .options(selectinload(Item.channel).selectinload(Channel.source))
            )
            item = result.scalar_one_or_none()
            if not item:
                continue

            llm = (item.metadata_ or {}).get("llm_analysis", {})
            if not llm or llm.get("topic"):
                skipped += 1
                continue

            source_name = item.channel.source.name if item.channel and item.channel.source else "Unbekannt"
            date_str = item.published_at.strftime("%Y-%m-%d") if item.published_at else "Unbekannt"

            prompt = f"""Titel: {item.title}
Inhalt: {item.content[:6000]}
Quelle: {source_name}
Datum: {date_str}"""

            assistant_json = json.dumps({
                "summary": item.summary or "",
                "relevant": True,
                "priority": llm.get("priority_suggestion"),
                "assigned_aks": llm.get("assigned_aks", []),
                "tags": llm.get("tags", []),
                "reasoning": llm.get("reasoning", ""),
            }, ensure_ascii=False)

            conversation = [
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": assistant_json},
            ]

            try:
                topic, suggestion = await processor.extract_topics(conversation)
                item.metadata_["llm_analysis"]["topic"] = topic
                item.metadata_["llm_analysis"]["topic_suggestion"] = suggestion
                flag_modified(item, "metadata_")
                await db.commit()
                processed += 1
                suffix = f" (suggestion: {suggestion})" if suggestion else ""
                logger.info(f"  [{processed}] [{item.id}] {item.title[:50]}... -> {topic}{suffix}")
            except Exception as e:
                errors += 1
                logger.error(f"  [{item.id}] Failed: {e}")

    logger.info(f"Phase 2 complete: {processed} processed, {skipped} skipped, {errors} errors")


async def main():
    parser = argparse.ArgumentParser(description="Backfill topic taxonomy")
    parser.add_argument("--llm", action="store_true", help="Also run LLM backfill for unmapped items")
    parser.add_argument("--days", type=int, default=90, help="Days to look back (default: 90)")
    args = parser.parse_args()

    logger.info(f"Starting topic backfill (days={args.days}, llm={args.llm})")

    # Phase 1: Fast tag mapping
    unmapped = await backfill_from_tags(args.days)

    # Phase 2: LLM backfill (optional)
    if args.llm and unmapped > 0:
        logger.info(f"\nStarting LLM backfill for {unmapped} unmapped items...")
        await backfill_with_llm(args.days)
    elif unmapped > 0:
        logger.info(f"\n{unmapped} items still unmapped. Run with --llm to backfill via LLM.")


if __name__ == "__main__":
    asyncio.run(main())
