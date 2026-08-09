"""Named scenarios — the public archetype library + comparison helpers.

Each scenario is a small factory that returns a fully populated ``Scenario``,
built by appending ``LifeEvent`` perturbations onto the user's baseline
(``base_scenario()`` — bare defaults + the ``user_data/profile.json`` overlay).
The machinery for registering and introspecting scenarios lives in
``scenario_registry.py`` so this file stays clean and focused on the actual
scenarios — read it like a config.

This library deliberately carries only **bare career-shape archetypes** any
tech worker can relate to: Stay put, Internal promotion, Change jobs,
Sabbatical / career gap, FIRE with Roth ladder, Buy home. Your own tuned
compositions (specific exit years, cities, prices, partner assumptions)
belong in ``user_data/scenarios.py`` — same ``@scenario`` decorator, loaded
automatically at startup, private to you. See docs/SCENARIOS.md.

Adding a scenario: write a function decorated with ``@scenario("Display name")``,
annotate each user-tunable parameter with ``Annotated[type, Knob("Label", ...)]``,
build ``s.events``, set ``s.summary``, return. The Streamlit UI picks it up
automatically.
"""
from __future__ import annotations

from typing import Annotated

from .config import MODEL_EPOCH, PROJECTION_END_YEAR, Scenario, base_scenario
from .events import LifeEvent

# Re-export the registry API so consumers can do
# ``from variometer.scenarios import SCENARIO_REGISTRY, knobs_for_scenario``.
from .scenario_registry import (
    SCENARIO_REGISTRY,
    BoundKnob,
    Knob,
    apply_core_overrides,
    grouped_scenario_names,
    is_builtin_scenario,
    knobs_for,
    knobs_for_scenario,
    scenario,
    visible_scenario_names,
)

__all__ = [
    # Registry API (re-exported)
    "BoundKnob",
    "Knob",
    "SCENARIO_REGISTRY",
    "apply_core_overrides",
    "grouped_scenario_names",
    "is_builtin_scenario",
    "knobs_for",
    "knobs_for_scenario",
    "scenario",
    "visible_scenario_names",
    # Archetype factories
    "stay_put",
    "internal_promotion",
    "change_jobs",
    "sabbatical",
    "fire_roth_ladder",
    "buy_home",
]


# ---------------------------------------------------------------------------
# Archetype factories
# ---------------------------------------------------------------------------

@scenario("Stay put")
def stay_put() -> Scenario:
    """No career or housing changes — the baseline everything else is compared against."""
    s = base_scenario()
    s.summary = (
        "Staying at the current employer and continuing to rent. No career or housing "
        "changes — this is the baseline everything else is compared against."
    )
    return s


