import pandas as pd
import pytest

from histwardb.core import (
    build_records,
    clean_dataframe,
    impute_end_year,
    parse_bindings,
    parse_date_parts,
    parse_entities,
    parse_year,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        (123, None),  # not a string
        ("1939-01-01T00:00:00Z", 1939),
        ("-0500-01-01T00:00:00Z", -500),
        ("no digits here", None),
    ],
)
def test_parse_year(value, expected):
    assert parse_year(value) == expected


@pytest.mark.parametrize(
    "value,precision,expected",
    [
        ("1939-09-01T00:00:00Z", 11, (1939, 9, 1)),  # day precision
        ("1939-09-01T00:00:00Z", 10, (1939, 9, None)),  # month precision
        ("1939-01-01T00:00:00Z", 9, (1939, None, None)),  # year precision
        ("1939-01-01T00:00:00Z", 7, (1939, None, None)),  # century: still year-only
        ("1939-01-01T00:00:00Z", None, (1939, 1, 1)),  # no precision info -> assume day
        (None, 11, (None, None, None)),
    ],
)
def test_parse_date_parts(value, precision, expected):
    assert parse_date_parts(value, precision) == expected


def test_parse_bindings_captures_month_and_day_precision():
    binding = {
        "countryLabel": {"value": "France"},
        "conflictLabel": {"value": "Precise War"},
        "startDate": {"value": "1939-09-01T00:00:00Z"},
        "startDatePrecision": {"value": "11"},
        "endDate": {"value": "1945-05-08T00:00:00Z"},
        "endDatePrecision": {"value": "11"},
    }
    record = parse_bindings([binding], end_year=2026)[0]
    assert (record["start_month"], record["start_day"]) == (9, 1)
    assert (record["end_month"], record["end_day"]) == (5, 8)


def test_parse_bindings_year_precision_leaves_month_day_none():
    binding = {
        "countryLabel": {"value": "Narnia"},
        "conflictLabel": {"value": "Imprecise War"},
        "startDate": {"value": "1950-01-01T00:00:00Z"},
        "startDatePrecision": {"value": "9"},
    }
    record = parse_bindings([binding], end_year=2026)[0]
    assert record["start_month"] is None
    assert record["start_day"] is None


def test_parse_bindings_fills_missing_end_date_recent(sample_bindings):
    records = parse_bindings(sample_bindings, end_year=2026)
    narnia = next(r for r in records if r["country"] == "Narnia")
    assert narnia["start_year"] == 2020
    assert narnia["end_year"] == 2026  # no end date, recent conflict -> runs to dataset end
    assert narnia["end_month"] is None  # estimated end -> no fabricated month/day


def test_parse_bindings_skips_entries_without_start_date():
    bindings = [
        {
            "countryLabel": {"value": "Atlantis"},
            "conflictLabel": {"value": "Mystery War"},
            "startDate": {"value": "not-a-date"},
        }
    ]
    # parse_year will return None for "not-a-date" has no digits -> skipped
    records = parse_bindings(bindings, end_year=2026)
    assert records == []


def test_clean_dataframe_drops_forced_dead_states():
    df_raw = pd.DataFrame(
        [
            {
                "country": "German Empire",
                "conflict_name": "Anachronistic War",
                "start_year": 1925,
                "end_year": 1925,
                "country_start": None,
                "country_end": None,
            },
            {
                "country": "France",
                "conflict_name": "Real War",
                "start_year": 1940,
                "end_year": 1944,
                "country_start": None,
                "country_end": None,
            },
        ]
    )
    cleaned = clean_dataframe(df_raw, start_year=1900, end_year=2026)
    assert "German Empire" not in cleaned["country"].values
    assert "France" in cleaned["country"].values


