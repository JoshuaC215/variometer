"""Housing model layer.

Covers:
- Amortization schedule correctness
- Annual interest / principal / equity helpers
- annual_housing_cost proration
- itemized_excess_for_year (SALT cap behavior is the key test)
- the buy_home factory (exercised through HCOL/LCOL parameter presets)
- project_net_worth integration (down payment, home equity, brokerage)
"""

import pytest

from variometer.cashflow import annual_expenses, annual_summary
from variometer.config import Scenario
from variometer.data.brackets import federal_standard_deduction
from variometer.events import LifeEvent
from variometer.housing import (
    MortgageTerms,
    active_house_purchase,
    amortize,
    annual_housing_cost,
    annual_interest_for_year,
    home_equity_at_eoy,
    itemized_excess_for_year,
    terms_from_event,
)
from variometer.projection import project_net_worth
from variometer.scenarios import buy_home, stay_put

# ---------------------------------------------------------------------------
# Fixtures / shared helpers
# ---------------------------------------------------------------------------


def buy_hcol(**kwargs) -> Scenario:
    """buy_home with an HCOL-metro parameter preset (big price, low tax rate),
    shared across the tests below."""
    kwargs.setdefault("price", 1_200_000)
    kwargs.setdefault("property_tax_rate", 0.011)
    kwargs.setdefault("insurance_monthly", 300)
    kwargs.setdefault("location", "HCOL metro")
    return buy_home(**kwargs)


def buy_lcol(**kwargs) -> Scenario:
    """buy_home with an LCOL-metro parameter preset (small price, higher tax rate)."""
    kwargs.setdefault("price", 700_000)
    kwargs.setdefault("property_tax_rate", 0.0125)
    kwargs.setdefault("insurance_monthly", 200)
    kwargs.setdefault("appreciation_rate", 0.025)
    kwargs.setdefault("location", "LCOL metro")
    return buy_home(**kwargs)


def _terms_1m_30yr() -> MortgageTerms:
    """$1M loan, 6.5%, 30yr — used across several amortization tests."""
    return MortgageTerms(
        price=1_250_000,
        down_pct=0.20,  # 20% down → $1M loan
        rate=0.065,
        term_years=30,
        purchase_year=2028,
        purchase_month=1,  # Jan for clean annual boundaries
    )


def _terms_hcol() -> MortgageTerms:
    """HCOL metro terms matching buy_hcol() defaults."""
    return MortgageTerms(
        price=1_200_000,
        down_pct=0.20,
        rate=0.0675,
        term_years=30,
        property_tax_rate=0.011,
        insurance_monthly=300,
        purchase_year=2029,
        purchase_month=1,  # Jan for clean year boundaries in standalone tests
    )


# ---------------------------------------------------------------------------
# Amortization schedule
# ---------------------------------------------------------------------------


def test_amortization_total_payments_match_expected():
    """Sum of all 360 monthly payments ≈ 360 × monthly_p_and_i."""
    terms = _terms_1m_30yr()
    schedule = amortize(terms)
    assert len(schedule) == 360
    total_paid = sum(row["payment"] for row in schedule)
    expected = 360 * terms.monthly_p_and_i
    assert total_paid == pytest.approx(expected, rel=1e-6)


def test_amortization_principal_sums_to_loan_amount():
    """Sum of all principal payments == loan_amount within $1."""
    terms = _terms_1m_30yr()
    schedule = amortize(terms)
    total_principal = sum(row["principal"] for row in schedule)
    assert total_principal == pytest.approx(terms.loan_amount, abs=1.0)


def test_amortization_ending_balance_zero():
    """Final row's remaining_balance is approximately 0."""
    terms = _terms_1m_30yr()
    schedule = amortize(terms)
    assert schedule[-1]["remaining_balance"] == pytest.approx(0.0, abs=1.0)


