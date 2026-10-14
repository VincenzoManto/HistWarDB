import pandas as pd
import pytest

from histwardb import HistWarDB


def test_long_view_has_full_grid(dataset_from_bindings):
    df = dataset_from_bindings(view="long")
    # 1900-2026 inclusive = 127 years, for each of the 3 surviving countries
    years = dataset_from_bindings.end_year - dataset_from_bindings.start_year + 1
    assert len(df) == years * len(dataset_from_bindings.available_countries)


def test_war_state_set_for_active_years(dataset_from_bindings):
    df = dataset_from_bindings(countries="France", from_date=1939, to_date=1945, view="long")
    assert (df["War_State"] == 1).all()
    before = dataset_from_bindings(countries="France", from_date=1930, to_date=1938, view="long")
    assert (before["War_State"] == 0).all()


def test_german_empire_anachronism_excluded(dataset_from_bindings):
    assert "German Empire" not in dataset_from_bindings.available_countries


def test_binary_view_shape(dataset_from_bindings):
    binary = dataset_from_bindings(countries=["France", "United Kingdom"], view="binary")
    assert set(binary.index) == {"France", "United Kingdom"}
    assert binary.loc["France", 1940] == 1


def test_countries_filter_is_exact_not_substring(dataset_from_bindings):
    # "France" must not pull in an unrelated country whose label happens to
    # contain it as a substring.
    result = dataset_from_bindings(countries="France", view="long")
    assert set(result["Country"]) == {"France"}


def test_names_view_contains_conflict_name(dataset_from_bindings):
    names = dataset_from_bindings(countries="France", from_date=1940, to_date=1940, view="names")
    assert "Test War One" in names.loc["France", 1940]


def test_list_view_returns_sorted_unique_names(dataset_from_bindings):
    result = dataset_from_bindings(view="list")
    assert result == sorted(set(result))


def test_countries_view_only_includes_active(dataset_from_bindings):
    result = dataset_from_bindings(from_date=1939, to_date=1945, view="countries")
    assert "France" in result
    assert "United Kingdom" in result


def test_conflicts_view(dataset_from_bindings):
    result = dataset_from_bindings(from_date=1939, to_date=1945, view="conflicts")
    assert "Test War One" in result


def test_keyword_filter(dataset_from_bindings):
    result = dataset_from_bindings(keyword="Ongoing", view="countries", only_in_war=True)
    assert result == ["Narnia"]


def test_invalid_view_raises(dataset_from_bindings):
    with pytest.raises(ValueError):
        dataset_from_bindings(view="not-a-real-view")


def test_uninitialized_dataset_raises():
    db = HistWarDB(auto_fetch=False)
    with pytest.raises(ValueError):
        db(view="long")


def test_load_records_with_empty_list_produces_empty_frame():
    db = HistWarDB(auto_fetch=False)
    db.load_records([])
    assert db.df_long.empty
    assert db.available_countries == []


# -- identifiers, lifespans, imputation flags ------------------------------------


@pytest.fixture
def lifespan_dataset():
    db = HistWarDB(start_year=1900, end_year=1960, auto_fetch=False)
    db.load_records(
        [
            {
                "country": "Short Republic",
                "country_qid": "Q901",
                "iso3": "SHR",
                "conflict_name": "Undated War",
                "conflict_qid": "Q900",
                "start_year": 1930,
                "end_year": 1960,
                "duration_estimated": True,
                "country_start": 1920,
                "country_end": 1950,
            },
            {
                "country": "Russia",
                "country_qid": "Q159",
                "iso3": "RUS",
                "conflict_name": "Dated War",
                "conflict_qid": "Q800",
                "start_year": 1940,
                "end_year": 1941,
                "duration_estimated": False,
                "country_start": None,
                "country_end": None,
            },
            {
                "country": "Prussia",
                "country_qid": "Q38872",
                "iso3": None,
                "conflict_name": "Dated War",
                "conflict_qid": "Q800",
                "start_year": 1940,
                "end_year": 1941,
                "duration_estimated": False,
                "country_start": None,
                "country_end": 1947,
            },
        ]
    )
    return db


def test_attach_codes_joins_crosswalk_by_qid():
    from histwardb.core import attach_codes

    crosswalk = pd.DataFrame(
        {"country_qid": ["Q159"], "cow_code": pd.array([365], dtype="Int64"), "gw_code": pd.array([365], dtype="Int64")}
    )
    records = pd.DataFrame({"country_qid": ["Q159", "Q901"], "country": ["Russia", "Short Republic"]})
    out = attach_codes(records, crosswalk).set_index("country_qid")
    assert out.loc["Q159", "cow_code"] == 365
    assert pd.isna(out.loc["Q901", "cow_code"])


def test_grid_carries_codes(monkeypatch, lifespan_dataset):
    from histwardb import core

    crosswalk = pd.DataFrame(
        {"country_qid": ["Q159"], "cow_code": pd.array([365], dtype="Int64"), "gw_code": pd.array([365], dtype="Int64")}
    )
    monkeypatch.setattr(core, "load_crosswalk", lambda: crosswalk)
    lifespan_dataset.load_records(lifespan_dataset.participations.to_dict("records"))
    grid = lifespan_dataset(countries="Russia", view="long")
    assert set(grid["COW_Code"]) == {365}
    assert lifespan_dataset(countries="Prussia", view="long")["COW_Code"].isna().all()


