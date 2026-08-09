"""Drawdown / runway waterfall.

Handles the ordered depletion of accounts when annual cash flow is negative:
  1. Brokerage (pro-rata cost basis → LTCG realized in the current year)
  2. Roth IRA contribution principal (tax-free, penalty-free)
  3. Seasoned Roth conversion cohorts, oldest first (tax-free, penalty-free)
  4. Roth IRA earnings/growth (non-qualified → ordinary + 10% penalty pre-60;
     qualified + tax-free at 60+)
  5. Pre-tax 401(k) early withdrawal (10% penalty + ordinary pre-59.5)

The brokerage bucket uses fixed-point iteration because the LTCG realized
creates additional tax in the same year, which increases the amount needed
from brokerage. The iteration solves for ``gross_pull`` such that::

    gross_pull - tax(LTCG_realized(gross_pull)) = net_shortfall_for_brokerage

Design decisions:
- Cost basis depletes pro-rata (not HIFO/LIFO).
- 5-year seasoning: conversion in year Y is accessible Jan 1 of year Y+5.
- Fixed-point iteration cap: 8 iterations. Non-convergence sets converged=False.
- 72(t) / SEPP NOT modeled (conflicts with "willing to return to work").
- ACA premium tax credit is computed in ``projection.py`` post-waterfall.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

from .tax import ltcg_federal_tax

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class WithdrawalLeg:
    """One leg of a year's withdrawal — a single bucket pull."""

    source: str  # "brokerage", "roth_ira_principal",
    # "roth_conversion_YYYY", "401k_early"
    amount: float  # gross amount pulled from the bucket
    taxable_ordinary: float  # added to ordinary income (401k withdrawal)
    taxable_ltcg: float  # LTCG realized (brokerage only)
    penalty: float  # 401k early withdrawal 10% penalty
    cohort_year: int | None  # populated for Roth conversion legs


@dataclass
class YearWithdrawal:
    """All withdrawals in a single year + resulting tax/penalty totals."""

    year: int
    shortfall_before_tax: float  # initial cash gap before tax-on-withdrawal
    legs: list[WithdrawalLeg]
    total_withdrawn: float
    total_realized_ltcg: float
    total_ordinary_income_added: float
    total_penalty: float
    iterations: int  # for diagnostics
    converged: bool  # False if hit the 8-iter cap
    unfunded_shortfall: float = 0.0  # remaining gap after all buckets exhausted


