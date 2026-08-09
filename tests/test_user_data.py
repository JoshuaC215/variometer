"""Tests for the user_data/ loaders and the base_scenario overlay."""

import json

import pytest

from variometer.config import Scenario, base_scenario
from variometer.user_data_io import (
    hide_builtin_scenarios,
    load_historical,
    load_profile,
    load_ui_prefs,
    load_vest_grants,
)


def _clear_caches():
    load_profile.cache_clear()
    load_ui_prefs.cache_clear()
    load_vest_grants.cache_clear()
    load_historical.cache_clear()


@pytest.fixture
def user_data(tmp_path, monkeypatch):
    """Point the loaders at a temp user_data dir; return it for file writes."""
    monkeypatch.setenv("VARIOMETER_USER_DATA", str(tmp_path))
    _clear_caches()
    yield tmp_path
    _clear_caches()


def _write_profile(user_data, data: dict):
    (user_data / "profile.json").write_text(json.dumps(data))
    _clear_caches()


# ---------------------------------------------------------------------------
# profile.json
# ---------------------------------------------------------------------------


def test_disabled_user_data_returns_empty():
    # conftest sets VARIOMETER_USER_DATA="" for the whole suite
    _clear_caches()
    assert load_profile() == {}
    assert load_vest_grants() is None


def test_base_scenario_equals_bare_scenario_without_profile():
    _clear_caches()
    assert base_scenario() == Scenario()


def test_profile_overlays_fields(user_data):
    _write_profile(user_data, {"base_salary": 300_000, "age": 42})
    s = base_scenario()
    assert s.base_salary == 300_000.0
    assert s.age == 42
    # Untouched fields keep their defaults
    assert s.bonus_rate == Scenario().bonus_rate


def test_profile_int_coerced_to_float(user_data):
    _write_profile(user_data, {"base_salary": 300_000})
    assert isinstance(load_profile()["base_salary"], float)


def test_profile_date_parsing(user_data):
    from datetime import date

    _write_profile(user_data, {"pay_anchor": "2026-01-09"})
    assert base_scenario().pay_anchor == date(2026, 1, 9)


def test_profile_derived_basis_recomputed(user_data):
    _write_profile(user_data, {"brokerage_balance": 200_000, "brokerage_cost_basis_fraction": 0.5})
    s = base_scenario()
    assert s.brokerage_cost_basis == pytest.approx(100_000)


def test_profile_unknown_key_is_hard_error(user_data):
    _write_profile(user_data, {"base_salarey": 300_000})  # typo
    with pytest.raises(KeyError):
        load_profile()


def test_profile_rejects_wrong_type(user_data):
    _write_profile(user_data, {"bonus_rate": "0.15"})  # string where number expected
    with pytest.raises(TypeError):
        load_profile()


def test_profile_rejects_events_key(user_data):
    _write_profile(user_data, {"events": []})
    with pytest.raises(KeyError):
        load_profile()


def test_profile_underscore_keys_ignored(user_data):
    _write_profile(user_data, {"_note": "a comment", "age": 40})
    assert load_profile() == {"age": 40}


# ---------------------------------------------------------------------------
# "_ui" display prefs
# ---------------------------------------------------------------------------


def test_ui_prefs_disabled_layer_returns_empty():
    _clear_caches()
    assert load_ui_prefs() == {}
    assert hide_builtin_scenarios() is False


def test_ui_prefs_absent_key_returns_empty(user_data):
    _write_profile(user_data, {"age": 40})
    assert load_ui_prefs() == {}


def test_ui_prefs_parsed_and_ignored_by_profile(user_data):
    _write_profile(user_data, {"_ui": {"hide_builtin_scenarios": True}, "age": 40})
    assert load_ui_prefs() == {"hide_builtin_scenarios": True}
    assert hide_builtin_scenarios() is True
    # load_profile treats "_ui" like any underscore key: skipped entirely.
    assert load_profile() == {"age": 40}


