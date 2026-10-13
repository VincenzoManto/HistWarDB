from histwardb.query import QUERY_LIMIT, build_entity_query, build_sparql_query


def test_build_sparql_query_contains_temporal_bounds():
    q = build_sparql_query(chunk_start=1900, chunk_end=1925, end_year=2026)
    assert "1925-01-01T00:00:00Z" in q
    assert "1900-01-01T00:00:00Z" in q
    assert "2026-12-31T23:59:59Z" in q


def test_build_sparql_query_excludes_noise_classes():
    q = build_sparql_query(1900, 1925, 2026)
    assert "wd:Q20082270" in q  # peacekeeping mission
    assert "wd:Q357104" in q  # military exercise


def test_build_sparql_query_does_not_exclude_organizations():
    q = build_sparql_query(1900, 1925, 2026)
    # Regression: sovereign state and historical country are subclasses of
    # organization (Q43229) in Wikidata, so excluding it drops every state.
    assert "wd:Q43229" not in q


def test_build_sparql_query_uses_subclass_path():
    q = build_sparql_query(1900, 1925, 2026)
    # Regression: `wdt:279` (missing "P") is not a Wikidata property, so the
    # `*` path matched zero steps and subclasses of war (world war, civil
    # war, proxy war, ...) were silently dropped.
    assert "wdt:279" not in q
    assert "?conflictClass wdt:P279* wd:Q198" in q
    assert "?conflict wdt:P31 ?conflictClass" in q
    assert "?countryClass wdt:P279* ?countryType" in q
    assert "?excludedClass wdt:P279* ?excludedRoot" in q


def test_build_sparql_query_is_shaped_for_the_public_endpoint():
    q = build_sparql_query(1900, 1925, 2026)
    # A fixed join order and no label service: with either missing, the
    # query times out on query.wikidata.org.
    assert 'hint:Query hint:optimizer "None"' in q
    assert "SERVICE wikibase:label" not in q
    # Deterministic truncation, detectable by the fetch layer.
    assert "ORDER BY ?conflict ?country" in q
    assert f"LIMIT {QUERY_LIMIT}" in q


def test_build_sparql_query_returns_identifiers():
    q = build_sparql_query(1900, 1925, 2026)
    assert "SELECT DISTINCT ?conflict ?country ?startDate ?endDate" in q


def test_build_sparql_query_is_valid_looking_sparql():
    q = build_sparql_query(1900, 1925, 2026)
    assert q.strip().startswith("SELECT DISTINCT")
    assert q.count("{") == q.count("}")


def test_build_entity_query_lists_qids_and_lifespan_properties():
    q = build_entity_query(["Q142", "Q145"])
    assert "VALUES ?item { wd:Q142 wd:Q145 }" in q
    for prop in ("wdt:P571", "wdt:P576", "wdt:P582", "wdt:P298"):
        assert prop in q
    assert q.count("{") == q.count("}")


def test_build_entity_query_labels_only():
    q = build_entity_query(["Q362"], with_lifespan=False)
    assert "rdfs:label" in q
    assert "wdt:P571" not in q
    assert q.count("{") == q.count("}")
