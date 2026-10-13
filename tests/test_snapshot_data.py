"""Integrity checks for the frozen snapshots shipped under data/snapshots/."""

import json
from pathlib import Path

import pytest

from histwardb import HistWarDB

SNAPSHOT_ROOT = Path(__file__).resolve().parent.parent / "data" / "snapshots"
SNAPSHOTS = sorted(p for p in SNAPSHOT_ROOT.iterdir() if p.is_dir()) if SNAPSHOT_ROOT.is_dir() else []


@pytest.mark.parametrize("snapshot", SNAPSHOTS, ids=lambda p: p.name)
def test_shipped_snapshot_loads_and_matches_its_metadata(snapshot):
    metadata = json.loads((snapshot / "metadata.json").read_text(encoding="utf-8"))
    db = HistWarDB.from_snapshot(snapshot)  # verifies the checksum

    parts = db.participations
    assert len(parts) == metadata["n_participations"]
    assert parts["conflict_qid"].nunique() == metadata["n_conflicts"]
    assert parts["country_qid"].nunique() == metadata["n_countries"]
    assert metadata["failed_chunks"] == []
    assert metadata["retrieved_at"]


@pytest.mark.parametrize("snapshot", SNAPSHOTS, ids=lambda p: p.name)
def test_shipped_snapshot_invariants(snapshot):
    db = HistWarDB.from_snapshot(snapshot)
    parts = db.participations
    grid = db.df_long

    # identifiers are present and unique per label
    assert parts["country_qid"].str.fullmatch(r"Q\d+").all()
    assert parts["conflict_qid"].str.fullmatch(r"Q\d+").all()
    assert parts.groupby("country")["country_qid"].nunique().max() == 1
    assert not parts.duplicated(["conflict_qid", "country_qid"]).any()

    # no state is at war outside its own lifespan
    assert not ((grid["State_Exists"] == 0) & (grid["War_State"] == 1)).any()
    assert (parts["start_year"] <= parts["end_year"]).all()

    # wars typed as subclasses of "war" are present
    assert {"World War I", "World War II", "Vietnam War", "American Civil War"} <= set(parts["conflict_name"])
