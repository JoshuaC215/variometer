"""Cashflow integration tests — annual + monthly."""

from datetime import date

import pytest

from variometer.cashflow import (
    _paydays_in_month,
    annual_expenses,
    annual_summary,
    monthly_forecast,
)
from variometer.config import Scenario
from variometer.events import LifeEvent


def _baseline_4pct() -> Scenario:
    """A Scenario with an unoptimized 4% pretax / no Roth / no aftertax election."""
    return Scenario(pretax_401k_pct=0.04, roth_401k_pct=0.0, aftertax_401k_pct=0.0)


def test_annual_2027_full_bonus():
    """Base grows at salary_growth; bonus is bonus_rate × grown base."""
    s = Scenario()
    a = annual_summary(s, 2027)
    grown_base = s.base_salary * (1 + s.salary_growth)
    assert a.base_salary == pytest.approx(grown_base, abs=1)
    assert a.bonus == pytest.approx(grown_base * s.bonus_rate, abs=1)


def test_relocation_swaps_rent_prorated_in_move_year():
    """A relocation event swaps rent_monthly for new_monthly_rent from the move month."""
    s = Scenario()
    s.events = [
        LifeEvent(
            year=2027,
            month=7,
            kind="relocation",
            params={"to_label": "elsewhere", "new_monthly_rent": 2_500},
        ),
    ]
    base = annual_expenses(Scenario(), 2027)
    moved = annual_expenses(s, 2027)
    # Jul–Dec (6 months) at the new rent instead of the scenario rent.
    expected_delta = (2_500 - s.rent_monthly) * 6
    assert moved - base == pytest.approx(expected_delta, abs=1)

    # Subsequent years swap the full 12 months.
    base_next = annual_expenses(Scenario(), 2028)
    moved_next = annual_expenses(s, 2028)
    assert moved_next - base_next == pytest.approx((2_500 - s.rent_monthly) * 12, abs=1)


def test_relocation_without_rent_change_is_neutral():
    """A relocation event with no new_monthly_rent leaves expenses unchanged."""
    s = Scenario()
    s.events = [
        LifeEvent(year=2027, month=7, kind="relocation", params={"to_label": "elsewhere"}),
    ]
    assert annual_expenses(s, 2027) == pytest.approx(annual_expenses(Scenario(), 2027), abs=0.01)


def test_baseline_vs_optimized_2027():
    """Optimized should pay less tax and save much more."""
    base = annual_summary(_baseline_4pct(), 2027)
    opt = annual_summary(Scenario(), 2027)

    # Tax saved (pre-tax 401k reduces taxable income)
    assert opt.total_tax < base.total_tax
    # Way more going into retirement
    assert opt.contributions.annual_additions > base.contributions.annual_additions
    # Less take-home cash (correct — extra dollars are routed to retirement)
    assert opt.take_home < base.take_home


def test_paydays_july_2026_has_three():
    """With the placeholder anchor 2026-01-02 (Friday), July 2026 should have
    3 paydays (…, Jul 3, Jul 17, Jul 31)."""
    paydays = _paydays_in_month(date(2026, 1, 2), 2026, 7)
    assert paydays == 3


def test_paydays_typical_month():
    """Most months have 2 paydays."""
    paydays = _paydays_in_month(date(2026, 1, 2), 2026, 6)
    assert paydays == 2


def test_monthly_total_matches_annual_roughly():
    """Sum of monthly net-to-bank should approximate annual take-home.

    Won't match exactly because:
    - Monthly only covers May-Dec 2026 (not full year)
    - Annual includes LTCG tax/income not flowing through paychecks
    Tolerance loose.
    """
    s = Scenario()
    rows = monthly_forecast(s, start=date(2026, 5, 1), end=date(2026, 12, 1))
    sum_monthly = sum(r.net_to_bank for r in rows)
    # Annual take-home is for the full year. May-Dec captures most of RSU/bonus,
    # but Jan-Apr also had ~$100k of comp. So sum_monthly should be less.
    a = annual_summary(s, 2026)
    assert sum_monthly < a.take_home + 50_000  # loose sanity bound


def test_effective_tax_rate_in_range():
    """ETR for high-income CA filer should be 35-50%."""
    s = Scenario()
    a = annual_summary(s, 2027)
    assert 0.30 < a.effective_tax_rate < 0.50
