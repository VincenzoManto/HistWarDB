import pytest

from histwardb import HistWarDB

ENTITY = "http://www.wikidata.org/entity/"


def _binding(country, conflict, start, end=None, country_start=None, country_end=None):
    b = {
        "countryLabel": {"value": country},
        "conflictLabel": {"value": conflict},
        "startDate": {"value": f"{start}-01-01T00:00:00Z"},
    }
    if end is not None:
        b["endDate"] = {"value": f"{end}-01-01T00:00:00Z"}
    if country_start is not None:
        b["countryStart"] = {"value": f"{country_start}-01-01T00:00:00Z"}
    if country_end is not None:
        b["countryEnd"] = {"value": f"{country_end}-01-01T00:00:00Z"}
    return b


def _participation(conflict_qid, country_qid, start, end=None):
    b = {
        "conflict": {"value": ENTITY + conflict_qid},
        "country": {"value": ENTITY + country_qid},
        "startDate": {"value": f"{start}-01-01T00:00:00Z"},
    }
    if end is not None:
        b["endDate"] = {"value": f"{end}-01-01T00:00:00Z"}
    return b


def _entity(qid, label=None, inception=None, dissolved=None, iso3=None):
    b = {"item": {"value": ENTITY + qid}}
    if label is not None:
        b["itemLabel"] = {"value": label}
    if inception is not None:
        b["inception"] = {"value": f"{inception}-01-01T00:00:00Z"}
    if dissolved is not None:
        b["dissolved"] = {"value": f"{dissolved}-01-01T00:00:00Z"}
    if iso3 is not None:
        b["iso3"] = {"value": iso3}
    return b


@pytest.fixture
def sample_bindings():
    return [
        _binding("France", "Test War One", 1939, 1945),
        _binding("United Kingdom", "Test War One", 1939, 1945),
        _binding("German Empire", "Anachronistic War", 1925),  # starts after 1918 -> forced dead
        _binding("Narnia", "Ongoing Conflict", 2020),  # no end date, recent -> runs to end_year
    ]


@pytest.fixture
def dataset_from_bindings(sample_bindings):
    from histwardb.core import parse_bindings

    records = parse_bindings(sample_bindings, end_year=2026)
    db = HistWarDB(start_year=1900, end_year=2026, auto_fetch=False)
    db.load_records(records)
    return db


@pytest.fixture
def sample_participations():
    """Bindings shaped like the participation query's output."""
    return [
        _participation("Q362", "Q142", 1939, 1945),
        _participation("Q362", "Q145", 1939, 1945),
        _participation("Q900", "Q901", 1930),  # undated end, state dissolved in 1950
    ]


@pytest.fixture
def sample_entities():
    """Bindings shaped like the entity query's output, keyed by QID."""
    return {
        "Q362": [_entity("Q362", "World War II")],
        "Q900": [_entity("Q900", "Undated War")],
        "Q142": [_entity("Q142", "France", iso3="FRA")],
        "Q145": [_entity("Q145", "United Kingdom", inception=1801, iso3="GBR")],
        # Two inception statements: the earliest must win.
        "Q901": [
            _entity("Q901", "Short Republic", inception=1920, dissolved=1950),
            _entity("Q901", "Short Republic", inception=1925, dissolved=1950),
        ],
    }
