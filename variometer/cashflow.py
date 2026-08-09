"""Annual and monthly cash-flow computation."""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from . import housing as _housing
from . import tax
from .config import MODEL_EPOCH, Scenario
from .data.brackets import (
    california_standard_deduction,
    federal_standard_deduction,
)
from .equity import vest_gross_for_month, vest_gross_for_year
from .events import (
    LifeEvent,
    events_of_kind,
    first_event_of_kind,
    most_recent_event_for_year,
)
from .retirement import Contributions, compute_contributions, limits_for


@dataclass
class AnnualResult:
    year: int

    # Income line items
    base_salary: float
    bonus: float
    rsu_gross: float
    other_income: float
    ltcg: float
    gross_income: float

    # Retirement contributions
    contributions: Contributions

    # AGI / taxable
    agi: float
    fed_taxable: float
    ca_taxable: float

    # Tax breakdown
    federal_income_tax: float
    federal_ltcg_tax: float
    ca_income_tax: float
    fica: tax.FicaBreakdown
    niit: float
    total_tax: float

    # Net
    take_home: float
    effective_tax_rate: float

    # Social Security (0 when no ss_start event is active)
    ss_income: float = 0.0


def _ss_income_for_year(scenario: Scenario, year: int) -> float:
    """Annual SS income (nominal dollars) for the given year.

    Reads ``ss_start`` events.  Each event has params:
      monthly_benefit_today_dollars — PIA-adjusted monthly benefit in today's dollars
      cola — optional annual COLA rate (default 2.5%)
      claim_year — optional override; defaults to the event's year

    Returns 0 if no ss_start event is active this year.
    CA does not tax SS — the caller is responsible for excluding this from
    CA taxable income.
    """
    from .data.social_security import ss_annual_income

    total = 0.0
    for e in events_of_kind(scenario.events, "ss_start"):
        if year < e.year:
            continue
        monthly_today = e.params.get("monthly_benefit_today_dollars", 0.0)
        cola = e.params.get("cola", 0.025)
        claim_year = e.params.get("claim_year", e.year)
        total += ss_annual_income(monthly_today, claim_year, year, cola)
    return total


# ---------------------------------------------------------------------------
# Event-aware helpers
# ---------------------------------------------------------------------------


def _employer_months_in_year(scenario: Scenario, year: int) -> int:
    """Number of calendar months (0–12) where employer employment is active.

    The end month itself counts as active — consistent with vest_schedule,
    which also includes vests *in* the end month (``key <= cutoff``). Both
    sides treat the departure month as a full month of employment.

    Exception: if a ``new_job`` starts at or before that same month (a
    zero-gap or overlapping transition), the new job claims that shared
    month instead — otherwise both jobs would double-count it. Scenarios
    with the usual one-month-plus gap (``change_jobs``: end in March, new
    job in April) are unaffected.
    """
    if scenario.retirement_year is not None and year >= scenario.retirement_year:
        return 0
    end_event = first_event_of_kind(scenario.events, "employment_end")
    if end_event is None:
        return 12
    if end_event.year < year:
        return 0
    if end_event.year > year:
        return 12
    # end_event.year == year: active Jan through end_event.month, inclusive —
    # capped earlier if a new_job starts at or before that month.
    last_month = end_event.month
    new_jobs_same_year = [e for e in events_of_kind(scenario.events, "new_job") if e.year == year]
    if new_jobs_same_year:
        earliest_new_job_month = min(e.month for e in new_jobs_same_year)
        last_month = min(last_month, earliest_new_job_month - 1)
    return max(0, last_month)


