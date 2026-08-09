"""Federal + California income tax brackets.

2026 brackets are hardcoded; future years are inflation-projected via
`brackets_for_year(year, inflation)`. Real-world IRS bracket inflation runs
~3% / yr historically.

Provenance of the 2026 (MODEL_EPOCH) tables:
  * Federal brackets, standard deduction, and LTCG thresholds are **published
    actuals** from IRS Rev. Proc. 2025-32 (which reflects the One Big Beautiful
    Bill Act).
  * California brackets and standard deduction are still **projected** from
    2025 actuals at ~3%; the FTB publishes official 2026 tables in fall 2026.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Bracket:
    """A single tax bracket. `hi` is the upper bound (exclusive); use a large
    sentinel value for the top bracket."""

    lo: float
    hi: float
    rate: float


# Federal single 2026 — PUBLISHED ACTUALS.
# Source: IRS Rev. Proc. 2025-32 (2026 inflation adjustments, reflecting the
# One Big Beautiful Bill Act). These replace the earlier ~3% projection.
FEDERAL_SINGLE_2026: list[Bracket] = [
    Bracket(0, 12_400, 0.10),
    Bracket(12_400, 50_400, 0.12),
    Bracket(50_400, 105_700, 0.22),
    Bracket(105_700, 201_775, 0.24),
    Bracket(201_775, 256_225, 0.32),
    Bracket(256_225, 640_600, 0.35),
    Bracket(640_600, float("inf"), 0.37),
]

# California single 2026 — PROJECTED, not published.
# Projected from 2025 actuals at ~3%. The FTB publishes the official 2026
# tables in fall 2026; as of Aug 2026 they are not yet available, so these
# figures are excluded from the model's "golden" correctness claims.
# Last bracket includes the 1% mental health surcharge over $1M.
CALIFORNIA_SINGLE_2026: list[Bracket] = [
    Bracket(0, 11_100, 0.01),
    Bracket(11_100, 26_300, 0.02),
    Bracket(26_300, 41_500, 0.04),
    Bracket(41_500, 57_600, 0.06),
    Bracket(57_600, 72_800, 0.08),
    Bracket(72_800, 371_800, 0.093),
    Bracket(371_800, 446_000, 0.103),
    Bracket(446_000, 743_500, 0.113),
    Bracket(743_500, 1_000_000, 0.123),
    Bracket(1_000_000, float("inf"), 0.133),
]

# Standard deductions (single) 2026
# Federal: published actual — IRS Rev. Proc. 2025-32 (post-OBBBA).
FEDERAL_STANDARD_DEDUCTION_2026 = 16_100.0
# California: projected from 2025 actuals at ~3% (official 2026 FTB figure
# not yet published as of Aug 2026).
CALIFORNIA_STANDARD_DEDUCTION_2026 = 5_710.0


def _inflate(brackets: list[Bracket], years: int, inflation: float) -> list[Bracket]:
    factor = (1 + inflation) ** years
    return [Bracket(b.lo * factor, b.hi * factor, b.rate) for b in brackets]


def federal_brackets(year: int, inflation: float = 0.03) -> list[Bracket]:
    """Federal single brackets for a given year. 2026 is the base."""
    return _inflate(FEDERAL_SINGLE_2026, year - 2026, inflation)


def california_brackets(year: int, inflation: float = 0.03) -> list[Bracket]:
    """CA single brackets for a given year. 2026 is the base."""
    return _inflate(CALIFORNIA_SINGLE_2026, year - 2026, inflation)


def federal_standard_deduction(year: int, inflation: float = 0.025) -> float:
    return FEDERAL_STANDARD_DEDUCTION_2026 * (1 + inflation) ** (year - 2026)


def california_standard_deduction(year: int, inflation: float = 0.025) -> float:
    return CALIFORNIA_STANDARD_DEDUCTION_2026 * (1 + inflation) ** (year - 2026)


# Federal long-term capital-gains rate thresholds (single filer).
# PUBLISHED ACTUALS — source: IRS Rev. Proc. 2025-32 (2026 adjustments).
# These replace the earlier projection from 2024 actuals at ~3%/yr.
FEDERAL_LTCG_ZERO_TOP_2026: float = 49_450.0  # below this: 0% rate
FEDERAL_LTCG_FIFTEEN_TOP_2026: float = 545_500.0  # above this: 20% rate


def federal_ltcg_brackets(year: int, inflation: float = 0.03) -> tuple[float, float]:
    """Return (zero_top, fifteen_top) for federal LTCG brackets, inflated to ``year``.

    Single filer thresholds.  LTCG stacks on top of ordinary taxable income for
    bracket purposes.

    Returns:
        zero_top:    income ceiling for the 0% LTCG rate
        fifteen_top: income ceiling for the 15% LTCG rate (above → 20%)
    """
    factor = (1 + inflation) ** (year - 2026)
    return (
        FEDERAL_LTCG_ZERO_TOP_2026 * factor,
        FEDERAL_LTCG_FIFTEEN_TOP_2026 * factor,
    )