def test_bundled_crosswalk_is_well_formed():
    from histwardb.core import load_crosswalk

    table = load_crosswalk()
    if table.empty:
        pytest.skip("no crosswalk bundled")
    assert table["country_qid"].str.fullmatch(r"Q\d+").all()
    assert not table["country_qid"].duplicated().any()
    # spot checks of hand-curated predecessor assignments
    codes = dict(zip(table["country_qid"], table["cow_code"]))
    for qid, code in {"Q142": 220, "Q145": 200, "Q43287": 255, "Q15180": 365}.items():
        if qid in codes:
            assert codes[qid] == code


def test_long_view_carries_identifiers(lifespan_dataset):
    df = lifespan_dataset(countries="Short Republic", view="long")
    assert set(df["Country_QID"]) == {"Q901"}
    assert set(df["ISO3"]) == {"SHR"}


def test_war_never_extends_past_state_dissolution(lifespan_dataset):
    df = lifespan_dataset(countries="Short Republic", view="long")
    at_war = df[df["War_State"] == 1]
    assert (at_war["Year"].min(), at_war["Year"].max()) == (1930, 1950)
    assert not ((df["State_Exists"] == 0) & (df["War_State"] == 1)).any()


def test_state_exists_marks_years_outside_lifespan(lifespan_dataset):
    df = lifespan_dataset(countries="Short Republic", view="long").set_index("Year")
    assert df.loc[1919, "State_Exists"] == 0
    assert df.loc[1920, "State_Exists"] == 1
    assert df.loc[1950, "State_Exists"] == 1
    assert df.loc[1951, "State_Exists"] == 0
    # unknown lifespan imposes no constraint
    assert (lifespan_dataset(countries="Russia", view="long")["State_Exists"] == 1).all()


def test_war_imputed_flags_years_beyond_an_undated_start(lifespan_dataset):
    df = lifespan_dataset(countries="Short Republic", view="long").set_index("Year")
    assert df.loc[1930, "War_Imputed"] == 0  # the start year is recorded
    assert df.loc[1931, "War_Imputed"] == 1
    assert df.loc[1929, "War_Imputed"] == 0  # not at war at all
    assert (lifespan_dataset(countries="Russia", view="long")["War_Imputed"] == 0).all()


def test_duration_estimated_marks_every_year_of_an_undated_conflict(lifespan_dataset):
    df = lifespan_dataset(countries="Short Republic", view="long").set_index("Year")
    # unlike War_Imputed, the start year is flagged too
    assert bool(df.loc[1930, "Duration_Estimated"])
    assert bool(df.loc[1945, "Duration_Estimated"])
    assert not bool(df.loc[1929, "Duration_Estimated"])
    assert not lifespan_dataset(countries="Russia", view="long")["Duration_Estimated"].any()


def test_only_existing_drops_years_outside_lifespan(lifespan_dataset):
    df = lifespan_dataset(countries="Short Republic", view="long", only_existing=True)
    assert (df["Year"].min(), df["Year"].max()) == (1920, 1950)


def test_binary_view_distinguishes_peace_from_non_existence(lifespan_dataset):
    default = lifespan_dataset(view="binary")
    assert default.loc["Short Republic", 1955] == 0
    existing = lifespan_dataset(view="binary", only_existing=True)
    assert pd.isna(existing.loc["Short Republic", 1955])
    assert existing.loc["Short Republic", 1925] == 0
    assert existing.loc["Short Republic", 1940] == 1


def test_countries_filter_does_not_match_substrings(lifespan_dataset):
    # "Russia" is a substring of "Prussia"
    assert set(lifespan_dataset(countries="Russia", view="long")["Country"]) == {"Russia"}


def test_countries_filter_accepts_qid_and_iso_code(lifespan_dataset):
    assert set(lifespan_dataset(countries="Q159", view="long")["Country"]) == {"Russia"}
    assert set(lifespan_dataset(countries="rus", view="long")["Country"]) == {"Russia"}


def test_countries_filter_warns_on_unknown_name(lifespan_dataset):
    with pytest.warns(UserWarning, match="Did you mean: Russia"):
        result = lifespan_dataset(countries="Rusia", view="long")
    assert result.empty


def test_keyword_is_literal_not_regex(lifespan_dataset):
    assert lifespan_dataset(keyword="(", view="countries") == []
    assert lifespan_dataset(keyword=".", view="countries") == []


def test_participations_table(lifespan_dataset):
    parts = lifespan_dataset.participations
    assert len(parts) == 3
    row = parts.set_index("country").loc["Short Republic"]
    assert (row["start_year"], row["end_year"]) == (1930, 1950)
    assert (row["war_start_year"], row["war_end_year"]) == (1930, 1960)


def test_invalid_missing_end_policy_raises():
    with pytest.raises(ValueError):
        HistWarDB(auto_fetch=False, missing_end="guess")


def test_heatmap_marks_non_existence(lifespan_dataset):
    fig = lifespan_dataset.plot_heatmap(top_n=3)
    values = {v for row in fig.data[0].z for v in row}
    assert values == {-1, 0, 1}