def _new_job_info_for_year(
    scenario: Scenario,
    year: int,
) -> tuple[int, dict]:
    """Return (active_months, params) for any new_job event active in ``year``.

    ``active_months`` is 0 if no new job is active this year.
    """
    if scenario.retirement_year is not None and year >= scenario.retirement_year:
        return 0, {}
    candidates = [e for e in events_of_kind(scenario.events, "new_job") if e.year <= year]
    if not candidates:
        return 0, {}

    # Use the most recently started new_job (last in chronological order)
    nj = candidates[-1]

    ends_year = nj.params.get("ends_year")
    if ends_year is not None and ends_year < year:
        return 0, {}  # this job ended before this year

    start_month = nj.month if nj.year == year else 1
    return max(0, 12 - start_month + 1), nj.params


def _new_job_base_for_year(params: dict, scenario: Scenario, year: int) -> float:
    """Growth-adjusted annual base for a new_job event in ``year``.

    ``params["base"]`` is expressed in 2026 real dollars (same convention as
    ``scenario.base_salary``), so we compound at ``salary_growth`` from 2026.
    This mirrors ``_employer_annual_base`` and ensures new-job salary doesn't
    silently stay flat in nominal terms over multi-year scenarios.

    Caller is still responsible for prorating by active_months / 12.
    """
    raw = params.get("base", 0.0)
    return raw * (1 + scenario.salary_growth) ** (year - MODEL_EPOCH)


def _employer_annual_base(scenario: Scenario, year: int) -> float:
    """Effective annual W-2 base for a full employer year, accounting for pay_change events.

    Returns a prorated value in the year a pay_change fires; returns the
    growth-adjusted anchored value in all subsequent years.  Employment-end
    prorating (employer_months / 12) is applied by the caller.
    """
    pay_changes = sorted(
        events_of_kind(scenario.events, "pay_change"),
        key=lambda e: (e.year, e.month),
    )
    applicable = [e for e in pay_changes if e.year <= year]
    if not applicable:
        return scenario.base_salary * (1 + scenario.salary_growth) ** (year - MODEL_EPOCH)

    prior = [e for e in applicable if e.year < year]
    in_year = [e for e in applicable if e.year == year]

    base_at_start = (
        prior[-1].params["new_base"] * (1 + scenario.salary_growth) ** (year - prior[-1].year)
        if prior
        else scenario.base_salary * (1 + scenario.salary_growth) ** (year - MODEL_EPOCH)
    )

    if not in_year:
        return base_at_start

    change = in_year[-1]  # last change in the year if multiple
    months_before = change.month - 1
    months_at_new = 12 - months_before
    return (base_at_start * months_before + change.params["new_base"] * months_at_new) / 12


def _employer_bonus_params(scenario: Scenario, year: int) -> tuple[float, float]:
    """Returns (bonus_base_annual, bonus_rate) for the employer bonus paid in
    ``scenario.bonus_month`` of ``year``.

    A pay_change event at or before (year, bonus_month) overrides both values.
    """
    pay_changes = sorted(
        events_of_kind(scenario.events, "pay_change"),
        key=lambda e: (e.year, e.month),
    )
    applicable = [e for e in pay_changes if (e.year, e.month) <= (year, scenario.bonus_month)]
    if not applicable:
        return (
            scenario.base_salary * (1 + scenario.salary_growth) ** (year - MODEL_EPOCH),
            scenario.bonus_rate,
        )
    last = applicable[-1]
    bonus_rate = last.params.get("bonus_rate", scenario.bonus_rate)
    bonus_base = (
        last.params["new_base"]
        if last.year == year
        else last.params["new_base"] * (1 + scenario.salary_growth) ** (year - last.year)
    )
    return bonus_base, bonus_rate


