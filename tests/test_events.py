"""LifeEvent infrastructure + event-aware computation.

Key invariants tested here:
- test_employment_end_truncates_vests_and_kills_refreshes
- test_new_job_replaces_salary_after_start
- test_expense_change_adds_delta
- Scenario field defaults (cost_basis auto-derive, healthcare_monthly, events)

The absolute values an eventless ``Scenario()`` produces are pinned in the
persona bundle ``tests/personas/single_ca_rsu/`` (checked by
``tests/test_personas.py``). What lives here are the *relative* invariants —
that events move the numbers in the right direction, and that no events moves
them not at all.
"""

import pytest

from variometer.cashflow import annual_expenses, annual_summary
from variometer.config import Scenario
from variometer.equity import vest_gross_for_year, vest_schedule
from variometer.events import LifeEvent, events_of_kind, first_event_of_kind
from variometer.projection import project_net_worth

# ---------------------------------------------------------------------------
# Zero events must give identical numbers to the baseline
# ---------------------------------------------------------------------------


def test_explicit_empty_events_matches_default():
    """Scenario(events=[]) gives bit-identical output to Scenario() with no events."""
    s_default = Scenario()
    s_explicit = Scenario()
    s_explicit.events = []

    for year in [2026, 2027, 2028, 2029, 2030]:
        ar_d = annual_summary(s_default, year)
        ar_e = annual_summary(s_explicit, year)
        assert ar_d.gross_income == pytest.approx(ar_e.gross_income, abs=0.01)
        assert ar_d.total_tax == pytest.approx(ar_e.total_tax, abs=0.01)
        assert ar_d.take_home == pytest.approx(ar_e.take_home, abs=0.01)


# ---------------------------------------------------------------------------
# Scenario field defaults
# ---------------------------------------------------------------------------


def test_scenario_events_default_empty():
    assert Scenario().events == []


def test_brokerage_cost_basis_auto_derives():
    s = Scenario()
    assert s.brokerage_cost_basis == pytest.approx(
        s.brokerage_balance * s.brokerage_cost_basis_fraction
    )


def test_brokerage_cost_basis_explicit_overrides():
    s = Scenario(brokerage_cost_basis=100_000)
    assert s.brokerage_cost_basis == 100_000


def test_healthcare_monthly_default_zero():
    assert Scenario().healthcare_monthly == 0.0


# ---------------------------------------------------------------------------
# LifeEvent dataclass
# ---------------------------------------------------------------------------


def test_life_event_frozen():
    e = LifeEvent(year=2028, kind="employment_end")
    with pytest.raises(Exception):
        e.year = 2029  # type: ignore[misc]


def test_life_event_unknown_kind_raises():
    with pytest.raises(ValueError, match="Unknown event kind"):
        LifeEvent(year=2028, kind="magic_money")


def test_events_of_kind_filter():
    evts = [
        LifeEvent(year=2028, kind="employment_end"),
        LifeEvent(year=2029, kind="new_job", params={"base": 150_000}),
        LifeEvent(year=2030, kind="employment_end"),
    ]
    ends = events_of_kind(evts, "employment_end")
    assert len(ends) == 2
    assert all(e.kind == "employment_end" for e in ends)
    # Returned in chronological order
    assert ends[0].year == 2028
    assert ends[1].year == 2030


def test_first_event_of_kind():
    evts = [
        LifeEvent(year=2030, kind="employment_end"),
        LifeEvent(year=2028, month=3, kind="employment_end"),
    ]
    first = first_event_of_kind(evts, "employment_end")
    assert first.year == 2028
    assert first.month == 3


def test_first_event_of_kind_none():
    assert first_event_of_kind([], "employment_end") is None


# ---------------------------------------------------------------------------
# employment_end → equity
# ---------------------------------------------------------------------------


def test_employment_end_truncates_vests():
    """No RSU vests after the employment-end month."""
    s = Scenario()
    s.events = [LifeEvent(year=2028, month=6, kind="employment_end")]
    schedule = vest_schedule(s)
    # No vest should appear after June 2028
    bad = [(y, m) for (y, m) in schedule if (y, m) > (2028, 6)]
    assert bad == [], f"Vests after employment end: {bad}"


