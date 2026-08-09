"""Golden tax cases — external correctness anchors for the 2026 epoch year.

Every other pinned test in this repo proves *self-consistency*: the model still
produces what it produced yesterday. These prove *correctness*: each expected
value was **hand-computed from a cited primary source** and written down before
the model was run against it. None of them is model output.

This file covers the function-level anchors — the ones where wrapping a
scenario around the arithmetic would obscure it rather than exercise it:
bracket walks, LTCG rate ceilings, FICA legs, the §1411 threshold, the ACA
applicable-percentage table, and the IRS contribution limits. The end-to-end
anchors (standard-deduction pass-through, FICA composition, NIIT and LTCG
stacking through ``annual_summary``) live as golden persona bundles in
``tests/personas/golden_2026_*``.

What "golden" claims and does not claim — and the full source list — is in
``docs/GOLDEN_CASES.md``. Two limits matter here:

* **2026 only.** The constants are published actuals *for the epoch year*.
  Later years multiply them by assumed inflation and are projections.
* **Federal and statutory only.** California brackets and the CA standard
  deduction are still projected from 2025 actuals and are excluded. The CA SDI
  *rate* is anchored — it is a published flat rate, not a bracket.

**A failure here is a finding, not a re-pin.** If one of these moves, the model
disagrees with the cited source: fix the model or correct the citation, after
re-deriving the arithmetic by hand.
"""

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from variometer.retirement import limits_for
from variometer.tax import (
    _aca_applicable_percentage,
    _federal_poverty_level,
    aca_premium_tax_credit,
    federal_income_tax,
    fica,
    ltcg_federal_tax,
    niit,
)

# ---------------------------------------------------------------------------
# Sources. The maintainer verifies each of these against its source before release.
# ---------------------------------------------------------------------------

RP_25_32 = (
    "IRS Rev. Proc. 2025-32 (2026 inflation adjustments, post-OBBBA) — "
    "https://www.irs.gov/pub/irs-drop/rp-25-32.pdf"
)
N_25_67 = (
    "IRS Notice 2025-67 (2026 retirement plan COLA limits) — "
    "https://www.irs.gov/pub/irs-drop/n-25-67.pdf"
)
RP_25_25 = (
    "IRS Rev. Proc. 2025-25 §3.01 (2026 Applicable Percentage Table) — "
    "https://www.irs.gov/pub/irs-drop/rp-25-25.pdf"
)
SSA_2026 = "SSA announcement 2025-10-24 — 2026 wage base $184,500, max employee SS tax $11,439"
CA_EDD_2026 = (
    "CA EDD 2026 rates (SDI 1.3%, uncapped) — "
    "https://edd.ca.gov/en/payroll_taxes/rates_and_withholding"
)
HHS_2025_FPL = (
    "HHS 2025 Poverty Guidelines (48 states + DC), which govern the 2026 coverage year — "
    "https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines"
)
IRC_1411 = "IRC §1411 — NIIT 3.8% on the lesser of net investment income or MAGI over $200,000"
IRC_3101 = "IRC §3101(b)(2) — Additional Medicare Tax 0.9% on wages over $200,000"


@dataclass(frozen=True)
class Golden:
    """One externally anchored expectation.

    ``arithmetic`` is the hand computation that produced ``expected`` — it is
    the actual evidence, so it is printed on failure alongside ``source``.
    """

    id: str
    expected: float
    source: str
    arithmetic: str
    compute: Callable[[], float]
    tolerance: float = 0.005  # half a cent


def _gid(case: Golden) -> str:
    return case.id


def assert_golden(case: Golden) -> None:
    got = float(case.compute())
    if abs(got - case.expected) > case.tolerance:
        raise AssertionError(
            f"golden case {case.id} disagrees with its source.\n"
            f"  expected : {case.expected:,.4f}\n"
            f"  got      : {got:,.4f}   (drift {got - case.expected:+,.4f}, "
            f"tolerance ±{case.tolerance})\n"
            f"  source   : {case.source}\n"
            f"  hand arithmetic: {case.arithmetic}\n"
            f"  This is a finding, not a re-pin: the model disagrees with a cited "
            f"primary source. Re-derive the arithmetic before changing anything."
        )


