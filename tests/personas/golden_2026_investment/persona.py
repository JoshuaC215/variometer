"""Golden persona: wages plus long-term capital gain, 2026 epoch year.

Companion to ``golden_2026_w2``. Same discipline — every expected value in
``expected.json`` was hand-computed from a cited primary source before the
model was run against it (see ``docs/GOLDEN_CASES.md``) — but these variants
exercise the two paths a pure wage case cannot reach:

* **LTCG stacking end to end.** ``ltcg_annual`` flows through gross income and
  AGI, is removed from ordinary taxable income, and is then taxed at the 0 /
  15 / 20% rates *stacked on top of* whatever ordinary taxable income is left.
  ``ltcg_zero_bracket`` deliberately straddles the published $49,450 0%-rate
  ceiling: part of the gain is free, the rest is taxed at 15%.
* **NIIT in both binding regimes.** IRC §1411 taxes 3.8% of
  ``min(net investment income, MAGI − $200,000)``. ``niit_income_binding`` has
  MAGI far above the threshold so the investment income binds;
  ``niit_agi_binding`` sits just over it so the MAGI excess binds — a much
  smaller number than the gain itself. ``niit_below_threshold`` proves the
  zero.

As in the W-2 bundle, only federal-side and statutory quantities are pinned;
CA bracket-derived fields carry no golden claim. Pinned at 2026 only.
"""

from variometer.config import Scenario

REQUIRES_USER_DATA = False


def golden_scenario(**overrides) -> Scenario:
    """A Scenario with every income path except wages and LTCG switched off."""
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
        # MAGI $210k, gain $30k — the MAGI-over-threshold excess ($10k) binds,
        # so NIIT is a third of what taxing the whole gain would give.
        "niit_agi_binding": golden_scenario(base_salary=180_000, ltcg_annual=30_000),
        # MAGI $500k, gain $100k — the gain binds; NIIT is 3.8% of all of it.
        "niit_income_binding": golden_scenario(base_salary=400_000, ltcg_annual=100_000),
        # MAGI $170k — under the unindexed $200k threshold, so no NIIT at all.
        "niit_below_threshold": golden_scenario(base_salary=150_000, ltcg_annual=20_000),
        # Ordinary taxable income low enough that the gain straddles the
        # published $49,450 top of the 0% LTCG bracket.
        "ltcg_zero_bracket": golden_scenario(base_salary=40_000, ltcg_annual=40_000),
    }
