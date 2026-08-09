"""Rough Social Security benefit estimator.

Uses the SSA AIME + bend point formula.  This is a planning tool, not an
official calculation — actual benefits depend on your complete indexed earnings
history, which you can view at ssa.gov/myaccount.

Key terminology
---------------
AIME  — Average Indexed Monthly Earnings (average of your top-35 indexed years)
PIA   — Primary Insurance Amount (monthly benefit at Full Retirement Age)
FRA   — Full Retirement Age (67 for anyone born 1960+)
COLA  — Cost-of-Living Adjustment (typically 2–3% per year)

Reference: https://www.ssa.gov/oact/cola/bendpoints.html

Simplifications vs. actual SSA formula
---------------------------------------
- We skip wage indexing (the AWI step) and treat all historical earnings as if
  they were already expressed in today's dollars.  This over-states AIME for
  early-career low-wage years but is directionally accurate for high earners.
- We project future bend points with a flat 2.8% annual wage growth.
- The 85% SS taxability fraction is always used for federal tax (correct when
  provisional income > $34k, which applies to most high-earner retirement
  scenarios with any investment income).
- CA does not tax SS — that is handled exactly.
"""

from __future__ import annotations

# 2026 bend point projections (actual announced after August 2025 AWI)
# 2025 published values: $1,226 / $7,391 × ~2.8% AWI growth ≈ 2026 values
_BEND_1_2026 = 1_260.0  # 90% → 32% rate crossover (monthly AIME)
_BEND_2_2026 = 7_603.0  # 32% → 15% rate crossover (monthly AIME)
# For retirement income calculations
SS_COLA_DEFAULT = 0.025  # 2.5% annual COLA (historical 10-year average ~2.6%)
SS_FEDERAL_TAXABLE_FRACTION = 0.85  # simplified: applies when provisional income > $34k


def pia_from_aime(aime: float) -> float:
    """Primary Insurance Amount (monthly, today's dollars) from AIME.

    Since all earnings are expressed in today's dollars (AWI indexing skipped),
    we use today's (2026) bend points for consistency — applying future nominal
    bend points to today's-dollar AIME would overstate the benefit.

    Returns benefit at Full Retirement Age (in today's purchasing power).
    """
    b1, b2 = _BEND_1_2026, _BEND_2_2026
    if aime <= 0:
        return 0.0
    elif aime <= b1:
        return 0.90 * aime
    elif aime <= b2:
        return 0.90 * b1 + 0.32 * (aime - b1)
    else:
        return 0.90 * b1 + 0.32 * (b2 - b1) + 0.15 * (aime - b2)


def claim_age_factor(claim_age: int, fra: int = 67) -> float:
    """Multiplier on PIA for early or delayed claiming relative to FRA.

    Early (62–66): −5/9% per month for first 36 months, −5/12% per month after.
    Delayed (68–70): +8% per year (2/3% per month), capped at age 70.
    """
    claim_age = max(62, min(70, claim_age))
    months_diff = (claim_age - fra) * 12
    if months_diff == 0:
        return 1.0
    elif months_diff < 0:
        months_early = -months_diff
        if months_early <= 36:
            return 1.0 - (5 / 9 / 100) * months_early
        else:
            return 1.0 - (5 / 9 / 100) * 36 - (5 / 12 / 100) * (months_early - 36)
    else:
        # +8% per year, credited monthly
        return 1.0 + (8 / 100) * (months_diff / 12)


def estimate_aime(career_earnings: list[float]) -> float:
    """AIME from a list of annual earnings (all in today's dollars).

    Takes the 35 highest years (matching SSA's formula), averages monthly.
    Provide at least 10 years for a useful estimate; <35 years means zeroes
    fill the remaining slots (depressing the AIME).
    """
    if not career_earnings:
        return 0.0
    top_35 = sorted(career_earnings, reverse=True)[:35]
    return sum(top_35) / (35 * 12)


def estimate_monthly_benefit(
    current_age: int,
    current_salary: float,
    prior_career_avg_salary: float = 100_000,
    prior_career_years: int = 10,
    claim_age: int = 67,
    fra: int = 67,
) -> float:
    """Estimate monthly SS benefit in today's dollars.

    Assumptions
    -----------
    - ``prior_career_years`` years of work at ``prior_career_avg_salary``
      (today's dollars; real indexing is skipped per the simplification note).
      Defaults match the Runway tab's estimator inputs.
    - Current salary assumed constant from now until ``claim_age``.
    - Returns PIA × claim_age_factor.

    Returns monthly amount in today's purchasing power.  Use
    ``cola_adjust_monthly`` to convert to nominal dollars in a future year.
    """
    years_until_claim = max(0, claim_age - current_age)

    prior_earnings = [prior_career_avg_salary] * prior_career_years
    future_earnings = [current_salary] * years_until_claim

    all_earnings = prior_earnings + future_earnings
    aime = estimate_aime(all_earnings)

    pia = pia_from_aime(aime)
    factor = claim_age_factor(claim_age, fra)
    return pia * factor


def cola_adjust_monthly(
    monthly_today: float,
    claim_year: int,
    projection_year: int,
    cola: float = SS_COLA_DEFAULT,
) -> float:
    """Convert a today's-dollar monthly benefit to nominal dollars in ``projection_year``.

    Returns 0 if ``projection_year < claim_year`` (SS not yet started).
    """
    if projection_year < claim_year:
        return 0.0
    years_of_cola = projection_year - claim_year
    return monthly_today * (1 + cola) ** years_of_cola


def ss_annual_income(
    monthly_benefit_today_dollars: float,
    claim_year: int,
    projection_year: int,
    cola: float = SS_COLA_DEFAULT,
) -> float:
    """Annual SS income (nominal dollars) for ``projection_year``.

    Returns 0 before ``claim_year``.
    """
    return (
        cola_adjust_monthly(monthly_benefit_today_dollars, claim_year, projection_year, cola) * 12
    )


def ss_federal_taxable(ss_annual: float) -> float:
    """Portion of annual SS included in federal gross income (simplified 85% rule).

    The actual IRS provisional-income test is complex; for planning purposes
    with investment income in retirement, 85% almost always applies.  CA
    exempts SS entirely — the caller is responsible for not adding this to
    CA taxable income.
    """
    return SS_FEDERAL_TAXABLE_FRACTION * ss_annual