def test_annual_interest_decreases_over_years():
    """Interest in year 1 > year 10 > year 25 (as expected for amortizing loan)."""
    terms = MortgageTerms(
        price=1_250_000,
        down_pct=0.20,
        rate=0.065,
        term_years=30,
        purchase_year=2028,
        purchase_month=1,
    )
    interest_yr1 = annual_interest_for_year(terms, 2028)
    interest_yr10 = annual_interest_for_year(terms, 2037)
    interest_yr25 = annual_interest_for_year(terms, 2052)

    assert interest_yr1 > interest_yr10, (
        f"Year 1 interest ({interest_yr1:,.0f}) should exceed year 10 ({interest_yr10:,.0f})"
    )
    assert interest_yr10 > interest_yr25, (
        f"Year 10 interest ({interest_yr10:,.0f}) should exceed year 25 ({interest_yr25:,.0f})"
    )


def test_home_equity_grows_with_payments():
    """Home equity at EOY of year 5 > EOY of year 1 (principal pays down balance)."""
    terms = MortgageTerms(
        price=1_250_000,
        down_pct=0.20,
        rate=0.065,
        term_years=30,
        purchase_year=2028,
        purchase_month=1,
    )
    equity_yr1 = home_equity_at_eoy(terms, 2028)
    equity_yr5 = home_equity_at_eoy(terms, 2032)
    assert equity_yr5 > equity_yr1, (
        f"Equity at yr 5 ({equity_yr5:,.0f}) should exceed yr 1 ({equity_yr1:,.0f})"
    )


# ---------------------------------------------------------------------------
# Appreciation
# ---------------------------------------------------------------------------


def test_home_equity_no_appreciation_when_rate_zero():
    """appreciation_rate=0.0 → home equity is exactly down payment + principal."""
    terms_no = MortgageTerms(
        price=1_200_000,
        down_pct=0.20,
        rate=0.0675,
        term_years=30,
        purchase_year=2028,
        purchase_month=1,
        appreciation_rate=0.0,
    )
    # After one full year: down payment + ~1 year of principal payments,
    # no appreciation contribution
    equity = home_equity_at_eoy(terms_no, 2028)
    expected_no_appreciation = 1_200_000 * 0.20  # just the down payment + tiny principal
    # Should be just barely above down payment (year 1 principal is small)
    assert equity == pytest.approx(expected_no_appreciation, abs=20_000), (
        f"Year-1 equity with 0% appreciation should be ≈ down payment, got {equity:,.0f}"
    )


def test_home_equity_appreciation_compounds_annually():
    """With 3% appreciation, year-5 gain ≈ price × (1.03^5 − 1) on top of principal."""
    terms = MortgageTerms(
        price=1_200_000,
        down_pct=0.20,
        rate=0.0675,
        term_years=30,
        purchase_year=2028,
        purchase_month=1,
        appreciation_rate=0.03,
    )
    terms_no = MortgageTerms(
        price=1_200_000,
        down_pct=0.20,
        rate=0.0675,
        term_years=30,
        purchase_year=2028,
        purchase_month=1,
        appreciation_rate=0.0,
    )
    # Year 5 EOY = 2032 → 5 years of growth from purchase year (2028 → year 1)
    equity_with = home_equity_at_eoy(terms, 2032)
    equity_without = home_equity_at_eoy(terms_no, 2032)

    # Difference should equal the compounded appreciation
    expected_appreciation_gain = 1_200_000 * ((1.03**5) - 1)
    actual_gain = equity_with - equity_without
    assert actual_gain == pytest.approx(expected_appreciation_gain, rel=0.01), (
        f"Year-5 appreciation gain: expected ~${expected_appreciation_gain:,.0f}, "
        f"got ${actual_gain:,.0f}"
    )


def test_home_equity_appreciation_is_zero_before_purchase():
    """Even with appreciation_rate>0, equity before purchase year is 0."""
    terms = MortgageTerms(
        price=1_200_000,
        down_pct=0.20,
        rate=0.0675,
        term_years=30,
        purchase_year=2030,
        purchase_month=1,
        appreciation_rate=0.05,
    )
    assert home_equity_at_eoy(terms, 2028) == 0.0
    assert home_equity_at_eoy(terms, 2029) == 0.0


