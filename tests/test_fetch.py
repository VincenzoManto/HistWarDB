"""Tests for the network layer, using a fake requests session (no live calls)."""

import re

import pytest
import requests

from histwardb import HistWarDB
from histwardb import core as core_module
from histwardb.core import FetchError

# No real waiting in tests.
FAST = {"retry_backoff": 0, "request_pause": 0}


class FakeResponse:
    def __init__(self, bindings, status_code=200, broken_json=False):
        self._bindings = bindings
        self.status_code = status_code
        self._broken_json = broken_json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self._broken_json:
            raise ValueError("Expecting ',' delimiter")
        return {"results": {"bindings": self._bindings}}


def query_kind(query):
    if "VALUES ?item" in query:
        return "entity"
    if "p:P710" in query:
        return "details"
    if "?coalition" in query:
        return "coalitions"
    return "chunk"


class FakeSession:
    """Answers every HistWarDB query kind from canned data.

    ``failures`` is a list of exceptions/responses served (once each, in
    order) before the session starts answering normally.
    """

    def __init__(self, participations, entities, failures=(), details=(), coalitions=()):
        self.participations = participations
        self.entities = entities
        self.details = list(details)
        self.coalitions = list(coalitions)
        self.failures = list(failures)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        query = params["query"]
        self.calls.append(query)
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        kind = query_kind(query)
        qids = set(re.findall(r"wd:(Q\d+)", query))
        if kind == "entity":
            return FakeResponse([b for qid in sorted(qids) for b in self.entities.get(qid, [])])
        if kind in ("details", "coalitions"):
            rows = self.details if kind == "details" else self.coalitions
            return FakeResponse([b for b in rows if b["conflict"]["value"].rsplit("/", 1)[-1] in qids])
        return FakeResponse(self.participations)

    def calls_of(self, kind):
        return [q for q in self.calls if query_kind(q) == kind]

    @property
    def chunk_calls(self):
        return self.calls_of("chunk")

    @property
    def entity_calls(self):
        return self.calls_of("entity")


class FailingSession:
    def __init__(self):
        self.calls = 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        raise requests.ConnectionError("simulated network failure")


def test_fetch_data_uses_injected_session(sample_participations, sample_entities):
    session = FakeSession(sample_participations, sample_entities)
    db = HistWarDB(start_year=1925, end_year=1975, chunk_size=25, session=session, **FAST)
    assert db.df_long is not None
    assert not db.df_long.empty
    # one request per 25-year chunk in [1925, 1975)
    assert len(session.chunk_calls) == 2
    # one structure query for conflicts, one label+lifespan query for countries
    assert len(session.entity_calls) == 2
    # one qualifier query and one coalition query
    assert len(session.calls_of("details")) == 1
    assert len(session.calls_of("coalitions")) == 1
    assert db.failed_chunks == []
    assert db.retrieved_at is not None


def test_fetch_data_joins_labels_qids_and_iso_codes(sample_participations, sample_entities):
    session = FakeSession(sample_participations, sample_entities)
    db = HistWarDB(start_year=1925, end_year=1975, session=session, **FAST)
    france = db.participations.query("country == 'France'").iloc[0]
    assert france["country_qid"] == "Q142"
    assert france["iso3"] == "FRA"
    assert france["conflict_qid"] == "Q362"
    assert france["conflict_name"] == "World War II"


def test_fetch_data_applies_casualty_threshold(sample_participations, sample_entities):
    session = FakeSession(sample_participations, sample_entities)
    db = HistWarDB(start_year=1925, end_year=1975, session=session, min_casualties=1000, **FAST)
    # the conflict query asks for death tolls; the country query does not
    conflict_query, country_query = session.entity_calls
    assert "wdt:P1120" in conflict_query
    assert "wdt:P1120" not in country_query
    # "Undated War" has a recorded toll of 300 and is dropped
    assert set(db.participations["conflict_name"]) == {"World War II"}
    assert db.participations["casualties"].max() == 85_000_000