# ---------------------------------------------------------------------------
# 1. Federal ordinary income tax, single, 2026
# ---------------------------------------------------------------------------
#
# Rev. Proc. 2025-32 single brackets and the tax cumulative at each top:
#   10% to    12,400 ->  0.10 x 12,400              =   1,240.00   (cum   1,240.00)
#   12% to    50,400 ->  0.12 x  38,000             =   4,560.00   (cum   5,800.00)
#   22% to   105,700 ->  0.22 x  55,300             =  12,166.00   (cum  17,966.00)
#   24% to   201,775 ->  0.24 x  96,075             =  23,058.00   (cum  41,024.00)
#   32% to   256,225 ->  0.32 x  54,450             =  17,424.00   (cum  58,448.00)
#   35% to   640,600 ->  0.35 x 384,375             = 134,531.25   (cum 192,979.25)
#   37% above 640,600

FEDERAL_ORDINARY_TAX = [
    Golden(
        id="fed_ord/12400_top_of_10pct",
        expected=1_240.00,
        source=RP_25_32,
        arithmetic="0.10 x 12,400 = 1,240.00",
        compute=lambda: federal_income_tax(12_400, 2026),
    ),
    Golden(
        id="fed_ord/20000_inside_12pct",
        expected=2_152.00,
        source=RP_25_32,
        arithmetic="0.10 x 12,400 = 1,240 + 0.12 x (20,000 - 12,400) = 912 => 2,152.00",
        compute=lambda: federal_income_tax(20_000, 2026),
    ),
    Golden(
        id="fed_ord/50400_top_of_12pct",
        expected=5_800.00,
        source=RP_25_32,
        arithmetic="1,240 + 0.12 x (50,400 - 12,400) = 4,560 => 5,800.00",
        compute=lambda: federal_income_tax(50_400, 2026),
    ),
    Golden(
        id="fed_ord/100000_inside_22pct",
        expected=16_712.00,
        source=RP_25_32,
        arithmetic="5,800 + 0.22 x (100,000 - 50,400) = 10,912 => 16,712.00",
        compute=lambda: federal_income_tax(100_000, 2026),
    ),
    Golden(
        id="fed_ord/105700_top_of_22pct",
        expected=17_966.00,
        source=RP_25_32,
        arithmetic="5,800 + 0.22 x (105,700 - 50,400) = 12,166 => 17,966.00",
        compute=lambda: federal_income_tax(105_700, 2026),
    ),
    Golden(
        id="fed_ord/201775_top_of_24pct",
        expected=41_024.00,
        source=RP_25_32,
        arithmetic="17,966 + 0.24 x (201,775 - 105,700) = 23,058 => 41,024.00",
        compute=lambda: federal_income_tax(201_775, 2026),
    ),
    Golden(
        id="fed_ord/300000_inside_35pct",
        expected=73_769.25,
        source=RP_25_32,
        arithmetic=(
            "cumulative at the 32% top (256,225) = 58,448 "
            "+ 0.35 x (300,000 - 256,225) = 15,321.25 => 73,769.25"
        ),
        compute=lambda: federal_income_tax(300_000, 2026),
    ),
    Golden(
        id="fed_ord/640600_top_of_35pct",
        expected=192_979.25,
        source=RP_25_32,
        arithmetic="58,448 + 0.35 x (640,600 - 256,225) = 134,531.25 => 192,979.25",
        compute=lambda: federal_income_tax(640_600, 2026),
    ),
    Golden(
        id="fed_ord/1000000_full_bracket_walk_into_37pct",
        expected=325_957.25,
        source=RP_25_32,
        arithmetic=(
            "1,240 + 4,560 + 12,166 + 23,058 + 17,424 + 134,531.25 = 192,979.25 "
            "+ 0.37 x (1,000,000 - 640,600) = 132,978.00 => 325,957.25"
        ),
        compute=lambda: federal_income_tax(1_000_000, 2026),
    ),
]


@pytest.mark.parametrize("case", FEDERAL_ORDINARY_TAX, ids=_gid)
def test_federal_ordinary_tax_2026(case: Golden) -> None:
    assert_golden(case)