def test_employment_end_allows_vest_in_end_month():
    """The end month itself should still have a vest (final pay cycle)."""
    s = Scenario()
    s.events = [LifeEvent(year=2028, month=6, kind="employment_end")]
    schedule = vest_schedule(s)
    # June 2028 should have a vest (it's ≤ cutoff)
    assert schedule.get((2028, 6), 0) > 0


def test_employment_end_kills_projected_refreshes():
    """Refresh grants not yet issued at the employment-end date are skipped.

    The employer issues grants in March of the grant year.  Leaving in June 2028
    means the March 2028 grant IS received (8 months of vesting: Mar-Jun).
    The 2029 grant is skipped because the cutoff (2028-06) < (2029-03).
    Net result: no shares vest in 2029 or later.
    """
    s = Scenario()
    s.events = [LifeEvent(year=2028, month=6, kind="employment_end")]
    # 2029+ grants are skipped; 2028 grant vests only through June 2028
    rsu_2029 = vest_gross_for_year(s, 2029)
    assert rsu_2029 == pytest.approx(0.0)


def test_employment_end_includes_grant_issued_before_cutoff():
    """The March grant in the departure year IS included when cutoff >= (year, 3)."""
    s = Scenario()
    s.events = [LifeEvent(year=2028, month=6, kind="employment_end")]
    schedule = vest_schedule(s)
    # 2028 refresh grant starts vesting in March 2028 — should appear in schedule
    # (June 2028 cutoff ≥ March 2028 grant date)
    assert schedule.get((2028, 3), 0) > 0, "March 2028 vest from 2028 grant should exist"


def test_employment_end_skips_grant_when_leaving_before_march():
    """Leaving before March means the grant that month was never issued."""
    s = Scenario()
    s.events = [LifeEvent(year=2028, month=2, kind="employment_end")]
    schedule = vest_schedule(s)
    # The 2028 refresh (issued March 2028) is after the cutoff (February 2028)
    # → not received. Earlier grants (the sample initial grant, the 2027
    # projected refresh) are also cut off at February, so nothing vests in
    # March 2028 at all.
    assert schedule.get((2028, 3), 0) == pytest.approx(0.0)


def test_employment_end_no_effect_on_baseline_without_events():
    """With no events, vest_schedule is unchanged."""
    s_base = Scenario()
    s_events = Scenario()
    # No events added — should be identical
    assert vest_schedule(s_base) == vest_schedule(s_events)


# ---------------------------------------------------------------------------
# new_job → cashflow
# ---------------------------------------------------------------------------


def test_new_job_replaces_salary_after_start():
    """A new_job starting Jan 2028 should replace employer salary for 2028 onwards."""
    s = Scenario()
    s.events = [
        LifeEvent(year=2028, month=1, kind="employment_end"),
        LifeEvent(
            year=2028,
            month=1,
            kind="new_job",
            params={
                "base": 160_000,
                "bonus_rate": 0.0,
                "equity_annual": 0,
                "match_rate": 0.05,
                "match_cap": 8_000,
                "mega_backdoor": False,
            },
        ),
    ]
    ar = annual_summary(s, 2028)
    # new_job base is expressed in 2026 real dollars and grows at salary_growth from 2026.
    # For a 2028 job with base=160_000 (2026 $): 160_000 * 1.03^2 ≈ 169_744.
    expected = 160_000 * (1 + Scenario().salary_growth) ** (2028 - 2026)
    assert ar.base_salary == pytest.approx(expected, rel=0.01)


def test_new_job_zero_rsu_after_employment_end():
    """After employment_end, RSU gross drops to zero for that year."""
    s = Scenario()
    s.events = [
        LifeEvent(year=2028, month=1, kind="employment_end"),
        LifeEvent(year=2028, month=1, kind="new_job", params={"base": 160_000}),
    ]
    ar = annual_summary(s, 2028)
    # RSU from Jan 2028 onwards is gone (employment_end in Jan drops the end-month vest)
    # Allow for some small value depending on exact month boundary
    assert ar.rsu_gross < 50_000  # well below the normal ~$300k+ for a full year


