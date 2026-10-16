import pandas as pd
import pytest

from histwardb.core import clean_dataframe, parse_bindings, parse_date_parts, parse_year


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


def test_parse_bindings_fills_missing_end_date_recent(sample_bindings):
    records = parse_bindings(sample_bindings, end_year=2026)
    narnia = next(r for r in records if r["country"] == "Narnia")
    assert narnia["start_year"] == 2020
    assert narnia["end_year"] == 2026  # no end date, recent conflict -> runs to dataset end
    assert narnia["end_month"] is None  # estimated end -> no fabricated month/day


def test_parse_bindings_captures_month_and_day_precision():
    bindings = [
        {
            "countryLabel": {"value": "France"},
            "conflictLabel": {"value": "Precise War"},
            "startDate": {"value": "1939-09-01T00:00:00Z"},
            "startDatePrecision": {"value": "11"},
            "endDate": {"value": "1945-05-08T00:00:00Z"},
            "endDatePrecision": {"value": "11"},
        },
        {
            "countryLabel": {"value": "Narnia"},
            "conflictLabel": {"value": "Imprecise War"},
            "startDate": {"value": "1950-01-01T00:00:00Z"},
            "startDatePrecision": {"value": "9"},
        },
    ]
    records = parse_bindings(bindings, end_year=2026)
    precise = next(r for r in records if r["country"] == "France")
    assert (precise["start_month"], precise["start_day"]) == (9, 1)
    assert (precise["end_month"], precise["end_day"]) == (5, 8)

    imprecise = next(r for r in records if r["country"] == "Narnia")
    assert imprecise["start_month"] is None
    assert imprecise["start_day"] is None


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