# ---------------------------------------------------------------------------
# 2. LTCG stacking at the published rate ceilings
# ---------------------------------------------------------------------------
#
# Rev. Proc. 2025-32, single: 0% up to 49,450 of stacked taxable income,
# 15% up to 545,500, 20% above. The gain stacks ON TOP of ordinary taxable
# income, so ordinary income consumes the 0% room first.

LTCG_STACKING = [
    Golden(
        id="ltcg/entirely_inside_0pct",
        expected=0.00,
        source=RP_25_32,
        arithmetic="ordinary 10,000 + gain 30,000 = 40,000 < 49,450 => whole gain at 0%",
        compute=lambda: ltcg_federal_tax(30_000, ordinary_taxable_income=10_000, year=2026),
    ),
    Golden(
        id="ltcg/straddles_49450_zero_pct_ceiling",
        expected=1_582.50,
        source=RP_25_32,
        arithmetic=(
            "0% room = 49,450 - 20,000 = 29,450 at 0%; "
            "remaining 40,000 - 29,450 = 10,550 at 15% = 1,582.50"
        ),
        compute=lambda: ltcg_federal_tax(40_000, ordinary_taxable_income=20_000, year=2026),
    ),
    Golden(
        id="ltcg/all_15pct_midrange",
        expected=15_000.00,
        source=RP_25_32,
        arithmetic=(
            "ordinary 200,000 already exceeds 49,450 so no 0% room; "
            "stack 300,000 < 545,500 => 100,000 x 15% = 15,000.00"
        ),
        compute=lambda: ltcg_federal_tax(100_000, ordinary_taxable_income=200_000, year=2026),
    ),
    Golden(
        id="ltcg/exactly_fills_545500_fifteen_pct_ceiling",
        expected=74_407.50,
        source=RP_25_32,
        arithmetic=(
            "no ordinary income: 49,450 at 0%; remaining 545,500 - 49,450 = 496,050 "
            "at 15% = 74,407.50; nothing reaches 20%"
        ),
        compute=lambda: ltcg_federal_tax(545_500, ordinary_taxable_income=0, year=2026),
    ),
    Golden(
        id="ltcg/straddles_545500_into_20pct",
        expected=94_407.50,
        source=RP_25_32,
        arithmetic=(
            "49,450 at 0%; 496,050 at 15% = 74,407.50; "
            "645,500 - 545,500 = 100,000 at 20% = 20,000 => 94,407.50"
        ),
        compute=lambda: ltcg_federal_tax(645_500, ordinary_taxable_income=0, year=2026),
    ),
    Golden(
        id="ltcg/ordinary_income_pushes_gain_across_545500",
        expected=17_725.00,
        source=RP_25_32,
        arithmetic=(
            "ordinary 500,000 leaves no 0% room and only 545,500 - 500,000 = 45,500 "
            "of 15% room: 45,500 x 15% = 6,825 + (100,000 - 45,500) = 54,500 x 20% "
            "= 10,900 => 17,725.00"
        ),
        compute=lambda: ltcg_federal_tax(100_000, ordinary_taxable_income=500_000, year=2026),
    ),
]


@pytest.mark.parametrize("case", LTCG_STACKING, ids=_gid)
def test_ltcg_stacking_2026(case: Golden) -> None:
    assert_golden(case)


# ---------------------------------------------------------------------------
# 3. FICA composition
# ---------------------------------------------------------------------------