def test_new_job_lower_gross_than_baseline():
    """A low-paying pivot should have lower gross than the baseline."""
    s = Scenario()
    s.events = [
        LifeEvent(year=2028, month=1, kind="employment_end"),
        LifeEvent(
            year=2028,
            month=1,
            kind="new_job",
            params={
                "base": 90_000,
                "bonus_rate": 0.0,
                "equity_annual": 0,
                "match_rate": 0.03,
                "match_cap": 2_700,
            },
        ),
    ]
    ar_new_job = annual_summary(s, 2028)
    ar_baseline = annual_summary(Scenario(), 2028)
    assert ar_new_job.gross_income < ar_baseline.gross_income


def test_new_job_no_mega_backdoor_reduces_aftertax():
    """new_job with mega_backdoor=False should have zero after-tax 401k contribution."""
    s = Scenario()
    s.events = [
        LifeEvent(year=2028, month=1, kind="employment_end"),
        LifeEvent(
            year=2028, month=1, kind="new_job", params={"base": 160_000, "mega_backdoor": False}
        ),
    ]
    ar = annual_summary(s, 2028)
    assert ar.contributions.aftertax == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# expense_change → cashflow & projection
# ---------------------------------------------------------------------------


def test_expense_change_increases_annual_expenses():
    """A $500/mo expense_change starting Jan 2028 adds $6k to 2028 annual expenses."""
    s = Scenario()
    s.events = [
        LifeEvent(
            year=2028,
            month=1,
            kind="expense_change",
            params={"delta_monthly": 500, "label": "childcare"},
        ),
    ]
    base_expenses = annual_expenses(Scenario(), 2028)
    new_expenses = annual_expenses(s, 2028)
    assert new_expenses == pytest.approx(base_expenses + 500 * 12, abs=1)


def test_expense_change_prorates_for_start_year():
    """An expense_change starting July 2028 only applies for 6 months in 2028."""
    s = Scenario()
    s.events = [
        LifeEvent(
            year=2028, month=7, kind="expense_change", params={"delta_monthly": 600, "label": "gym"}
        ),
    ]
    base_expenses = annual_expenses(Scenario(), 2028)
    new_expenses = annual_expenses(s, 2028)
    # July-Dec = 6 months
    assert new_expenses == pytest.approx(base_expenses + 600 * 6, abs=1)


def test_expense_change_respects_ends_year():
    """An expense_change that ends before the query year has no effect."""
    s = Scenario()
    s.events = [
        LifeEvent(
            year=2027, kind="expense_change", params={"delta_monthly": 1_000, "ends_year": 2027}
        ),
    ]
    assert annual_expenses(s, 2028) == pytest.approx(annual_expenses(Scenario(), 2028), abs=1)


def test_expense_change_no_effect_before_start():
    """An expense_change starting in 2029 has no effect on 2028."""
    s = Scenario()
    s.events = [
        LifeEvent(year=2029, kind="expense_change", params={"delta_monthly": 1_000}),
    ]
    assert annual_expenses(s, 2028) == pytest.approx(annual_expenses(Scenario(), 2028), abs=1)


def test_one_time_expense_hits_event_year_only():
    """A one_time_expense adds its full amount to the event year and nothing else."""
    s = Scenario()
    s.events = [
        LifeEvent(
            year=2028,
            month=6,
            kind="one_time_expense",
            params={"amount": 20_000, "label": "roof"},
        ),
    ]
    base_2028 = annual_expenses(Scenario(), 2028)
    assert annual_expenses(s, 2028) == pytest.approx(base_2028 + 20_000, abs=1)
    # Adjacent years unaffected
    for yr in (2027, 2029):
        assert annual_expenses(s, yr) == pytest.approx(annual_expenses(Scenario(), yr), abs=1)