@scenario("Internal promotion")
def internal_promotion(
    promo_year:         Annotated[int,   Knob("Promotion year",                 min=2026, max=2040, step=1)]                = 2028,
    promo_month:        Annotated[int,   Knob("Promotion month",                min=1,    max=12,   step=1)]                = 3,
    base_bump:          Annotated[float, Knob("Base salary bump",               "percent", min=0.05, max=0.40, step=0.01)]  = 0.15,
    new_bonus_rate:     Annotated[float, Knob("New bonus rate",                 "percent", min=0.10, max=0.40, step=0.01)]  = 0.20,
    equity_bump:        Annotated[float, Knob("Future refresh target bump",     "percent", min=0.00, max=2.00, step=0.05)]  = 0.50,
    one_time_grant_pct: Annotated[float, Knob("One-time grant (% of refresh)",  "percent", min=0.00, max=2.00, step=0.05)]  = 0.50,
) -> Scenario:
    """Internal promotion — base bump, higher bonus, larger equity refresh.

    The promo applies multiplicative bumps relative to the in-flight comp:
      * Base salary  ×(1 + base_bump) from promo_year/month forward
      * Bonus rate   set to ``new_bonus_rate`` (absolute, applied from promo_year)
      * Refresh grant target  ×(1 + equity_bump) from promo_year forward
      * One-time supplemental equity grant in promo_year sized at
        ``one_time_grant_pct`` × the pre-promo refresh target (vests over 48 mo)

    Uses a ``pay_change`` event so the existing vest schedule is untouched,
    and an ``equity_grant`` event for the one-time bonus grant.  Pre-promo
    years are identical to the baseline.
    """
    s = base_scenario()
    # Salary grown to promo year, then bumped.
    salary_at_promo = s.base_salary * (1 + s.salary_growth) ** (promo_year - MODEL_EPOCH)
    new_base = salary_at_promo * (1 + base_bump)
    # Refresh grant target grown to promo year (pre-bump), used for the one-time
    # grant size; the new ongoing target is the bumped value.
    refresh_at_promo = s.equity_refresh_grant_value * (
        (1 + s.equity_refresh_growth) ** (promo_year - s.equity_refresh_base_year)
    )
    new_refresh_target = refresh_at_promo * (1 + equity_bump)
    one_time_value = refresh_at_promo * one_time_grant_pct

    events = [
        LifeEvent(
            year=promo_year, month=promo_month, kind="pay_change",
            params={
                "new_base": new_base,
                "bonus_rate": new_bonus_rate,
                "equity_refresh_grant_value": new_refresh_target,
            },
        ),
    ]
    if one_time_value > 0:
        events.append(
            LifeEvent(
                year=promo_year, month=s.refresh_issue_month, kind="equity_grant",
                params={"value": one_time_value, "label": "Promo grant"},
            )
        )
    s.events = events
    s.summary = (
        f"Promoted in {promo_year} (month {promo_month}). "
        f"Base +{base_bump:.0%} → ${new_base:,.0f}, bonus {new_bonus_rate:.0%}, "
        f"refresh target +{equity_bump:.0%} → ${new_refresh_target:,.0f}/grant, "
        f"one-time grant ${one_time_value:,.0f}."
    )
    return s


@scenario("Change jobs")
def change_jobs(
    start_year:        Annotated[int,   Knob("Start year",            min=2026, max=2050, step=1)]                       = 2028,
    new_base:          Annotated[float, Knob("New base salary ($)",   "dollars", min=50_000, max=500_000, step=5_000)]   = 180_000,
    new_bonus_rate:    Annotated[float, Knob("New bonus rate",        "percent", min=0.0,  max=0.40, step=0.01)]          = 0.10,
    new_equity_annual: Annotated[float, Knob("New annual equity ($)", "dollars", min=0,      max=500_000, step=5_000)]   = 60_000,
    new_match_rate:    Annotated[float, Knob("401(k) match rate",     "percent", min=0.0,  max=0.10, step=0.005)]         = 0.05,
    new_match_cap:     Annotated[float, Knob("401(k) match cap ($)",  "dollars", min=0,      max=30_000,  step=500)]      = 8_000,
    has_mega_backdoor: Annotated[bool,  Knob("Has mega backdoor?")]                                                      = False,
) -> Scenario:
    """Move to a different company.

    Healthcare is employer-paid, the match defaults to a typical Safe-Harbor
    5% capped at $8k, and mega backdoor is off by default (most companies
    don't offer it). Equity is modelled as ``equity_annual`` W-2 income spread
    monthly — a deliberate simplification vs. modelling another grant schedule.

    ``employment_end`` lands in March (Q1 two-week-notice timing) and
    ``new_job`` starts in April, capturing a one-month income gap.
    """
    s = base_scenario()
    s.events = [
        LifeEvent(year=start_year, month=3, kind="employment_end"),
        LifeEvent(
            year=start_year, month=4, kind="new_job",
            params={
                "base": new_base,
                "bonus_rate": new_bonus_rate,
                "equity_annual": new_equity_annual,
                "healthcare_monthly": 0,
                "match_rate": new_match_rate,
                "match_cap": new_match_cap,
                "mega_backdoor": has_mega_backdoor,
            },
        ),
    ]
    s.summary = (
        f"Change jobs in {start_year}. "
        f"${new_base:,.0f} base + ${new_equity_annual:,.0f}/yr equity + "
        f"{new_bonus_rate:.0%} bonus target. One month gap before starting. "
        f"Healthcare employer-paid, {new_match_rate:.0%} 401(k) match."
    )
    return s