FICA_COMPOSITION = [
    Golden(
        id="fica/100k_below_wage_base/social_security",
        expected=6_200.00,
        source=SSA_2026,
        arithmetic="100,000 < wage base 184,500 => 100,000 x 6.2% = 6,200.00",
        compute=lambda: fica(100_000, 2026).social_security,
    ),
    Golden(
        id="fica/100k_below_wage_base/medicare",
        expected=1_450.00,
        source="IRC §3101(b)(1) — Medicare 1.45%, uncapped",
        arithmetic="100,000 x 1.45% = 1,450.00",
        compute=lambda: fica(100_000, 2026).medicare,
    ),
    Golden(
        id="fica/100k_below_wage_base/additional_medicare",
        expected=0.00,
        source=IRC_3101,
        arithmetic="max(0, 100,000 - 200,000) x 0.9% = 0.00",
        compute=lambda: fica(100_000, 2026).additional_medicare,
    ),
    Golden(
        id="fica/100k_below_wage_base/ca_sdi",
        expected=1_300.00,
        source=CA_EDD_2026,
        arithmetic="100,000 x 1.3% = 1,300.00 (uncapped since 2024)",
        compute=lambda: fica(100_000, 2026).ca_sdi,
    ),
    Golden(
        id="fica/100k_below_wage_base/total",
        expected=8_950.00,
        source=f"{SSA_2026}; {CA_EDD_2026}",
        arithmetic="6,200 + 1,450 + 0 + 1,300 = 8,950.00",
        compute=lambda: fica(100_000, 2026).total,
    ),
    Golden(
        id="fica/184500_exactly_at_wage_base/social_security",
        expected=11_439.00,
        source=SSA_2026,
        arithmetic="184,500 x 6.2% = 11,439.00 — the published maximum employee SS tax",
        compute=lambda: fica(184_500, 2026).social_security,
    ),
    Golden(
        id="fica/184500_exactly_at_wage_base/medicare",
        expected=2_675.25,
        source="IRC §3101(b)(1) — Medicare 1.45%, uncapped",
        arithmetic="184,500 x 1.45% = 2,675.25",
        compute=lambda: fica(184_500, 2026).medicare,
    ),
    Golden(
        id="fica/184500_exactly_at_wage_base/ca_sdi",
        expected=2_398.50,
        source=CA_EDD_2026,
        arithmetic="184,500 x 1.3% = 2,398.50",
        compute=lambda: fica(184_500, 2026).ca_sdi,
    ),
    Golden(
        id="fica/300k_capped_ss_plus_surtax/social_security",
        expected=11_439.00,
        source=SSA_2026,
        arithmetic="min(300,000, 184,500) x 6.2% = 11,439.00 — capped at the wage base",
        compute=lambda: fica(300_000, 2026).social_security,
    ),
    Golden(
        id="fica/300k_capped_ss_plus_surtax/medicare",
        expected=4_350.00,
        source="IRC §3101(b)(1) — Medicare 1.45%, uncapped",
        arithmetic="300,000 x 1.45% = 4,350.00",
        compute=lambda: fica(300_000, 2026).medicare,
    ),
    Golden(
        id="fica/300k_capped_ss_plus_surtax/additional_medicare",
        expected=900.00,
        source=IRC_3101,
        arithmetic="(300,000 - 200,000) x 0.9% = 900.00",
        compute=lambda: fica(300_000, 2026).additional_medicare,
    ),
    Golden(
        id="fica/300k_capped_ss_plus_surtax/ca_sdi",
        expected=3_900.00,
        source=CA_EDD_2026,
        arithmetic="300,000 x 1.3% = 3,900.00 (uncapped)",
        compute=lambda: fica(300_000, 2026).ca_sdi,
    ),
    Golden(
        id="fica/300k_capped_ss_plus_surtax/total",
        expected=20_589.00,
        source=f"{SSA_2026}; {IRC_3101}; {CA_EDD_2026}",
        arithmetic="11,439 + 4,350 + 900 + 3,900 = 20,589.00",
        compute=lambda: fica(300_000, 2026).total,
    ),
]


@pytest.mark.parametrize("case", FICA_COMPOSITION, ids=_gid)
def test_fica_composition_2026(case: Golden) -> None:
    assert_golden(case)


# ---------------------------------------------------------------------------
# 4. NIIT — both binding regimes plus the zero
# ---------------------------------------------------------------------------

