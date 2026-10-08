import pandas as pd
import pytest

from histwardb.core import clean_dataframe, parse_bindings, parse_year


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


def test_parse_bindings_fills_missing_end_date_recent(sample_bindings):
    records = parse_bindings(sample_bindings, end_year=2026)
    narnia = next(r for r in records if r["country"] == "Narnia")
    assert narnia["start_year"] == 2020
    assert narnia["end_year"] == 2026  # no end date, recent conflict -> runs to dataset end


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
