"""SPARQL query construction for HistWarDB.

Kept separate from the network/IO layer so the query text itself can be
unit-tested without hitting the live Wikidata endpoint.
"""

WIKIDATA_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

DEFAULT_USER_AGENT = "HistWarDB/0.1 (academic-research; +https://github.com/VincenzoManto/histwardb)"

# Conflict class: military conflict (and subclasses).
CONFLICT_CLASS = "wd:Q198"

# Classes excluded from being treated as a "war": peacekeeping missions and
# military exercises are noise for a sovereign-war dataset.
EXCLUDED_CONFLICT_CLASSES = ("wd:Q35349", "wd:Q1151430")

# Entity types eligible as belligerents: sovereign state, historical country,
# country.
COUNTRY_TYPES = ("wd:Q3624078", "wd:Q3024240", "wd:Q6256")

# Non-state entity classes excluded from the belligerent side: human,
# organization, national sports team.
EXCLUDED_COUNTRY_CLASSES = ("wd:Q5", "wd:Q83370", "wd:Q7278")


def build_sparql_query(chunk_start: int, chunk_end: int, end_year: int) -> str:
    """Build the SPARQL query fetching conflicts overlapping [chunk_start, chunk_end).

    Parameters
    ----------
    chunk_start, chunk_end:
        Temporal window (inclusive/exclusive) used to page around Wikidata's
        query result limits.
    end_year:
        Overall dataset end year, used as a fallback for ongoing conflicts
        with no recorded end date.
    """
    excluded_conflicts = "\n".join(
        f"MINUS {{ ?conflict wdt:P31/wdt:279* {cls} . }}" for cls in EXCLUDED_CONFLICT_CLASSES
    )
    excluded_countries = "\n".join(
        f"MINUS {{ ?country wdt:P31/wdt:279* {cls} . }}" for cls in EXCLUDED_COUNTRY_CLASSES
    )
    country_values = " ".join(COUNTRY_TYPES)

    return f"""
    SELECT DISTINCT ?conflictLabel ?countryLabel ?startDate ?endDate ?countryStart ?countryEnd ?countryEndFallback WHERE {{
        ?conflict wdt:P31/wdt:279* {CONFLICT_CLASS} ;
                wdt:P710 ?country ;
                wdt:P580 ?startDate .

        OPTIONAL {{ ?conflict wdt:P582 ?endDate . }}

        {excluded_conflicts}

        ?country wdt:P31/wdt:279* ?countryType .
        VALUES ?countryType {{ {country_values} }}

        OPTIONAL {{ ?country wdt:P571 ?countryStart . }}
        OPTIONAL {{ ?country wdt:P576 ?countryEnd . }}
        OPTIONAL {{ ?country wdt:P582 ?countryEndFallback . }}

        {excluded_countries}

        BIND(COALESCE(?endDate, "{end_year}-12-31T23:59:59Z"^^xsd:dateTime) as ?effEndDate)
        FILTER(?startDate < "{chunk_end}-01-01T00:00:00Z"^^xsd:dateTime && ?effEndDate >= "{chunk_start}-01-01T00:00:00Z"^^xsd:dateTime)

        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}
    LIMIT 5000
    """