@dataclass
class WithdrawalState:
    """Mutable state for the drawdown engine — updated each year in-place."""

    brokerage_balance: float
    brokerage_basis: float
    roth_ira_balance: float
    roth_ira_principal: float  # cumulative contributions, accessible any time
    # NOTE: the Roth 401(k) is not a waterfall bucket — projection.py rolls it
    # into Roth IRA principal on employment_end before the waterfall runs.
    pretax_401k_balance: float
    # Roth conversion cohorts: {conversion_year: balance_remaining}
    roth_conversion_cohorts: dict[int, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def is_cohort_seasoned(cohort_year: int, current_year: int) -> bool:
    """A conversion made in cohort_year is accessible Jan 1 of cohort_year + 5.

    Equivalently: ``current_year - cohort_year >= 5``.

    Examples:
        is_cohort_seasoned(2027, 2032) → True   (2032 - 2027 = 5)
        is_cohort_seasoned(2027, 2031) → False  (only 4 years)
    """
    return current_year - cohort_year >= 5


def _ltcg_realized_for_pull(
    pull: float,
    balance: float,
    basis: float,
) -> float:
    """Realized LTCG for pulling ``pull`` from a brokerage of ``balance`` with ``basis``."""
    if balance <= 0 or pull <= 0:
        return 0.0
    actual = min(pull, balance)
    basis_fraction = basis / balance
    return actual * (1.0 - basis_fraction)


def _tax_on_ltcg(
    new_ltcg: float,
    ordinary_taxable_income: float,
    ltcg_already: float,
    year: int,
    bracket_inflation: float,
) -> float:
    """Total additional tax from adding ``new_ltcg`` on top of existing LTCG.

    Uses the bracketed federal LTCG function (which handles the 0% bracket)
    plus the CA marginal rate (CA taxes LTCG as ordinary income).

    We compute incremental federal LTCG tax (tax_at_total - tax_at_prior) plus
    the CA component (new_ltcg × CA marginal rate at the combined CA income).
    """
    if new_ltcg <= 0:
        return 0.0

    # Incremental federal LTCG tax
    fed_total = ltcg_federal_tax(
        ltcg=ltcg_already + new_ltcg,
        ordinary_taxable_income=ordinary_taxable_income,
        year=year,
        bracket_inflation=bracket_inflation,
    )
    fed_prior = ltcg_federal_tax(
        ltcg=ltcg_already,
        ordinary_taxable_income=ordinary_taxable_income,
        year=year,
        bracket_inflation=bracket_inflation,
    )
    fed_incremental = max(0.0, fed_total - fed_prior)

    # CA marginal rate on LTCG (CA treats LTCG as ordinary income)
    from .data.brackets import california_brackets

    ca_bkts = california_brackets(year, bracket_inflation)
    # CA income position = ordinary_taxable_income + ltcg_already (approximation;
    # exact CA taxable would require deductions, but this gives the right marginal rate)
    ca_income_position = ordinary_taxable_income + ltcg_already
    ca_marginal = 0.0
    for b in ca_bkts:
        if ca_income_position < b.hi:
            ca_marginal = b.rate
            break
    else:
        ca_marginal = ca_bkts[-1].rate if ca_bkts else 0.0
    ca_incremental = new_ltcg * ca_marginal

    return fed_incremental + ca_incremental


def _marginal_ordinary_rate(
    ordinary_taxable_income: float,
    year: int,
    bracket_inflation: float,
) -> float:
    """Approximate combined federal + CA marginal ordinary income tax rate."""
    from .data.brackets import california_brackets, federal_brackets

    fed_bkts = federal_brackets(year, bracket_inflation)
    ca_bkts = california_brackets(year, bracket_inflation)

    fed_rate = 0.0
    for b in fed_bkts:
        if ordinary_taxable_income < b.hi:
            fed_rate = b.rate
            break
    else:
        fed_rate = fed_bkts[-1].rate if fed_bkts else 0.0

    ca_rate = 0.0
    for b in ca_bkts:
        if ordinary_taxable_income < b.hi:
            ca_rate = b.rate
            break
    else:
        ca_rate = ca_bkts[-1].rate if ca_bkts else 0.0

    return fed_rate + ca_rate


# ---------------------------------------------------------------------------
# Main waterfall function
# ---------------------------------------------------------------------------

_MAX_ITERATIONS = 8
_CONVERGENCE_THRESHOLD = 10.0  # dollars — if delta < $10, treat as converged
_MIN_PULL = 1.0  # dollars — don't create a leg for < $1


def waterfall_withdrawal(
    state: WithdrawalState,
    shortfall: float,
    year: int,
    age: int,
    ordinary_taxable_income_before: float,
    ltcg_before: float,
    bracket_inflation: float,
) -> YearWithdrawal:
    """Execute the waterfall to cover ``shortfall`` and return what was done.

    Mutates ``state`` in place (deducts from each bucket).  Iterates to a
    fixed point so brokerage LTCG tax is self-financing.

    The key accounting model:
      - ``shortfall`` is the net cash gap (e.g. expenses + retirement contribs
        − take_home).
      - Brokerage withdrawal realizes LTCG which creates a TAX BILL that must
        also be funded.  The iteration ensures: net_cash_from_brokerage = shortfall
        i.e. gross_pull = shortfall + tax_on_realized_ltcg.
      - Once we find the converged brokerage pull, ``remaining_shortfall``
        (for subsequent buckets) is 0 if brokerage sufficed, or the leftover gap
        if brokerage was exhausted before covering the full shortfall + tax.
      - Subsequent buckets (Roth principal, seasoned cohorts, 401k) are tax-free
        or carry their own tax (401k), so remaining_shortfall reduces 1:1.

    Args:
        state:                        Mutable account balances.  Modified in place.
        shortfall:                    Net cash gap.  Positive = need cash.
        year:                         Calendar year.
        age:                          Age (integer) for 59.5 rule — age < 60 → penalty.
        ordinary_taxable_income_before: Fed ordinary taxable income this year from
                                      annual_summary, BEFORE withdrawal income.
        ltcg_before:                  LTCG already recognized (scenario.ltcg_annual),
                                      before withdrawal-driven LTCG.
        bracket_inflation:            Annual bracket inflation rate.
    """
    if shortfall <= 0:
        return YearWithdrawal(
            year=year,
            shortfall_before_tax=shortfall,
            legs=[],
            total_withdrawn=0.0,
            total_realized_ltcg=0.0,
            total_ordinary_income_added=0.0,
            total_penalty=0.0,
            iterations=0,
            converged=True,
            unfunded_shortfall=0.0,
        )

    # Pre-59.5 rule: use age < 60 as proxy for "no 10% penalty-free window yet"
    # (conservative: anyone turning 60 this year pays no penalty — slightly favors them)
    early_withdrawal_penalty_rate = 0.10 if age < 60 else 0.0

    all_legs: list[WithdrawalLeg] = []
    total_ltcg_realized = 0.0
    total_ordinary_added = 0.0
    total_penalty = 0.0
    iterations = 0
    converged = True

    remaining_shortfall = shortfall  # cash we still need after each bucket

    # -------------------------------------------------------------------------
    # Bucket 1: Brokerage — fixed-point iteration for self-financing LTCG tax
    #
    # We want to find gross_pull G such that:
    #   G - tax_on_ltcg(G * gain_rate) = remaining_shortfall
    #
    # where gain_rate = 1 - basis/balance.
    #
    # Iterative approach:
    #   G_0 = remaining_shortfall
    #   G_{i+1} = remaining_shortfall + tax_on_ltcg(G_i * gain_rate)
    # Converges quickly because gain_rate * marginal_tax_rate < 1.
    # -------------------------------------------------------------------------
    if state.brokerage_balance > 0 and remaining_shortfall > 0:
        # Snapshot so we can redo the pull at the final converged amount
        snap_balance = state.brokerage_balance
        snap_basis = state.brokerage_basis

        # Gain rate (fraction of each dollar pulled that is taxable gain)
        gain_rate = 1.0 - (snap_basis / snap_balance) if snap_balance > 0 else 0.0

        # Iteration target: how much net cash from brokerage we need
        brokerage_cash_needed = remaining_shortfall  # may be reduced if brokerage exhausted

        gross_pull = min(brokerage_cash_needed, snap_balance)
        local_converged = True

        for i in range(_MAX_ITERATIONS):
            iterations = i + 1
            actual_pull = min(gross_pull, snap_balance)
            realized_ltcg = actual_pull * gain_rate

            # Total tax on the LTCG from this pull (incremental, on top of ltcg_before)
            ltcg_tax = _tax_on_ltcg(
                new_ltcg=realized_ltcg,
                ordinary_taxable_income=ordinary_taxable_income_before,
                ltcg_already=ltcg_before,
                year=year,
                bracket_inflation=bracket_inflation,
            )

            # Updated gross pull target: we need enough to cover the shortfall PLUS the tax
            new_gross_pull = min(brokerage_cash_needed + ltcg_tax, snap_balance)

            delta = abs(new_gross_pull - gross_pull)
            gross_pull = new_gross_pull
            if delta < _CONVERGENCE_THRESHOLD:
                break
        else:
            local_converged = False
            warnings.warn(
                f"waterfall_withdrawal: brokerage LTCG iteration did not converge "
                f"in {_MAX_ITERATIONS} iterations for year={year}, shortfall={shortfall:.0f}",
                RuntimeWarning,
                stacklevel=2,
            )

        converged = local_converged

        # Final pull at the converged gross_pull
        actual_pull = min(gross_pull, snap_balance)
        if actual_pull > 0:
            # Pro-rata basis depletion
            realized_ltcg = actual_pull * gain_rate
            new_basis = snap_basis * (snap_balance - actual_pull) / snap_balance

            state.brokerage_balance = snap_balance - actual_pull
            state.brokerage_basis = max(0.0, new_basis)
            total_ltcg_realized += realized_ltcg

            # Compute the final LTCG tax cost for this pull
            ltcg_tax_final = _tax_on_ltcg(
                new_ltcg=realized_ltcg,
                ordinary_taxable_income=ordinary_taxable_income_before,
                ltcg_already=ltcg_before,
                year=year,
                bracket_inflation=bracket_inflation,
            )

            all_legs.append(
                WithdrawalLeg(
                    source="brokerage",
                    amount=actual_pull,
                    taxable_ordinary=0.0,
                    taxable_ltcg=realized_ltcg,
                    penalty=0.0,
                    cohort_year=None,
                )
            )

            # Net cash from brokerage = gross_pull - ltcg_tax_final
            net_cash_from_brokerage = actual_pull - ltcg_tax_final
            # Reduce remaining shortfall by the net cash delivered
            remaining_shortfall = max(0.0, remaining_shortfall - net_cash_from_brokerage)

    # -------------------------------------------------------------------------
    # Bucket 2: Roth IRA contribution principal (tax-free, penalty-free, always)
    # Capped at cumulative contributions (roth_ira_principal).
    # -------------------------------------------------------------------------
    if remaining_shortfall > _MIN_PULL and state.roth_ira_principal > 0:
        pull = min(remaining_shortfall, state.roth_ira_principal)
        state.roth_ira_principal -= pull
        state.roth_ira_balance = max(0.0, state.roth_ira_balance - pull)
        all_legs.append(
            WithdrawalLeg(
                source="roth_ira_principal",
                amount=pull,
                taxable_ordinary=0.0,
                taxable_ltcg=0.0,
                penalty=0.0,
                cohort_year=None,
            )
        )
        remaining_shortfall = max(0.0, remaining_shortfall - pull)

    # -------------------------------------------------------------------------
    # Bucket 3: Seasoned Roth conversion cohorts — oldest-first FIFO
    # No tax, no penalty (principal was taxed at conversion time).
    # -------------------------------------------------------------------------
    if remaining_shortfall > _MIN_PULL and state.roth_conversion_cohorts:
        seasoned_years = sorted(
            cy for cy in state.roth_conversion_cohorts if is_cohort_seasoned(cy, year)
        )
        for cohort_year in seasoned_years:
            if remaining_shortfall <= 0:
                break
            available = state.roth_conversion_cohorts.get(cohort_year, 0.0)
            if available <= 0:
                continue
            pull = min(remaining_shortfall, available)
            state.roth_conversion_cohorts[cohort_year] -= pull
            all_legs.append(
                WithdrawalLeg(
                    source=f"roth_conversion_{cohort_year}",
                    amount=pull,
                    taxable_ordinary=0.0,
                    taxable_ltcg=0.0,
                    penalty=0.0,
                    cohort_year=cohort_year,
                )
            )
            remaining_shortfall = max(0.0, remaining_shortfall - pull)

    # -------------------------------------------------------------------------
    # Bucket 4: Roth IRA earnings / growth (non-qualified distribution before 59.5)
    #
    # Roth IRA *principal* (contributions) is always accessible tax-free (bucket 2).
    # Roth IRA *earnings* are a qualified distribution only after 59.5 + 5-year rule;
    # before that they're subject to income tax + 10% early withdrawal penalty —
    # the same treatment as a pretax 401k withdrawal.  After 59.5 they're tax-free.
    #
    # We pull this bucket BEFORE pretax 401k because:
    #   • Age >= 60: Roth earnings are tax-free vs. 401k ordinary income → clearly
    #     better to drain Roth earnings first.
    #   • Age < 60: identical tax treatment, but Roth earnings carry no future
    #     ordinary-income burden; 401k withdrawals may push into higher brackets.
    #
    # Growth = max(0, roth_ira_balance − roth_ira_principal).
    # -------------------------------------------------------------------------
    if remaining_shortfall > _MIN_PULL:
        roth_growth = max(0.0, state.roth_ira_balance - state.roth_ira_principal)
        if roth_growth > 0:
            if age >= 60:
                # Qualified distribution: tax-free, no penalty
                pull = min(remaining_shortfall, roth_growth)
                state.roth_ira_balance = max(0.0, state.roth_ira_balance - pull)
                all_legs.append(
                    WithdrawalLeg(
                        source="roth_ira_growth",
                        amount=pull,
                        taxable_ordinary=0.0,
                        taxable_ltcg=0.0,
                        penalty=0.0,
                        cohort_year=None,
                    )
                )
                remaining_shortfall = max(0.0, remaining_shortfall - pull)
            else:
                # Non-qualified distribution: income tax + 10% penalty — gross up same
                # way as the pretax 401k bucket so net cash covers the shortfall.
                current_ordinary = ordinary_taxable_income_before + total_ordinary_added
                marginal_rate = _marginal_ordinary_rate(current_ordinary, year, bracket_inflation)
                gross_up_denom = 1.0 - marginal_rate - early_withdrawal_penalty_rate
                if gross_up_denom <= 0.05:
                    gross_up_denom = 0.05
                gross_pull_rg = min(
                    remaining_shortfall / gross_up_denom,
                    roth_growth,
                )
                penalty_rg = gross_pull_rg * early_withdrawal_penalty_rate
                state.roth_ira_balance = max(0.0, state.roth_ira_balance - gross_pull_rg)
                total_ordinary_added += gross_pull_rg
                total_penalty += penalty_rg
                all_legs.append(
                    WithdrawalLeg(
                        source="roth_ira_growth",
                        amount=gross_pull_rg,
                        taxable_ordinary=gross_pull_rg,
                        taxable_ltcg=0.0,
                        penalty=penalty_rg,
                        cohort_year=None,
                    )
                )
                net_cash_rg = gross_pull_rg - gross_pull_rg * marginal_rate - penalty_rg
                remaining_shortfall = max(0.0, remaining_shortfall - net_cash_rg)

    # -------------------------------------------------------------------------
    # Bucket 5: Pre-tax 401(k) — last resort
    # Ordinary income + 10% penalty (if age < 60).
    # We gross up to cover the shortfall net of expected tax + penalty.
    # -------------------------------------------------------------------------
    if remaining_shortfall > _MIN_PULL and state.pretax_401k_balance > 0:
        # Estimate combined marginal rate (fed + CA ordinary) at current income level.
        # This is used to gross up so the net cash (after tax + penalty) covers the shortfall.
        current_ordinary = ordinary_taxable_income_before + total_ordinary_added
        marginal_rate = _marginal_ordinary_rate(current_ordinary, year, bracket_inflation)
        gross_up_denom = 1.0 - marginal_rate - early_withdrawal_penalty_rate
        if gross_up_denom <= 0.05:
            gross_up_denom = 0.05  # floor at 5% to avoid extreme gross-ups

        # Gross pull needed to deliver remaining_shortfall net of tax + penalty
        gross_pull_401k = min(
            remaining_shortfall / gross_up_denom,
            state.pretax_401k_balance,
        )
        penalty_401k = gross_pull_401k * early_withdrawal_penalty_rate
        state.pretax_401k_balance -= gross_pull_401k
        total_ordinary_added += gross_pull_401k
        total_penalty += penalty_401k

        all_legs.append(
            WithdrawalLeg(
                source="401k_early",
                amount=gross_pull_401k,
                taxable_ordinary=gross_pull_401k,
                taxable_ltcg=0.0,
                penalty=penalty_401k,
                cohort_year=None,
            )
        )
        net_cash_from_401k = gross_pull_401k - gross_pull_401k * marginal_rate - penalty_401k
        remaining_shortfall = max(0.0, remaining_shortfall - net_cash_from_401k)

    # -------------------------------------------------------------------------
    # Aggregate
    # -------------------------------------------------------------------------
    total_withdrawn = sum(leg.amount for leg in all_legs)
    total_ltcg = sum(leg.taxable_ltcg for leg in all_legs)
    total_ord = sum(leg.taxable_ordinary for leg in all_legs)
    total_pen = sum(leg.penalty for leg in all_legs)

    return YearWithdrawal(
        year=year,
        shortfall_before_tax=shortfall,
        legs=all_legs,
        total_withdrawn=total_withdrawn,
        total_realized_ltcg=total_ltcg,
        total_ordinary_income_added=total_ord,
        total_penalty=total_pen,
        iterations=iterations,
        converged=converged,
        unfunded_shortfall=max(0.0, remaining_shortfall),
    )