def test_clean_dataframe_enforces_country_lifespan():
    df_raw = pd.DataFrame(
        [
            {
                "country": "Short-Lived Republic",
                "conflict_name": "Post-dissolution War",
                "start_year": 2000,
                "end_year": 2001,
                "country_start": 1990,
                "country_end": 1995,  # dissolved before the war started
            }
        ]
    )
    cleaned = clean_dataframe(df_raw, start_year=1900, end_year=2026)
    assert cleaned.empty


def test_clean_dataframe_drops_wikidata_qid_placeholders():
    df_raw = pd.DataFrame(
        [
            {
                "country": "Q12345",
                "conflict_name": "Unlabeled War",
                "start_year": 1950,
                "end_year": 1951,
                "country_start": None,
                "country_end": None,
            }
        ]
    )
    cleaned = clean_dataframe(df_raw, start_year=1900, end_year=2026)
    assert cleaned.empty


def test_clean_dataframe_out_of_range_is_discarded():
    df_raw = pd.DataFrame(
        [
            {
                "country": "Old Kingdom",
                "conflict_name": "Ancient War",
                "start_year": 1600,
                "end_year": 1605,
                "country_start": None,
                "country_end": None,
            }
        ]
    )
    cleaned = clean_dataframe(df_raw, start_year=1900, end_year=2026)
    assert cleaned.empty


# -- missing end dates ---------------------------------------------------------


@pytest.mark.parametrize(
    "policy,start,expected",
    [
        ("heuristic", 1850, 2026),
        ("heuristic", 1750, 1755),
        ("open", 1750, 2026),
        ("start_only", 1850, 1850),
        ("drop", 1850, None),
    ],
)
def test_impute_end_year_policies(policy, start, expected):
    assert impute_end_year(start, 2026, policy) == expected


def test_impute_end_year_heuristic_is_configurable():
    assert impute_end_year(1850, 2026, "heuristic", heuristic_cutoff_year=1900, pre_cutoff_duration_years=2) == 1852
    assert impute_end_year(1950, 2026, "heuristic", heuristic_cutoff_year=1900, pre_cutoff_duration_years=2) == 2026


def test_parse_bindings_passes_heuristic_parameters(sample_bindings):
    records = parse_bindings(sample_bindings, end_year=2026, heuristic_cutoff_year=2100, pre_cutoff_default_duration_years=3)
    narnia = next(r for r in records if r["country"] == "Narnia")
    assert narnia["end_year"] == 2023


def test_parse_bindings_reads_casualties():
    binding = {
        "countryLabel": {"value": "France"},
        "conflictLabel": {"value": "Big War"},
        "startDate": {"value": "1914-01-01T00:00:00Z"},
        "casualties": {"value": "1500000"},
    }
    bad = dict(binding, casualties={"value": "unknown"})
    assert parse_bindings([binding], end_year=2026)[0]["casualties"] == 1_500_000
    assert parse_bindings([bad], end_year=2026)[0]["casualties"] is None


def test_impute_end_year_rejects_unknown_policy():
    with pytest.raises(ValueError):
        impute_end_year(1850, 2026, "guess")


def test_parse_bindings_flags_imputed_end_dates(sample_bindings):
    records = parse_bindings(sample_bindings, end_year=2026)
    by_country = {r["country"]: r for r in records}
    assert by_country["Narnia"]["duration_estimated"] is True
    assert by_country["France"]["duration_estimated"] is False


def test_parse_bindings_drop_policy_discards_undated_conflicts(sample_bindings):
    records = parse_bindings(sample_bindings, end_year=2026, missing_end="drop")
    assert {r["country"] for r in records} == {"France", "United Kingdom"}


# -- entity metadata -------------------------------------------------------------


def test_parse_entities_reduces_multiple_values(sample_entities):
    entities = parse_entities([b for bindings in sample_entities.values() for b in bindings])
    assert entities["Q901"] == {"label": "Short Republic", "start": 1920, "end": 1950, "iso3": None, "casualties": None}
    assert entities["Q142"]["iso3"] == "FRA"


