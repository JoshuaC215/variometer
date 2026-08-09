# ad_hoc/example_promo_house_fire.py — CANONICAL EXAMPLE
# Run: uv run variometer explain --python ad_hoc/example_promo_house_fire.py
#      uv run variometer explain --python ad_hoc/example_promo_house_fire.py --mc --ss-monthly 3500
#
# This file is the reference example for ad-hoc scenario modeling.
# Copy it, rename it, tweak the parameters at the top, and re-run.
# See the ad_hoc/ convention in CLAUDE.md and .claude/skills/manage-scenarios/SKILL.md.
"""
Scenario: promotion → buy a home → FIRE with a Roth conversion ladder
─────────────────────────────────────────────────────────────────────
Timeline:
  2028 Mar  Internal promotion (+15% base, 20% bonus, +50% equity refresh target)
  2029 Jun  Buy an $900k house (20% down, 30yr @ 6.75%)
  2034 Jan  FIRE: lean non-housing budget, ACA healthcare, annual Roth conversions

Modeling notes:
  - Composes the same events the named factories use — an ad-hoc file is just
    a factory without the registration.
  - Start from base_scenario(), not Scenario(): base_scenario() applies your
    user_data/profile.json overlay, so the numbers are yours. Bare Scenario()
    is the illustrative placeholder persona.
  - Roth conversions ($30k/yr from FIRE_YEAR) roughly fill the low federal
    brackets during the low-income window and season principal for
    penalty-free access five calendar years later.
  - 'child' events are UI timeline markers only — model childcare costs as a
    separate expense_change.
"""

from variometer.config import PROJECTION_END_YEAR, base_scenario
from variometer.events import LifeEvent

s = base_scenario()

# ── Parameters ───────────────────────────────────────────────────────────────

PROMO_YEAR = 2028
PROMO_MONTH = 3
HOUSE_YEAR = 2029
HOUSE_MONTH = 6
FIRE_YEAR = 2034

LEAN_MONTHLY = 6_000  # FIRE non-housing budget, 2026 real dollars
ACA_MONTHLY = 700  # marketplace premium, flat nominal (subsidy-aware)
ROTH_CONVERSION = 30_000  # per year, from FIRE_YEAR on

# Promotion math (mirrors the internal_promotion() factory)
_salary_at_promo = s.base_salary * (1 + s.salary_growth) ** (PROMO_YEAR - 2026)
_new_base = _salary_at_promo * 1.15  # +15% base
_refresh_at_promo = s.equity_refresh_grant_value * (
    (1 + s.equity_refresh_growth) ** (PROMO_YEAR - s.equity_refresh_base_year)
)

# ── Events ───────────────────────────────────────────────────────────────────

s.events = [
    LifeEvent(
        year=PROMO_YEAR,
        month=PROMO_MONTH,
        kind="pay_change",
        params={
            "new_base": _new_base,
            "bonus_rate": 0.20,
            "equity_refresh_grant_value": _refresh_at_promo * 1.5,
        },
    ),
    LifeEvent(
        year=PROMO_YEAR,
        month=s.refresh_issue_month,
        kind="equity_grant",
        params={"value": _refresh_at_promo * 0.5, "label": "Promo grant"},
    ),
    LifeEvent(
        year=HOUSE_YEAR,
        month=HOUSE_MONTH,
        kind="house_purchase",
        params={
            "price": 900_000,
            "down_pct": 0.20,
            "rate": 0.0675,
            "term_years": 30,
            "property_tax_rate": 0.011,
            "insurance_monthly": 250,
            "hoa_monthly": 0,
            "location_label": "new home",
            "replaces_monthly_rent": True,
            "appreciation_rate": 0.03,
            "maintenance_pct": 0.01,
        },
    ),
    LifeEvent(year=FIRE_YEAR, month=1, kind="employment_end"),
    LifeEvent(
        year=FIRE_YEAR,
        month=1,
        kind="expense_level",
        params={"monthly": LEAN_MONTHLY, "label": "FIRE non-housing budget"},
    ),
    LifeEvent(
        year=FIRE_YEAR,
        month=1,
        kind="expense_change",
        # "aca": True marks this premium for the ACA premium-tax-credit calc.
        params={"delta_monthly": ACA_MONTHLY, "label": "ACA marketplace premium", "aca": True},
    ),
] + [
    LifeEvent(
        year=yr,
        month=1,
        kind="roth_conversion",
        params={"amount": ROTH_CONVERSION, "recurring": False},
    )
    for yr in range(FIRE_YEAR, PROJECTION_END_YEAR)
]

s.summary = (
    f"Promotion {PROMO_YEAR}, buy $900k home {HOUSE_YEAR}, FIRE Jan {FIRE_YEAR}. "
    f"${LEAN_MONTHLY:,.0f}/mo non-housing budget (2026 $) + ${ACA_MONTHLY}/mo ACA. "
    f"${ROTH_CONVERSION:,.0f}/yr Roth conversions from {FIRE_YEAR}."
)

# `variometer explain --python` reads this variable (override with --var).
scenario = s
