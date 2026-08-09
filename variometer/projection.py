"""Long-term balance projection — 401k, IRA, brokerage, home equity.

Compounding assumption: prior balance earns a full year; new inflows
(contributions, net surplus) earn half a year (mid-year deposit).

Drawdown integration: when annual net surplus is negative, ``project_net_worth``
invokes the waterfall from ``withdrawals.py`` instead of letting brokerage
absorb the negative number. Roth conversion events transfer money from
``pretax_401k`` into a per-year cohort that seasons for 5 years before its
principal becomes accessible. Brokerage cost basis depletes pro-rata on every
withdrawal so the 0% federal LTCG bracket is correctly exploited in lean years.

Market-path features:
- ``return_path``: optional ``dict[year, float]`` that overrides the per-year
  return rate. Used by deterministic stress paths and the Monte Carlo loop.
- ``stress_return_path``: factory for named return paths (flat, bear, slow_growth).
- ``monte_carlo_nw``: N log-normal simulations → P10/P25/P50/P75/P90 NW bands.

Regression invariant: with a bare ``Scenario()`` (no events) the per-year
balance trajectory is bit-identical to the historical baseline. New tracking
adds reporting fields (basis, principal split, cohorts) without changing the
balance-evolution formulas in the positive-surplus path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import MODEL_EPOCH, Scenario
from .events import events_of_kind
from .withdrawals import (
    WithdrawalState,
    YearWithdrawal,
    waterfall_withdrawal,
)


@dataclass
class NetWorthYear:
    year: int
    pretax_401k: float
    roth_401k: float
    roth_ira: float  # backdoor Roth IRA contributions + growth (= principal + growth)
    ira: float  # rollover IRA (compounds only, no new contributions)
    brokerage: float
    home_equity: float = 0.0  # down payment + cumulative principal paid

    # Drawdown reporting fields — all safe defaults; zero-event scenarios
    # produce 0/None and the regression invariant holds.
    roth_ira_principal: float = 0.0  # cumulative backdoor contributions (accessible)
    roth_ira_growth: float = 0.0  # = roth_ira − roth_ira_principal
    brokerage_basis: float = 0.0  # cost basis for LTCG calc
    conversion_cohorts: dict = field(default_factory=dict)  # {conversion_year: remaining_bal}
    realized_ltcg_this_year: float = 0.0  # from waterfall + down-payment depletion
    withdrawal: YearWithdrawal | None = None  # full waterfall result if it fired, else None
    aca_subsidy_monthly: float = 0.0  # ACA PTC applied this year
    unfunded_shortfall: float = 0.0  # cash gap not covered by any bucket (insolvency signal)
    take_home_income: float = 0.0  # ar.take_home for this year
    total_expenses: float = 0.0  # expenses_annual for this year
    # -----------------------------------------------------------------------
    total_nw: float = 0.0  # computed in project_net_worth


def _conversion_amount_for_year(scenario: Scenario, year: int) -> float:
    """Sum of ``roth_conversion`` event amounts firing in ``year``.

    Mirrors the logic in ``cashflow.annual_summary`` so the projection's
    pretax-401k drawdown stays in sync with the ordinary-income recognition.
    Both must use the same rules:
      - ``recurring=False`` events fire only in their own ``year``.
      - ``recurring=True`` events fire every year from ``event.year`` through
        ``ends_year`` inclusive.
    """
    total = 0.0
    for e in events_of_kind(scenario.events, "roth_conversion"):
        if e.year > year:
            continue  # not yet started
        ends_year = e.params.get("ends_year")
        if ends_year is not None and ends_year < year:
            continue  # already ended
        recurring = e.params.get("recurring", False)
        if not recurring and e.year != year:
            continue  # one-shot, fires only in its own year
        total += e.params.get("amount", 0.0)
    return total


def project_net_worth(
    scenario: Scenario,
    years: list[int],
    return_path: dict[int, float] | None = None,
) -> list[NetWorthYear]:
    """Year-by-year net worth across all accounts.

    Brokerage surplus = annual take-home minus expenses minus backdoor Roth
    contribution (includes RSU proceeds net of tax).
    Rollover IRA compounds silently; backdoor Roth IRA grows from annual contributions.

    Housing: when a house_purchase event is active, the down payment is
    deducted from brokerage in the purchase year (one-shot, before compounding),
    and home_equity grows from down payment + cumulative principal payments.
    Mortgage P&I + property tax + insurance + HOA costs flow through
    ``annual_expenses`` and reduce brokerage surplus automatically.

    Drawdown mechanics
    ------------------
    * Roth conversions: each conversion year transfers ``amount`` from
      ``pretax_401k`` into a new cohort. The cohort grows at ``return_rate``
      and seasons for 5 calendar years before its principal is accessible.
      ``cashflow.annual_summary`` already adds the conversion to ordinary
      income; here we only do the balance-sheet transfer.
    * Brokerage cost basis: tracked alongside balance. Positive surplus adds
      to both (new savings are 100% basis). Returns grow the balance but not
      the basis. Withdrawals deplete basis pro-rata.
    * Waterfall: when net surplus is negative, the shortfall is routed through
      ``waterfall_withdrawal`` (5 buckets — see ``withdrawals.py``). The
      result is recorded on ``NetWorthYear.withdrawal`` for the UI to display.

    Regression invariant: a bare ``Scenario()`` (no events) drives the
    positive-surplus path every year; the new tracking adds only reporting
    fields and the baseline NW trajectory is unchanged.
    """
    from . import housing as _housing
    from .cashflow import aca_monthly_subsidy_for_year, annual_expenses, annual_summary
    from .withdrawals import _tax_on_ltcg as _compute_ltcg_tax

    r = scenario.return_rate
    br = scenario.brokerage_return_rate
    # brokerage drag relative to the 401k/IRA rate (maintained across stress paths)
    _br_drag = r - br  # e.g. 0.01 (1 pp) for tax drag

    # Balance trackers --------------------------------------------------------
    pretax_bal = scenario.pretax_401k_balance
    roth_bal = 0.0
    ira_bal = scenario.ira_balance
    brokerage_bal = scenario.brokerage_balance
    brokerage_basis = scenario.brokerage_cost_basis

    # Roth IRA principal-vs-growth split.  principal = cumulative backdoor
    # contributions; growth = (total roth_ira balance) − principal.  Tracking
    # them separately lets the waterfall pull only principal without touching
    # the locked growth pool.
    roth_ira_principal = 0.0
    roth_ira_total = 0.0

    # Roth conversion cohorts: {conversion_year: remaining_balance}.  Each
    # cohort grows at ``return_rate`` each year.
    cohorts: dict[int, float] = {}

    # Track whether we've already deducted the down payment (one-shot per purchase)
    down_payment_deducted_years: set[int] = set()

    # Compute age progression for the waterfall's pre-59.5 rule
    age_at_epoch = scenario.age

    out: list[NetWorthYear] = []
    for y in years:
        # Per-year return override (stress paths / Monte Carlo inner loop).
        # Brokerage return maintains its historical drag vs. the 401k/IRA rate.
        if return_path is not None and y in return_path:
            r_y = return_path[y]
            br_y = r_y - _br_drag
        else:
            r_y = r
            br_y = br

        age_this_year = age_at_epoch + (y - MODEL_EPOCH)
        realized_ltcg_this_year = 0.0  # accumulator (down payment + waterfall)

        # ── Down payment deduction (one-shot in purchase year) ──────────────
        # Deplete basis pro-rata so future withdrawals see the correct ratio.
        # LTCG tax on the down payment is routed into net_surplus below so
        # lean-year composites fund it correctly.
        ev = _housing.active_house_purchase(scenario, y)
        down_pmt_ltcg_tax = 0.0
        if ev is not None:
            terms = _housing.terms_from_event(ev)
            if ev.year == y and y not in down_payment_deducted_years:
                if brokerage_bal > 0:
                    basis_fraction = brokerage_basis / brokerage_bal
                    realized_ltcg_this_year += terms.down_payment * (1 - basis_fraction)
                    brokerage_basis = max(
                        0.0, brokerage_basis - terms.down_payment * basis_fraction
                    )
                brokerage_bal -= terms.down_payment
                down_payment_deducted_years.add(y)

        # ── Compound everything for the year ────────────────────────────────
        ar = annual_summary(scenario, y)

        # Compute LTCG tax on the down-payment realization now that we have
        # ar.fed_taxable for this year's ordinary income baseline.
        if realized_ltcg_this_year > 0 and y in down_payment_deducted_years:
            down_pmt_ltcg_tax = _compute_ltcg_tax(
                new_ltcg=realized_ltcg_this_year,
                ordinary_taxable_income=ar.fed_taxable,
                ltcg_already=ar.ltcg,
                year=y,
                bracket_inflation=scenario.bracket_inflation,
            )
        contribs = ar.contributions
        pretax_in = contribs.pretax + contribs.employer_match
        roth_in = contribs.roth + contribs.aftertax

        pretax_bal = pretax_bal * (1 + r_y) + pretax_in * (1 + r_y / 2)
        roth_bal = roth_bal * (1 + r_y) + roth_in * (1 + r_y / 2)

        ira_bal = ira_bal * (1 + r_y)

        # ── Roth 401k rollover on employment separation ─────────────────────
        # When employment_end fires the user is no longer with the employer,
        # so the Roth 401k can be rolled over to a Roth IRA immediately.
        # All rolled principal is treated as accessible Roth IRA principal
        # (the 5-year earnings rule is satisfied by the existing Roth IRA
        # history from 2026+, and contributions were already after-tax).
        # We do this BEFORE the waterfall so the rolled balance is available
        # as a waterfall bucket in the same year.
        employment_ends_this_year = any(
            e.year == y for e in events_of_kind(scenario.events, "employment_end")
        )
        if employment_ends_this_year and roth_bal > 0:
            roth_ira_total += roth_bal
            roth_ira_principal += roth_bal
            roth_bal = 0.0

        # Backdoor Roth IRA: annual contribution grows with limit_inflation.
        # Total balance evolves with the mid-year-deposit formula; principal
        # tracks cumulative contributions; growth is derived.
        # IRA contributions require earned income — cap at this year's W-2 comp
        # so no-wage years (FIRE / gaps) stop contributing.
        roth_ira_contrib = scenario.roth_ira_annual * (1 + scenario.limit_inflation) ** (
            y - MODEL_EPOCH
        )
        earned_income = ar.base_salary + ar.bonus + ar.rsu_gross
        roth_ira_contrib = min(roth_ira_contrib, max(0.0, earned_income))
        roth_ira_total = roth_ira_total * (1 + r_y) + roth_ira_contrib * (1 + r_y / 2)
        roth_ira_principal += roth_ira_contrib

        # Roth conversion cohorts: each existing cohort earns the return
        # (they're invested in the Roth account like any other Roth balance).
        cohorts = {cy: bal * (1 + r_y) for cy, bal in cohorts.items()}

        # ── Process roth_conversion events for this year ────────────────────
        # Transfer the conversion amount from pretax_401k to a new cohort.
        # cashflow.annual_summary already added it to ordinary income; here we
        # only do the balance-sheet move.
        conv_amount = _conversion_amount_for_year(scenario, y)
        if conv_amount > 0 and pretax_bal > 0:
            actual_conv = min(conv_amount, pretax_bal)
            pretax_bal -= actual_conv
            # Add to existing cohort for this year if one exists (e.g., recurring
            # events that conceptually fire once per year)
            cohorts[y] = cohorts.get(y, 0.0) + actual_conv

        # ── Annual expenses ─────────────────────────────────────────────────
        # Event-aware. Housing costs (P&I + property tax + insurance + HOA) are
        # included here when a house_purchase event is active (see
        # cashflow.annual_expenses).
        expenses_annual = annual_expenses(scenario, y)

        # Roth IRA contribution comes out of cash (after-tax dollars), so reduce surplus.
        # down_pmt_ltcg_tax is a real cash outflow that must be funded this year.
        # NOTE: ACA subsidy is computed post-waterfall (below) so it can use the
        # actual realized LTCG as part of the effective AGI.
        net_surplus = ar.take_home - expenses_annual - roth_ira_contrib - down_pmt_ltcg_tax

        # Event-based brokerage deposits (one-time windfalls: a liquidity
        # event, inheritance, asset sale, …). Amounts are net-of-tax cash by
        # convention and land as 100%-basis brokerage money via net_surplus.
        for _dep_ev in events_of_kind(scenario.events, "brokerage_deposit"):
            if _dep_ev.year == y:
                net_surplus += _dep_ev.params.get("amount", 0.0)

        # ── Apply surplus to brokerage (positive path) OR run waterfall ─────
        withdrawal_record: YearWithdrawal | None = None
        if net_surplus >= 0:
            # Positive surplus path: balance compounds and basis accretes
            # (new savings are 100% basis).
            brokerage_bal = brokerage_bal * (1 + br_y) + net_surplus * (1 + br_y / 2)
            brokerage_basis += net_surplus
        else:
            # Negative surplus → shortfall.  Compound brokerage forward first
            # (returns are earned regardless of when in the year the
            # withdrawal happens), then route the shortfall through the
            # waterfall.
            brokerage_bal = brokerage_bal * (1 + br_y)
            shortfall = -net_surplus

            state = WithdrawalState(
                brokerage_balance=brokerage_bal,
                brokerage_basis=brokerage_basis,
                roth_ira_balance=roth_ira_total,
                roth_ira_principal=roth_ira_principal,
                pretax_401k_balance=pretax_bal,
                roth_conversion_cohorts=dict(cohorts),  # waterfall mutates in place
            )

            withdrawal_record = waterfall_withdrawal(
                state=state,
                shortfall=shortfall,
                year=y,
                age=age_this_year,
                ordinary_taxable_income_before=ar.fed_taxable,
                ltcg_before=ar.ltcg,
                bracket_inflation=scenario.bracket_inflation,
            )

            # Apply mutations back from state.
            # Use state.roth_ira_balance directly to capture BOTH principal
            # withdrawals AND the new bucket-4 Roth earnings withdrawals.
            brokerage_bal = state.brokerage_balance
            brokerage_basis = state.brokerage_basis
            roth_ira_total = state.roth_ira_balance
            roth_ira_principal = state.roth_ira_principal
            pretax_bal = state.pretax_401k_balance
            cohorts = state.roth_conversion_cohorts

            realized_ltcg_this_year += withdrawal_record.total_realized_ltcg

        # ── ACA premium tax credit (post-waterfall) ─────────────────────────
        # Effective AGI = ordinary income + realized LTCG (scenario + waterfall).
        # Computing post-waterfall ensures lean-year drawdown LTCG is included
        # so the subsidy fires correctly when AGI is otherwise near-zero.
        wr = withdrawal_record
        effective_agi = (
            ar.agi
            + (wr.total_realized_ltcg if wr is not None else 0.0)
            + (wr.total_ordinary_income_added if wr is not None else 0.0)
        )
        aca_sub_monthly = aca_monthly_subsidy_for_year(scenario, y, effective_agi)
        if aca_sub_monthly > 0:
            # Credit the subsidy to brokerage: it's cash we didn't have to spend.
            # New cash is 100% basis (it's a non-taxable government transfer).
            annual_aca = aca_sub_monthly * 12
            brokerage_bal += annual_aca
            brokerage_basis += annual_aca

        # ── Reporting fields ────────────────────────────────────────────────
        # Home equity: down payment + cumulative principal paid to date
        home_eq = 0.0
        if ev is not None:
            home_eq = _housing.home_equity_at_eoy(terms, y)

        # roth_ira aggregate (matches existing field semantics — total balance)
        roth_ira_growth = max(0.0, roth_ira_total - roth_ira_principal)

        # Sum cohort balances into total NW (they're real money in the Roth
        # umbrella, distinct from the seedable backdoor Roth IRA)
        cohort_total = sum(cohorts.values())

        total_nw = (
            pretax_bal
            + roth_bal
            + roth_ira_total
            + ira_bal
            + brokerage_bal
            + home_eq
            + cohort_total
        )

        out.append(
            NetWorthYear(
                year=y,
                pretax_401k=pretax_bal,
                roth_401k=roth_bal,
                roth_ira=roth_ira_total,
                ira=ira_bal,
                brokerage=brokerage_bal,
                home_equity=home_eq,
                roth_ira_principal=roth_ira_principal,
                roth_ira_growth=roth_ira_growth,
                brokerage_basis=brokerage_basis,
                conversion_cohorts=dict(cohorts),
                realized_ltcg_this_year=realized_ltcg_this_year,
                withdrawal=withdrawal_record,
                aca_subsidy_monthly=aca_sub_monthly,
                unfunded_shortfall=withdrawal_record.unfunded_shortfall
                if withdrawal_record
                else 0.0,
                take_home_income=ar.take_home,
                total_expenses=expenses_annual,
                total_nw=total_nw,
            )
        )

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic stress paths and Monte Carlo bands
# ─────────────────────────────────────────────────────────────────────────────


def stress_return_path(
    shape: str,
    years: list[int],
    base_rate: float,
    crash_year: int | None = None,
) -> dict[int, float]:
    """Return a per-year market return dict for a named stress shape.

    Shapes
    ------
    "flat"        — ``base_rate`` every year (passthrough; identical to no path)
    "bear"        — −35% in ``crash_year``, +15% next, +10% next, then ``base_rate``
    "slow_growth" — 4% every year regardless of ``base_rate``

    Designed to compose with any scenario via ``project_net_worth(return_path=...)``.
    """
    path: dict[int, float] = {}
    for y in years:
        if shape == "slow_growth":
            path[y] = 0.04
        elif shape == "bear" and crash_year is not None:
            if y == crash_year:
                path[y] = -0.35
            elif y == crash_year + 1:
                path[y] = 0.15
            elif y == crash_year + 2:
                path[y] = 0.10
            else:
                path[y] = base_rate
        else:
            path[y] = base_rate
    return path


@dataclass
class MCBands:
    """P10/P25/P50/P75/P90 NW bands from a Monte Carlo simulation."""

    years: list[int]
    p10: list[float]
    p25: list[float]
    p50: list[float]
    p75: list[float]
    p90: list[float]


def monte_carlo_nw(
    scenario: Scenario,
    years: list[int],
    n: int = 500,
    sigma: float = 0.15,
    seed: int = 42,
) -> MCBands:
    """Run N Monte Carlo simulations and return NW percentile bands.

    Each simulation draws log-normal annual returns (μ = return_rate,
    σ = sigma) correlated across all accounts (same draw per year).
    The brokerage maintains its historical drag vs. the 401k/IRA rate.

    Performance: cashflows (take_home, expenses, contributions) are
    pre-computed once outside the inner loop since they don't depend on
    returns.  Only the balance accumulation (and waterfall when applicable)
    is repeated N times.

    Waterfall note: the waterfall is path-dependent (account depletion order
    depends on running balances).  For scenarios without drawdown years this
    is free; for lean-year scenarios it adds O(drawdown_years) work per sim.
    With N=500 this is fast enough; use ``@st.cache_data`` in the UI layer.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    r_base = scenario.return_rate

    # Log-normal params: E[R] = r_base → μ_ln, σ_ln
    # For small σ the approximation μ_ln ≈ ln(1+r) − σ²/2 is accurate.
    mu_ln = math.log(1 + r_base) - 0.5 * sigma**2
    sigma_ln = sigma

    n_years = len(years)

    # Shape: (n_sims, n_years); each row is one simulation's return sequence.
    # Log-normal draws so returns can't go below −100%.
    log_returns = rng.normal(mu_ln, sigma_ln, size=(n, n_years))
    # Convert to actual return rates; floor at −50% (extreme crash guard)
    returns_matrix = np.exp(log_returns) - 1.0
    returns_matrix = np.clip(returns_matrix, -0.50, None)

    # Collect total_nw for each sim × year
    all_nw = np.zeros((n, n_years), dtype=np.float64)

    for sim_idx in range(n):
        sim_path = {y: float(returns_matrix[sim_idx, i]) for i, y in enumerate(years)}
        nw_rows = project_net_worth(scenario, years, return_path=sim_path)
        for j, row in enumerate(nw_rows):
            all_nw[sim_idx, j] = row.total_nw

    # Compute percentiles column-wise (per year)
    pcts = np.percentile(all_nw, [10, 25, 50, 75, 90], axis=0)

    return MCBands(
        years=list(years),
        p10=pcts[0].tolist(),
        p25=pcts[1].tolist(),
        p50=pcts[2].tolist(),
        p75=pcts[3].tolist(),
        p90=pcts[4].tolist(),
    )