def test_parse_entities_keeps_largest_casualty_estimate(sample_entities):
    entities = parse_entities([b for bindings in sample_entities.values() for b in bindings])
    assert entities["Q362"]["casualties"] == 85_000_000
    assert entities["Q142"]["casualties"] is None


def test_clean_dataframe_min_casualties_keeps_unknown_tolls():
    df_raw = pd.DataFrame(
        [
            _record("France", "Skirmish", 1900, 1900, casualties=12),
            _record("France", "Big War", 1914, 1918, casualties=1_500_000),
            _record("France", "Undocumented War", 1920, 1921),
        ]
    )
    cleaned = clean_dataframe(df_raw, start_year=1800, end_year=2026, min_casualties=1000)
    assert set(cleaned["conflict_name"]) == {"Big War", "Undocumented War"}
    # no threshold: nothing is filtered on severity
    assert len(clean_dataframe(df_raw, start_year=1800, end_year=2026)) == 3


def test_parse_entities_uses_qid_when_label_is_missing():
    entities = parse_entities([{"item": {"value": "http://www.wikidata.org/entity/Q42"}}])
    assert entities["Q42"]["label"] == "Q42"


def test_parse_entities_end_time_is_only_a_fallback_for_dissolution():
    item = {"value": "http://www.wikidata.org/entity/Q1"}
    with_both = [
        {"item": item, "dissolved": {"value": "1918-11-09T00:00:00Z"}, "endTime": {"value": "1933-01-01T00:00:00Z"}}
    ]
    only_end_time = [{"item": item, "endTime": {"value": "1933-01-01T00:00:00Z"}}]
    assert parse_entities(with_both)["Q1"]["end"] == 1918
    assert parse_entities(only_end_time)["Q1"]["end"] == 1933


def test_build_records_joins_metadata(sample_participations, sample_entities):
    entities = parse_entities([b for bindings in sample_entities.values() for b in bindings])
    records = build_records(sample_participations, entities, entities, end_year=1975)
    undated = next(r for r in records if r["conflict_qid"] == "Q900")
    assert undated["country"] == "Short Republic"
    assert undated["country_start"] == 1920
    assert undated["country_end"] == 1950
    assert undated["duration_estimated"] is True


# -- lifespan clipping -----------------------------------------------------------


def _record(country, conflict, start, end, country_start=None, country_end=None, **extra):
    return {
        "country": country,
        "conflict_name": conflict,
        "start_year": start,
        "end_year": end,
        "country_start": country_start,
        "country_end": country_end,
        **extra,
    }


def test_clean_dataframe_clips_war_to_state_lifespan():
    df_raw = pd.DataFrame(
        [
            # state founded during the war
            _record("Late Kingdom", "Long War", 1700, 1721, country_start=1707),
            # state dissolved during the war
            _record("Doomed Empire", "Long War", 1700, 1721, country_end=1712),
        ]
    )
    cleaned = clean_dataframe(df_raw, start_year=1600, end_year=2026).set_index("country")
    assert (cleaned.loc["Late Kingdom", "start_year"], cleaned.loc["Late Kingdom", "end_year"]) == (1707, 1721)
    assert (cleaned.loc["Doomed Empire", "start_year"], cleaned.loc["Doomed Empire", "end_year"]) == (1700, 1712)
    # the conflict's own dates are preserved
    assert cleaned.loc["Late Kingdom", "war_start_year"] == 1700
    assert cleaned.loc["Doomed Empire", "war_end_year"] == 1721


def test_clean_dataframe_forced_max_year_clips_instead_of_dropping():
    df_raw = pd.DataFrame([_record("Qing dynasty", "Sino-Japanese Wars", 1894, 1945)])
    cleaned = clean_dataframe(df_raw, start_year=1800, end_year=2026)
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["end_year"] == 1912


