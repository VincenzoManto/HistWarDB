import pytest

from histwardb import HistWarDB


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
