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
# military exercises are noise for a sovereign-war dataset; hypothetical,
# interstellar and other fictional wars are subclasses of "war" in Wikidata
# and would otherwise be included (e.g. "Space War I"). The broader
# "fictional entity" class is not usable here: the MINUS below would have to
# enumerate every fictional item in Wikidata.
EXCLUDED_CONFLICT_CLASSES = (
    "wd:Q20082270",  # peacekeeping mission
    "wd:Q357104",  # military exercise
    "wd:Q124042044",  # hypothetical war
    "wd:Q2661982",  # interstellar war
    "wd:Q17198419",  # fictional war
)

# Classes of non-state participants whose member states are expanded into
# participants on a common side (e.g. "Allies of World War II"). Members are
# read from ``P527`` (has part) on the coalition and ``P463`` (member of) on
# the state. Restricted to alliances on purpose: states such as the Soviet
# Union also use ``P527`` for their own constituent republics.
COALITION_CLASSES = (
    "wd:Q878249",  # alliance
    "wd:Q1127126",  # military alliance
    "wd:Q100906234",  # multinational military coalition
)

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

# Wikidata property used to fetch a conflict's recorded death toll, so
# callers can filter out low-severity/noise entries (see ``min_casualties``
# on :class:`~histwardb.core.HistWarDB`). Fetched as OPTIONAL in the entity
# query: conflicts without a recorded figure are never dropped purely for
# lacking this data.
CASUALTIES_PROPERTY = "wdt:P1120"

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
    SELECT DISTINCT ?conflict ?country ?startDate ?startDatePrecision ?endDate ?endDatePrecision WHERE {{
        hint:Query hint:optimizer "None" .

        ?conflictClass wdt:P279* {CONFLICT_CLASS} .
        ?conflict wdt:P31 ?conflictClass ;
                wdt:P710 ?country ;
                p:P580/psv:P580 ?startNode .
        ?startNode wikibase:timeValue ?startDate ;
                   wikibase:timePrecision ?startDatePrecision .

        OPTIONAL {{
            ?conflict p:P582/psv:P582 ?endNode .
            ?endNode wikibase:timeValue ?endDate ;
                     wikibase:timePrecision ?endDatePrecision .
        }}

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


def _values(qids: Iterable[str]) -> str:
    return " ".join(f"wd:{qid}" for qid in qids)


def build_entity_query(
    qids: Iterable[str],
    with_lifespan: bool = True,
    with_casualties: bool = False,
    with_structure: bool = False,
) -> str:
    """Build the query fetching English labels (and attributes) for ``qids``.

    Parameters
    ----------
    qids:
        Bare Wikidata identifiers (``"Q142"``), not full URIs.
    with_lifespan:
        When ``True`` also fetch inception (``P571``), dissolution (``P576``),
        end time (``P582``) and ISO 3166-1 alpha-3 code (``P298``). Used for
        countries.
    with_casualties:
        When ``True`` also fetch the recorded death toll
        (:data:`CASUALTIES_PROPERTY`). Used for conflicts.
    with_structure:
        When ``True`` also fetch the item's classes (``P31``, with English
        labels), the items it is part of (``P361``), its start and end time,
        and whether it is itself a war (``isWar``). Used for conflicts and
        for walking up their ``part of`` chain.
    """
    variables = ""
    patterns = ""
    if with_lifespan:
        variables += " ?inception ?dissolved ?endTime ?iso3"
        patterns += """
        OPTIONAL { ?item wdt:P571 ?inception . }
        OPTIONAL { ?item wdt:P576 ?dissolved . }
        OPTIONAL { ?item wdt:P582 ?endTime . }
        OPTIONAL { ?item wdt:P298 ?iso3 . }"""
    if with_casualties:
        variables += " ?casualties"
        patterns += f"""
        OPTIONAL {{ ?item {CASUALTIES_PROPERTY} ?casualties . }}"""
    if with_structure:
        variables += " ?type ?typeLabel ?parent ?warStart ?warEnd ?isWar"
        patterns += f"""
        OPTIONAL {{ ?item wdt:P31 ?type . ?type rdfs:label ?typeLabel . FILTER(LANG(?typeLabel) = "en") }}
        OPTIONAL {{ ?item wdt:P361 ?parent . }}
        OPTIONAL {{ ?item wdt:P580 ?warStart . }}
        OPTIONAL {{ ?item wdt:P582 ?warEnd . }}
        BIND(EXISTS {{ ?item wdt:P31/wdt:P279* {CONFLICT_CLASS} . }} AS ?isWar)"""
    return f"""
    SELECT ?item ?itemLabel{variables} WHERE {{
        VALUES ?item {{ {_values(qids)} }}
        OPTIONAL {{ ?item rdfs:label ?itemLabel . FILTER(LANG(?itemLabel) = "en") }}{patterns}
    }}
    """


def build_participant_detail_query(conflict_qids: Iterable[str]) -> str:
    """Build the query fetching qualifiers on the ``P710`` statements of conflicts.

    Returns one row per qualifier value found: the participant's role
    (``P3831``, with English label) and its own start/end time (``P580`` /
    ``P582`` as qualifiers). Statements without any of these qualifiers
    produce no row.
    """
    return f"""
    SELECT ?conflict ?participant ?roleLabel ?participantStart ?participantEnd WHERE {{
        VALUES ?conflict {{ {_values(conflict_qids)} }}
        ?conflict p:P710 ?statement .
        ?statement ps:P710 ?participant .
        {{ ?statement pq:P3831 ?role . ?role rdfs:label ?roleLabel . FILTER(LANG(?roleLabel) = "en") }}
        UNION {{ ?statement pq:P580 ?participantStart . }}
        UNION {{ ?statement pq:P582 ?participantEnd . }}
    }}
    """


def build_coalition_query(conflict_qids: Iterable[str]) -> str:
    """Build the query expanding coalition participants into their member states.

    A participant counts as a coalition when it is an instance of one of
    :data:`COALITION_CLASSES` (or a subclass). Its members are the states
    linked by ``P527`` (has part) from the coalition or ``P463`` (member of)
    to it, restricted to :data:`COUNTRY_TYPES`.
    """
    coalition_values = " ".join(COALITION_CLASSES)
    country_values = " ".join(COUNTRY_TYPES)
    return f"""
    SELECT DISTINCT ?conflict ?coalition ?coalitionLabel ?member WHERE {{
        VALUES ?conflict {{ {_values(conflict_qids)} }}
        ?conflict wdt:P710 ?coalition .
        FILTER EXISTS {{
            VALUES ?coalitionRoot {{ {coalition_values} }}
            ?coalition wdt:P31/wdt:P279* ?coalitionRoot .
        }}
        {{ ?coalition wdt:P527 ?member . }} UNION {{ ?member wdt:P463 ?coalition . }}
        FILTER EXISTS {{
            VALUES ?memberRoot {{ {country_values} }}
            ?member wdt:P31/wdt:P279* ?memberRoot .
        }}
        OPTIONAL {{ ?coalition rdfs:label ?coalitionLabel . FILTER(LANG(?coalitionLabel) = "en") }}
    }}
    """
