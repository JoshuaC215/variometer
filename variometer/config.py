"""Scenario — all model inputs as a single dataclass.

This is the contract between the model and the UI/CLI. The dataclass defaults
are **illustrative placeholders** for a plausible persona — a mid-30s senior
IC at a public tech company — so a fresh checkout renders something sensible.
They are not anyone's real numbers: your own baseline lives in
``user_data/profile.json`` and is applied by ``base_scenario()``.

Model epoch: all growth arithmetic (salary, expenses, brackets, IRS limits)
is anchored at ``MODEL_EPOCH`` (2026) — the year the bracket tables in
``data/brackets.py`` are pinned to. "2026 real dollars" throughout the code
means values expressed at this anchor and inflated forward from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .events import LifeEvent

# The calendar year the model's tax tables and growth arithmetic are anchored
# to. Updating it means refreshing data/brackets.py (and re-pinning tests) —
# it is NOT a per-user setting.
MODEL_EPOCH = 2026

# Last calendar year of the projection horizon. Shared by the projected-refresh
# generator (equity.py), the FIRE Roth-ladder factory, and the app/CLI
# extended-year ranges so they can't drift apart.
PROJECTION_END_YEAR = 2060

# The modeling envelope. tax.py implements single-filer federal brackets and CA
# state brackets, with no dependent credits — any other profile is out of scope
# and rejected by Scenario.__post_init__ rather than silently mismodeled.
SUPPORTED_FILING_STATUS = "single"
SUPPORTED_STATE = "CA"

# How many years past MODEL_EPOCH the pinned bracket tables are considered
# fresh enough not to warn about.
BRACKET_STALENESS_GRACE_YEARS = 1


def bracket_staleness_warning(today: date | None = None) -> str | None:
    """Warn when the pinned bracket tables are more than a year out of date.

    The tax tables in ``data/brackets.py`` are pinned to ``MODEL_EPOCH``, and
    every later year is derived by inflating them at ``bracket_inflation``
    (~3%/yr). That is fine for a year or two and increasingly fictional after
    that. Returns ``None`` while still inside the grace window, otherwise a
    one-paragraph warning string.

    ``today`` is injectable so this is deterministic under test.
    """
    today = today or date.today()
    years_stale = today.year - MODEL_EPOCH
    if years_stale <= BRACKET_STALENESS_GRACE_YEARS:
        return None
    return (
        f"Tax tables are pinned to {MODEL_EPOCH} and it is now {today.year}. "
        f"Results project {years_stale} years of assumed ~3% bracket inflation on "
        f"top of tables that were never refreshed — brackets, the standard "
        f"deduction and IRS limits will have drifted from the real ones. Refresh "
        f"variometer/data/brackets.py and bump MODEL_EPOCH."
    )


def _default_vest_grants() -> dict[str, dict[tuple[int, int], float]]:
    """Explicit vest schedules for grants already issued.

    {grant label: {(year, month): shares}}. The label is what the Vest
    Schedule tab groups by. Real users supply ``user_data/grants.csv``
    (loaded by ``base_scenario``); this default only exists so a fresh
    checkout produces a plausible-looking projection.
    """
    from .data.sample_grants import SAMPLE_GRANTS

    return {label: dict(sched) for label, sched in SAMPLE_GRANTS.items()}


@dataclass
class Scenario:
    # ---- Personal ----
    age: int = 35
    # The modeling envelope, made explicit. The tax code in tax.py implements
    # single-filer federal brackets and CA state brackets only, with no
    # dependent credits. These two fields feed no computation — they exist so
    # an out-of-envelope profile fails loudly in __post_init__ instead of
    # being silently mismodeled.
    filing_status: str = "single"
    state: str = "CA"

    # ---- Compensation ----
    base_salary: float = 220_000
    salary_growth: float = 0.03
    bonus_rate: float = 0.15
    # Calendar month the annual bonus pays out.
    bonus_month: int = 3

    # ---- Pay schedule ----
    # Any known recent Friday payday. Bi-weekly cadence derives from this.
    pay_anchor: date = date(2026, 1, 2)

    # ---- Stock / equity ----
    price_now: float = 100.0  # employer share price at price_now_date
    price_now_date: date = date(2026, 5, 1)
    price_growth: float = 0.00  # annual; try 0.08-0.12 for bull case

    # Refresh grant model: each annual refresh is sized at a flat target value
    # independent of prior grants (a per-grant dollar amount, not a band-targeted
    # top-up). The target appreciates slowly each year, similar to salary growth.
    # Vests flat over 48 months in the standard cadence.
    equity_refresh_grant_value: float = 120_000  # per-grant value at base year
    equity_refresh_growth: float = 0.03  # annual appreciation of the grant target
    equity_refresh_base_year: int = MODEL_EPOCH

    # Manual overrides (year -> dollar value at grant date). When set for a year,
    # bypasses auto-compute for that year. Empty = fully auto-computed.
    refresh_overrides: dict[int, float] = field(default_factory=dict)

    # Explicit vest schedules for grants already issued: {label: {(y, m): shares}}.
    # Loaded from user_data/grants.csv by base_scenario(); the dataclass default
    # is an illustrative schedule so a fresh checkout still renders.
    vest_grants: dict[str, dict[tuple[int, int], float]] = field(
        default_factory=_default_vest_grants
    )

    # First calendar year of auto-projected refresh grants. Must sit after the
    # last actual refresh grant in vest_grants (or you'd double-count a year).
    refresh_projection_start_year: int = 2027
    # Calendar month refresh grants are issued (and projected grants start
    # vesting). employment_end before this month in a year skips that year's
    # refresh entirely.
    refresh_issue_month: int = 3

    # ---- Retirement contributions (% of base salary) ----
    pretax_401k_pct: float = 0.111  # ~$24,500 / $220k maxes the EE limit
    roth_401k_pct: float = 0.00  # pre-tax > Roth at this bracket
    aftertax_401k_pct: float = 0.10  # mega backdoor, where the plan allows; 0 to disable

    # 401(k) match
    match_rate: float = 0.50  # 50% of pre-tax contributions…
    match_cap: float = 6_600  # …up to 6% of the placeholder base (a common shape)

    # ---- Other income / deductions ----
    other_monthly_income: float = 0  # interest, refunds — taxable
    ltcg_annual: float = 2_000  # modest brokerage gains

    # Itemized deduction amount in excess of standard. 0 = take standard.
    itemized_excess_over_standard: float = 0

    # ---- Expenses ----
    expense_base: float = 8_500  # baseline monthly
    # Portion of expense_base that represents rent. On house_purchase events with
    # replaces_monthly_rent=True, this amount is subtracted from expenses and
    # replaced by P&I + property tax + insurance + HOA. expense_base itself stays
    # the full "total monthly burn" so other tabs don't need to change.
    rent_monthly: float = 3_500
    expense_growth: float = 0.03

    # ---- Long-term projection ----
    return_rate: float = 0.07  # nominal annual return — 401k, IRA (tax-deferred/free, no drag)
    # Taxable brokerage earns less after dividend tax + occasional LTCG realization.
    # At this income (LTCG 23.8% incl. NIIT), ~1% drag on a mostly-index portfolio is reasonable.
    brokerage_return_rate: float = 0.06  # after-tax nominal return on taxable brokerage

    # ---- Portfolio starting balances (at the model epoch) ----
    brokerage_balance: float = 150_000  # taxable brokerage
    ira_balance: float = 0  # rollover / traditional IRA
    pretax_401k_balance: float = 100_000

    # Backdoor Roth IRA — annual contribution at the IRA limit, grows with limit_inflation
    roth_ira_annual: float = 7_500

    # ---- Tax / IRS limit growth assumptions ----
    bracket_inflation: float = 0.03  # fed + CA bracket inflation
    deduction_inflation: float = 0.025
    limit_inflation: float = 0.025  # 401k / SS base growth

    # ---- Brokerage cost basis ----
    # Used for LTCG tax on brokerage withdrawals. Set explicitly, or leave at
    # 0 to auto-derive from brokerage_cost_basis_fraction in __post_init__.
    brokerage_cost_basis_fraction: float = 0.5  # fraction of balance that is basis
    brokerage_cost_basis: float = 0.0  # 0 → auto-derive in __post_init__

    # ---- Healthcare ----
    # Monthly out-of-pocket cost. 0 while employed (employer-paid). Overridden by
    # new_job events (via event params) or set explicitly for lean years.
    healthcare_monthly: float = 0.0

    # ---- TCJA toggle ----
    # When True, the TCJA $10k SALT cap applies to itemized deductions.
    # When False (pre-TCJA / post-sunset), SALT is uncapped.
    # Affects housing.itemized_excess_for_year() only when a house is owned.
    tcja_active: bool = True

    # ---- Retirement income cutoff ----
    # When set, all employment income (employer salary + any new_job) goes to zero
    # starting this calendar year.  None = no cutoff (income runs as long as the
    # scenario's events allow).  Set by the Runway UI and --retire-year CLI flag;
    # also settable directly on any Scenario instance.
    retirement_year: int | None = None

    # ---- Human-readable description (set by factory functions) ----
    summary: str = ""

    # ---- Life events ----
    # Chronological list of perturbations applied to the baseline projection.
    # Empty list → identical output to the pre-events baseline (regression invariant).
    events: list[LifeEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.filing_status != SUPPORTED_FILING_STATUS:
            raise ValueError(
                f"filing_status={self.filing_status!r} is not supported: the model "
                f"currently models a single filer resident in California only "
                f"(filing_status={SUPPORTED_FILING_STATUS!r}, state={SUPPORTED_STATE!r}). "
                f"Federal brackets, the standard deduction, ACA thresholds and the "
                f"state tax tables are all single-filer CA values."
            )
        if self.state != SUPPORTED_STATE:
            raise ValueError(
                f"state={self.state!r} is not supported: the model currently models "
                f"a single filer resident in California only "
                f"(filing_status={SUPPORTED_FILING_STATUS!r}, state={SUPPORTED_STATE!r}). "
                f"There is no state tax table for {self.state!r}."
            )
        if self.brokerage_cost_basis == 0.0:
            self.brokerage_cost_basis = self.brokerage_balance * self.brokerage_cost_basis_fraction


def base_scenario() -> Scenario:
    """The user's baseline: ``Scenario()`` with the ``user_data/profile.json``
    overlay applied.

    This is THE constructor for "the current user's situation" — scenario
    factories, the sidebar defaults, and the CLI all start from it. Without a
    profile (fresh clone, or ``VARIOMETER_USER_DATA=""``) it is identical
    to ``Scenario()``.

    Profile values pass through the ``Scenario`` constructor, so derived
    fields (``brokerage_cost_basis``) recompute in ``__post_init__``. The
    explicit vest schedule comes from ``user_data/grants.csv`` when present.
    """
    from .user_data_io import load_profile, load_vest_grants  # local import avoids module cycle

    overrides = dict(load_profile())
    grants = load_vest_grants()
    if grants is not None:
        overrides["vest_grants"] = grants
    return Scenario(**overrides)