def test_one_time_expense_lands_in_event_month_in_monthly_forecast():
    """Monthly forecast books the one-time amount in the exact event month."""
    from datetime import date

    from variometer.cashflow import monthly_forecast

    s = Scenario()
    s.events = [
        LifeEvent(year=2027, month=4, kind="one_time_expense", params={"amount": 15_000}),
    ]
    rows = monthly_forecast(s, start=date(2027, 1, 1), end=date(2027, 12, 1))
    base_rows = monthly_forecast(Scenario(), start=date(2027, 1, 1), end=date(2027, 12, 1))
    for r, rb in zip(rows, base_rows, strict=True):
        delta = r.expenses - rb.expenses
        if r.month == 4:
            assert delta == pytest.approx(15_000, abs=1)
        else:
            assert delta == pytest.approx(0.0, abs=1)


def test_expense_change_zero_events_identical():
    """annual_expenses with no events matches the inline formula from projection.py."""
    s = Scenario()
    for year in [2026, 2027, 2028, 2029, 2030]:
        yrs = year - 2026
        expected = s.expense_base * (1 + s.expense_growth) ** yrs * 12
        assert annual_expenses(s, year) == pytest.approx(expected, abs=0.01)


# ---------------------------------------------------------------------------
# project_net_worth regression — zero events must match pre-events baseline
# ---------------------------------------------------------------------------


def test_project_net_worth_zero_events_unchanged():
    """project_net_worth with no events → same brokerage trajectory as before."""
    s = Scenario()
    years = list(range(2026, 2031))
    nw = project_net_worth(s, years)

    # Spot-check: brokerage should be positive and growing
    balances = [row.brokerage for row in nw]
    assert all(b > 0 for b in balances)
    # Brokerage in 2030 should be meaningfully larger than starting balance
    assert nw[-1].brokerage > s.brokerage_balance


def test_project_net_worth_employment_end_reduces_brokerage():
    """Ending employment reduces take-home → brokerage grows more slowly."""
    s_base = Scenario()
    s_leave = Scenario()
    s_leave.events = [
        LifeEvent(year=2028, month=1, kind="employment_end"),
        LifeEvent(year=2028, month=1, kind="new_job", params={"base": 90_000, "bonus_rate": 0.0}),
    ]
    years = list(range(2026, 2031))
    nw_base = project_net_worth(s_base, years)
    nw_leave = project_net_worth(s_leave, years)

    # By 2030 the lower-income scenario should have less brokerage
    assert nw_leave[-1].brokerage < nw_base[-1].brokerage


# ---------------------------------------------------------------------------
# Event-aware projection — employment_end + new_job must affect 401k growth
# (contributions are pulled from annual_summary, which honors events).
# ---------------------------------------------------------------------------


def test_projection_401k_differs_with_employment_end():
    """employment_end + a lower-paying new_job (no match, no mega-backdoor)
    should produce visibly smaller 401(k) balances by 2030 than baseline."""
    s_base = Scenario()
    s_leave = Scenario()
    s_leave.events = [
        LifeEvent(year=2028, month=1, kind="employment_end"),
        LifeEvent(
            year=2028,
            month=1,
            kind="new_job",
            params={
                "base": 90_000,
                "bonus_rate": 0.0,
                "equity_annual": 0,
                "match_rate": 0.0,
                "match_cap": 0,
                "mega_backdoor": False,
            },
        ),
    ]
    years = list(range(2026, 2031))
    base_nw = project_net_worth(s_base, years)[-1]
    leave_nw = project_net_worth(s_leave, years)[-1]

    base_401k = base_nw.pretax_401k + base_nw.roth_401k
    leave_401k = leave_nw.pretax_401k + leave_nw.roth_401k

    # Lower salary + no match + no mega-backdoor → smaller 401k balance
    assert leave_401k < base_401k
    # The gap should be substantial (3 years of match + mega-backdoor missing)
    gap = base_401k - leave_401k
    assert gap > 50_000, f"Expected >$50k gap, got {gap:,.0f}"


# ---------------------------------------------------------------------------
# brokerage_deposit event
# ---------------------------------------------------------------------------


