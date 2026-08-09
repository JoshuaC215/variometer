"""401(k) contributions, IRS limits, and employer match.

Allocation order:
  1. Pre-tax + Roth combined respect employee deferral limit ($24,500 in 2026)
  2. Employer match is ``match_rate`` × pre-tax contributions up to ``match_cap``
  3. After-tax (mega backdoor) fills remaining annual addition limit ($72k in 2026)
"""

from dataclasses import dataclass

from .config import MODEL_EPOCH, Scenario

# 2026 limits — published actuals from IRS Notice 2025-67 (401(k)/IRA COLA
# adjustments for 2026). Projected forward via Scenario.limit_inflation.
EMPLOYEE_401K_LIMIT_2026 = 24_500.0
TOTAL_401K_LIMIT_2026 = 72_000.0
IRA_LIMIT_2026 = 7_500.0


@dataclass(frozen=True)
class Limits:
    employee_401k: float  # pre-tax + Roth combined cap
    total_401k: float  # annual addition limit (EE + ER + after-tax)
    ira: float


def limits_for(year: int, inflation: float = 0.025) -> Limits:
    factor = (1 + inflation) ** (year - MODEL_EPOCH)
    return Limits(
        employee_401k=EMPLOYEE_401K_LIMIT_2026 * factor,
        total_401k=TOTAL_401K_LIMIT_2026 * factor,
        ira=IRA_LIMIT_2026 * factor,
    )


@dataclass(frozen=True)
class Contributions:
    pretax: float
    roth: float
    aftertax: float
    employer_match: float

    @property
    def employee_total(self) -> float:
        return self.pretax + self.roth + self.aftertax

    @property
    def annual_additions(self) -> float:
        """All money flowing into the 401(k) plan from any source."""
        return self.employee_total + self.employer_match


def compute_contributions(scenario: Scenario, year: int) -> Contributions:
    """Allocate contributions respecting IRS limits and scenario inputs."""
    base = scenario.base_salary * (1 + scenario.salary_growth) ** (year - MODEL_EPOCH)
    limits = limits_for(year, scenario.limit_inflation)

    desired_pretax = base * scenario.pretax_401k_pct
    desired_roth = base * scenario.roth_401k_pct
    desired_aftertax = base * scenario.aftertax_401k_pct

    # Step 1: pre-tax + Roth share the employee deferral limit.
    # Allocate pre-tax first (higher value at high marginal rate).
    pretax = min(desired_pretax, limits.employee_401k)
    roth = min(desired_roth, limits.employee_401k - pretax)

    # Step 2: employer match — 50% of pre-tax up to cap.
    match = min(pretax * scenario.match_rate, scenario.match_cap)

    # Step 3: after-tax fills remaining annual addition headroom.
    remaining_total = limits.total_401k - pretax - roth - match
    aftertax = max(0.0, min(desired_aftertax, remaining_total))

    return Contributions(
        pretax=pretax,
        roth=roth,
        aftertax=aftertax,
        employer_match=match,
    )