def test_buy_hcol_appreciation_flows_through_to_nw():
    """Setting appreciation > 0 in buy_hcol materially increases year-5 NW."""
    years = list(range(2026, 2035))
    s_flat = buy_hcol(purchase_year=2028, appreciation_rate=0.0)
    s_growth = buy_hcol(purchase_year=2028, appreciation_rate=0.03)

    nw_flat = project_net_worth(s_flat, years)
    nw_growth = project_net_worth(s_growth, years)

    yr_5_flat = next(r for r in nw_flat if r.year == 2033).total_nw
    yr_5_growth = next(r for r in nw_growth if r.year == 2033).total_nw

    # 6 years of $1.2M at 3% compounded ≈ $233k extra equity
    expected_extra = 1_200_000 * ((1.03**6) - 1)  # 2028..2033 inclusive = 6 years
    actual_extra = yr_5_growth - yr_5_flat
    assert actual_extra == pytest.approx(expected_extra, rel=0.05), (
        f"NW delta from 3% appreciation: expected ~${expected_extra:,.0f}, got ${actual_extra:,.0f}"
    )


def test_buy_hcol_default_has_nonzero_appreciation():
    """The default HCOL metro factory now ships with 3% appreciation, not 0."""
    s = buy_hcol()
    house_evt = next(e for e in s.events if e.kind == "house_purchase")
    assert house_evt.params.get("appreciation_rate", 0) > 0, (
        "buy_hcol() should default to a non-zero appreciation rate "
        "(stress-test 0% explicitly if needed)"
    )


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------


def test_annual_housing_cost_includes_maintenance_full_year():
    """With maintenance_pct=0.01, a full ownership year adds 1% × price."""
    terms = MortgageTerms(
        price=1_200_000,
        down_pct=0.20,
        rate=0.0675,
        term_years=30,
        property_tax_rate=0.011,
        insurance_monthly=300,
        purchase_year=2028,
        purchase_month=1,  # Jan → 2029 is a clean full year
        maintenance_pct=0.01,
    )
    terms_no_maint = MortgageTerms(
        price=1_200_000,
        down_pct=0.20,
        rate=0.0675,
        term_years=30,
        property_tax_rate=0.011,
        insurance_monthly=300,
        purchase_year=2028,
        purchase_month=1,
        maintenance_pct=0.0,
    )
    cost_with = annual_housing_cost(terms, 2029)
    cost_without = annual_housing_cost(terms_no_maint, 2029)
    assert cost_with - cost_without == pytest.approx(1_200_000 * 0.01, abs=1.0), (
        f"Maintenance contribution: expected ${1_200_000 * 0.01:,.0f}, "
        f"got ${cost_with - cost_without:,.0f}"
    )


def test_annual_housing_cost_maintenance_prorates_for_purchase_year():
    """A June purchase → 7 months of maintenance (Jun–Dec)."""
    terms = MortgageTerms(
        price=1_200_000,
        down_pct=0.20,
        rate=0.0675,
        term_years=30,
        property_tax_rate=0.011,
        insurance_monthly=300,
        hoa_monthly=0,
        purchase_year=2028,
        purchase_month=6,
        maintenance_pct=0.01,
    )
    terms_no_maint = MortgageTerms(
        price=1_200_000,
        down_pct=0.20,
        rate=0.0675,
        term_years=30,
        property_tax_rate=0.011,
        insurance_monthly=300,
        hoa_monthly=0,
        purchase_year=2028,
        purchase_month=6,
        maintenance_pct=0.0,
    )
    cost_with = annual_housing_cost(terms, 2028)
    cost_without = annual_housing_cost(terms_no_maint, 2028)
    months_owned = 7
    expected_maintenance_2028 = 1_200_000 * 0.01 * months_owned / 12
    assert cost_with - cost_without == pytest.approx(expected_maintenance_2028, abs=1.0)


def test_annual_housing_cost_post_payoff_keeps_maintenance():
    """Past the mortgage payoff year: no more P&I, but tax + insurance + HOA
    + maintenance continue. The original code returned 0 here; the new one
    correctly keeps ongoing costs."""
    terms = MortgageTerms(
        price=1_200_000,
        down_pct=0.20,
        rate=0.0675,
        term_years=30,
        property_tax_rate=0.011,
        insurance_monthly=300,
        hoa_monthly=0,
        purchase_year=2028,
        purchase_month=1,
        maintenance_pct=0.01,
    )
    payoff_year = 2058  # 2028 + 30
    cost_after_payoff = annual_housing_cost(terms, payoff_year)
    expected = (
        terms.annual_property_tax
        + 12 * terms.insurance_monthly
        + 12 * terms.hoa_monthly
        + terms.price * terms.maintenance_pct
    )
    assert cost_after_payoff == pytest.approx(expected, abs=1.0)