@pytest.fixture
def enriched_session(sample_participations, sample_entities, sample_details, sample_coalitions):
    return FakeSession(sample_participations, sample_entities, details=sample_details, coalitions=sample_coalitions)


def test_coalition_members_become_participants_with_sides(enriched_session):
    db = HistWarDB(start_year=1925, end_year=1975, session=enriched_session, **FAST)
    parts = db.participations.set_index("country")
    assert parts.loc["Nazi Germany", "side"] == "Axis Powers"
    assert bool(parts.loc["Nazi Germany", "via_coalition"])
    assert parts.loc["France", "side"] == "Allies of World War II"
    assert not bool(parts.loc["France", "via_coalition"])
    # Nazi Germany is clipped to its own lifespan's end (1945) and the war's dates
    assert (parts.loc["Nazi Germany", "start_year"], parts.loc["Nazi Germany", "end_year"]) == (1939, 1945)


def test_participant_qualifiers_set_role_and_clip_dates(enriched_session):
    db = HistWarDB(start_year=1925, end_year=1975, session=enriched_session, **FAST)
    uk = db.participations.set_index("country").loc["United Kingdom"]
    assert uk["role"] == "defender"
    assert uk["start_year"] == 1940
    assert uk["war_start_year"] == 1939


def test_conflict_structure_is_recorded(enriched_session):
    db = HistWarDB(start_year=1925, end_year=1975, session=enriched_session, **FAST)
    conflicts = db.conflicts.set_index("conflict_qid")
    assert conflicts.loc["Q362", "conflict_type"] == "world war"
    assert conflicts.loc["Q900", "parent_conflict_qid"] == "Q362"
    assert conflicts.loc["Q900", "root_conflict_qid"] == "Q362"
    assert conflicts.loc["Q362", "n_states_with_side"] == 3


def test_dyads_from_coalitions(enriched_session):
    db = HistWarDB(start_year=1925, end_year=1975, session=enriched_session, **FAST)
    pairs = {(r.country_a, r.country_b, r.start_year) for r in db.dyads.itertuples()}
    assert pairs == {("France", "Nazi Germany", 1939), ("United Kingdom", "Nazi Germany", 1940)}


def test_subconflicts_roll_up_and_drop(enriched_session, sample_participations, sample_entities):
    rolled = HistWarDB(start_year=1925, end_year=1975, session=enriched_session, subconflicts="roll_up", **FAST)
    short = rolled.participations.set_index("country").loc["Short Republic"]
    assert short["conflict_qid"] == "Q362"
    assert short["conflict_name"] == "World War II"

    dropped = HistWarDB(
        start_year=1925, end_year=1975, session=FakeSession(sample_participations, sample_entities),
        subconflicts="drop", **FAST
    )
    assert "Q900" not in set(dropped.participations["conflict_qid"])

    with pytest.raises(ValueError):
        HistWarDB(auto_fetch=False, subconflicts="merge")


def test_ancestors_outside_the_dataset_are_fetched(sample_participations, sample_entities):
    # Q362 is now itself part of a war that has no state participants.
    entities = dict(sample_entities)
    entities["Q362"] = [dict(b, parent={"value": "http://www.wikidata.org/entity/Q1000"}) for b in entities["Q362"]]
    entities["Q1000"] = [
        {"item": {"value": "http://www.wikidata.org/entity/Q1000"}, "itemLabel": {"value": "Mega War"},
         "isWar": {"value": "true"}}
    ]
    session = FakeSession(sample_participations, entities)
    db = HistWarDB(start_year=1925, end_year=1975, session=session, **FAST)
    assert db.conflicts.set_index("conflict_qid").loc["Q362", "root_conflict_qid"] == "Q1000"
    # structure for the conflicts, one more round for the missing ancestor, then countries
    assert len(session.entity_calls) == 3


def test_last_chunk_includes_wars_starting_in_end_year(sample_participations, sample_entities):
    session = FakeSession(sample_participations, sample_entities)
    HistWarDB(start_year=1925, end_year=1975, chunk_size=25, session=session, **FAST)
    assert "1976-01-01T00:00:00Z" in session.chunk_calls[-1]
    assert "1976-01-01T00:00:00Z" not in session.chunk_calls[0]


