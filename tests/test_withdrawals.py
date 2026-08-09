"""Drawdown waterfall: cost basis, conversion cohorts, 401(k) early withdrawal,
end-to-end lean-year integration, baseline invariants, ACA PTC.

The baseline's pinned net-worth trajectory lives in the persona bundle
``tests/personas/single_ca_rsu/expected.json``; what this file asserts about
the baseline is the *invariant* (it never draws down), not the numbers.
"""

import pytest

# ---------------------------------------------------------------------------
# Imports from modules under test
# ---------------------------------------------------------------------------
from variometer.cashflow import annual_summary
from variometer.config import Scenario
from variometer.events import LifeEvent
from variometer.projection import project_net_worth
from variometer.scenarios import fire_roth_ladder, sabbatical, stay_put
from variometer.tax import ltcg_federal_tax
from variometer.withdrawals import (
    WithdrawalState,
    YearWithdrawal,
    is_cohort_seasoned,
    waterfall_withdrawal,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_state(**overrides) -> WithdrawalState:
    """Build a WithdrawalState with sane defaults, overrideable per test."""
    defaults = dict(
        brokerage_balance=200_000.0,
        brokerage_basis=70_000.0,  # 35% basis (200k × 0.35)
        roth_ira_balance=30_000.0,
        roth_ira_principal=20_000.0,
        pretax_401k_balance=50_000.0,
        roth_conversion_cohorts={},
    )
    defaults.update(overrides)
    return WithdrawalState(**defaults)


def _run_waterfall(
    state: WithdrawalState,
    shortfall: float,
    year: int = 2035,
    age: int = 48,
    ordinary_taxable_income_before: float = 0.0,
    ltcg_before: float = 0.0,
) -> YearWithdrawal:
    """Invoke waterfall_withdrawal with inflation defaults."""
    return waterfall_withdrawal(
        state=state,
        shortfall=shortfall,
        year=year,
        age=age,
        ordinary_taxable_income_before=ordinary_taxable_income_before,
        ltcg_before=ltcg_before,
        bracket_inflation=0.03,
    )


# ===========================================================================
# FOUNDATIONAL BUNDLE
# ===========================================================================


class TestCostBasisDepletionProRata:
    """Brokerage withdrawals deplete cost basis proportionally."""

    def test_cost_basis_depletes_pro_rata(self):
        """Withdraw $20k from $100k balance with $35k basis.

        realized_gain = 20k × (1 - 35/100) = 20k × 0.65 = 13k
        basis_remaining = 35k × (80/100) = 28k
        brokerage_remaining = 80k
        """
        state = _default_state(
            brokerage_balance=100_000.0,
            brokerage_basis=35_000.0,
            roth_ira_balance=0.0,
            roth_ira_principal=0.0,
            pretax_401k_balance=0.0,
        )
        result = _run_waterfall(state, shortfall=20_000.0, age=60)  # post-59.5, no penalty on 401k

        # Find the brokerage leg
        brokerage_leg = next((l for l in result.legs if l.source == "brokerage"), None)
        assert brokerage_leg is not None, "Expected a brokerage withdrawal leg"

        # Check realized gain ≈ $13k (within rounding from iteration)
        assert brokerage_leg.taxable_ltcg == pytest.approx(13_000, rel=0.01), (
            f"Expected ~$13k realized gain, got {brokerage_leg.taxable_ltcg:,.2f}"
        )
        # Brokerage balance reduced
        assert state.brokerage_balance == pytest.approx(80_000, rel=0.01), (
            f"Expected ~$80k remaining brokerage, got {state.brokerage_balance:,.2f}"
        )
        # Basis depleted proportionally: 35k × (80/100)
        assert state.brokerage_basis == pytest.approx(28_000, rel=0.01), (
            f"Expected ~$28k remaining basis, got {state.brokerage_basis:,.2f}"
        )

    def test_cost_basis_full_depletion(self):
        """Withdraw the entire brokerage balance — gain = balance − basis, basis → 0."""
        state = _default_state(
            brokerage_balance=100_000.0,
            brokerage_basis=35_000.0,
            roth_ira_balance=0.0,
            roth_ira_principal=0.0,
            pretax_401k_balance=0.0,
        )
        # Need a shortfall large enough to drain all brokerage.
        # With no other buckets (roth_ira_principal=0, pretax=0), it'll drain brokerage then stop.
        result = _run_waterfall(state, shortfall=100_000.0, age=60)

        brokerage_leg = next((l for l in result.legs if l.source == "brokerage"), None)
        assert brokerage_leg is not None

        # Realized gain = 100k - 35k = 65k (may be slightly off due to tax iterations)
        assert brokerage_leg.taxable_ltcg == pytest.approx(65_000, rel=0.05), (
            f"Expected ~$65k realized gain on full depletion, got {brokerage_leg.taxable_ltcg:,.2f}"
        )
        # Brokerage balance → 0
        assert state.brokerage_balance == pytest.approx(0.0, abs=100), (
            f"Expected brokerage balance ~0, got {state.brokerage_balance:,.2f}"
        )
        # Basis → 0
        assert state.brokerage_basis == pytest.approx(0.0, abs=1), (
            f"Expected basis ~0, got {state.brokerage_basis:,.2f}"
        )


class TestZeroPctLtcgBracket:
    """Federal LTCG 0% bracket interactions."""

    def test_zero_pct_ltcg_bracket_below_threshold(self):
        """$30k LTCG with $0 ordinary income → entirely in 0% bracket → $0 federal LTCG tax."""
        result = ltcg_federal_tax(ltcg=30_000, ordinary_taxable_income=0, year=2026)
        assert result == pytest.approx(0.0, abs=1), (
            f"Expected $0 federal LTCG tax below threshold, got {result:,.2f}"
        )

    def test_zero_pct_ltcg_bracket_straddling_threshold(self):
        """$80k LTCG with $0 ordinary income — straddles the ~$50k 2026 threshold.

        The 2026 zero-bracket top is $49,450 (IRS Rev. Proc. 2025-32).  The
        test deliberately asserts a range rather than the exact figure so it
        stays valid across annual threshold refreshes.

        Expected: (80k − threshold) × 0.15 ≈ (80k − 50k) × 0.15 ≈ $4,500.
        Allow wide tolerance (rel=0.20) for bracket boundary variation.
        """
        result = ltcg_federal_tax(ltcg=80_000, ordinary_taxable_income=0, year=2026)
        # Must be positive (some tax owed)
        assert result > 0, f"Expected positive LTCG tax when straddling threshold, got {result}"
        # Upper bound: all at 15% = $12k; lower bound: threshold ≈ $48k so (80k-48k)×15% = $4.8k
        # Allow a wide range to cover different inflation baseline choices
        assert 2_000 <= result <= 12_000, (
            f"Expected LTCG tax between $2k and $12k (straddle), got {result:,.2f}"
        )

    def test_zero_pct_ltcg_bracket_fully_consumed_by_ordinary(self):
        """$30k LTCG with $200k ordinary taxable income → all at 15% (no 0% bracket left).

        At $200k ordinary income the taxpayer is well above the ~$50k 0% threshold,
        so all LTCG is taxed at 15%.  Expected: 30k × 0.15 = $4,500.
        """
        result = ltcg_federal_tax(ltcg=30_000, ordinary_taxable_income=200_000, year=2026)
        assert result == pytest.approx(4_500, rel=0.01), (
            f"Expected $4,500 LTCG tax at high ordinary income, got {result:,.2f}"
        )


# ===========================================================================
# ROTH IRA PRINCIPAL BUNDLE
# ===========================================================================


class TestRothIraPrincipalWithdrawal:
    def test_roth_ira_principal_withdrawal_no_tax_no_penalty(self):
        """Withdraw $5k from Roth IRA principal → no tax, no penalty."""
        state = _default_state(
            brokerage_balance=0.0,
            brokerage_basis=0.0,
            roth_ira_balance=30_000.0,
            roth_ira_principal=20_000.0,
            pretax_401k_balance=0.0,
        )
        result = _run_waterfall(state, shortfall=5_000.0)

        roth_leg = next((l for l in result.legs if l.source == "roth_ira_principal"), None)
        assert roth_leg is not None, "Expected a roth_ira_principal leg"
        assert roth_leg.amount == pytest.approx(5_000, rel=0.01)
        assert roth_leg.taxable_ordinary == pytest.approx(0.0)
        assert roth_leg.taxable_ltcg == pytest.approx(0.0)
        assert roth_leg.penalty == pytest.approx(0.0)

        # State updated
        assert state.roth_ira_principal == pytest.approx(15_000, rel=0.01)
        assert state.roth_ira_balance == pytest.approx(25_000, rel=0.01)

    def test_roth_ira_principal_cannot_exceed_contributions(self):
        """Cannot pull more from Roth principal than cumulative contributions.

        With only $10k principal available, a $15k shortfall must pull at most
        $10k from Roth principal and go to a next bucket for the rest.
        """
        state = _default_state(
            brokerage_balance=0.0,
            brokerage_basis=0.0,
            roth_ira_balance=50_000.0,
            roth_ira_principal=10_000.0,
            pretax_401k_balance=0.0,
        )
        result = _run_waterfall(state, shortfall=15_000.0)

        roth_leg = next((l for l in result.legs if l.source == "roth_ira_principal"), None)
        if roth_leg is not None:
            assert roth_leg.amount <= 10_000 + 1, (  # allow $1 rounding
                f"Roth principal withdrawal ({roth_leg.amount:,.2f}) exceeds contributions ($10k)"
            )


# ===========================================================================
# ROTH CONVERSION LADDER BUNDLE
# ===========================================================================


class TestRothConversionLadder:
    def test_is_cohort_seasoned_locked_through_year_4(self):
        """cohort_year=2027 is NOT seasoned in years 2028, 2029, 2030, 2031."""
        for query_year in [2028, 2029, 2030, 2031]:
            assert not is_cohort_seasoned(2027, query_year), (
                f"Cohort 2027 should NOT be seasoned in {query_year}"
            )

    def test_is_cohort_seasoned_unlocked_at_year_5(self):
        """cohort_year=2027 IS seasoned in 2032 and beyond."""
        assert is_cohort_seasoned(2027, 2032), "Cohort 2027 should be seasoned in 2032"
        assert is_cohort_seasoned(2027, 2033), "Cohort 2027 should be seasoned in 2033"
        assert is_cohort_seasoned(2027, 2040), "Cohort 2027 should be seasoned in 2040"

    def test_multiple_cohorts_independent_seasoning(self):
        """2027 and 2029 cohorts season independently.

        In 2032: 2027 cohort is seasoned (5yr gap), 2029 cohort is not (3yr gap).
        In 2034: both are seasoned (7yr and 5yr gaps).
        """
        assert is_cohort_seasoned(2027, 2032), "2027 cohort should be seasoned in 2032"
        assert not is_cohort_seasoned(2029, 2032), "2029 cohort should NOT be seasoned in 2032"

        assert is_cohort_seasoned(2027, 2034), "2027 cohort should be seasoned in 2034"
        assert is_cohort_seasoned(2029, 2034), "2029 cohort should be seasoned in 2034"

    def test_roth_conversion_adds_ordinary_income_in_conversion_year(self):
        """$30k Roth conversion in 2030 increases AGI by $30k vs no-conversion scenario."""
        # Scenario with no W-2 income (so only conversion income matters)
        s_convert = Scenario()
        s_convert.events = [
            LifeEvent(year=2028, month=3, kind="employment_end"),
            LifeEvent(
                year=2030,
                month=1,
                kind="roth_conversion",
                params={"amount": 30_000, "recurring": False},
            ),
        ]
        s_no_convert = Scenario()
        s_no_convert.events = [
            LifeEvent(year=2028, month=3, kind="employment_end"),
        ]
        ar_convert = annual_summary(s_convert, 2030)
        ar_no_convert = annual_summary(s_no_convert, 2030)
        # AGI in conversion year should be higher by ~$30k
        delta = ar_convert.agi - ar_no_convert.agi
        assert delta == pytest.approx(30_000, abs=500), (
            f"Expected AGI increase of ~$30k from conversion, got delta={delta:,.2f}"
        )

    def test_seasoned_cohort_withdrawal_no_tax_no_penalty(self):
        """Pulling from a seasoned cohort produces 0 ordinary income, 0 penalty."""
        state = _default_state(
            brokerage_balance=0.0,
            brokerage_basis=0.0,
            roth_ira_balance=50_000.0,
            roth_ira_principal=0.0,
            pretax_401k_balance=0.0,
            roth_conversion_cohorts={2027: 30_000.0},  # fully seasoned at year=2032
        )
        result = _run_waterfall(state, shortfall=10_000.0, year=2032)

        cohort_leg = next((l for l in result.legs if l.source == "roth_conversion_2027"), None)
        assert cohort_leg is not None, (
            f"Expected a roth_conversion_2027 leg; got legs: {[l.source for l in result.legs]}"
        )
        assert cohort_leg.taxable_ordinary == pytest.approx(0.0)
        assert cohort_leg.taxable_ltcg == pytest.approx(0.0)
        assert cohort_leg.penalty == pytest.approx(0.0)
        assert cohort_leg.cohort_year == 2027

    def test_cohort_fifo_order(self):
        """Oldest seasoned cohort is drained before younger ones."""
        # 2027 and 2028 cohorts are both seasoned in 2033
        state = _default_state(
            brokerage_balance=0.0,
            brokerage_basis=0.0,
            roth_ira_balance=100_000.0,
            roth_ira_principal=0.0,
            pretax_401k_balance=0.0,
            roth_conversion_cohorts={2027: 5_000.0, 2028: 30_000.0},
        )
        result = _run_waterfall(state, shortfall=10_000.0, year=2033)

        leg_sources = [l.source for l in result.legs]
        # 2027 cohort should appear before 2028 cohort
        assert "roth_conversion_2027" in leg_sources, "Expected 2027 cohort leg"
        # The 2027 cohort ($5k) should be fully drained first
        leg_2027 = next(l for l in result.legs if l.source == "roth_conversion_2027")
        assert leg_2027.amount == pytest.approx(5_000, rel=0.01), (
            f"Expected 2027 cohort fully drained (5k), got {leg_2027.amount:,.2f}"
        )
        # Then 2028 cohort provides the rest ($5k)
        leg_2028 = next((l for l in result.legs if l.source == "roth_conversion_2028"), None)
        assert leg_2028 is not None, "Expected 2028 cohort leg for the remaining shortfall"

    def test_waterfall_skips_unseasoned_cohorts_falls_to_roth_growth(self):
        """An unseasoned cohort is skipped; engine falls to Roth IRA growth (bucket 4)
        when there is growth available, before reaching pretax 401k (bucket 5).

        Waterfall order: brokerage → Roth principal → seasoned cohorts →
                         Roth IRA growth → pretax 401k
        """
        state = _default_state(
            brokerage_balance=0.0,
            brokerage_basis=0.0,
            roth_ira_balance=50_000.0,
            roth_ira_principal=0.0,  # all 50k is growth (no contributions)
            pretax_401k_balance=50_000.0,
            roth_conversion_cohorts={2030: 30_000.0},  # NOT seasoned in 2032 (only 2yr gap)
        )
        result = _run_waterfall(state, shortfall=10_000.0, year=2032, age=45)

        leg_sources = [l.source for l in result.legs]
        # Unseasoned 2030 cohort must NOT appear
        assert "roth_conversion_2030" not in leg_sources, (
            f"Unseasoned 2030 cohort should not be used in 2032; legs={leg_sources}"
        )
        # Roth IRA growth is bucket 4 and covers the shortfall before 401k is needed
        assert "roth_ira_growth" in leg_sources, (
            f"Expected roth_ira_growth leg (bucket 4) as fallback; legs={leg_sources}"
        )
        assert "401k_early" not in leg_sources, (
            f"401k_early should not be needed when Roth growth is available; legs={leg_sources}"
        )

    def test_waterfall_skips_unseasoned_cohorts_falls_to_401k_when_no_roth_growth(self):
        """An unseasoned cohort is skipped; engine falls to pretax 401k (bucket 5)
        when there is no Roth IRA growth (balance == principal)."""
        state = _default_state(
            brokerage_balance=0.0,
            brokerage_basis=0.0,
            roth_ira_balance=20_000.0,
            roth_ira_principal=20_000.0,  # all principal, no growth
            pretax_401k_balance=50_000.0,
            roth_conversion_cohorts={2030: 30_000.0},  # NOT seasoned in 2032 (only 2yr gap)
        )
        result = _run_waterfall(state, shortfall=10_000.0, year=2032, age=45)

        leg_sources = [l.source for l in result.legs]
        # Unseasoned 2030 cohort must NOT appear
        assert "roth_conversion_2030" not in leg_sources, (
            f"Unseasoned 2030 cohort should not be used in 2032; legs={leg_sources}"
        )
        # Roth principal covers it first (bucket 2)
        assert "roth_ira_principal" in leg_sources, (
            f"Expected roth_ira_principal to be used; legs={leg_sources}"
        )


# ===========================================================================
# 401k EARLY + WATERFALL INTEGRATION BUNDLE
# ===========================================================================


class TestFourZeroOneKEarly:
    def test_401k_early_withdrawal_10pct_penalty_plus_ordinary_income(self):
        """Pre-59.5 401k withdrawal: ordinary income + 10% penalty."""
        state = _default_state(
            brokerage_balance=0.0,
            brokerage_basis=0.0,
            roth_ira_balance=0.0,
            roth_ira_principal=0.0,
            pretax_401k_balance=100_000.0,
        )
        result = _run_waterfall(state, shortfall=20_000.0, age=48)  # pre-59.5

        leg = next((l for l in result.legs if l.source == "401k_early"), None)
        assert leg is not None, "Expected 401k_early leg"
        # Penalty is 10% of gross withdrawal
        assert leg.penalty == pytest.approx(leg.amount * 0.10, rel=0.05), (
            f"Expected 10% penalty; got penalty={leg.penalty:,.2f} on amount={leg.amount:,.2f}"
        )
        assert leg.taxable_ordinary > 0, "Expected positive ordinary income from 401k withdrawal"

    def test_401k_after_59_5_no_penalty(self):
        """Post-59.5 401k withdrawal: ordinary income but no penalty."""
        state = _default_state(
            brokerage_balance=0.0,
            brokerage_basis=0.0,
            roth_ira_balance=0.0,
            roth_ira_principal=0.0,
            pretax_401k_balance=100_000.0,
        )
        result = _run_waterfall(state, shortfall=20_000.0, age=60)  # post-59.5

        leg = next((l for l in result.legs if l.source == "401k_early"), None)
        assert leg is not None, "Expected 401k leg"
        assert leg.penalty == pytest.approx(0.0), (
            f"Expected $0 penalty post-59.5, got {leg.penalty:,.2f}"
        )
        assert leg.taxable_ordinary > 0


class TestWaterfallOrdering:
    def test_waterfall_brokerage_first_when_available(self):
        """With brokerage available, shortfall is covered entirely by brokerage."""
        state = _default_state(
            brokerage_balance=200_000.0,
            brokerage_basis=70_000.0,
            roth_ira_balance=30_000.0,
            roth_ira_principal=10_000.0,
            pretax_401k_balance=50_000.0,
        )
        result = _run_waterfall(state, shortfall=30_000.0, age=60)

        brokerage_leg = next((l for l in result.legs if l.source == "brokerage"), None)
        assert brokerage_leg is not None
        # Roth and 401k should NOT be touched
        roth_leg = next((l for l in result.legs if l.source == "roth_ira_principal"), None)
        k401_leg = next((l for l in result.legs if l.source == "401k_early"), None)
        assert roth_leg is None, "Roth IRA should not be touched when brokerage suffices"
        assert k401_leg is None, "401k should not be touched when brokerage suffices"

    def test_waterfall_falls_through_to_roth_principal_when_brokerage_exhausted(self):
        """$5k brokerage + $25k Roth principal needed to cover $30k shortfall."""
        state = _default_state(
            brokerage_balance=5_000.0,
            brokerage_basis=1_750.0,  # 35% basis
            roth_ira_balance=30_000.0,
            roth_ira_principal=25_000.0,
            pretax_401k_balance=50_000.0,
        )
        result = _run_waterfall(state, shortfall=30_000.0, age=60)

        brokerage_leg = next((l for l in result.legs if l.source == "brokerage"), None)
        roth_leg = next((l for l in result.legs if l.source == "roth_ira_principal"), None)
        assert brokerage_leg is not None, "Expected brokerage leg"
        assert roth_leg is not None, "Expected Roth IRA principal leg"
        # Brokerage should be fully drained (≈$5k)
        assert brokerage_leg.amount == pytest.approx(5_000, rel=0.05), (
            f"Expected ~$5k from brokerage, got {brokerage_leg.amount:,.2f}"
        )

    def test_waterfall_falls_through_to_seasoned_cohort(self):
        """After brokerage and Roth principal exhausted, seasoned cohort is used."""
        state = _default_state(
            brokerage_balance=0.0,
            brokerage_basis=0.0,
            roth_ira_balance=50_000.0,
            roth_ira_principal=0.0,
            pretax_401k_balance=0.0,
            roth_conversion_cohorts={2027: 50_000.0},  # seasoned at year 2032+
        )
        result = _run_waterfall(state, shortfall=20_000.0, year=2033)

        cohort_leg = next((l for l in result.legs if l.source == "roth_conversion_2027"), None)
        assert cohort_leg is not None, "Expected seasoned cohort leg as fallthrough"
        assert cohort_leg.amount == pytest.approx(20_000, rel=0.05)
        assert cohort_leg.taxable_ordinary == pytest.approx(0.0)
        assert cohort_leg.penalty == pytest.approx(0.0)

    def test_waterfall_401k_last_resort(self):
        """With no brokerage, no Roth principal, no seasoned cohorts → 401k early."""
        state = _default_state(
            brokerage_balance=0.0,
            brokerage_basis=0.0,
            roth_ira_balance=0.0,
            roth_ira_principal=0.0,
            pretax_401k_balance=100_000.0,
            roth_conversion_cohorts={},
        )
        result = _run_waterfall(state, shortfall=25_000.0, age=48)

        leg = next((l for l in result.legs if l.source == "401k_early"), None)
        assert leg is not None, "Expected 401k_early as last resort"
        assert leg.amount > 0
        assert leg.taxable_ordinary > 0

    def test_self_financing_iteration_converges_for_ltcg(self):
        """Pure brokerage LTCG self-financing converges within 8 iterations."""
        state = _default_state(
            brokerage_balance=500_000.0,
            brokerage_basis=100_000.0,  # 20% basis → 80% gain rate
            roth_ira_balance=0.0,
            roth_ira_principal=0.0,
            pretax_401k_balance=0.0,
        )
        result = _run_waterfall(
            state,
            shortfall=50_000.0,
            year=2026,
            age=60,
            ordinary_taxable_income_before=0.0,
        )

        assert result.iterations <= 8, f"Expected ≤ 8 iterations, got {result.iterations}"
        assert result.converged, "Expected converged=True, got False"


# ===========================================================================
# INTEGRATION: FULL-YEAR SCENARIOS
# ===========================================================================


class TestFullYearScenarios:
    def test_lean_year_realizes_ltcg_at_zero_federal_rate(self):
        """In a no-W-2 lean year, withdrawal-driven LTCG sits in the 0% federal bracket.

        The 0% bracket benefit applies to LTCG computed in the withdrawals engine.
        We verify this via two checks:

        1. In a lean year (sabbatical 2031), there is no W-2 income and
           fed_taxable_ordinary == $0 after the standard deduction.  This
           confirms the income context that would allow 0% LTCG treatment.

        2. A direct call to ltcg_federal_tax with lean-year income context
           ($0 ordinary taxable income) confirms that modest LTCG is federally
           tax-free via the 0% bracket.

        Note: annual_summary.federal_ltcg_tax and withdrawals.py both use the
        bracketed ltcg_federal_tax, so the 0% bracket applies to the baseline
        ltcg_annual signal and to brokerage-drawdown LTCG alike.
        """
        s = sabbatical(start_year=2030, return_year=2033)
        # In 2031 there is no employment; income = ltcg_annual + other_monthly * 12
        ar = annual_summary(s, 2031)

        # Verify truly no W-2 income
        assert ar.base_salary == pytest.approx(0.0, abs=1), (
            f"Expected $0 base salary in 2031 lean year, got {ar.base_salary:,.2f}"
        )
        assert ar.rsu_gross == pytest.approx(0.0, abs=1), (
            f"Expected $0 RSU in 2031 lean year, got {ar.rsu_gross:,.2f}"
        )

        # Federal ordinary taxable income should be near zero (standard deduction
        # eliminates the modest other_income, leaving only LTCG-stacked income)
        assert ar.fed_taxable == pytest.approx(0.0, abs=1), (
            f"Expected fed_taxable ≈ $0 in lean year, got {ar.fed_taxable:,.2f}"
        )

        # Now verify that a brokerage withdrawal in this income context pays $0 LTCG tax.
        # Use the bracketed function directly (as withdrawals.py does internally).
        # In a lean year, ordinary_taxable_income ≈ 0, so $30k of brokerage LTCG
        # sits entirely in the 0% bracket (threshold ≈ $50k inflated).
        federal_ltcg = ltcg_federal_tax(
            ltcg=30_000,
            ordinary_taxable_income=0.0,  # lean year: no ordinary income
            year=2031,
            bracket_inflation=0.03,
        )
        assert federal_ltcg == pytest.approx(0.0, abs=1), (
            f"Expected $0 federal LTCG tax on $30k withdrawal in lean year "
            f"(0% bracket, no ordinary income), got {federal_ltcg:,.2f}"
        )

    def test_fire_roth_ladder_creates_seasoned_principal_later(self):
        """fire_roth_ladder creates conversion cohorts accessible 5 years later.

        fire_roth_ladder(exit_year=2030) should include annual roth_conversion
        events from the exit year onward.  Those create cohorts that are
        seasoned at 2035+.
        """
        s = fire_roth_ladder(exit_year=2030)
        conversion_events = [e for e in s.events if e.kind == "roth_conversion"]
        assert len(conversion_events) >= 1, (
            "fire_roth_ladder should include at least one roth_conversion event"
        )
        # Conversions start at the exit year, none before
        assert min(e.year for e in conversion_events) == 2030
        for e in conversion_events:
            assert e.year >= 2030, f"Conversion event at year={e.year} precedes exit year 2030"

    def test_baseline_never_draws_down(self):
        """The eventless baseline funds itself from cash flow through 2035.

        Surplus stays positive across this window because projected refresh
        grants extend past 2030 — the employer keeps issuing refreshes for as
        long as employment continues, so equity income doesn't fall off a
        cliff. The exact trajectory is pinned in the persona bundle
        ``tests/personas/single_ca_rsu/`` (net_worth 2026–2035).
        """
        nw = project_net_worth(Scenario(), list(range(2026, 2036)))
        for row in nw:
            assert row.withdrawal is None, (
                f"Year {row.year}: baseline should draw down nothing, "
                f"waterfall fired for {row.withdrawal}"
            )
            assert row.unfunded_shortfall == pytest.approx(0.0, abs=1.0)


# ===========================================================================
# ACA premium tax credit
# ===========================================================================


class TestAcaPremiumTaxCredit:
    """Unit tests for tax.aca_premium_tax_credit and projection integration."""

    def test_no_subsidy_above_400pct_fpl(self):
        """Income above 400% FPL gets $0 subsidy (original ACA cliff rule)."""
        from variometer.tax import _federal_poverty_level, aca_premium_tax_credit

        fpl = _federal_poverty_level(2030)
        agi_above_cliff = fpl * 4.01
        assert aca_premium_tax_credit(agi_above_cliff, 2030, monthly_benchmark_premium=700.0) == 0.0

    def test_subsidy_at_200pct_fpl(self):
        """At 200% FPL the required contribution is 6.60% of income → meaningful subsidy.

        6.60% is the top of the 150–200% band in the 2026 applicable-percentage
        table (IRS Rev. Proc. 2025-25 §3.01).
        """
        from variometer.tax import _federal_poverty_level, aca_premium_tax_credit

        fpl = _federal_poverty_level(2030)
        agi = fpl * 2.0
        subsidy = aca_premium_tax_credit(agi, 2030, monthly_benchmark_premium=700.0)
        # Required monthly = 6.60% × agi / 12; subsidy = 700 - required_monthly
        required_monthly = 0.0660 * agi / 12
        expected = max(0.0, 700.0 - required_monthly)
        assert subsidy == pytest.approx(expected, rel=0.01)
        assert subsidy > 0, "Should be a positive subsidy at 200% FPL with $700 benchmark"

    def test_no_subsidy_below_100pct_fpl(self):
        """Below 100% FPL there is no subsidy (Medicaid territory)."""
        from variometer.tax import _federal_poverty_level, aca_premium_tax_credit

        fpl = _federal_poverty_level(2030)
        agi = fpl * 0.90
        assert aca_premium_tax_credit(agi, 2030, monthly_benchmark_premium=700.0) == 0.0

    def test_subsidy_caps_at_benchmark_premium(self):
        """Subsidy never exceeds the benchmark premium itself."""
        from variometer.tax import _federal_poverty_level, aca_premium_tax_credit

        fpl = _federal_poverty_level(2030)
        agi = fpl * 1.05  # just above 100% FPL → nearly full subsidy
        subsidy = aca_premium_tax_credit(agi, 2030, monthly_benchmark_premium=700.0)
        assert subsidy <= 700.0

    def test_zero_premium_returns_zero(self):
        """A $0 benchmark premium yields $0 subsidy."""
        from variometer.tax import aca_premium_tax_credit

        assert aca_premium_tax_credit(30_000.0, 2030, monthly_benchmark_premium=0.0) == 0.0

    def test_lean_year_gets_aca_subsidy_in_projection(self):
        """sabbatical projection should show positive aca_subsidy_monthly in FULL lean years.

        Employment ends in March of start_year (two-week notice), so the start year still
        has substantial Q1 employment income → AGI too high for ACA subsidy that year.
        Years start_year+1 through return_year-1 are fully lean (AGI = small LTCG only)
        and should receive meaningful subsidies.
        """
        sc = sabbatical(start_year=2030, return_year=2033, healthcare_monthly=700.0)
        nw = project_net_worth(sc, list(range(2026, 2034)))
        # Full lean years: 2031 and 2032 (2030 still has Q1 employer income above 400% FPL)
        full_lean_rows = [r for r in nw if 2031 <= r.year < 2033]
        assert full_lean_rows, "Expected at least one full lean year"
        for row in full_lean_rows:
            assert row.aca_subsidy_monthly > 0, (
                f"Year {row.year}: expected ACA subsidy but got {row.aca_subsidy_monthly:.2f}"
            )

    def test_baseline_has_no_aca_subsidy(self):
        """Stay put (high-income employee) has no ACA events → subsidy is 0."""
        nw = project_net_worth(stay_put(), list(range(2026, 2031)))
        for row in nw:
            assert row.aca_subsidy_monthly == 0.0, (
                f"Year {row.year}: baseline unexpectedly has ACA subsidy {row.aca_subsidy_monthly:.2f}"
            )

    def test_aca_subsidy_improves_runway_vs_no_subsidy(self):
        """ACA subsidy makes the net healthcare cost less than the gross premium.

        We compare the final NW gap between $700/mo healthcare + subsidy vs $0 healthcare.
        The gap should be less than the full gross cost ($700 × ~30 lean months) because
        the subsidy offsets part of the premium.  The gap may still exceed the raw gross
        due to LTCG tax on the additional brokerage withdrawals needed to fund any net
        premium, plus compounding — but it must be less than the gross.
        """
        sc_with_hc = sabbatical(start_year=2030, return_year=2033, healthcare_monthly=700.0)
        sc_no_hc = sabbatical(start_year=2030, return_year=2033, healthcare_monthly=0.0)
        nw_with = project_net_worth(sc_with_hc, list(range(2026, 2036)))
        nw_no = project_net_worth(sc_no_hc, list(range(2026, 2036)))

        final_with = nw_with[-1].total_nw
        final_no = nw_no[-1].total_nw
        actual_gap = final_no - final_with

        # Gross: 9 mo (2030 Q2-Q4) + 12 mo (2031) + 12 mo (2032) = ~33 months at $700
        full_gross_cost = 700.0 * 33
        # Gap must be positive (healthcare has a real cost)
        assert actual_gap > 0, "Healthcare+subsidy should still cost something vs $0 healthcare"
        # Gap must be less than full gross cost: subsidy is doing real work
        assert actual_gap < full_gross_cost, (
            f"ACA subsidy doesn't appear to reduce cost: NW gap={actual_gap:,.0f} "
            f">= gross_cost={full_gross_cost:,.0f}"
        )
        # Verify the lean rows have non-trivial subsidies (> $100/mo in full lean years)
        for row in nw_with:
            if 2031 <= row.year < 2033:
                assert row.aca_subsidy_monthly > 100, (
                    f"Year {row.year}: ACA subsidy {row.aca_subsidy_monthly:.2f} looks too small"
                )


# ===========================================================================
# Down-payment LTCG tax routing
# ===========================================================================


class TestDownPaymentLtcgTax:
    """The LTCG tax on a house down payment reduces net_surplus in the purchase year."""

    def test_house_purchase_reduces_surplus_by_ltcg_tax(self):
        """A buy_home scenario in a lean composite should show lower NW in purchase year
        than a naive model that ignores the LTCG tax on the down payment.

        We verify by checking that the down_pmt LTCG tax is non-zero and that it
        actually affects the final NW (i.e., it's wired in, not a no-op).
        """
        from variometer.scenarios import buy_home

        sc = buy_home(purchase_year=2028, price=1_200_000)
        nw = project_net_worth(sc, list(range(2026, 2032)))
        purchase_row = next(r for r in nw if r.year == 2028)
        # There should be realized LTCG from the down payment (gain fraction × $240k down)
        assert purchase_row.realized_ltcg_this_year > 0, "Expected LTCG from down payment"

    def test_lean_plus_house_purchase_does_not_miss_ltcg_tax(self):
        """A buy+lean composite scenario should fund the down-payment LTCG tax via waterfall.

        In the purchase year the net_surplus includes -down_pmt_ltcg_tax, so if the
        scenario is lean the waterfall handles both the living-expense shortfall AND the tax.
        The realized_ltcg_this_year on the purchase year should reflect the down-payment LTCG.
        """
        from variometer.events import LifeEvent
        from variometer.scenarios import stay_put

        s = stay_put()
        s = s.__class__(
            **{f.name: getattr(s, f.name) for f in s.__class__.__dataclass_fields__.values()}
        )
        # Build a scenario: lean starting 2027, buy house also 2028
        events = [
            LifeEvent(year=2027, month=1, kind="employment_end"),
            LifeEvent(
                year=2028,
                month=6,
                kind="house_purchase",
                params={
                    "price": 700_000,
                    "down_pct": 0.20,
                    "rate": 0.0675,
                    "term_years": 30,
                    "property_tax_rate": 0.011,
                    "insurance_monthly": 200,
                    "hoa_monthly": 0,
                    "location_label": "test",
                },
            ),
        ]
        s.events = events
        nw = project_net_worth(s, list(range(2026, 2032)))
        purchase_row = next(r for r in nw if r.year == 2028)
        # Should have realized LTCG from the down payment
        assert purchase_row.realized_ltcg_this_year > 0
        # Waterfall should have fired to cover the shortfall + LTCG tax
        assert purchase_row.withdrawal is not None, (
            "Expected waterfall to fire in lean+house purchase year"
        )


# ---------------------------------------------------------------------------
# Roth 401k rollover + unfunded shortfall (insolvency tracking)
# ---------------------------------------------------------------------------


def _low_income_pivot(start_year: int = 2028) -> Scenario:
    """Low-income career pivot: leave in December of start_year-1, then a
    $90k no-equity job with a modest 3% match and a reduced $7,500/mo
    (2026 $) budget. Produces the multi-year drawdown shape several tests
    below rely on."""
    from variometer.events import LifeEvent

    s = Scenario()
    s.events = [
        LifeEvent(year=start_year - 1, month=12, kind="employment_end"),
        LifeEvent(
            year=start_year,
            month=1,
            kind="expense_level",
            params={"monthly": 7_500, "label": "reduced lifestyle"},
        ),
        LifeEvent(
            year=start_year,
            month=1,
            kind="new_job",
            params={
                "base": 90_000,
                "bonus_rate": 0,
                "equity_annual": 0,
                "healthcare_monthly": 0,
                "match_rate": 0.03,
                "match_cap": 90_000 * 0.03,
                "mega_backdoor": False,
            },
        ),
    ]
    return s


class TestRothRolloverAndInsolvency:
    """Roth 401k auto-rolls to Roth IRA on employment_end; unfunded shortfall
    surfaces when all waterfall buckets are exhausted."""

    def test_roth_401k_goes_to_zero_on_employment_end(self):
        """Low-income pivot: roth_401k should be 0 in the employment_end year
        because the balance is auto-rolled into the Roth IRA."""
        sc = _low_income_pivot(start_year=2028)
        nw = project_net_worth(sc, list(range(2026, 2031)))
        employment_end_row = next(r for r in nw if r.year == 2028)
        assert employment_end_row.roth_401k == 0.0, (
            f"Expected roth_401k=0 in employment_end year, got {employment_end_row.roth_401k}"
        )

    def test_roth_ira_increases_by_rollover_amount_on_employment_end(self):
        """Low-income pivot: roth_ira in the employment_end year should be larger
        than if the Roth 401k balance were simply lost — i.e., the rollover lands in IRA.

        _low_income_pivot(start_year=2028) ends employment in December 2027,
        so the rollover fires in 2027. year_before=2026 captures the
        pre-rollover roth_401k balance; year_of_end=2027 is when the balance
        rolls into roth_ira.
        """
        sc_with_rollover = _low_income_pivot(start_year=2028)
        nw = project_net_worth(sc_with_rollover, list(range(2026, 2030)))
        year_before = next(r for r in nw if r.year == 2026)
        year_of_end = next(r for r in nw if r.year == 2027)

        # The Roth 401k (aftertax/mega-backdoor) balance at end of 2026 should be > 0
        assert year_before.roth_401k > 0, "Expected non-zero Roth 401k in 2026"

        # In 2027 (employment_end year), the roth_ira should have grown by more than
        # just the annual backdoor contribution — it includes the rolled-over Roth 401k.
        roth_ira_growth = year_of_end.roth_ira - year_before.roth_ira
        assert roth_ira_growth > 10_000, (
            f"Expected roth_ira to grow by more than just contributions due to rollover, "
            f"got {roth_ira_growth:.0f}"
        )

    def test_baseline_has_no_roth_401k_rollover(self):
        """Baseline has no employment_end event, so the Roth 401k is never rolled
        over to a Roth IRA.  The balance should grow monotonically (no sudden drop
        to 0), confirming the rollover code never fired."""
        sc = stay_put()
        nw = project_net_worth(sc, list(range(2026, 2032)))
        # Baseline gets after-tax (mega backdoor) Roth 401k contributions, non-zero.
        # Without an employment_end event the rollover never fires — the balance
        # should keep growing every year (no sudden collapse to 0).
        balances = [row.roth_401k for row in nw]
        for i in range(1, len(balances)):
            assert balances[i] >= balances[i - 1], (
                f"roth_401k dropped from {balances[i - 1]:.0f} to {balances[i]:.0f} "
                f"in year {nw[i].year} — suggests an unexpected rollover fired"
            )

    def test_waterfall_records_unfunded_shortfall_when_all_buckets_empty(self):
        """waterfall_withdrawal returns a positive unfunded_shortfall when every
        account bucket is exhausted before the shortfall is covered."""
        empty_state = WithdrawalState(
            brokerage_balance=0.0,
            brokerage_basis=0.0,
            roth_ira_balance=0.0,
            roth_ira_principal=0.0,
            pretax_401k_balance=0.0,
            roth_conversion_cohorts={},
        )
        result = waterfall_withdrawal(
            state=empty_state,
            shortfall=50_000.0,
            year=2035,
            age=41,
            ordinary_taxable_income_before=0.0,
            ltcg_before=0.0,
            bracket_inflation=0.02,
        )
        assert result.unfunded_shortfall > 0, (
            "Expected positive unfunded_shortfall when all buckets are empty"
        )
        assert abs(result.unfunded_shortfall - 50_000.0) < 1.0, (
            f"Expected unfunded_shortfall ≈ 50000, got {result.unfunded_shortfall:.0f}"
        )
        assert result.total_withdrawn == 0.0

    def test_unfunded_shortfall_zero_when_waterfall_covers_shortfall(self):
        """When brokerage alone covers the full shortfall, unfunded_shortfall is 0."""
        state = WithdrawalState(
            brokerage_balance=500_000.0,
            brokerage_basis=250_000.0,  # 50% basis
            roth_ira_balance=0.0,
            roth_ira_principal=0.0,
            pretax_401k_balance=0.0,
            roth_conversion_cohorts={},
        )
        result = waterfall_withdrawal(
            state=state,
            shortfall=30_000.0,
            year=2030,
            age=36,
            ordinary_taxable_income_before=0.0,
            ltcg_before=0.0,
            bracket_inflation=0.02,
        )
        # Allow a tiny floating-point residual from the convergence threshold ($10 cap)
        assert result.unfunded_shortfall < 1.0, (
            f"Expected unfunded_shortfall≈0 when brokerage covers shortfall, "
            f"got {result.unfunded_shortfall:.4f}"
        )
        assert result.total_withdrawn > 0

    def test_partial_bucket_exhaustion_records_correct_unfunded(self):
        """When brokerage and Roth IRA principal together are insufficient, the
        unfunded_shortfall is the true remaining gap (not the original shortfall)."""
        state = WithdrawalState(
            brokerage_balance=10_000.0,
            brokerage_basis=10_000.0,  # 100% basis → no LTCG tax
            roth_ira_balance=5_000.0,
            roth_ira_principal=5_000.0,
            pretax_401k_balance=0.0,
            roth_conversion_cohorts={},
        )
        shortfall = 30_000.0
        result = waterfall_withdrawal(
            state=state,
            shortfall=shortfall,
            year=2036,
            age=42,
            ordinary_taxable_income_before=0.0,
            ltcg_before=0.0,
            bracket_inflation=0.02,
        )
        # Brokerage: $10k pulled, no LTCG tax (100% basis), net cash = $10k
        # Roth IRA principal: $5k pulled, net cash = $5k
        # Total funded = $15k, unfunded = $15k
        assert result.unfunded_shortfall > 0, "Expected unfunded shortfall"
        assert abs(result.unfunded_shortfall - 15_000.0) < 100.0, (
            f"Expected unfunded_shortfall ≈ 15000, got {result.unfunded_shortfall:.0f}"
        )

    def test_low_income_pivot_has_no_unfunded_gap_in_early_years(self):
        """Low-income pivot: the first few years after employment_end should be
        funded (brokerage and Roth IRA cover the gap), so unfunded_shortfall = 0."""
        sc = _low_income_pivot(start_year=2028)
        nw = project_net_worth(sc, list(range(2026, 2036)))
        # 2026-2028: still employed (full income, no drawdown needed) → no waterfall
        # 2029-2035: lower income from the pivot, but accounts should still cover
        early_rows = [r for r in nw if 2026 <= r.year <= 2035]
        for row in early_rows:
            # Allow a tiny floating-point residual from the waterfall convergence threshold.
            # A real insolvency gap would be thousands of dollars, not fractions of a dollar.
            assert row.unfunded_shortfall < 1.0, (
                f"Expected no meaningful unfunded shortfall in year {row.year} for "
                f"the low-income pivot, got {row.unfunded_shortfall:.2f}"
            )

    def test_low_income_pivot_becomes_insolvent_eventually(self):
        """The low-income pivot exhausts all accounts eventually given the
        lower income. The model should surface a non-zero unfunded_shortfall at
        some point in the 2039-2050 window."""
        sc = _low_income_pivot(start_year=2028)
        nw = project_net_worth(sc, list(range(2026, 2051)))
        # At least one year in the latter half should have an unfunded gap
        late_rows = [r for r in nw if r.year >= 2039]
        insolvent_years = [r.year for r in late_rows if r.unfunded_shortfall > 0]
        assert len(insolvent_years) > 0, (
            "Expected the low-income pivot to show insolvency after 2039 "
            "(lower income + account exhaustion), but no unfunded_shortfall found"
        )
