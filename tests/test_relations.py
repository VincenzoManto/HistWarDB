import pandas as pd

from histwardb.relations import (
    SERIES_OF_WARS,
    assign_sides,
    build_dyads,
    coalition_participations,
    missing_ancestors,
    parse_coalitions,
    parse_participant_details,
    parse_structure,
    resolve_roots,
    summarize_conflicts,
)

ENTITY = "http://www.wikidata.org/entity/"


def _node(label, parents=(), is_war=True, type_qids=()):
    return {
        "label": label,
        "types": [],
        "type_qids": list(type_qids),
        "parents": list(parents),
        "is_war": is_war,
        "start": None,
        "end": None,
        "casualties": None,
    }


# -- structure -------------------------------------------------------------------


def test_parse_structure_collects_types_parents_and_flags(sample_entities):
    structure = parse_structure(sample_entities["Q362"] + sample_entities["Q900"])
    wwii = structure["Q362"]
    assert wwii["types"] == ["world war"]
    assert wwii["type_qids"] == ["Q103495"]
    assert wwii["is_war"] is True
    assert (wwii["start"], wwii["end"]) == (1939, 1945)
    assert wwii["casualties"] == 85_000_000
    assert structure["Q900"]["parents"] == ["Q362"]


def test_missing_ancestors_lists_unfetched_parents():
    structure = {"Q1": _node("A", parents=["Q2", "Q3"]), "Q2": _node("B")}
    assert missing_ancestors(structure) == ["Q3"]


def test_resolve_roots_follows_chain_to_topmost_war():
    structure = {
        "Q1": _node("Front", parents=["Q2"]),
        "Q2": _node("Theatre", parents=["Q3"], is_war=False),  # not a war: walked through
        "Q3": _node("World War", parents=[]),
    }
    roots = resolve_roots(structure, ["Q1", "Q3"])
    assert roots["Q1"] == {"parent": "Q3", "root": "Q3"}
    assert roots["Q3"] == {"parent": None, "root": "Q3"}


def test_resolve_roots_nearest_parent_and_top_root():
    structure = {
        "Q1": _node("Battle-war", parents=["Q2"]),
        "Q2": _node("Campaign war", parents=["Q3"]),
        "Q3": _node("Big war"),
    }
    assert resolve_roots(structure, ["Q1"])["Q1"] == {"parent": "Q2", "root": "Q3"}


def test_resolve_roots_never_uses_series_of_wars():
    structure = {
        "Q1": _node("Eastern Front", parents=["Q2", "Q3"]),
        "Q2": _node("German-Russian wars", type_qids=[SERIES_OF_WARS]),
        "Q3": _node("World War II"),
    }
    assert resolve_roots(structure, ["Q1"])["Q1"]["root"] == "Q3"


def test_resolve_roots_survives_cycles():
    structure = {"Q1": _node("A", parents=["Q2"]), "Q2": _node("B", parents=["Q1"])}
    roots = resolve_roots(structure, ["Q1", "Q2"])
    assert roots["Q1"]["parent"] == "Q2"
    assert roots["Q2"]["parent"] == "Q1"


def test_resolve_roots_prefers_candidates_present_in_dataset():
    structure = {"Q1": _node("A", parents=["Q20", "Q30"]), "Q20": _node("B"), "Q30": _node("C")}
    assert resolve_roots(structure, ["Q1", "Q30"])["Q1"]["root"] == "Q30"


# -- participant qualifiers ------------------------------------------------------


def test_parse_participant_details(sample_details):
    details = parse_participant_details(sample_details)
    assert details[("Q362", "Q145")] == {"roles": ["defender"], "start": 1940, "end": None}


# -- coalitions and sides ----------------------------------------------------------


def test_parse_coalitions_deduplicates(sample_coalitions):
    rows = parse_coalitions(sample_coalitions + sample_coalitions[:1])
    assert len(rows) == 3
    assert rows[0]["coalition_label"] in {"Allies of World War II", "Axis Powers"}