def _blended_contributions(
    scenario: Scenario,
    year: int,
    employer_months: int,
    new_job_months: int,
    new_job_params: dict,
) -> Contributions:
    """Compute contributions when employment may be split across two jobs.

    For a pure single-employer year (12 months, no new job) the result is identical
    to ``compute_contributions(scenario, year)`` — bit-for-bit, so the
    regression snapshot is preserved.
    """
    has_pay_change = bool(events_of_kind(scenario.events, "pay_change"))
    if employer_months == 12 and not new_job_params and not has_pay_change:
        return compute_contributions(scenario, year)

    limits = limits_for(year, scenario.limit_inflation)
    emp_frac = employer_months / 12
    nj_frac = new_job_months / 12

    # --- Primary-employer segment ---
    emp_base = _employer_annual_base(scenario, year)
    emp_desired_pretax = emp_base * scenario.pretax_401k_pct * emp_frac
    emp_desired_roth = emp_base * scenario.roth_401k_pct * emp_frac
    emp_desired_aftertax = emp_base * scenario.aftertax_401k_pct * emp_frac

    # --- New-job segment ---
    nj_base = _new_job_base_for_year(new_job_params, scenario, year)
    nj_match_rate = new_job_params.get("match_rate", 0.0)
    nj_match_cap = new_job_params.get("match_cap", 0.0)
    nj_mega_backdoor = new_job_params.get("mega_backdoor", False)

    # New job: same pre-tax percentage as baseline (per-job override not currently exposed)
    nj_desired_pretax = nj_base * scenario.pretax_401k_pct * nj_frac
    nj_desired_aftertax = (
        (nj_base * scenario.aftertax_401k_pct * nj_frac) if nj_mega_backdoor else 0.0
    )

    # --- Employee deferral limit is annual and shared across employers ---
    total_desired_pretax = emp_desired_pretax + nj_desired_pretax
    total_desired_roth = emp_desired_roth  # new jobs rarely have Roth 401k option

    pretax = min(total_desired_pretax, limits.employee_401k)
    roth = min(total_desired_roth, limits.employee_401k - pretax)

    # Employer match: compute each segment on actual (post-cap) pretax contributions
    # Prorate the employer match cap linearly with months worked
    emp_pretax_actual = min(emp_desired_pretax, limits.employee_401k)
    emp_match = min(emp_pretax_actual * scenario.match_rate, scenario.match_cap * emp_frac)

    nj_pretax_actual = min(nj_desired_pretax, limits.employee_401k)
    nj_match = (
        min(nj_pretax_actual * nj_match_rate, nj_match_cap * nj_frac) if nj_match_rate else 0.0
    )
    total_match = emp_match + nj_match

    # After-tax (mega backdoor) fills remaining annual addition headroom
    remaining = limits.total_401k - pretax - roth - total_match
    aftertax = max(0.0, min(emp_desired_aftertax + nj_desired_aftertax, remaining))

    return Contributions(
        pretax=pretax,
        roth=roth,
        aftertax=aftertax,
        employer_match=total_match,
    )


def _active_expense_level(events: list[LifeEvent], year: int) -> LifeEvent | None:
    """Return the most recently started expense_level event active in ``year``, or None.

    An event is active if it has started (event.year <= year) and has not
    ended (ends_year is None or ends_year >= year).
    """
    candidates = [
        e
        for e in events_of_kind(events, "expense_level")
        if e.year <= year and (e.params.get("ends_year") is None or e.params["ends_year"] >= year)
    ]
    return candidates[-1] if candidates else None


def _resolve_expense_base(scenario: Scenario, year: int) -> float:
    """Effective monthly expense baseline (in 2026 real dollars) for ``year``.

    Returns ``expense_level.params["monthly"]`` if an expense_level event is
    active, otherwise ``scenario.expense_base``.  Both values are anchored in
    2026 real dollars and must be inflated by the caller via
    ``* (1 + expense_growth) ** (year - 2026)``.

    Handles partial transition years: if the expense_level event begins
    mid-year, returns a month-weighted blend of the old and new bases so
    that ``annual_expenses`` stays consistent.  The monthly forecast uses
    the raw base for each month individually, so it does its own check.
    """
    ev = _active_expense_level(scenario.events, year)
    if ev is None:
        return scenario.expense_base
    if ev.year < year:
        return ev.params["monthly"]
    # Transition year: blend old base for months before event, new base from event month
    months_before = ev.month - 1
    months_after = 12 - ev.month + 1
    return (scenario.expense_base * months_before + ev.params["monthly"] * months_after) / 12