def test_buy_hcol_default_has_nonzero_maintenance():
    """The default HCOL metro factory ships with 1% maintenance."""
    s = buy_hcol()
    house_evt = next(e for e in s.events if e.kind == "house_purchase")
    assert house_evt.params.get("maintenance_pct", 0) > 0, (
        "buy_hcol() should default to a non-zero maintenance rate "
        "(stress-test 0% explicitly if needed)"
    )


def test_buy_hcol_maintenance_reduces_nw_vs_zero_maintenance():
    """Comparing same scenario with vs without maintenance, the with-maintenance
    case has lower total NW by year 5 (real cashflow drain)."""
    years = list(range(2026, 2035))
    s_with = buy_hcol(purchase_year=2028, maintenance_pct=0.01)
    s_without = buy_hcol(purchase_year=2028, maintenance_pct=0.0)

    nw_with = project_net_worth(s_with, years)
    nw_without = project_net_worth(s_without, years)

    yr5_with = next(r for r in nw_with if r.year == 2033).total_nw
    yr5_without = next(r for r in nw_without if r.year == 2033).total_nw

    # 5 years × $12k/yr maintenance ≈ $60k less in brokerage (plus forgone
    # returns on that). Conservative floor: at least $50k delta.
    assert yr5_without - yr5_with > 50_000, (
        f"Year-5 NW with maintenance ({yr5_with:,.0f}) should be at least $50k "
        f"lower than without ({yr5_without:,.0f})"
    )


# ---------------------------------------------------------------------------
# annual_housing_cost
# ---------------------------------------------------------------------------


def test_annual_housing_cost_zero_before_purchase():
    """Returns 0 for any year before the purchase year."""
    terms = _terms_hcol()
    assert annual_housing_cost(terms, terms.purchase_year - 1) == pytest.approx(0.0)
    assert annual_housing_cost(terms, 2025) == pytest.approx(0.0)


def test_annual_housing_cost_full_year_after_purchase():
    """A full year after purchase returns 12×P&I + full property tax + insurance + HOA."""
    terms = MortgageTerms(
        price=1_200_000,
        down_pct=0.20,
        rate=0.0675,
        term_years=30,
        property_tax_rate=0.011,
        insurance_monthly=300,
        hoa_monthly=500,
        purchase_year=2028,
        purchase_month=1,  # Jan → first full year is 2028 itself
    )
    cost = annual_housing_cost(terms, 2029)  # 2029 is a fully-owned year
    expected = (
        12 * terms.monthly_p_and_i
        + terms.annual_property_tax
        + 12 * terms.insurance_monthly
        + 12 * terms.hoa_monthly
    )
    assert cost == pytest.approx(expected, rel=1e-6)


def test_annual_housing_cost_prorated_purchase_year():
    """Purchase in June → roughly 7 months of costs (Jun–Dec)."""
    terms = MortgageTerms(
        price=1_200_000,
        down_pct=0.20,
        rate=0.0675,
        term_years=30,
        property_tax_rate=0.011,
        insurance_monthly=300,
        hoa_monthly=0,
        purchase_year=2028,
        purchase_month=6,  # June
    )
    cost = annual_housing_cost(terms, 2028)
    months = 7  # Jun, Jul, Aug, Sep, Oct, Nov, Dec
    expected = (
        months * terms.monthly_p_and_i
        + terms.annual_property_tax * months / 12
        + months * terms.insurance_monthly
    )
    assert cost == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# itemized_excess_for_year — SALT cap behavior (key test)
# ---------------------------------------------------------------------------


