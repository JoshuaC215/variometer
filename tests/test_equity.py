"""RSU vest schedule tests."""

import pytest

from variometer.config import Scenario
from variometer.equity import (
    computed_refresh_values,
    project_flat_refresh,
    projected_refresh_years,
    vest_gross_for_month,
    vest_gross_for_year,
    vest_schedule,
)

# Shorthand to suppress all projected refreshes via explicit zero overrides.
# Derived from projected_refresh_years so it stays in sync if the range moves.
_NO_PROJECTED = dict.fromkeys(projected_refresh_years(Scenario()), 0)


def test_sample_grant_monthly_vest():
    """The placeholder sample grant vests 100 shares/month from Jan 2026."""
    schedule = vest_schedule(Scenario())
    assert schedule[(2026, 1)] == pytest.approx(100.0)
    assert schedule[(2026, 12)] == pytest.approx(100.0)


def test_sample_grant_ends_after_48_months():
    """The sample grant's last vest is Dec 2029; nothing explicit after."""
    s = Scenario()
    s.refresh_overrides = _NO_PROJECTED
    schedule = vest_schedule(s)
    assert schedule.get((2029, 12), 0) == pytest.approx(100.0)
    assert schedule.get((2030, 1), 0) == 0


def test_explicit_vest_grants_override():
    """vest_grants supplied directly (as grants.csv would) drive the schedule."""
    s = Scenario(vest_grants={"custom": {(2026, 6): 42.5}})
    s.refresh_overrides = _NO_PROJECTED
    schedule = vest_schedule(s)
    assert schedule == {(2026, 6): pytest.approx(42.5)}


def test_flat_refresh_total_shares():
    """A $100k refresh at $200/share should total 500 shares over 48 months."""
    sched = project_flat_refresh(2027, total_value=100_000, price_at_grant=200)
    total = sum(sched.values())
    assert total == pytest.approx(500.0, abs=0.01)
    assert len(sched) == 48


def test_flat_refresh_is_48_consecutive_months():
    """A March-2027 refresh vests every month through Feb 2031, no gaps."""
    sched = project_flat_refresh(2027, 100_000, 200)
    assert min(sched.keys()) == (2027, 3)
    assert max(sched.keys()) == (2031, 2)
    assert (2028, 2) in sched


def test_vest_gross_uses_price_growth():
    """Stock price growth compounds the gross value of future vests.

    Suppress projected refreshes so share counts are identical between
    growth and no-growth runs (only the sample initial grant is in play).
    """
    s = Scenario()
    s.refresh_overrides = _NO_PROJECTED
    s.price_growth = 0.10

    s_ng = Scenario()
    s_ng.refresh_overrides = _NO_PROJECTED
    s_ng.price_growth = 0.0

    # May 2026 (now) — should match (no growth period)
    assert vest_gross_for_month(s, 2026, 5) == pytest.approx(
        vest_gross_for_month(s_ng, 2026, 5), rel=0.01
    )
    # May 2028 — 2 years out → price ratio ≈ 1.21
    ratio = vest_gross_for_month(s, 2028, 5) / vest_gross_for_month(s_ng, 2028, 5)
    assert ratio == pytest.approx(1.10**2, rel=0.01)


def test_2026_full_year_rsu_gross_reasonable():
    """Sanity: 2026 RSU gross matches the sample grant (no refresh vests in 2026)."""
    s = Scenario()  # $100 price, 1,200 sample shares vest in 2026
    g = vest_gross_for_year(s, 2026)
    assert 100_000 < g < 150_000


def test_refresh_grant_value_linear_baseline():
    """Each year's refresh equals the base value grown at equity_refresh_growth."""
    s = Scenario()
    vals = computed_refresh_values(s)
    # 2027 grant = base × (1 + g)^(2027 - 2026)
    expected_2027 = s.equity_refresh_grant_value * (1 + s.equity_refresh_growth)
    assert vals[2027] == pytest.approx(expected_2027, rel=1e-6)
    # 2030 grant = base × (1 + g)^4
    expected_2030 = s.equity_refresh_grant_value * (1 + s.equity_refresh_growth) ** 4
    assert vals[2030] == pytest.approx(expected_2030, rel=1e-6)


def test_refresh_grant_value_independent_of_prior_grants():
    """The linear model does not depend on past grants — overrides don't shift later years."""
    s_all = Scenario()
    s_no27 = Scenario()
    s_no27.refresh_overrides = {2027: 0}
    vals_all = computed_refresh_values(s_all)
    vals_no27 = computed_refresh_values(s_no27)
    # 2028 and beyond should be identical (no band-targeting feedback loop)
    assert vals_no27[2028] == pytest.approx(vals_all[2028], rel=1e-9)
    assert vals_no27[2030] == pytest.approx(vals_all[2030], rel=1e-9)


def test_refresh_override_bypasses_auto():
    """An explicit refresh_overrides entry is used verbatim."""
    s = Scenario()
    s.refresh_overrides = {2027: 999_999}
    vals = computed_refresh_values(s)
    assert vals[2027] == 999_999
    # Other years still follow the linear model
    expected_2028 = s.equity_refresh_grant_value * (1 + s.equity_refresh_growth) ** 2
    assert vals[2028] == pytest.approx(expected_2028, rel=1e-6)


def test_equity_grant_event_layers_supplemental_vest():
    """An equity_grant event adds a 48-month vest schedule on top of the normal refresh."""
    from variometer.events import LifeEvent

    s_base = Scenario()
    s_extra = Scenario()
    s_extra.events = [LifeEvent(year=2028, month=3, kind="equity_grant", params={"value": 100_000})]
    emp_base = vest_gross_for_year(s_base, 2029)
    g_extra = vest_gross_for_year(s_extra, 2029)
    # A $100k grant vests evenly over 48 months → ~$25k/yr in full years.
    assert g_extra - emp_base == pytest.approx(25_000, rel=0.10)