NIIT_CASES = [
    Golden(
        id="niit/magi_excess_binds",
        expected=380.00,
        source=IRC_1411,
        arithmetic="3.8% x min(30,000, 210,000 - 200,000 = 10,000) = 3.8% x 10,000 = 380.00",
        compute=lambda: niit(30_000, 210_000),
    ),
    Golden(
        id="niit/investment_income_binds",
        expected=1_900.00,
        source=IRC_1411,
        arithmetic="3.8% x min(50,000, 500,000 - 200,000 = 300,000) = 3.8% x 50,000 = 1,900.00",
        compute=lambda: niit(50_000, 500_000),
    ),
    Golden(
        id="niit/below_threshold_is_zero",
        expected=0.00,
        source=IRC_1411,
        arithmetic="MAGI 180,000 < 200,000 threshold => no NIIT",
        compute=lambda: niit(20_000, 180_000),
    ),
    Golden(
        id="niit/exactly_at_threshold_is_zero",
        expected=0.00,
        source=IRC_1411,
        arithmetic="MAGI 200,000 does not EXCEED 200,000 => no NIIT",
        compute=lambda: niit(20_000, 200_000),
    ),
]


@pytest.mark.parametrize("case", NIIT_CASES, ids=_gid)
def test_niit_2026(case: Golden) -> None:
    assert_golden(case)


# ---------------------------------------------------------------------------
# 5. ACA premium tax credit — FPL anchor, table boundaries, interpolation, cliff
# ---------------------------------------------------------------------------
#
# FPL (single, 48 states + DC) for the 2026 coverage year = the HHS 2025
# guidelines: 15,650 first person + 5,500 each additional. So:
#   200% FPL = 31,300      275% FPL = 43,037.50
#   300% FPL = 46,950      400% FPL = 62,600
#
# Rev. Proc. 2025-25 §3.01 applicable percentages: 200% -> 6.60%,
# 250% -> 8.44%, 300% -> 9.96%, 300-400% flat 9.96%, over 400% no credit.
# The credit is benchmark premium minus the required contribution:
#   credit = benchmark_monthly - (applicable_pct x MAGI) / 12
# All ACA cases below use a $700/month benchmark Silver premium.

_BENCH = 700.0


def _ptc(agi: float) -> float:
    return aca_premium_tax_credit(agi=agi, year=2026, monthly_benchmark_premium=_BENCH)


ACA_CASES = [
    Golden(
        id="aca/fpl_anchor_single_2026",
        expected=15_650.00,
        source=HHS_2025_FPL,
        arithmetic="first-person guideline 15,650, exact at the epoch (no projection applied)",
        compute=lambda: _federal_poverty_level(2026),
        tolerance=0.0,
    ),
    Golden(
        id="aca/fpl_anchor_household_of_two_2026",
        expected=21_150.00,
        source=HHS_2025_FPL,
        arithmetic="15,650 + 5,500 = 21,150",
        compute=lambda: _federal_poverty_level(2026, 2),
        tolerance=0.0,
    ),
    Golden(
        id="aca/applicable_pct_at_200pct_fpl",
        expected=0.0660,
        source=RP_25_25,
        arithmetic="table boundary: 200% FPL -> 6.60% exactly",
        compute=lambda: _aca_applicable_percentage(2.00),
        tolerance=1e-9,
    ),
    Golden(
        id="aca/applicable_pct_at_300pct_fpl",
        expected=0.0996,
        source=RP_25_25,
        arithmetic="table boundary: 300% FPL -> 9.96% exactly (and flat to 400%)",
        compute=lambda: _aca_applicable_percentage(3.00),
        tolerance=1e-9,
    ),
    Golden(
        id="aca/applicable_pct_interpolated_at_275pct_fpl",
        expected=0.0920,
        source=RP_25_25,
        arithmetic=(
            "inside the 250-300% band: 8.44% + (2.75 - 2.50)/(3.00 - 2.50) x "
            "(9.96% - 8.44%) = 8.44% + 0.5 x 1.52% = 9.20%"
        ),
        compute=lambda: _aca_applicable_percentage(2.75),
        tolerance=1e-9,
    ),
    Golden(
        id="aca/credit_at_200pct_fpl_boundary",
        expected=527.85,
        source=f"{RP_25_25}; {HHS_2025_FPL}",
        arithmetic=(
            "MAGI 31,300 = 200% of 15,650 -> 6.60%; required = 31,300 x 0.0660 "
            "= 2,065.80 / 12 = 172.15; credit = 700 - 172.15 = 527.85"
        ),
        compute=lambda: _ptc(31_300.00),
    ),
    Golden(
        id="aca/credit_interpolated_at_275pct_fpl",
        expected=370.045833,
        source=f"{RP_25_25}; {HHS_2025_FPL}",
        arithmetic=(
            "MAGI 43,037.50 = 275% of 15,650 -> interpolated 9.20%; required = "
            "43,037.50 x 0.0920 = 3,959.45 / 12 = 329.954167; "
            "credit = 700 - 329.954167 = 370.045833"
        ),
        compute=lambda: _ptc(43_037.50),
    ),
    Golden(
        id="aca/credit_at_300pct_fpl_boundary",
        expected=310.315,
        source=f"{RP_25_25}; {HHS_2025_FPL}",
        arithmetic=(
            "MAGI 46,950 = 300% of 15,650 -> 9.96%; required = 46,950 x 0.0996 "
            "= 4,676.22 / 12 = 389.685; credit = 700 - 389.685 = 310.315"
        ),
        compute=lambda: _ptc(46_950.00),
    ),
    Golden(
        id="aca/credit_just_below_the_400pct_cliff",
        expected=180.42,
        source=f"{RP_25_25}; {HHS_2025_FPL}",
        arithmetic=(
            "MAGI 62,600 = exactly 400% of 15,650 -> 9.96% (still eligible); "
            "required = 62,600 x 0.0996 = 6,234.96 / 12 = 519.58; "
            "credit = 700 - 519.58 = 180.42"
        ),
        compute=lambda: _ptc(62_600.00),
    ),
    Golden(
        id="aca/credit_just_above_the_400pct_cliff",
        expected=0.00,
        source=f"{RP_25_25}; {HHS_2025_FPL}",
        arithmetic=(
            "MAGI 62,601 = 400.0064% of 15,650 -> over 400% FPL, no credit. "
            "The ARPA/IRA subsidy expansion expired end-2025, restoring the cliff: "
            "one dollar of MAGI costs the full 180.42/month."
        ),
        compute=lambda: _ptc(62_601.00),
    ),
]


