"""SPARQL query construction for HistWarDB.

Kept separate from the network/IO layer so the query text itself can be
unit-tested without hitting the live Wikidata endpoint.

Two kinds of query are issued:

1. :func:`build_sparql_query` -- the *participation* query. Returns bare
   ``(conflict, country, start, end)`` rows for one temporal chunk.
2. :func:`build_entity_query` -- the *entity* query. Returns labels,
   lifespans and ISO codes for an explicit list of QIDs.

They are split on purpose. Asking the public endpoint for labels and
optional country attributes in the same query as the ``P279*`` subclass
closures makes it time out or drop the connection; each half on its own
returns in a few seconds.
"""

from __future__ import annotations

from collections.abc import Iterable

WIKIDATA_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

DEFAULT_USER_AGENT = "HistWarDB/0.1 (academic-research; +https://github.com/VincenzoManto/histwardb)"

# Conflict class: war (and subclasses).
CONFLICT_CLASS = "wd:Q198"

# Classes excluded from being treated as a "war": peacekeeping missions and
# military exercises are noise for a sovereign-war dataset.
EXCLUDED_CONFLICT_CLASSES = ("wd:Q20082270", "wd:Q357104")

# Entity types eligible as belligerents: sovereign state, historical country,
# country. This positive filter is what keeps non-state actors out: a
# participant is kept only if one of its P31 classes is one of these or a
# subclass of them.
#
# There is deliberately no exclusion list on the country side. "Sovereign
# state" and "historical country" are themselves subclasses of
# "organization" (wd:Q43229) in Wikidata, so excluding organizations removes
# every state; and humans or sports teams never pass the positive filter.
COUNTRY_TYPES = ("wd:Q3624078", "wd:Q3024240", "wd:Q6256")

# Maximum rows per participation query. A chunk returning exactly this many
# rows is treated as truncated by the fetch layer.
QUERY_LIMIT = 5000


def build_sparql_query(chunk_start: int, chunk_end: int, end_year: int) -> str:
    """Build the participation query for conflicts overlapping [chunk_start, chunk_end).

    Parameters
    ----------
    chunk_start, chunk_end:
        Temporal window (inclusive/exclusive) used to page around Wikidata's
        query result limits.
    end_year:
        Overall dataset end year, used as a fallback for ongoing conflicts
        with no recorded end date.
    """
    excluded_roots = " ".join(EXCLUDED_CONFLICT_CLASSES)
    country_values = " ".join(COUNTRY_TYPES)

    # The join order is fixed by hand (optimizer off): resolve the small
    # subclass closure of the conflict class first, then follow P31/P710/P580
    # from it. Left to itself the endpoint's planner follows P279* upwards
    # from every candidate item and times out.
    #
    # The exclusion is a MINUS over a sub-select of the excluded *classes*
    # (a handful of items), not a path from each conflict, for the same
    # reason.
    return f"""
    SELECT DISTINCT ?conflict ?country ?startDate ?endDate WHERE {{
        hint:Query hint:optimizer "None" .

        ?conflictClass wdt:P279* {CONFLICT_CLASS} .
        ?conflict wdt:P31 ?conflictClass ;
                wdt:P710 ?country ;
                wdt:P580 ?startDate .

        OPTIONAL {{ ?conflict wdt:P582 ?endDate . }}

        BIND(COALESCE(?endDate, "{end_year}-12-31T23:59:59Z"^^xsd:dateTime) as ?effEndDate)
        FILTER(?startDate < "{chunk_end}-01-01T00:00:00Z"^^xsd:dateTime && ?effEndDate >= "{chunk_start}-01-01T00:00:00Z"^^xsd:dateTime)

        VALUES ?countryType {{ {country_values} }}
        ?countryClass wdt:P279* ?countryType .
        ?country wdt:P31 ?countryClass .

        MINUS {{
            {{ SELECT DISTINCT ?excludedClass WHERE {{
                VALUES ?excludedRoot {{ {excluded_roots} }}
                ?excludedClass wdt:P279* ?excludedRoot .
            }} }}
            ?conflict wdt:P31 ?excludedClass .
        }}
    }}
    ORDER BY ?conflict ?country ?startDate
    LIMIT {QUERY_LIMIT}
    """


def build_entity_query(qids: Iterable[str], with_lifespan: bool = True) -> str:
    """Build the query fetching English labels (and lifespans) for ``qids``.

    Parameters
    ----------
    qids:
        Bare Wikidata identifiers (``"Q142"``), not full URIs.
    with_lifespan:
        When ``True`` also fetch inception (``P571``), dissolution (``P576``),
        end time (``P582``) and ISO 3166-1 alpha-3 code (``P298``). Used for
        countries; conflicts only need a label.
    """
    values = " ".join(f"wd:{qid}" for qid in qids)
    lifespan_vars = " ?inception ?dissolved ?endTime ?iso3" if with_lifespan else ""
    lifespan_patterns = (
        """
        OPTIONAL { ?item wdt:P571 ?inception . }
        OPTIONAL { ?item wdt:P576 ?dissolved . }
        OPTIONAL { ?item wdt:P582 ?endTime . }
        OPTIONAL { ?item wdt:P298 ?iso3 . }"""
        if with_lifespan
        else ""
    )
    return f"""
    SELECT ?item ?itemLabel{lifespan_vars} WHERE {{
        VALUES ?item {{ {values} }}
        OPTIONAL {{ ?item rdfs:label ?itemLabel . FILTER(LANG(?itemLabel) = "en") }}{lifespan_patterns}
    }}
    """