def annual_expenses(scenario: Scenario, year: int) -> float:
    """Annual expense total, including any expense_change event deltas.

    With no events this is identical to the pre-events inline calculation.
    Each expense_change event adds its delta_monthly for the months it is
    active within ``year``; each one_time_expense event adds its full
    ``amount`` in the event year only.

    Housing integration:
    - If a house_purchase event is active with replaces_monthly_rent=True,
      the rent component of expense_base is subtracted (prorated for the
      purchase year) and replaced by annual_housing_cost for that event.
    - If a relocation event is active, the effective rent is overridden to
      new_monthly_rent for the active period.
    """
    yrs = year - MODEL_EPOCH
    total = _resolve_expense_base(scenario, year) * (1 + scenario.expense_growth) ** yrs * 12

    # --- expense_change deltas ---
    for e in events_of_kind(scenario.events, "expense_change"):
        if e.year > year:
            continue  # not yet started
        ends_year = e.params.get("ends_year")
        if ends_year is not None and ends_year < year:
            continue  # already ended

        # Prorate for the start year; assume runs to Dec 31 in subsequent years
        start_month = e.month if e.year == year else 1
        active_months = 12 - start_month + 1
        total += e.params.get("delta_monthly", 0.0) * active_months

    # --- one_time_expense events: single-shot cash cost in the event year ---
    for e in events_of_kind(scenario.events, "one_time_expense"):
        if e.year == year:
            total += e.params.get("amount", 0.0)

    # --- Effective rent (before housing swap) ---
    # Start with the scenario-level rent_monthly.
    # A relocation event (no purchase) overrides the rent portion.
    effective_rent_monthly = scenario.rent_monthly
    relocation_ev = most_recent_event_for_year(scenario.events, year, "relocation")
    if relocation_ev is not None:
        new_rent = relocation_ev.params.get("new_monthly_rent")
        if new_rent is not None:
            effective_rent_monthly = new_rent

    # --- Housing swap: replace rent with mortgage costs ---
    house_ev = _housing.active_house_purchase(scenario, year)
    if house_ev is not None:
        terms = _housing.terms_from_event(house_ev)
        if terms.replaces_monthly_rent:
            # Only the months AFTER purchase get their rent swapped for mortgage.
            # Pre-purchase months in the purchase year keep paying rent (already
            # baked into the expense_base * 12 above).
            if house_ev.year == year:
                # Purchase year: rent for months 1..(purchase_month-1), mortgage from purchase_month onward
                mortgage_months = 12 - house_ev.month + 1
                total -= effective_rent_monthly * mortgage_months
            else:
                # Post-purchase years: remove the full year of rent
                total -= effective_rent_monthly * 12

        # Add housing cost (P&I + property tax + insurance + HOA), prorated for purchase year
        total += _housing.annual_housing_cost(terms, year)

    elif relocation_ev is not None:
        # Relocation without house purchase: adjust rent if changed
        new_rent = relocation_ev.params.get("new_monthly_rent")
        if new_rent is not None:
            # Subtract old rent for the active period and add new rent
            if relocation_ev.year == year:
                active_months = 12 - relocation_ev.month + 1
            else:
                active_months = 12
            # The total already has scenario.rent_monthly * 12 baked into expense_base.
            # We need to swap from scenario.rent_monthly to new_rent for active_months.
            total -= scenario.rent_monthly * active_months
            total += new_rent * active_months

    return total


# ---------------------------------------------------------------------------
# Main functions
# ---------------------------------------------------------------------------


