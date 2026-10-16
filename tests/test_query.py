from histwardb.query import build_sparql_query


def test_build_sparql_query_contains_temporal_bounds():
    q = build_sparql_query(chunk_start=1900, chunk_end=1925, end_year=2026)
    assert "1925-01-01T00:00:00Z" in q
    assert "1900-01-01T00:00:00Z" in q
    assert "2026-12-31T23:59:59Z" in q


def test_build_sparql_query_excludes_noise_classes():
    q = build_sparql_query(1900, 1925, 2026)
    assert "wd:Q20082270" in q  # peacekeeping mission
    assert "wd:Q357104" in q  # military exercise
    assert "wd:Q43229" in q  # organization
    assert "wd:Q1194951" in q  # national sports team


def test_build_sparql_query_uses_subclass_path():
    q = build_sparql_query(1900, 1925, 2026)
    assert "wdt:279*" not in q
    assert q.count("wdt:P31/wdt:P279*") == 7


def test_build_sparql_query_is_valid_looking_sparql():
    q = build_sparql_query(1900, 1925, 2026)
    assert q.strip().startswith("SELECT DISTINCT")
    assert q.count("{") == q.count("}")


def test_build_sparql_query_fetches_date_precision():
    q = build_sparql_query(1900, 1925, 2026)
    assert "psv:P580" in q
    assert "psv:P582" in q
    assert "wikibase:timePrecision" in q
    assert "?startDatePrecision" in q
    assert "?endDatePrecision" in q
