from histwardb.query import build_sparql_query


def test_build_sparql_query_contains_temporal_bounds():
    q = build_sparql_query(chunk_start=1900, chunk_end=1925, end_year=2026)
    assert "1925-01-01T00:00:00Z" in q
    assert "1900-01-01T00:00:00Z" in q
    assert "2026-12-31T23:59:59Z" in q


def test_build_sparql_query_excludes_noise_classes():
    q = build_sparql_query(1900, 1925, 2026)
    assert "wd:Q35349" in q  # peacekeeping missions
    assert "wd:Q1151430" in q  # military exercises


def test_build_sparql_query_is_valid_looking_sparql():
    q = build_sparql_query(1900, 1925, 2026)
    assert q.strip().startswith("SELECT DISTINCT")
    assert q.count("{") == q.count("}")