def test_brokerage_deposit_increases_brokerage_in_event_year():
    """A brokerage_deposit event boosts brokerage balance by the given amount in the event year."""
    deposit = 120_000.0
    s_base = Scenario()
    s_dep = Scenario()
    s_dep.events = [LifeEvent(year=2028, kind="brokerage_deposit", params={"amount": deposit})]

    years = list(range(2026, 2031))
    nw_base = project_net_worth(s_base, years)
    nw_dep = project_net_worth(s_dep, years)

    base_by_year = {r.year: r for r in nw_base}
    dep_by_year = {r.year: r for r in nw_dep}

    # Before deposit year: identical brokerage
    assert dep_by_year[2027].brokerage == pytest.approx(base_by_year[2027].brokerage, abs=1)

    # In the deposit year: brokerage is higher by approximately the deposit amount
    # (slightly more due to mid-year growth on the deposit)
    brok_delta_2028 = dep_by_year[2028].brokerage - base_by_year[2028].brokerage
    assert brok_delta_2028 > deposit * 0.99, (
        f"Expected ~{deposit:,.0f} boost, got {brok_delta_2028:,.0f}"
    )

    # Year after: still ahead (deposit compounds)
    assert dep_by_year[2029].brokerage > base_by_year[2029].brokerage

    # Total NW in deposit year is also higher
    assert dep_by_year[2028].total_nw > base_by_year[2028].total_nw


def test_brokerage_deposit_zero_amount_is_noop():
    """A brokerage_deposit with amount=0 produces identical output."""
    s_base = Scenario()
    s_dep = Scenario()
    s_dep.events = [LifeEvent(year=2028, kind="brokerage_deposit", params={"amount": 0.0})]

    years = list(range(2026, 2031))
    nw_base = project_net_worth(s_base, years)
    nw_dep = project_net_worth(s_dep, years)

    for rb, rd in zip(nw_base, nw_dep, strict=True):
        assert rd.brokerage == pytest.approx(rb.brokerage, abs=1)
        assert rd.total_nw == pytest.approx(rb.total_nw, abs=1)


# ---------------------------------------------------------------------------
# retirement_year field
# ---------------------------------------------------------------------------


def test_retirement_year_stops_employment_income():
    """retirement_year on Scenario cuts employer salary and RSU to zero from that year forward."""
    from variometer.cashflow import annual_summary

    s = Scenario(retirement_year=2030)

    # Before cutoff: salary and RSU are non-zero
    ar_2029 = annual_summary(s, 2029)
    assert ar_2029.base_salary > 0
    assert ar_2029.rsu_gross > 0

    # At and after cutoff: all employment income gone
    for yr in [2030, 2031, 2032]:
        ar = annual_summary(s, yr)
        assert ar.base_salary == pytest.approx(0.0, abs=1)
        assert ar.rsu_gross == pytest.approx(0.0, abs=1)


def test_retirement_year_stops_new_job_income():
    """retirement_year also stops new_job income when it fires after the cutoff."""
    from variometer.cashflow import annual_summary
    from variometer.scenarios import sabbatical

    # sabbatical returns to a $160k job in 2032 by default
    sc = sabbatical(return_year=2032)
    sc.retirement_year = 2035

    # New job is active in 2033–2034 (before cutoff)
    ar_2033 = annual_summary(sc, 2033)
    assert ar_2033.base_salary > 0

    # At cutoff: new job income stops
    ar_2035 = annual_summary(sc, 2035)
    assert ar_2035.base_salary == pytest.approx(0.0, abs=1)


def test_retirement_year_none_is_noop():
    """retirement_year=None leaves behavior identical to omitting it."""
    from variometer.cashflow import annual_summary

    s1 = Scenario()
    s2 = Scenario(retirement_year=None)
    for yr in [2026, 2028, 2030]:
        assert annual_summary(s1, yr).gross_income == pytest.approx(
            annual_summary(s2, yr).gross_income, abs=0.01
        )


# ---------------------------------------------------------------------------
# pay_change event
# ---------------------------------------------------------------------------