def _buy_hcol_scenario_full_year() -> tuple[Scenario, int]:
    """Return (scenario, first_full_year) with a Jan 2029 purchase for clean test."""
    s = Scenario()
    s.events = [
        LifeEvent(
            year=2029,
            month=1,
            kind="house_purchase",
            params={
                "price": 1_200_000,
                "down_pct": 0.20,
                "rate": 0.0675,
                "term_years": 30,
                "property_tax_rate": 0.011,
                "insurance_monthly": 300,
                "hoa_monthly": 0,
                "location_label": "HCOL metro",
                "replaces_monthly_rent": True,
            },
        )
    ]
    return s, 2029  # 2029 is the purchase year; first full year is also 2029 (Jan purchase)


def test_itemized_excess_saturated_salt_cap():
    """THE KEY TEST: SALT cap binding.

    For a buy_hcol scenario (first full year of interest):
    - salt_paid = $25k and salt_paid = $50k must produce IDENTICAL itemized_excess
      (SALT cap is binding at $10k in both cases).
    - salt_paid = $5k (under cap) must produce LOWER itemized_excess.
    """
    scenario, year = _buy_hcol_scenario_full_year()
    fed_std = federal_standard_deduction(year, scenario.deduction_inflation)
    ev = active_house_purchase(scenario, year)
    assert ev is not None
    terms = terms_from_event(ev)

    # Verify the loan is substantial — interest should far exceed the standard deduction
    interest_full_year = annual_interest_for_year(terms, year)
    assert interest_full_year > 60_000, (
        f"Expected >$60k interest in first year, got {interest_full_year:,.0f}"
    )

    # High SALT (both over the $10k cap) → same result
    excess_25k = itemized_excess_for_year(scenario, year, fed_std, salt_paid=25_000)
    excess_50k = itemized_excess_for_year(scenario, year, fed_std, salt_paid=50_000)
    assert excess_25k == pytest.approx(excess_50k, abs=0.01), (
        f"SALT cap not binding: excess_25k={excess_25k:,.2f} vs excess_50k={excess_50k:,.2f}"
    )

    # Low SALT (under the $10k cap) → lower excess
    excess_5k = itemized_excess_for_year(scenario, year, fed_std, salt_paid=5_000)
    assert excess_5k < excess_25k, (
        f"Expected lower excess with salt_paid=$5k ({excess_5k:,.2f}) "
        f"vs salt_paid=$25k ({excess_25k:,.2f})"
    )


def test_itemized_excess_property_tax_irrelevant_at_high_income():
    """When CA income tax alone exceeds $10k SALT cap, property_tax_rate variation is irrelevant.

    Vary property_tax_rate between 0.5% and 2.0%.  If the CA income tax portion
    of salt_paid is already >= $10k (which it is at this income level), both
    calls must return identical itemized_excess.
    """
    # Use a high CA income tax level (e.g. $20k, well above the $10k cap)
    ca_income_tax = 20_000  # well above cap
    scenario, year = _buy_hcol_scenario_full_year()
    fed_std = federal_standard_deduction(year, scenario.deduction_inflation)

    # Low property tax rate scenario
    s_low_ptax = Scenario()
    s_low_ptax.events = [
        LifeEvent(
            year=2029,
            month=1,
            kind="house_purchase",
            params={
                "price": 1_200_000,
                "down_pct": 0.20,
                "rate": 0.0675,
                "term_years": 30,
                "property_tax_rate": 0.005,
                "insurance_monthly": 300,
                "hoa_monthly": 0,
                "location_label": "HCOL metro",
                "replaces_monthly_rent": True,
            },
        )
    ]

    # High property tax rate scenario
    s_high_ptax = Scenario()
    s_high_ptax.events = [
        LifeEvent(
            year=2029,
            month=1,
            kind="house_purchase",
            params={
                "price": 1_200_000,
                "down_pct": 0.20,
                "rate": 0.0675,
                "term_years": 30,
                "property_tax_rate": 0.020,
                "insurance_monthly": 300,
                "hoa_monthly": 0,
                "location_label": "HCOL metro",
                "replaces_monthly_rent": True,
            },
        )
    ]

    # salt_paid includes CA income tax (already over cap) + property tax from each scenario
    # Both result in salt_paid >> $10k, so both cap at $10k
    low_salt = ca_income_tax + 1_200_000 * 0.005
    high_salt = ca_income_tax + 1_200_000 * 0.020

    excess_low = itemized_excess_for_year(s_low_ptax, 2029, fed_std, salt_paid=low_salt)
    excess_high = itemized_excess_for_year(s_high_ptax, 2029, fed_std, salt_paid=high_salt)

    assert excess_low == pytest.approx(excess_high, abs=0.01), (
        f"Expected identical excess when SALT cap is saturated: "
        f"low_ptax={excess_low:,.2f} vs high_ptax={excess_high:,.2f}"
    )


