# ad_hoc/demo_quit_2029.py — the file behind docs/DEMO.md
# Run: uv run variometer explain --python ad_hoc/demo_quit_2029.py
#      uv run variometer explain --python ad_hoc/demo_quit_2029.py --mc --ss-monthly 3500
"""
Question: "What if I quit in June 2029, take two lean years at $5.5k/month,
then take a lower-paying job in 2031?"
──────────────────────────────────────────────────────────────────────────
Timeline:
  2029 Jun  Quit — salary, bonus, employer match and all unvested RSUs stop
  2029 Jun  Lean budget: $5.5k/mo (2026 real dollars) through end of 2030
  2029 Jun  ACA marketplace premium, subsidy-aware, through end of 2030
  2031 Jan  New job at a lower base, smaller bonus and equity, no mega-backdoor

Modeling notes:
  - Built on base_scenario(), so it runs against user_data/profile.json when
    that exists and against the illustrative placeholder persona when it
    doesn't. docs/DEMO.md was generated with the private layer switched off
    (VARIOMETER_USER_DATA=""), i.e. the placeholder persona.
  - LEAN_MONTHLY and NEW_BASE are 2026 real dollars — the model inflates them
    forward, same convention as expense_base and base_salary.
  - "aca": True marks the premium so the projection computes the premium tax
    credit against it; in a low-income window that credit is most of the cost.
  - expense_level with ends_year reverts to the normal budget afterwards,
    which is what makes this a *lean window* rather than a permanent downshift.
"""

from variometer.config import base_scenario
from variometer.events import LifeEvent

s = base_scenario()

# ── Parameters ───────────────────────────────────────────────────────────────

QUIT_YEAR = 2029
QUIT_MONTH = 6
LEAN_END_YEAR = 2030  # last lean year; normal budget resumes in 2031
NEW_JOB_YEAR = 2031

LEAN_MONTHLY = 5_500  # total monthly burn during the gap, 2026 real dollars
ACA_MONTHLY = 650  # marketplace premium before the tax credit

NEW_BASE = 165_000  # 2026 real dollars — a deliberate step down
NEW_BONUS_RATE = 0.10
NEW_EQUITY_ANNUAL = 60_000  # annual RSU grant value at the new job

# ── Events ───────────────────────────────────────────────────────────────────

s.events = [
    LifeEvent(year=QUIT_YEAR, month=QUIT_MONTH, kind="employment_end"),
    LifeEvent(
        year=QUIT_YEAR,
        month=QUIT_MONTH,
        kind="expense_level",
        params={
            "monthly": LEAN_MONTHLY,
            "label": "Lean years",
            "ends_year": LEAN_END_YEAR,
        },
    ),
    LifeEvent(
        year=QUIT_YEAR,
        month=QUIT_MONTH,
        kind="expense_change",
        params={
            "delta_monthly": ACA_MONTHLY,
            "label": "ACA marketplace premium",
            "aca": True,
            "ends_year": LEAN_END_YEAR,
        },
    ),
    LifeEvent(
        year=NEW_JOB_YEAR,
        month=1,
        kind="new_job",
        params={
            "base": NEW_BASE,
            "bonus_rate": NEW_BONUS_RATE,
            "equity_annual": NEW_EQUITY_ANNUAL,
            "healthcare_monthly": 0,
            "match_rate": 0.50,
            "match_cap": 5_000,
            "mega_backdoor": False,
        },
    ),
]

s.summary = (
    f"Quit {QUIT_MONTH}/{QUIT_YEAR}, ${LEAN_MONTHLY:,.0f}/mo lean through {LEAN_END_YEAR} "
    f"(+${ACA_MONTHLY}/mo ACA), new job {NEW_JOB_YEAR} at ${NEW_BASE:,.0f} base "
    f"(2026 $), {NEW_BONUS_RATE:.0%} bonus, ${NEW_EQUITY_ANNUAL:,.0f}/yr equity."
)

# `variometer explain --python` reads this variable (override with --var).
scenario = s