@scenario("Sabbatical / career gap")
def sabbatical(
    start_year:            Annotated[int,   Knob("Start year",                 min=2026, max=2050, step=1)]                = 2030,
    return_year:           Annotated[int,   Knob("Return year",                min=2026, max=2055, step=1)]                = 2032,
    lean_monthly_expenses: Annotated[float, Knob("Lean budget/mo (2026 $)",    "dollars", min=2_000, max=15_000, step=250)] = 7_000,
    healthcare_monthly:    Annotated[float, Knob("Healthcare monthly ($)",     "dollars", min=0,     max=3_000,  step=50)]  = 700,
    return_base:           Annotated[float, Knob("Return-job base (2026 $)",   "dollars", min=50_000, max=500_000, step=5_000)] = 160_000,
    return_equity_annual:  Annotated[float, Knob("Return-job equity/yr ($)",   "dollars", min=0,      max=500_000, step=5_000)] = 60_000,
) -> Scenario:
    """No W-2 income for [start_year, return_year). Then return to work.

    ``lean_monthly_expenses`` is in 2026 real dollars — the model inflates it
    at ``expense_growth`` from 2026.  The expense_level event starts in month 4
    of start_year (employment ends month 3) and expires after return_year-1,
    reverting to the original expense_base trajectory when work resumes.

    The ACA premium is a flat nominal add-on (not base-derived) so it stays as
    a separate expense_change event.

    The return-to-work new_job base is also in 2026 real dollars and grows at
    ``salary_growth`` from 2026 onward.
    """
    s = base_scenario()
    s.events = [
        LifeEvent(year=start_year, month=3, kind="employment_end"),
        LifeEvent(
            year=start_year, month=4, kind="expense_level",
            params={
                "monthly": lean_monthly_expenses,
                "label": f"lean spending — ${lean_monthly_expenses:,.0f}/mo (2026 $)",
                "ends_year": return_year - 1,
            },
        ),
        LifeEvent(
            year=start_year, month=4, kind="expense_change",
            params={
                "delta_monthly": healthcare_monthly,
                "label": "ACA marketplace healthcare premium",
                "aca": True,
                "ends_year": return_year - 1,
            },
        ),
        LifeEvent(
            year=return_year, month=1, kind="new_job",
            params={
                "base": return_base,
                "bonus_rate": 0.10,
                "equity_annual": return_equity_annual,
                "healthcare_monthly": 0,
                "match_rate": 0.05,
                "match_cap": 8_000,
                "mega_backdoor": False,
            },
        ),
    ]
    years_off = return_year - start_year
    s.summary = (
        f"{years_off} year{'s' if years_off != 1 else ''} off from {start_year}–{return_year - 1}. "
        f"Living on ${lean_monthly_expenses:,.0f}/mo (2026 $) + ${healthcare_monthly:,.0f}/mo ACA healthcare. "
        f"Return to a ${return_base:,.0f} (2026 $) job in {return_year}."
    )
    return s


@scenario("FIRE with Roth ladder")
def fire_roth_ladder(
    exit_year:              Annotated[int,   Knob("Exit year",                     min=2026, max=2055, step=1)]                  = 2035,
    lean_monthly:           Annotated[float, Knob("FIRE total budget/mo (2026 $)", "dollars", min=2_000, max=15_000, step=250)]   = 7_000,
    healthcare_monthly:     Annotated[float, Knob("Healthcare/mo ($)",             "dollars", min=0,     max=3_000,  step=50)]    = 700,
    roth_conversion_annual: Annotated[float, Knob("Roth conversion/yr ($)",        "dollars", min=0,     max=100_000, step=5_000)] = 30_000,
) -> Scenario:
    """Permanent early retirement with an annual Roth conversion ladder.

    Employment ends January of ``exit_year``; expenses drop to ``lean_monthly``
    (2026 real dollars, rent included) plus an ACA marketplace premium (flat
    nominal, subsidy-aware in projection). Each year from exit onward converts
    ``roth_conversion_annual`` from pre-tax 401k into a Roth cohort at low
    marginal rates; each cohort seasons for 5 calendar years before its
    principal is penalty-free accessible.
    """
    s = base_scenario()
    roth_events = [
        LifeEvent(
            year=yr, month=1, kind="roth_conversion",
            params={"amount": roth_conversion_annual, "recurring": False},
        )
        for yr in range(exit_year, PROJECTION_END_YEAR)
        if roth_conversion_annual > 0
    ]
    s.events = [
        LifeEvent(year=exit_year, month=1, kind="employment_end"),
        LifeEvent(
            year=exit_year, month=1, kind="expense_level",
            params={"monthly": lean_monthly, "label": "FIRE budget"},
        ),
        # ACA premium — flat nominal add-on; no ends_year → runs indefinitely
        LifeEvent(
            year=exit_year, month=1, kind="expense_change",
            params={"delta_monthly": healthcare_monthly, "label": "ACA marketplace premium",
                    "aca": True},
        ),
    ] + roth_events
    s.summary = (
        f"FIRE January {exit_year}. ${lean_monthly:,.0f}/mo total budget "
        f"(2026 $, grows with inflation) + ${healthcare_monthly:,.0f}/mo ACA. "
        f"${roth_conversion_annual:,.0f}/yr Roth conversions build the tax-free ladder."
    )
    return s


