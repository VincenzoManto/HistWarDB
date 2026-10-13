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


class FakeSession:
    """Answers participation queries and entity queries from canned data.

    ``failures`` is a list of exceptions/responses served (once each, in
    order) before the session starts answering normally.
    """

    def __init__(self, participations, entities, failures=()):
        self.participations = participations
        self.entities = entities
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
        if "VALUES ?item" in query:
            qids = re.findall(r"wd:(Q\d+)", query)
            return FakeResponse([b for qid in qids for b in self.entities.get(qid, [])])
        return FakeResponse(self.participations)

    @property
    def chunk_calls(self):
        return [q for q in self.calls if "VALUES ?item" not in q]

    @property
    def entity_calls(self):
        return [q for q in self.calls if "VALUES ?item" in q]


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
    # one label query for conflicts, one label+lifespan query for countries
    assert len(session.entity_calls) == 2
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
    # 3 failed attempts + 1 good chunk + 2 entity queries
    assert len(session.calls) == 6


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
