"""Parse every generated query with a real SPARQL 1.1 parser.

Catches malformed paths, undeclared prefixes and unbalanced groups without
network access. Wikidata's prefixes are predeclared on the endpoint, so they
are declared here the same way.
"""

import pytest

from histwardb.query import (
    build_coalition_query,
    build_entity_query,
    build_participant_detail_query,
    build_sparql_query,
)

prepare_query = pytest.importorskip("rdflib.plugins.sparql").prepareQuery

WIKIDATA_PREFIXES = {
    "wd": "http://www.wikidata.org/entity/",
    "wdt": "http://www.wikidata.org/prop/direct/",
    "p": "http://www.wikidata.org/prop/",
    "ps": "http://www.wikidata.org/prop/statement/",
    "pq": "http://www.wikidata.org/prop/qualifier/",
    "wikibase": "http://wikiba.se/ontology#",
    "hint": "http://www.bigdata.com/queryHints#",
    "bd": "http://www.bigdata.com/rdf#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

QUERIES = {
    "participation": build_sparql_query(1900, 1925, 2026),
    "entity_country": build_entity_query(["Q142", "Q145"]),
    "entity_conflict": build_entity_query(["Q362"], with_lifespan=False, with_casualties=True, with_structure=True),
    "participant_detail": build_participant_detail_query(["Q362", "Q361"]),
    "coalition": build_coalition_query(["Q362"]),
}


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_query_parses(name):
    prepare_query(QUERIES[name], initNs=WIKIDATA_PREFIXES)


def test_old_typo_is_syntactically_valid():
    # A parser alone would not have caught the bug in #2: `wdt:279` is a
    # legal prefixed name. That is why the label and canonical-war checks in
    # test_live.py exist as well.
    broken = QUERIES["participation"].replace("wdt:P279*", "wdt:279*")
    prepare_query(broken, initNs=WIKIDATA_PREFIXES)