def annual_summary(scenario: Scenario, year: int) -> AnnualResult:
    """Full annual income & tax ladder for the given year.

    Honors ``employment_end``, ``new_job``, ``expense_change``, ``roth_conversion``,
    and ``ss_start`` events. With an empty events list the output is bit-for-bit
    identical to a bare ``Scenario()`` (regression invariant).
    """
    # ---- Employment segments for this year ----
    employer_months = _employer_months_in_year(scenario, year)
    new_job_months, new_job_params = _new_job_info_for_year(scenario, year)

    # ---- employer income (prorated over active months) ----
    employer_base_annual = _employer_annual_base(scenario, year)
    employer_base = employer_base_annual * employer_months / 12

    # Bonus lands in scenario.bonus_month. Include employer bonus only if that
    # month falls within the employment window.
    employer_bonus = 0.0
    if employer_months >= scenario.bonus_month:  # bonus month still within the window
        bonus_base_annual, effective_bonus_rate = _employer_bonus_params(scenario, year)
        employer_bonus = bonus_base_annual * effective_bonus_rate

    # ---- New-job income (prorated over active months) ----
    nj_base_annual = _new_job_base_for_year(new_job_params, scenario, year)
    nj_base = nj_base_annual * new_job_months / 12

    nj_equity_annual = new_job_params.get("equity_annual", 0.0)
    nj_equity = nj_equity_annual * new_job_months / 12

    nj_bonus = nj_base * new_job_params.get("bonus_rate", 0.0)

    # ---- Combined income ----
    base = employer_base + nj_base  # combined W-2 base
    bonus = employer_bonus + nj_bonus
    rsu = vest_gross_for_year(scenario, year)  # equity.py already honors employment_end
    other = scenario.other_monthly_income * 12

    ltcg = scenario.ltcg_annual

    # nj_equity folds into gross and FICA wages (it's W-2 comp delivered as
    # equity) but is not broken out as its own AnnualResult field.
    gross = base + bonus + rsu + nj_equity + other + ltcg
    wages = base + bonus + rsu + nj_equity  # W-2 wages for FICA

    # ---- Contributions ----
    contribs = _blended_contributions(
        scenario,
        year,
        employer_months=employer_months,
        new_job_months=new_job_months,
        new_job_params=new_job_params,
    )

    # ---- AGI = gross - pre-tax 401(k) ----
    agi = gross - contribs.pretax

    # ---- Federal taxable income ----
    fed_std = federal_standard_deduction(year, scenario.deduction_inflation)

    # ---- CA taxable income ----
    # CA always uses the standard deduction in this model (no itemized on CA
    # side) — a deliberate simplification, see docs/DESIGN.md. Do NOT add
    # property tax to CA income; property tax only affects federal itemized
    # via the SALT cap.
    ca_std = california_standard_deduction(year, scenario.deduction_inflation)
    ca_taxable = max(0.0, agi - ca_std)

    # ---- CA tax (compute first — needed for SALT calculation) ----
    ca_tax = tax.ca_income_tax(ca_taxable, year, scenario.bracket_inflation)

    # ---- Itemized deduction: two-pass approach ----
    # Pass 1: compute CA tax with standard deduction (done above).
    # Pass 2: use CA tax as part of SALT for federal itemized comparison.
    # Property tax paid this year (prorated for purchase year if applicable):
    house_ev_for_salt = _housing.active_house_purchase(scenario, year)
    if house_ev_for_salt is not None:
        terms_for_salt = _housing.terms_from_event(house_ev_for_salt)
        # Prorate property tax to months owned in this year
        if house_ev_for_salt.year == year:
            months_owned = 12 - house_ev_for_salt.month + 1
        else:
            months_owned = 12
        property_tax_paid = terms_for_salt.annual_property_tax * months_owned / 12
    else:
        property_tax_paid = 0.0

    # SALT = CA income tax + property tax. This function applies the $10k SALT cap.
    salt_paid = ca_tax + property_tax_paid
    itemized_excess = _housing.itemized_excess_for_year(
        scenario,
        year,
        standard_deduction=fed_std,
        salt_paid=salt_paid,
    )
    fed_deduction = fed_std + itemized_excess
    fed_taxable_ordinary = max(0.0, agi - fed_deduction - ltcg)

    # ---- Roth conversion ordinary income (added to AGI / ordinary income) ----
    roth_conversion_income = 0.0
    for e in events_of_kind(scenario.events, "roth_conversion"):
        if e.year > year:
            continue  # not yet started
        ends_year = e.params.get("ends_year")
        if ends_year is not None and ends_year < year:
            continue  # already ended
        recurring = e.params.get("recurring", False)
        if not recurring and e.year < year:
            continue  # one-shot event that fired in a prior year
        roth_conversion_income += e.params.get("amount", 0.0)

    # Roth conversion adds to AGI (ordinary income in the conversion year)
    # and to gross income so the gross_income field reports the conversion correctly.
    # Re-derive key quantities that depend on the updated AGI.
    gross += roth_conversion_income
    agi_with_conversion = agi + roth_conversion_income
    fed_taxable_ordinary_with_conversion = max(0.0, agi_with_conversion - fed_deduction - ltcg)
    ca_taxable_with_conversion = max(0.0, agi_with_conversion - ca_std)
    # Re-compute CA tax with conversion (needed for accurate total tax)
    ca_tax_with_conversion = tax.ca_income_tax(
        ca_taxable_with_conversion, year, scenario.bracket_inflation
    )

    # Use the conversion-aware values for all downstream tax calculations.
    agi = agi_with_conversion
    fed_taxable_ordinary = fed_taxable_ordinary_with_conversion
    ca_taxable = ca_taxable_with_conversion
    ca_tax = ca_tax_with_conversion

    # ---- Social Security income ----
    # SS is taxable at the federal level (85% included in gross income, simplified).
    # CA does NOT tax SS — ca_taxable is not adjusted.
    # SS is not subject to FICA (it's a government transfer, not earned income).
    ss_annual = _ss_income_for_year(scenario, year)
    if ss_annual > 0:
        from .data.social_security import ss_federal_taxable

        ss_taxable = ss_federal_taxable(ss_annual)
        # Add full SS cash to gross (for take_home) and taxable SS to fed/agi only.
        gross += ss_annual
        agi += ss_taxable
        fed_taxable_ordinary = max(0.0, fed_taxable_ordinary + ss_taxable)
        # ca_taxable and ca_tax intentionally unchanged.

    # ---- Federal tax ----
    fed_ordinary_tax = tax.federal_income_tax(
        fed_taxable_ordinary, year, scenario.bracket_inflation
    )
    # Bracketed 0/15/20% LTCG, stacked on ordinary taxable income — the same
    # function the drawdown waterfall uses, so lean years get the 0% bracket
    # on the baseline ltcg_annual signal too.
    fed_ltcg_tax = tax.ltcg_federal_tax(
        ltcg,
        ordinary_taxable_income=fed_taxable_ordinary,
        year=year,
        bracket_inflation=scenario.bracket_inflation,
    )

    # ---- FICA ----
    fica = tax.fica(wages, year, scenario.limit_inflation)

    # ---- NIIT on investment income ----
    niit_tax = tax.niit(ltcg + other, agi)

    total_tax = fed_ordinary_tax + fed_ltcg_tax + ca_tax + fica.total + niit_tax

    take_home = gross - contribs.pretax - total_tax - contribs.roth - contribs.aftertax

    return AnnualResult(
        year=year,
        base_salary=base,
        bonus=bonus,
        rsu_gross=rsu,
        other_income=other,
        ltcg=ltcg,
        gross_income=gross,
        contributions=contribs,
        agi=agi,
        fed_taxable=fed_taxable_ordinary,
        ca_taxable=ca_taxable,
        federal_income_tax=fed_ordinary_tax,
        federal_ltcg_tax=fed_ltcg_tax,
        ca_income_tax=ca_tax,
        fica=fica,
        niit=niit_tax,
        total_tax=total_tax,
        take_home=take_home,
        effective_tax_rate=total_tax / gross if gross else 0,
        ss_income=ss_annual,
    )