def test_clean_dataframe_uses_one_lifespan_per_country():
    # Wikidata returned two inception dates: every row of the country must
    # be judged against the same (earliest) one.
    df_raw = pd.DataFrame(
        [
            _record("Twice Founded", "Early War", 1805, 1806, country_start=1800, country_qid="Q1"),
            _record("Twice Founded", "Early War", 1805, 1806, country_start=1810, country_qid="Q1"),
        ]
    )
    cleaned = clean_dataframe(df_raw, start_year=1700, end_year=2026)
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["country_start"] == 1800
    assert cleaned.iloc[0]["start_year"] == 1805


def test_clean_dataframe_recorded_end_beats_imputed_end():
    df_raw = pd.DataFrame(
        [
            _record("France", "Some War", 1900, 2026, duration_estimated=True, country_qid="Q142", conflict_qid="Q7"),
            _record("France", "Some War", 1900, 1902, duration_estimated=False, country_qid="Q142", conflict_qid="Q7"),
        ]
    )
    cleaned = clean_dataframe(df_raw, start_year=1800, end_year=2026)
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["end_year"] == 1902
    assert not cleaned.iloc[0]["duration_estimated"]


def test_clean_dataframe_series_of_wars_policy():
    df_raw = pd.DataFrame(
        [
            _record("France", "Coalition Wars", 1792, 1815, conflict_type="series of wars"),
            _record("France", "War of the Third Coalition", 1803, 1806, conflict_type="war"),
            _record("Spain", "Mixed", 1800, 1801, conflict_type="series of wars; war"),
        ]
    )
    kept = clean_dataframe(df_raw, start_year=1700, end_year=2026)
    assert len(kept) == 3
    dropped = clean_dataframe(df_raw, start_year=1700, end_year=2026, series_of_wars="drop")
    assert set(dropped["conflict_name"]) == {"War of the Third Coalition"}
    with pytest.raises(ValueError):
        clean_dataframe(df_raw, start_year=1700, end_year=2026, series_of_wars="merge")


def test_clean_dataframe_keeps_war_month_when_not_clipped():
    df_raw = pd.DataFrame(
        [_record("France", "Precise War", 1939, 1945, start_month=9, start_day=1, end_month=5, end_day=8)]
    )
    cleaned = clean_dataframe(df_raw, start_year=1900, end_year=2026).iloc[0]
    assert (cleaned["start_month"], cleaned["start_day"]) == (9, 1)
    assert (cleaned["end_month"], cleaned["end_day"]) == (5, 8)
    assert (cleaned["war_start_month"], cleaned["war_end_month"]) == (9, 5)


def test_clean_dataframe_drops_month_when_clipped_by_country_lifespan():
    df_raw = pd.DataFrame(
        [
            _record(
                "Late Kingdom", "Long War", 1700, 1721, country_start=1707,
                start_month=3, start_day=15, end_month=11, end_day=20,
            )
        ]
    )
    cleaned = clean_dataframe(df_raw, start_year=1600, end_year=2026).iloc[0]
    # the clipped start comes from the country's (year-only) founding, not the war's own date
    assert cleaned["start_year"] == 1707
    assert pd.isna(cleaned["start_month"])
    assert pd.isna(cleaned["start_day"])
    # the end was not clipped, so its month/day survive
    assert cleaned["end_year"] == 1721
    assert cleaned["end_month"] == 11
    assert cleaned["end_day"] == 20
    # the conflict's own (unclipped) dates always keep their precision
    assert cleaned["war_start_month"] == 3


def test_clean_dataframe_keeps_same_label_entities_apart():
    df_raw = pd.DataFrame(
        [
            _record("Kingdom of Sardinia", "War A", 1720, 1721, country_qid="Q1"),
            _record("Kingdom of Sardinia", "War B", 1850, 1851, country_qid="Q2"),
        ]
    )
    cleaned = clean_dataframe(df_raw, start_year=1700, end_year=2026)
    assert set(cleaned["country"]) == {"Kingdom of Sardinia (Q1)", "Kingdom of Sardinia (Q2)"}