def test_itemized_excess_zero_without_house_event():
    """With no house_purchase event, returns scenario.itemized_excess_over_standard (default 0)."""
    s = Scenario()  # no events
    fed_std = federal_standard_deduction(2028, s.deduction_inflation)
    result = itemized_excess_for_year(s, 2028, fed_std, salt_paid=15_000)
    assert result == pytest.approx(s.itemized_excess_over_standard)
    assert result == pytest.approx(0.0)


def test_itemized_excess_uses_scenario_default_when_no_house():
    """When itemized_excess_over_standard is non-zero and no house, it's returned as-is."""
    s = Scenario()
    s.itemized_excess_over_standard = 5_000.0
    fed_std = federal_standard_deduction(2028, s.deduction_inflation)
    result = itemized_excess_for_year(s, 2028, fed_std, salt_paid=0)
    assert result == pytest.approx(5_000.0)


# ---------------------------------------------------------------------------
# buy_hcol integration — projection tests
# ---------------------------------------------------------------------------


def test_buy_hcol_reduces_brokerage_at_purchase():
    """In the purchase year, buy_hcol depletes brokerage by *at least*
    the down payment.

    Asserting a structural property rather than a tight numeric range — the
    gap is the sum of down payment + (mortgage_cost − rent) + maintenance +
    down-payment LTCG tax, and any of those can move as defaults evolve.
    What matters is the floor: brokerage drops by at least the down payment.
    """
    years = list(range(2026, 2035))
    s_base = stay_put()
    s_buy = buy_hcol(purchase_year=2028, purchase_month=1)

    nw_base = project_net_worth(s_base, years)
    nw_buy = project_net_worth(s_buy, years)

    base_2028 = next(r for r in nw_base if r.year == 2028)
    buy_2028 = next(r for r in nw_buy if r.year == 2028)

    down_payment = 1_200_000 * 0.20  # $240k

    diff = base_2028.brokerage - buy_2028.brokerage
    assert diff >= down_payment, (
        f"Brokerage diff ({diff:,.0f}) should be ≥ down payment ({down_payment:,.0f})"
    )
    # Sanity: not absurdly far above the down payment (catch e.g. a bug that
    # double-counts the down payment). 2× is a generous upper bound that
    # accommodates maintenance + mortgage-vs-rent delta + LTCG tax + a year
    # of compounding differences.
    assert diff < down_payment * 2.0, (
        f"Brokerage diff ({diff:,.0f}) is suspiciously > 2× down payment "
        f"({down_payment:,.0f}); check for a regression."
    )


def test_buy_hcol_increases_total_nw_via_home_equity():
    """By year 5 after purchase, buy scenario's total_nw includes meaningful home_equity > 0."""
    years = list(range(2026, 2035))
    s_buy = buy_hcol(purchase_year=2028)
    nw_buy = project_net_worth(s_buy, years)

    year_5 = next(r for r in nw_buy if r.year == 2033)  # 5 years after 2028
    assert year_5.home_equity > 0, (
        f"Expected positive home equity by 2033, got {year_5.home_equity:,.0f}"
    )
    # Home equity should be at least the down payment ($240k)
    assert year_5.home_equity >= 1_200_000 * 0.20 * 0.9, (
        f"Home equity ({year_5.home_equity:,.0f}) should be >= 90% of down payment"
    )