# ---- ACA subsidy helper ----


def aca_monthly_subsidy_for_year(scenario: Scenario, year: int, agi: float) -> float:
    """Monthly ACA premium tax credit for ACA healthcare expense_change events active in ``year``.

    An event is an ACA premium if and only if it sets ``params={"aca": True}``
    — the explicit contract. The PTC uses the event's delta_monthly as the
    benchmark premium.

    Returns the total monthly subsidy across all matching events (usually just one).
    The subsidy reduces net expenses in projection.py — it is NOT additional income.
    """
    from .tax import aca_premium_tax_credit

    subsidy = 0.0
    for e in events_of_kind(scenario.events, "expense_change"):
        if e.params.get("aca") is not True:
            continue
        if e.year > year:
            continue
        ends_year = e.params.get("ends_year")
        if ends_year is not None and ends_year < year:
            continue
        gross_monthly = e.params.get("delta_monthly", 0.0)
        if gross_monthly > 0:
            subsidy += aca_premium_tax_credit(
                agi=agi,
                year=year,
                monthly_benchmark_premium=gross_monthly,
            )
    return subsidy


# ---- Monthly ----


@dataclass
class MonthlyResult:
    year: int
    month: int
    paydays_in_month: int
    base_paycheck_gross: float  # gross from base only, this month
    bonus_gross: float  # only in scenario.bonus_month
    rsu_gross: float
    other_income: float
    estimated_tax: float
    pretax_401k: float
    roth_aftertax_savings: float
    backdoor_roth_contrib: float  # monthly IRA contribution (after-tax, routed to Roth IRA)
    expenses: float
    net_to_bank: float  # cash arriving in bank (payroll − tax − retirement)
    net_cash_flow: float  # net_to_bank − expenses − backdoor_roth_contrib
    brokerage_balance: (
        float  # taxable account: starts from scenario.brokerage_balance, compounds monthly
    )


