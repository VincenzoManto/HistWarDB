"""Opt-in checks against the live Wikidata endpoint.

Deselected by default (see ``addopts`` in pyproject.toml). Run with::

    pytest -m network

These exist because unit tests on the query *text* cannot tell whether a
QID means what its comment says, or whether the query returns anything.
"""

import pytest
import requests

from histwardb import HistWarDB
from histwardb.core import parse_entities
from histwardb.query import (
    CONFLICT_CLASS,
    COUNTRY_TYPES,
    DEFAULT_USER_AGENT,
    EXCLUDED_CONFLICT_CLASSES,
    WIKIDATA_SPARQL_ENDPOINT,
    build_entity_query,
)

pytestmark = pytest.mark.network

EXPECTED_LABELS = {
    "Q198": "war",
    "Q20082270": "peacekeeping mission",
    "Q357104": "military exercise",
    "Q3624078": "sovereign state",
    "Q3024240": "historical country",
    "Q6256": "country",
}


def test_qid_constants_mean_what_the_code_says():
    used = {q.split(":")[1] for q in (CONFLICT_CLASS, *EXCLUDED_CONFLICT_CLASSES, *COUNTRY_TYPES)}
    assert used == set(EXPECTED_LABELS)

    response = requests.get(
        WIKIDATA_SPARQL_ENDPOINT,
        params={"query": build_entity_query(sorted(used), with_lifespan=False), "format": "json"},
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    labels = {qid: meta["label"] for qid, meta in parse_entities(response.json()["results"]["bindings"]).items()}
    assert labels == EXPECTED_LABELS


def test_canonical_wars_and_states_are_returned():
    db = HistWarDB(start_year=1935, end_year=1955, chunk_size=25)
    assert db.failed_chunks == []

    conflicts = set(db.participations["conflict_name"])
    # typed as "world war" / plain "war": needs the subclass path to work
    assert "World War II" in conflicts
    assert "Korean War" in conflicts

    countries = set(db.participations["country_qid"])
    # ordinary sovereign states must survive the belligerent filters
    assert {"Q145", "Q30"} <= countries  # United Kingdom, United States
    assert len(db.participations) > 50
