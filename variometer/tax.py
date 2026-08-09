"""Tax math — federal, California, FICA, NIIT.

All pure functions. Brackets and limits live in `data/brackets.py`.
"""

from dataclasses import dataclass

from .data.brackets import (
    Bracket,
    california_brackets,
    federal_brackets,
    federal_ltcg_brackets,
)

# ---- IRS limits (2026 baselines, single filer) ----
# Published actual: SSA announcement of 2025-10-24 set the 2026 Social Security
# wage base at $184,500 (max employee SS tax $11,439 = 184,500 × 6.2%).
SS_WAGE_BASE_2026 = 184_500.0
SS_RATE = 0.062
MEDICARE_RATE = 0.0145
# Additional Medicare tax: statutory and unindexed — IRC §3101(b)(2).
ADDL_MEDICARE_RATE = 0.009
ADDL_MEDICARE_THRESHOLD_SINGLE = 200_000.0
# CA EDD 2026: SDI withholding rate 1.3%, uncapped (the taxable wage ceiling
# was removed effective 2024).
CA_SDI_RATE = 0.013
# Net Investment Income Tax: statutory and unindexed — IRC §1411.
NIIT_RATE = 0.038
NIIT_THRESHOLD_SINGLE = 200_000.0
LTCG_RATE_15 = 0.15
LTCG_RATE_20 = 0.20


def progressive_tax(income: float, brackets: list[Bracket]) -> float:
    """Step through brackets, summing tax owed."""
    if income <= 0:
        return 0.0
    tax = 0.0
    for b in brackets:
        if income <= b.lo:
            break
        taxable_in_bracket = min(income, b.hi) - b.lo
        tax += taxable_in_bracket * b.rate
    return tax


def federal_income_tax(taxable_ordinary: float, year: int, inflation: float = 0.03) -> float:
    """Federal ordinary-income tax (excludes LTCG, which has its own rate)."""
    return progressive_tax(taxable_ordinary, federal_brackets(year, inflation))


def ca_income_tax(taxable: float, year: int, inflation: float = 0.03) -> float:
    """CA income tax. CA taxes LTCG as ordinary — pass total taxable income."""
    return progressive_tax(taxable, california_brackets(year, inflation))


@dataclass(frozen=True)
class FicaBreakdown:
    social_security: float
    medicare: float
    additional_medicare: float
    ca_sdi: float

    @property
    def total(self) -> float:
        return self.social_security + self.medicare + self.additional_medicare + self.ca_sdi


def fica(wages: float, year: int, limit_inflation: float = 0.025) -> FicaBreakdown:
    """Compute SS + Medicare + addl Medicare + CA SDI on wage income."""
    ss_base = SS_WAGE_BASE_2026 * (1 + limit_inflation) ** (year - 2026)
    return FicaBreakdown(
        social_security=min(wages, ss_base) * SS_RATE,
        medicare=wages * MEDICARE_RATE,
        additional_medicare=max(0, wages - ADDL_MEDICARE_THRESHOLD_SINGLE) * ADDL_MEDICARE_RATE,
        ca_sdi=wages * CA_SDI_RATE,
    )


def niit(investment_income: float, agi: float) -> float:
    """3.8% Net Investment Income Tax on inv income, only if AGI exceeds
    the threshold. (Actual rule: 3.8% × min(inv_income, AGI − threshold);
    we use the simpler conservative form since at this income level the
    binding constraint is always inv_income.)"""
    if agi <= NIIT_THRESHOLD_SINGLE:
        return 0.0
    return min(investment_income, agi - NIIT_THRESHOLD_SINGLE) * NIIT_RATE


def ltcg_federal_tax(
    ltcg: float,
    ordinary_taxable_income: float = 0.0,
    year: int = 2026,
    bracket_inflation: float = 0.03,
) -> float:
    """Federal long-term capital gains tax using the three-rate bracket structure.

    LTCG stacks on top of ordinary taxable income for bracket-placement purposes.
    The 0% bracket applies to the portion of (ordinary + LTCG) that falls below
    the zero_top threshold.  The 15% bracket applies to the remainder up to
    fifteen_top.  The 20% rate applies above fifteen_top.

    Args:
        ltcg:                      Total long-term capital gain to tax.
        ordinary_taxable_income:   Federal ordinary taxable income BEFORE LTCG
                                   (after deductions, before LTCG is stacked on).
        year:                      Tax year (used to inflate bracket thresholds).
        bracket_inflation:         Annual inflation rate for brackets.
    """
    if ltcg <= 0:
        return 0.0

    zero_top, fifteen_top = federal_ltcg_brackets(year, bracket_inflation)

    # How much of the 0% bucket remains after ordinary income fills it?
    ltcg_below_zero_top = max(0.0, zero_top - ordinary_taxable_income)
    ltcg_at_0pct = min(ltcg, ltcg_below_zero_top)
    ltcg_remaining = ltcg - ltcg_at_0pct

    if ltcg_remaining <= 0:
        return 0.0

    # Where does the ordinary income + 0%-taxed LTCG land relative to the 15% top?
    combined_floor = max(ordinary_taxable_income, zero_top)
    ltcg_below_fifteen_top = max(0.0, fifteen_top - combined_floor)
    ltcg_at_15pct = min(ltcg_remaining, ltcg_below_fifteen_top)
    ltcg_at_20pct = max(0.0, ltcg_remaining - ltcg_at_15pct)

    return ltcg_at_15pct * LTCG_RATE_15 + ltcg_at_20pct * LTCG_RATE_20