def test_buy_hcol_annual_expenses_higher_than_baseline_after_purchase():
    """After purchase, annual_expenses for buy > annual_expenses for baseline.

    HCOL metro mortgage (P&I alone ≈ $6.2k/mo on a $960k loan at 6.75%) exceeds
    the $3.5k/mo rent_monthly component being replaced, so total expenses rise.
    """
    s_base = stay_put()
    s_buy = buy_hcol(purchase_year=2028, purchase_month=1)

    exp_base = annual_expenses(s_base, 2029)  # post-purchase full year
    exp_buy = annual_expenses(s_buy, 2029)

    assert exp_buy > exp_base, (
        f"Buy expenses ({exp_buy:,.0f}) should exceed baseline ({exp_base:,.0f}) after purchase"
    )


def test_buy_hcol_itemized_kicks_in_purchase_year_plus_1():
    """Federal taxable income in the year after purchase is lower in buy vs baseline.

    The mortgage interest deduction (itemized > standard) reduces federal
    taxable income meaningfully relative to the standard-deduction baseline.
    """
    s_base = stay_put()
    s_buy = buy_hcol(purchase_year=2028, purchase_month=1)

    ar_base_2029 = annual_summary(s_base, 2029)
    ar_buy_2029 = annual_summary(s_buy, 2029)

    assert ar_buy_2029.fed_taxable < ar_base_2029.fed_taxable, (
        f"Buy federal taxable ({ar_buy_2029.fed_taxable:,.0f}) should be < "
        f"baseline ({ar_base_2029.fed_taxable:,.0f}) due to itemized deduction"
    )
    # The difference should be meaningful (tens of thousands, not pennies)
    diff = ar_base_2029.fed_taxable - ar_buy_2029.fed_taxable
    assert diff > 10_000, f"Expected >$10k itemized benefit, got {diff:,.0f}"


# ---------------------------------------------------------------------------
# buy_lcol — smaller down payment impact
# ---------------------------------------------------------------------------


def test_buy_lcol_smaller_down_payment_impact():
    """Brokerage drop in purchase year is smaller for LCOL than HCOL metro."""
    years = list(range(2026, 2035))
    s_hcol = buy_hcol(purchase_year=2028, purchase_month=1)
    s_lcol = buy_lcol(purchase_year=2028, purchase_month=1)

    nw_hcol = project_net_worth(s_hcol, years)
    nw_lcol = project_net_worth(s_lcol, years)
    nw_base = project_net_worth(stay_put(), years)

    base_2028 = next(r for r in nw_base if r.year == 2028)
    hcol_2028 = next(r for r in nw_hcol if r.year == 2028)
    lcol_2028 = next(r for r in nw_lcol if r.year == 2028)

    drop_hcol = base_2028.brokerage - hcol_2028.brokerage
    drop_lcol = base_2028.brokerage - lcol_2028.brokerage

    assert drop_lcol < drop_hcol, (
        f"LCOL brokerage drop ({drop_lcol:,.0f}) should be < "
        f"HCOL metro drop ({drop_hcol:,.0f}) due to smaller down payment"
    )


# ---------------------------------------------------------------------------
# Buy scenario net worth: home equity is additive to liquid + retirement
# ---------------------------------------------------------------------------


def test_buy_scenario_includes_home_equity_in_total_nw():
    """In a buy scenario, total NW = liquid + retirement accounts + home equity.

    Verify several years post-purchase:
    1. total_nw > liquid (brokerage + roth_ira) — retirement and equity add value.
    2. home_equity > ~90% of down payment — equity is being built.
    3. total_nw >= liquid + home_equity — equity contributes additively.
    """
    years = list(range(2026, 2040))
    s_buy = buy_hcol()
    nw_series = project_net_worth(s_buy, years)
    row = next((r for r in nw_series if r.year == 2035), None)  # 7 yrs after purchase
    assert row is not None

    liquid = row.brokerage + row.roth_ira

    assert row.total_nw > liquid, (
        f"total_nw ({row.total_nw:,.0f}) should exceed liquid ({liquid:,.0f})"
    )
    assert row.home_equity > 1_200_000 * 0.20 * 0.9, (
        f"home_equity ({row.home_equity:,.0f}) should be > 90% of down payment"
    )
    assert row.total_nw >= liquid + row.home_equity, (
        f"total_nw ({row.total_nw:,.0f}) should be >= liquid ({liquid:,.0f}) "
        f"+ home_equity ({row.home_equity:,.0f})"
    )