def test_ui_prefs_unknown_key_is_hard_error(user_data):
    _write_profile(user_data, {"_ui": {"hide_builtin_scenarois": True}})  # typo
    with pytest.raises(KeyError):
        load_ui_prefs()


def test_ui_prefs_rejects_wrong_type(user_data):
    _write_profile(user_data, {"_ui": {"hide_builtin_scenarios": "yes"}})
    with pytest.raises(TypeError):
        load_ui_prefs()


def test_ui_prefs_rejects_non_object(user_data):
    _write_profile(user_data, {"_ui": True})
    with pytest.raises(TypeError):
        load_ui_prefs()


def test_missing_env_dir_is_error(monkeypatch, tmp_path):
    monkeypatch.setenv("VARIOMETER_USER_DATA", str(tmp_path / "nope"))
    _clear_caches()
    with pytest.raises(FileNotFoundError):
        load_profile()
    _clear_caches()


# ---------------------------------------------------------------------------
# grants.csv
# ---------------------------------------------------------------------------


def _write_grants(user_data, text: str):
    (user_data / "grants.csv").write_text(text)
    _clear_caches()


def test_grants_csv_parsed_and_grouped(user_data):
    _write_grants(
        user_data,
        "grant_id,year,month,shares\n"
        "Initial,2026,1,100.5\n"
        "Initial,2026,2,100.5\n"
        "Refresh 2026,2026,3,4.0\n",
    )
    grants = load_vest_grants()
    assert grants == {
        "Initial": {(2026, 1): 100.5, (2026, 2): 100.5},
        "Refresh 2026": {(2026, 3): 4.0},
    }


def test_grants_csv_feeds_base_scenario_and_vest_schedule(user_data):
    from variometer.equity import vest_schedule

    _write_grants(user_data, "grant_id,year,month,shares\nInitial,2026,1,120\n")
    s = base_scenario()
    assert s.vest_grants == {"Initial": {(2026, 1): 120.0}}
    schedule = vest_schedule(s)
    assert schedule[(2026, 1)] == pytest.approx(120.0)


def test_grants_csv_duplicate_rows_sum(user_data):
    _write_grants(
        user_data,
        "grant_id,year,month,shares\nInitial,2026,1,100\nInitial,2026,1,25\n",
    )
    assert load_vest_grants() == {"Initial": {(2026, 1): 125.0}}


def test_grants_csv_bad_header_is_error(user_data):
    _write_grants(user_data, "grant,yr,mo,sh\nInitial,2026,1,100\n")
    with pytest.raises(ValueError):
        load_vest_grants()


def test_grants_csv_bad_month_is_error(user_data):
    _write_grants(user_data, "grant_id,year,month,shares\nInitial,2026,13,100\n")
    with pytest.raises(ValueError):
        load_vest_grants()


# ---------------------------------------------------------------------------
# historical.csv
# ---------------------------------------------------------------------------


def test_historical_absent_returns_none():
    _clear_caches()
    assert load_historical() is None


def test_historical_csv_parsed_sorted_with_blanks(user_data):
    (user_data / "historical.csv").write_text(
        "year,month,total_income,paychecks,other_income,expenses\n"
        "2025,2,9000,8500,500,6000\n"
        "2025,1,8000,,,5500\n"
    )
    _clear_caches()
    rows = load_historical()
    assert [r["month"] for r in rows] == [1, 2]  # sorted
    assert rows[0]["paychecks"] is None and rows[0]["other_income"] is None
    assert rows[1] == {
        "year": 2025,
        "month": 2,
        "total_income": 9000.0,
        "paychecks": 8500.0,
        "other_income": 500.0,
        "expenses": 6000.0,
    }


def test_historical_csv_bad_header_is_error(user_data):
    (user_data / "historical.csv").write_text("yr,mo,inc,pay,other,exp\n2025,1,1,1,1,1\n")
    _clear_caches()
    with pytest.raises(ValueError):
        load_historical()
