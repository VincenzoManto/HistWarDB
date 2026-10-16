"""SPARQL query construction for HistWarDB.

Kept separate from the network/IO layer so the query text itself can be
unit-tested without hitting the live Wikidata endpoint.
"""

WIKIDATA_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

DEFAULT_USER_AGENT = "HistWarDB/0.1 (academic-research; +https://github.com/VincenzoManto/histwardb)"

# Conflict class: war (and subclasses).
CONFLICT_CLASS = "wd:Q198"

# Classes excluded from being treated as a "war": peacekeeping missions and
# military exercises are noise for a sovereign-war dataset.
EXCLUDED_CONFLICT_CLASSES = ("wd:Q20082270", "wd:Q357104")

# Entity types eligible as belligerents: sovereign state, historical country,
# country.
COUNTRY_TYPES = ("wd:Q3624078", "wd:Q3024240", "wd:Q6256")

# Non-state entity classes excluded from the belligerent side: human,
# organization, national sports team.
EXCLUDED_COUNTRY_CLASSES = ("wd:Q5", "wd:Q43229", "wd:Q1194951")

# Wikidata property used to fetch a conflict's recorded death toll, so
# callers can filter out low-severity/noise entries (see
# ``min_casualties`` on :class:`~histwardb.core.HistWarDB`). Left as
# OPTIONAL in the query: conflicts without a recorded figure are still
# fetched and are never dropped purely for lacking this data.
CASUALTIES_PROPERTY = "wdt:P1120"


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
        f"MINUS {{ ?conflict wdt:P31/wdt:P279* {cls} . }}" for cls in EXCLUDED_CONFLICT_CLASSES
    )
    excluded_countries = "\n".join(
        f"MINUS {{ ?country wdt:P31/wdt:P279* {cls} . }}" for cls in EXCLUDED_COUNTRY_CLASSES
    )
    country_values = " ".join(COUNTRY_TYPES)

    return f"""
    SELECT DISTINCT ?conflictLabel ?countryLabel ?startDate ?startDatePrecision ?endDate ?endDatePrecision ?countryStart ?countryEnd ?countryEndFallback ?casualties WHERE {{
        ?conflict wdt:P31/wdt:P279* {CONFLICT_CLASS} ;
                wdt:P710 ?country ;
                p:P580/psv:P580 ?startNode .
        ?startNode wikibase:timeValue ?startDate ;
                   wikibase:timePrecision ?startDatePrecision .

        OPTIONAL {{
            ?conflict p:P582/psv:P582 ?endNode .
            ?endNode wikibase:timeValue ?endDate ;
                     wikibase:timePrecision ?endDatePrecision .
        }}
        OPTIONAL {{ ?conflict {CASUALTIES_PROPERTY} ?casualties . }}

        {excluded_conflicts}

        ?country wdt:P31/wdt:P279* ?countryType .
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