def _paydays_in_month(anchor: date, year: int, month: int) -> int:
    """Count Fridays-from-anchor that fall in the given month."""
    last_day = calendar.monthrange(year, month)[1]
    som = date(year, month, 1)
    eom = date(year, month, last_day)
    count = 0
    # Walk backward and forward from anchor by 14-day steps
    # Simpler: enumerate days in month and test
    for d in (som + timedelta(n) for n in range((eom - som).days + 1)):
        days_from_anchor = (d - anchor).days
        if days_from_anchor % 14 == 0:
            count += 1
    return count


def monthly_forecast(
    scenario: Scenario,
    start: date = date(2026, 1, 1),
    end: date = date(2030, 12, 1),
) -> list[MonthlyResult]:
    """Generate month-by-month forecast rows.

    Strategy: compute annual tax / contributions once per year, then
    spread proportionally across months. RSU and bonus appear in the
    months they actually hit.

    Note: expense_change events are applied as flat monthly deltas on top
    of the base expense schedule.
    """
    results: list[MonthlyResult] = []
    brokerage_bal = scenario.brokerage_balance
    monthly_r = scenario.brokerage_return_rate / 12

    # Cache annual results
    annual_cache: dict[int, AnnualResult] = {}

    def annual_for(year: int) -> AnnualResult:
        if year not in annual_cache:
            annual_cache[year] = annual_summary(scenario, year)
        return annual_cache[year]

    # Pre-compute expense_change deltas by year for monthly use
    def expense_delta_for_month(year: int, month: int) -> float:
        total = 0.0
        for e in events_of_kind(scenario.events, "expense_change"):
            if (e.year, e.month) > (year, month):
                continue
            ends_year = e.params.get("ends_year")
            if ends_year is not None and ends_year < year:
                continue
            total += e.params.get("delta_monthly", 0.0)
        return total

    cur = start
    while cur <= end:
        year, month = cur.year, cur.month
        ar = annual_for(year)

        paydays = _paydays_in_month(scenario.pay_anchor, year, month)
        # Base gross per check = annual base / 26. In a transition year
        # ar.base_salary is the *blended* annual base across all employment
        # segments (old employer + new_job prorated), so the Monthly Forecast chart
        # smooths a real "two big old-employer checks, gap, then smaller new-job
        # checks" pattern. Annual totals are correct; only the monthly view is
        # misleading for transition years.
        gross_per_check = ar.base_salary / 26
        base_gross = gross_per_check * paydays

        bonus_gross = ar.bonus if month == scenario.bonus_month else 0.0
        rsu_gross = vest_gross_for_month(scenario, year, month)

        # Cash items
        other = scenario.other_monthly_income
        month_gross_cash = base_gross + bonus_gross + rsu_gross + other

        # Pre-tax 401k: spread annual evenly (~real-world payroll behaviour)
        pretax_monthly = ar.contributions.pretax / 12
        roth_aftertax_monthly = (ar.contributions.roth + ar.contributions.aftertax) / 12

        # Tax: apply annual effective rate to taxable cash this month
        month_tax = month_gross_cash * ar.effective_tax_rate

        # Expenses: base + event deltas. For the monthly view, resolve the
        # expense base per-month so the transition month switches cleanly on
        # the event's start month.
        yrs = year - MODEL_EPOCH
        level_ev = _active_expense_level(scenario.events, year)
        if level_ev is not None and (year > level_ev.year or month >= level_ev.month):
            monthly_base = level_ev.params["monthly"]
        else:
            monthly_base = scenario.expense_base
        base_expenses = monthly_base * (1 + scenario.expense_growth) ** yrs
        expenses = base_expenses + expense_delta_for_month(year, month)
        # One-time expenses land in their exact (year, month)
        for _ote in events_of_kind(scenario.events, "one_time_expense"):
            if (_ote.year, _ote.month) == (year, month):
                expenses += _ote.params.get("amount", 0.0)

        # Net cash that actually arrives in the bank account
        net_to_bank = (
            month_gross_cash
            - pretax_monthly  # never reaches paycheck
            - month_tax  # withheld
            - roth_aftertax_monthly  # post-tax but routed to retirement
        )
        # Backdoor Roth IRA: annual limit grows with limit_inflation, spread
        # monthly; capped at the year's W-2 comp (mirrors projection.py).
        yrs_from_base = year - MODEL_EPOCH
        roth_ira_annual = scenario.roth_ira_annual * (1 + scenario.limit_inflation) ** yrs_from_base
        earned_income = ar.base_salary + ar.bonus + ar.rsu_gross
        roth_ira_annual = min(roth_ira_annual, max(0.0, earned_income))
        roth_ira_monthly = roth_ira_annual / 12

        net_cash_flow = net_to_bank - expenses - roth_ira_monthly

        # Brokerage: compound prior balance then add net surplus
        brokerage_bal = brokerage_bal * (1 + monthly_r) + net_cash_flow

        results.append(
            MonthlyResult(
                year=year,
                month=month,
                paydays_in_month=paydays,
                base_paycheck_gross=base_gross,
                bonus_gross=bonus_gross,
                rsu_gross=rsu_gross,
                other_income=other,
                estimated_tax=month_tax,
                pretax_401k=pretax_monthly,
                roth_aftertax_savings=roth_aftertax_monthly,
                backdoor_roth_contrib=roth_ira_monthly,
                expenses=expenses,
                net_to_bank=net_to_bank,
                net_cash_flow=net_cash_flow,
                brokerage_balance=brokerage_bal,
            )
        )

        # Next month
        if month == 12:
            cur = date(year + 1, 1, 1)
        else:
            cur = date(year, month + 1, 1)

    return results