# ---------------------------------------------------------------------------
# ACA Premium Tax Credit (post-enhancement §36B rules, 2026 table)
# ---------------------------------------------------------------------------

# Federal Poverty Level baselines (48 contiguous states + DC).
# PTC eligibility for coverage year Y uses the HHS poverty guidelines published
# in January of Y−1. For the 2026 coverage year that is the **2025** HHS
# guidelines: $15,650 for the first person, $5,500 for each additional person.
# Anchored at the MODEL_EPOCH and inflated forward at ~3%/yr thereafter.
_FPL_2026_COVERAGE_SINGLE = 15_650.0
_FPL_2026_COVERAGE_PER_ADDITIONAL = 5_500.0
_FPL_INFLATION = 0.03  # approximate annual HHS adjustment


def _federal_poverty_level(year: int, household_size: int = 1) -> float:
    """Estimated Federal Poverty Level applicable to ``year``'s coverage.

    Exact for 2026 (HHS 2025 Poverty Guidelines, used for the 2026 coverage
    year); projected at ``_FPL_INFLATION`` for other years.
    """
    base = _FPL_2026_COVERAGE_SINGLE + _FPL_2026_COVERAGE_PER_ADDITIONAL * max(
        0, household_size - 1
    )
    return base * (1 + _FPL_INFLATION) ** (year - 2026)


def _aca_applicable_percentage(income_pct_fpl: float) -> float:
    """Required contribution as % of income under §36B of the IRC.

    Uses the **2026 Applicable Percentage Table** published in IRS
    Rev. Proc. 2025-25 §3.01, with linear interpolation within each band.
    This is the post-enhancement schedule: the ARPA/IRA subsidy expansion
    expired at the end of 2025, so the 400% FPL subsidy cliff is restored.

    The table is pinned to plan year 2026 (the MODEL_EPOCH) and held constant
    for later years. The IRS re-indexes it annually by a premium-growth /
    income-growth ratio that does not follow general inflation, so projecting
    it forward would be spurious precision — holding it flat is a stated
    approximation.

    Returns float("inf") for income > 400% FPL (no subsidy available).
    """
    if income_pct_fpl > 4.00:
        return float("inf")
    # (lo_fpl_pct, hi_fpl_pct, lo_req_pct, hi_req_pct)
    bands = [
        (1.00, 1.33, 0.0210, 0.0210),
        (1.33, 1.50, 0.0314, 0.0419),
        (1.50, 2.00, 0.0419, 0.0660),
        (2.00, 2.50, 0.0660, 0.0844),
        (2.50, 3.00, 0.0844, 0.0996),
        (3.00, 4.00, 0.0996, 0.0996),
    ]
    for lo_pct, hi_pct, lo_req, hi_req in bands:
        if income_pct_fpl <= hi_pct:
            frac = (income_pct_fpl - lo_pct) / (hi_pct - lo_pct)
            return lo_req + frac * (hi_req - lo_req)
    return 0.0996


def aca_premium_tax_credit(
    agi: float,
    year: int,
    household_size: int = 1,
    monthly_benchmark_premium: float = 0.0,
) -> float:
    """Monthly ACA premium tax credit under the post-enhancement §36B rules.

    The IRA-era enhanced subsidies expired end-2025, restoring the 400% FPL
    cliff. Uses the 2026 applicable-percentage table (IRS Rev. Proc. 2025-25
    §3.01) held constant for later years — see ``_aca_applicable_percentage``.

    Net monthly premium = max(0, monthly_benchmark_premium - return_value).

    Args:
        agi: Modified AGI (wages + LTCG + other income).
        year: Calendar year (used to inflate the FPL baseline).
        household_size: Number of people covered.
        monthly_benchmark_premium: The second-lowest-cost Silver plan monthly
            premium for the household.  Pass the gross healthcare_monthly from
            the scenario as an approximation.

    Returns:
        Monthly subsidy in dollars.  0.0 if above 400% FPL or premium is 0.
    """
    if monthly_benchmark_premium <= 0 or agi <= 0:
        return 0.0

    fpl = _federal_poverty_level(year, household_size)
    income_pct_fpl = agi / fpl

    if income_pct_fpl < 1.00 or income_pct_fpl > 4.00:
        return 0.0

    applicable_pct = _aca_applicable_percentage(income_pct_fpl)
    if applicable_pct == float("inf"):
        return 0.0

    required_monthly = (applicable_pct * agi) / 12
    monthly_credit = monthly_benchmark_premium - required_monthly
    return max(0.0, min(monthly_credit, monthly_benchmark_premium))