def test_fetch_data_raises_on_network_failure_by_default():
    session = FailingSession()
    with pytest.raises(FetchError):
        HistWarDB(start_year=1900, end_year=1950, session=session, max_retries=2, **FAST)
    # first chunk only: initial attempt + 2 retries, then it stops
    assert session.calls == 3


def test_fetch_data_non_strict_records_failed_chunks():
    with pytest.warns(RuntimeWarning, match="dataset is incomplete"):
        db = HistWarDB(start_year=1900, end_year=1950, session=FailingSession(), strict=False, max_retries=0, **FAST)
    # all chunks fail -> empty but initialized dataframe, and the gap is recorded
    assert db.df_long is not None
    assert db.df_long.empty
    assert db.failed_chunks == [(1900, 1925), (1925, 1950)]


def test_transient_failures_are_retried(sample_participations, sample_entities):
    failures = [
        requests.ConnectionError("dropped"),
        FakeResponse([], status_code=429),
        FakeResponse([], broken_json=True),  # truncated body
    ]
    session = FakeSession(sample_participations, sample_entities, failures=failures)
    db = HistWarDB(start_year=1925, end_year=1950, session=session, max_retries=3, **FAST)
    assert not db.df_long.empty
    assert db.failed_chunks == []
    # 3 failed attempts + 1 good chunk + structure, details, coalitions, countries
    assert len(session.calls) == 8


def test_chunk_hitting_query_limit_is_an_error(monkeypatch, sample_participations, sample_entities):
    monkeypatch.setattr(core_module, "QUERY_LIMIT", len(sample_participations))
    session = FakeSession(sample_participations, sample_entities)
    with pytest.raises(FetchError, match="truncated"):
        HistWarDB(start_year=1925, end_year=1950, session=session, **FAST)


def test_entity_queries_are_batched(monkeypatch, sample_participations, sample_entities):
    monkeypatch.setattr(core_module, "ENTITY_BATCH_SIZE", 2)
    session = FakeSession(sample_participations, sample_entities)
    HistWarDB(start_year=1925, end_year=1950, session=session, **FAST)
    # 2 conflicts -> 1 batch; 3 countries -> 2 batches
    assert len(session.entity_calls) == 3


def test_snapshot_round_trip(tmp_path, sample_participations, sample_entities):
    session = FakeSession(sample_participations, sample_entities)
    db = HistWarDB(start_year=1925, end_year=1975, session=session, **FAST)
    db.save_snapshot(tmp_path)

    restored = HistWarDB.from_snapshot(tmp_path)
    assert restored.retrieved_at == db.retrieved_at
    assert restored.start_year == 1925
    assert restored.end_year == 1975
    assert restored.df_long.equals(db.df_long)


def test_snapshot_checksum_is_verified(tmp_path, sample_participations, sample_entities):
    session = FakeSession(sample_participations, sample_entities)
    db = HistWarDB(start_year=1925, end_year=1975, session=session, **FAST)
    db.save_snapshot(tmp_path)

    data_file = tmp_path / "participations.csv"
    data_file.write_text(data_file.read_text(encoding="utf-8").replace("France", "Freedonia"), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        HistWarDB.from_snapshot(tmp_path)
    assert "Freedonia" in HistWarDB.from_snapshot(tmp_path, verify=False).available_countries


def test_snapshot_checksum_ignores_line_endings(tmp_path, sample_participations, sample_entities):
    session = FakeSession(sample_participations, sample_entities)
    db = HistWarDB(start_year=1925, end_year=1975, session=session, **FAST)
    db.save_snapshot(tmp_path)

    data_file = tmp_path / "participations.csv"
    data_file.write_bytes(data_file.read_bytes().replace(b"\n", b"\r\n"))
    assert HistWarDB.from_snapshot(tmp_path).df_long.equals(db.df_long)
