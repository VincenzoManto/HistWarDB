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