@pytest.mark.parametrize("case", ACA_CASES, ids=_gid)
def test_aca_premium_tax_credit_2026(case: Golden) -> None:
    assert_golden(case)


# ---------------------------------------------------------------------------
# 6. Retirement contribution limits
# ---------------------------------------------------------------------------

RETIREMENT_LIMITS = [
    Golden(
        id="limits/402g_elective_deferral",
        expected=24_500.00,
        source=N_25_67,
        arithmetic="published 2026 §402(g) limit, used verbatim at the epoch (no projection)",
        compute=lambda: limits_for(2026).employee_401k,
    ),
    Golden(
        id="limits/415c_annual_additions",
        expected=72_000.00,
        source=N_25_67,
        arithmetic="published 2026 §415(c) annual additions limit, used verbatim at the epoch",
        compute=lambda: limits_for(2026).total_401k,
    ),
    Golden(
        id="limits/ira_contribution",
        expected=7_500.00,
        source=N_25_67,
        arithmetic="published 2026 IRA contribution limit, used verbatim at the epoch",
        compute=lambda: limits_for(2026).ira,
    ),
]


@pytest.mark.parametrize("case", RETIREMENT_LIMITS, ids=_gid)
def test_retirement_limits_2026(case: Golden) -> None:
    assert_golden(case)


# ---------------------------------------------------------------------------
# Meta: every golden case must carry its evidence.
# ---------------------------------------------------------------------------

ALL_GOLDEN = (
    FEDERAL_ORDINARY_TAX
    + LTCG_STACKING
    + FICA_COMPOSITION
    + NIIT_CASES
    + ACA_CASES
    + RETIREMENT_LIMITS
)


@pytest.mark.parametrize("case", ALL_GOLDEN, ids=_gid)
def test_every_golden_case_carries_a_citation_and_arithmetic(case: Golden) -> None:
    """The citation is the point — a golden case without one is just a pin."""
    assert case.source.strip(), f"{case.id} has no source"
    assert case.arithmetic.strip(), f"{case.id} has no hand arithmetic"


def test_golden_case_ids_are_unique() -> None:
    ids = [c.id for c in ALL_GOLDEN]
    assert len(ids) == len(set(ids))