def test_assign_sides_marks_states_in_several_coalitions_ambiguous():
    rows = [
        {"conflict": "Q1", "coalition": "Q10", "coalition_label": "Axis", "member": "Q38"},
        {"conflict": "Q1", "coalition": "Q11", "coalition_label": "Allies", "member": "Q38"},
        {"conflict": "Q1", "coalition": "Q11", "coalition_label": "Allies", "member": "Q142"},
    ]
    sides = assign_sides(rows)
    assert sides[("Q1", "Q142")] == {"side_qid": "Q11", "side": "Allies", "side_ambiguous": False}
    assert sides[("Q1", "Q38")]["side_ambiguous"] is True
    assert sides[("Q1", "Q38")]["side_qid"] == "Q10; Q11"


def test_coalition_participations_adds_only_new_members(sample_coalitions, sample_participations, sample_entities):
    structure = parse_structure(sample_entities["Q362"])
    extra = coalition_participations(parse_coalitions(sample_coalitions), structure, sample_participations)
    assert len(extra) == 1  # France and the UK are already direct participants
    assert extra[0]["country"]["value"] == ENTITY + "Q7318"
    assert extra[0]["startDate"]["value"].startswith("1939")
    assert extra[0]["endDate"]["value"].startswith("1945")


def test_coalition_participations_falls_back_to_participation_dates(sample_participations):
    rows = [{"conflict": "Q362", "coalition": "Q1", "coalition_label": "X", "member": "Q999"}]
    extra = coalition_participations(rows, {}, sample_participations)
    assert extra[0]["startDate"]["value"].startswith("1939")


# -- dyads and conflict summary -----------------------------------------------------


def _part(country, qid, side_qid, side, start=1939, end=1945, conflict="Q362"):
    return {
        "conflict_qid": conflict,
        "conflict_name": "World War II",
        "country": country,
        "country_qid": qid,
        "side_qid": side_qid,
        "side": side,
        "start_year": start,
        "end_year": end,
    }


def test_build_dyads_pairs_opposing_sides_with_overlap():
    records = pd.DataFrame(
        [
            _part("France", "Q142", "Q11", "Allies", 1939, 1940),
            _part("United Kingdom", "Q145", "Q11", "Allies"),
            _part("Nazi Germany", "Q7318", "Q10", "Axis"),
            _part("Japan", "Q17", "Q10", "Axis", 1941, 1945),
            _part("Switcher", "Q38", "Q10; Q11", "Axis; Allies"),
            _part("Unknown", "Q1", None, None),
        ]
    )
    dyads = build_dyads(records)
    pairs = {(r.country_a_qid, r.country_b_qid): (r.start_year, r.end_year) for r in dyads.itertuples()}
    assert pairs == {
        ("Q142", "Q7318"): (1939, 1940),
        ("Q145", "Q17"): (1941, 1945),
        ("Q145", "Q7318"): (1939, 1945),
    }
    # France left in 1940, Japan joined in 1941: no overlap, no dyad


def test_build_dyads_empty_without_sides():
    assert build_dyads(pd.DataFrame([_part("France", "Q142", None, None)])).empty


def test_summarize_conflicts():
    records = pd.DataFrame(
        [
            dict(
                _part("France", "Q142", "Q11", "Allies"),
                conflict_type="world war",
                parent_conflict_qid=None,
                root_conflict_qid="Q362",
                war_start_year=1939,
                war_end_year=1945,
                duration_estimated=False,
                casualties=85e6,
            ),
            dict(
                _part("Narnia", "Q1", None, None),
                conflict_type="world war",
                parent_conflict_qid=None,
                root_conflict_qid="Q362",
                war_start_year=1939,
                war_end_year=1945,
                duration_estimated=False,
                casualties=85e6,
            ),
        ]
    )
    row = summarize_conflicts(records).iloc[0]
    assert (row["n_states"], row["n_states_with_side"]) == (2, 1)
    assert row["conflict_type"] == "world war"
