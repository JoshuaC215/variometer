"""Golden persona: a plain W-2 wage earner in the 2026 epoch year.

Unlike ``single_ca_rsu`` — which pins whatever the model produced yesterday —
this bundle's expected values were **hand-computed from cited primary sources**
before the model was ever run against them. See ``docs/GOLDEN_CASES.md`` for
what that claim covers and what it deliberately does not.

Each variant is a maximally boring scenario: no bonus, no equity, no
investment income, no itemized deduction, no events. Everything that could
muddy the arithmetic is switched off, so a pinned number is a short chain from
statute:

    gross W-2 wages
      − pre-tax 401(k) deferral         → AGI
      − federal standard deduction      → federal ordinary taxable income
      → federal ordinary bracket walk   → federal_income_tax
    gross W-2 wages → FICA legs (SS / Medicare / Additional Medicare / CA SDI)

**Only federal-side and statutory quantities are pinned.** CA bracket-derived
fields (``ca_taxable``, ``ca_income_tax``, and therefore ``total_tax``,
``take_home`` and ``effective_tax_rate``) are excluded: the 2026 CA tables in
``data/brackets.py`` are still projected from 2025 actuals, so they carry no
golden claim. CA SDI *is* pinned — it is a published flat rate, not a bracket.

Pinned at 2026 only. The epoch year is where the constants are published
actuals; every later year multiplies them by assumed inflation and is a
projection, not an anchor.
"""

from variometer.config import Scenario

REQUIRES_USER_DATA = False


def golden_scenario(**overrides) -> Scenario:
    """A Scenario with every non-wage income and deduction path switched off."""
    fields = dict(
        base_salary=0.0,
        salary_growth=0.0,
        bonus_rate=0.0,
        vest_grants={},
        equity_refresh_grant_value=0.0,
        refresh_overrides={},
        other_monthly_income=0.0,
        ltcg_annual=0.0,
        pretax_401k_pct=0.0,
        roth_401k_pct=0.0,
        aftertax_401k_pct=0.0,
        match_rate=0.0,
        match_cap=0.0,
        itemized_excess_over_standard=0.0,
        healthcare_monthly=0.0,
    )
    fields.update(overrides)
    return Scenario(**fields)


def scenarios() -> dict[str, Scenario]:
    return {
        # Wages exactly at the Additional Medicare threshold — the 0.9% leg
        # must still be zero at the threshold, not above it.
        "w2_200k": golden_scenario(base_salary=200_000),
        # Wages above the SS wage base and the Additional Medicare threshold:
        # capped SS, uncapped Medicare, 0.9% surtax, uncapped CA SDI.
        "w2_300k": golden_scenario(base_salary=300_000),
        # A 20%-of-base deferral election against the §402(g) limit: the
        # deferral is capped at $24,500 and drops AGI by exactly that much,
        # while FICA wages are untouched (deferrals are FICA-taxable).
        "w2_pretax_max": golden_scenario(base_salary=200_000, pretax_401k_pct=0.20),
    }