@scenario("Buy home")
def buy_home(
    purchase_year:     Annotated[int,   Knob("Purchase year",         min=2026, max=2050, step=1)]                          = 2028,
    purchase_month:    Annotated[int,   Knob("Purchase month",        min=1,    max=12,   step=1)]                          = 6,
    price:             Annotated[float, Knob("Purchase price ($)",    "dollars", min=100_000, max=5_000_000, step=50_000)]  = 800_000,
    location:          Annotated[str,   Knob("Location label")]                                                             = "",
    down_pct:          Annotated[float, Knob("Down payment %",        "percent", min=0.05, max=0.40, step=0.05)]             = 0.20,
    rate:              Annotated[float, Knob("Mortgage rate",         "percent", min=0.03, max=0.10, step=0.0025)]           = 0.0675,
    term_years:        Annotated[int,   Knob("Term (years)",          "choice",  choices=(15, 20, 30))]                     = 30,
    property_tax_rate: Annotated[float, Knob("Property tax rate",     "percent", min=0.005, max=0.025, step=0.001)]          = 0.011,
    insurance_monthly: Annotated[float, Knob("Insurance monthly ($)", "dollars", min=0,    max=2_000, step=25)]              = 300,
    hoa_monthly:       Annotated[float, Knob("HOA monthly ($)",       "dollars", min=0,    max=2_000, step=50)]              = 0,
    appreciation_rate: Annotated[float, Knob("Appreciation / yr",     "percent", min=0.0, max=0.08, step=0.005,
                                              help="Nominal annual price appreciation. Set to 0 to stress-test a flat-price decade. Paper wealth only — doesn't affect cashflow.")] = 0.03,
    maintenance_pct:   Annotated[float, Knob("Maintenance / yr",      "percent", min=0.0, max=0.03, step=0.0025,
                                              help="Annual maintenance + capex as % of price. Rule of thumb: 1%/yr (mix of routine repairs and amortized big-ticket items). Real cashflow drain — leaving it out understates ownership cost.")] = 0.01,
) -> Scenario:
    """Buy a home.

    Down payment comes from brokerage in the purchase year; LTCG tax on the
    realized portion is routed into net surplus. Mortgage P&I + property tax
    + insurance + HOA + maintenance replace the rent component from
    ``purchase_month`` on. Tune price / tax rate / insurance for your market
    (HCOL metros run higher price + lower tax rate; LCOL the reverse).
    """
    s = base_scenario()
    s.events = [
        LifeEvent(
            year=purchase_year, month=purchase_month, kind="house_purchase",
            params={
                "price": price,
                "down_pct": down_pct,
                "rate": rate,
                "term_years": term_years,
                "property_tax_rate": property_tax_rate,
                "insurance_monthly": insurance_monthly,
                "hoa_monthly": hoa_monthly,
                "location_label": location or "new home",
                "replaces_monthly_rent": True,
                "appreciation_rate": appreciation_rate,
                "maintenance_pct": maintenance_pct,
            },
        ),
    ]
    where = f" in {location}" if location else ""
    s.summary = (
        f"Buy a home{where} in {purchase_year} for ${price:,.0f} "
        f"({down_pct:.0%} down = ${price * down_pct:,.0f}). "
        f"Down payment comes from brokerage. "
        f"Maintenance {maintenance_pct:.1%}/yr (≈ ${price * maintenance_pct / 12:,.0f}/mo). "
        f"Appreciation {appreciation_rate:.1%}/yr (paper wealth only)."
    )
    return s