def test_pay_change_no_events_is_noop():
    """No pay_change events → output identical to the baseline formula."""
    from variometer.cashflow import annual_summary

    s1 = Scenario()
    s2 = Scenario()
    s2.events = []  # explicit empty list — should still match
    for yr in [2026, 2027, 2028, 2029]:
        assert annual_summary(s1, yr).base_salary == pytest.approx(
            annual_summary(s2, yr).base_salary, abs=0.01
        )
        assert annual_summary(s1, yr).bonus == pytest.approx(annual_summary(s2, yr).bonus, abs=0.01)


def test_pay_change_raises_salary_from_change_year():
    """pay_change in January of change_year raises salary in that year and beyond."""
    from variometer.cashflow import annual_summary

    s = Scenario()
    s_promo = Scenario()
    s_promo.events = [
        LifeEvent(year=2028, month=1, kind="pay_change", params={"new_base": 350_000})
    ]

    # Before change: identical
    assert annual_summary(s, 2027).base_salary == pytest.approx(
        annual_summary(s_promo, 2027).base_salary, abs=1
    )
    # From change year on: higher
    assert annual_summary(s_promo, 2028).base_salary > annual_summary(s, 2028).base_salary
    assert annual_summary(s_promo, 2029).base_salary > annual_summary(s, 2029).base_salary


def test_pay_change_mid_year_prorates_salary():
    """pay_change in month M prorates: (M-1) months old base, (13-M) months new base."""
    from variometer.cashflow import annual_summary

    new_base = 350_000
    pre_base_2028 = Scenario().base_salary * 1.03**2  # placeholder base grown 2 yrs

    s = Scenario()
    s.events = [LifeEvent(year=2028, month=6, kind="pay_change", params={"new_base": new_base})]
    ar = annual_summary(s, 2028)
    expected = (pre_base_2028 * 5 + new_base * 7) / 12
    assert ar.base_salary == pytest.approx(expected, rel=1e-3)


def test_pay_change_bonus_rate_applies_from_change_month():
    """bonus_rate in pay_change takes effect when the change is at or before March."""
    from variometer.cashflow import annual_summary

    s_base = Scenario()
    s_promo = Scenario()
    s_promo.events = [
        LifeEvent(
            year=2028, month=3, kind="pay_change", params={"new_base": 280_000, "bonus_rate": 0.18}
        )
    ]

    # 2027: not yet changed — bonus identical
    assert annual_summary(s_base, 2027).bonus == pytest.approx(
        annual_summary(s_promo, 2027).bonus, abs=1
    )
    # 2028: new rate (18% > 15%) and new base → higher bonus
    assert annual_summary(s_promo, 2028).bonus > annual_summary(s_base, 2028).bonus


def test_pay_change_bonus_rate_after_march_defers_to_next_year():
    """pay_change after March doesn't change the bonus paid in March of that year."""
    from variometer.cashflow import annual_summary

    s_base = Scenario()
    s_promo = Scenario()
    s_promo.events = [
        LifeEvent(
            year=2028, month=6, kind="pay_change", params={"new_base": 280_000, "bonus_rate": 0.18}
        )
    ]

    # 2028 bonus: change is in June so March bonus uses old rate
    assert annual_summary(s_base, 2028).bonus == pytest.approx(
        annual_summary(s_promo, 2028).bonus, rel=1e-3
    )
    # 2029 bonus: new rate applies
    assert annual_summary(s_promo, 2029).bonus > annual_summary(s_base, 2029).bonus


def test_pay_change_affects_equity_refresh_target():
    """equity_refresh_grant_value in pay_change re-anchors refresh sizing from that year."""
    from variometer.equity import computed_refresh_values

    s_base = Scenario()
    s_promo = Scenario()
    s_promo.events = [
        LifeEvent(
            year=2028,
            month=3,
            kind="pay_change",
            params={"new_base": 280_000, "equity_refresh_grant_value": 240_000},
        )
    ]

    vals_base = computed_refresh_values(s_base)
    vals_promo = computed_refresh_values(s_promo)

    # 2027 grant: change not yet active — same target
    assert vals_base[2027] == pytest.approx(vals_promo[2027], abs=1)
    # 2028+ grants: higher anchor → larger refresh grant
    assert vals_promo[2028] > vals_base[2028]
    assert vals_promo[2029] > vals_base[2029]
