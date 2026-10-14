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


def _entity(
    qid,
    label=None,
    inception=None,
    dissolved=None,
    iso3=None,
    casualties=None,
    type_qid=None,
    type_label=None,
    parent=None,
    war_start=None,
    war_end=None,
    is_war=None,
):
    b = {"item": {"value": ENTITY + qid}}
    if type_qid is not None:
        b["type"] = {"value": ENTITY + type_qid}
    if type_label is not None:
        b["typeLabel"] = {"value": type_label}
    if parent is not None:
        b["parent"] = {"value": ENTITY + parent}
    if war_start is not None:
        b["warStart"] = {"value": f"{war_start}-01-01T00:00:00Z"}
    if war_end is not None:
        b["warEnd"] = {"value": f"{war_end}-01-01T00:00:00Z"}
    if is_war is not None:
        b["isWar"] = {"value": "true" if is_war else "false"}
    if label is not None:
        b["itemLabel"] = {"value": label}
    if inception is not None:
        b["inception"] = {"value": f"{inception}-01-01T00:00:00Z"}
    if dissolved is not None:
        b["dissolved"] = {"value": f"{dissolved}-01-01T00:00:00Z"}
    if iso3 is not None:
        b["iso3"] = {"value": iso3}
    if casualties is not None:
        b["casualties"] = {"value": str(casualties)}
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
        # Two death-toll estimates: the largest must win.
        "Q362": [
            _entity("Q362", "World War II", casualties=60000000, type_qid="Q103495", type_label="world war",
                    war_start=1939, war_end=1945, is_war=True),
            _entity("Q362", "World War II", casualties=85000000, type_qid="Q103495", type_label="world war",
                    war_start=1939, war_end=1945, is_war=True),
        ],
        # A war that is part of World War II.
        "Q900": [_entity("Q900", "Undated War", casualties=300, type_qid="Q198", type_label="war",
                         parent="Q362", is_war=True)],
        "Q7318": [_entity("Q7318", "Nazi Germany", inception=1933, dissolved=1945)],
        "Q142": [_entity("Q142", "France", iso3="FRA")],
        "Q145": [_entity("Q145", "United Kingdom", inception=1801, iso3="GBR")],
        # Two inception statements: the earliest must win.
        "Q901": [
            _entity("Q901", "Short Republic", inception=1920, dissolved=1950),
            _entity("Q901", "Short Republic", inception=1925, dissolved=1950),
        ],
    }


def _detail(conflict, participant, role=None, start=None, end=None):
    b = {"conflict": {"value": ENTITY + conflict}, "participant": {"value": ENTITY + participant}}
    if role is not None:
        b["roleLabel"] = {"value": role}
    if start is not None:
        b["participantStart"] = {"value": f"{start}-01-01T00:00:00Z"}
    if end is not None:
        b["participantEnd"] = {"value": f"{end}-01-01T00:00:00Z"}
    return b


def _coalition(conflict, coalition, label, member):
    return {
        "conflict": {"value": ENTITY + conflict},
        "coalition": {"value": ENTITY + coalition},
        "coalitionLabel": {"value": label},
        "member": {"value": ENTITY + member},
    }


@pytest.fixture
def sample_details():
    """Bindings shaped like the participant-detail query: UK joins in 1940 as "defender"."""
    return [_detail("Q362", "Q145", role="defender"), _detail("Q362", "Q145", start=1940)]


@pytest.fixture
def sample_coalitions():
    """Bindings shaped like the coalition query: Allies (France, UK) and Axis (Nazi Germany)."""
    return [
        _coalition("Q362", "Q329888", "Allies of World War II", "Q142"),
        _coalition("Q362", "Q329888", "Allies of World War II", "Q145"),
        _coalition("Q362", "Q43056", "Axis Powers", "Q7318"),
    ]
