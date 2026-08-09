"""RSU vest schedule.

Combines explicit grant schedules (``Scenario.vest_grants``, loaded from
``user_data/grants.csv``) with projected refreshes.
Projected refreshes are sized at a flat per-grant dollar target that
appreciates slowly each year (similar to salary growth) and is independent
of prior grants — i.e. a linear model, not a band-targeted top-up.

Supplemental one-time grants (e.g. a promo equity bonus) can be layered on
via ``equity_grant`` events; they vest on the same 48-month flat cadence
starting the event's own (year, month).
"""

from .config import PROJECTION_END_YEAR, Scenario
from .events import events_of_kind, first_event_of_kind

# Projected refreshes are generated through PROJECTION_END_YEAR (config.py) —
# refreshes past the scenario's employment_end / retirement_year cutoff are
# skipped by vest_schedule(), so over-extending is harmless.


def projected_refresh_years(scenario: Scenario) -> range:
    """Calendar years for which auto-projected refresh grants are generated.

    Starts at ``refresh_projection_start_year`` (the first year NOT covered by
    an explicit grant in ``vest_grants``) and extends through the projection
    horizon; the employer keeps issuing refreshes for as long as employment
    continues.
    """
    return range(scenario.refresh_projection_start_year, PROJECTION_END_YEAR + 1)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def project_flat_refresh(
    grant_year: int,
    total_value: float,
    price_at_grant: float,
    issue_month: int = 3,
) -> dict[tuple[int, int], float]:
    """Generate a flat-vesting refresh schedule.

    48 consecutive months starting ``issue_month`` of grant_year. Employers
    with a different projected-refresh cadence are covered by the explicit
    ``grants.csv`` schedule, which always wins for the years it covers.
    """
    if total_value <= 0 or price_at_grant <= 0:
        return {}
    total_shares = total_value / price_at_grant
    per_month = total_shares / 48

    schedule: dict[tuple[int, int], float] = {}
    year, month = grant_year, issue_month
    for _ in range(48):
        schedule[(year, month)] = per_month
        year, month = _next_month(year, month)
    return schedule


def _price_at_date(scenario: Scenario, year: int, month: int) -> float:
    """Price projected from ``price_now`` (as of ``price_now_date``) using ``price_growth``."""
    anchor = scenario.price_now_date
    years_from_now = (year - anchor.year) + (month - anchor.month) / 12.0
    return scenario.price_now * (1 + scenario.price_growth) ** years_from_now


def _effective_grant_value(scenario: Scenario, grant_year: int) -> float:
    """Per-grant target value for the refresh issued in March of ``grant_year``.

    Anchored at ``equity_refresh_grant_value`` in ``equity_refresh_base_year``
    and grown at ``equity_refresh_growth`` annually. A pay_change event may
    include ``equity_refresh_grant_value`` to re-anchor the target (e.g. after
    a promotion); subsequent years grow the new anchor.

    Only events at or before the grant's March issue date are considered —
    the decision is made when the grant is issued.
    """
    pay_changes = sorted(
        events_of_kind(scenario.events, "pay_change"),
        key=lambda e: (e.year, e.month),
    )
    applicable = [
        e
        for e in pay_changes
        if "equity_refresh_grant_value" in e.params
        and (e.year, e.month) <= (grant_year, scenario.refresh_issue_month)
    ]
    if not applicable:
        return scenario.equity_refresh_grant_value * (
            (1 + scenario.equity_refresh_growth) ** (grant_year - scenario.equity_refresh_base_year)
        )
    last = applicable[-1]
    return last.params["equity_refresh_grant_value"] * (
        (1 + scenario.equity_refresh_growth) ** (grant_year - last.year)
    )


def vest_schedule(scenario: Scenario) -> dict[tuple[int, int], float]:
    """Return {(year, month): total_shares} combining all grants.

    - ``scenario.vest_grants`` — explicit schedules for grants already issued
      (from ``user_data/grants.csv``, or the illustrative default)
    - Projected refreshes — linear per-grant target (``equity_refresh_grant_value``
      grown at ``equity_refresh_growth`` from the base year), or overridden
      per-year via ``refresh_overrides``
    - ``equity_grant`` events — supplemental one-time grants layered on top
      of the normal refresh, vesting over 48 months from the event's month

    Honors ``employment_end`` events: vests after the end month are dropped,
    and refresh grants not yet issued by the end date are skipped.  Refresh
    grants are issued in ``refresh_issue_month`` of the grant year, so a grant
    is included if and only if the employment end date is >= (grant_year,
    refresh_issue_month).  With no events the output is identical to the
    pre-events baseline.

    Note: the end month is treated as active on both sides — vesting uses
    ``key <= cutoff`` (end month included) and ``cashflow._employer_months_in_year``
    also counts the end month as a full worked month, so a departure in
    March means a full 3 months of salary plus any March vest.
    """
    # Determine the employment cutoff from events or retirement_year field.
    end_event = first_event_of_kind(scenario.events, "employment_end")
    if end_event:
        cutoff: tuple[int, int] | None = (end_event.year, end_event.month)
    elif scenario.retirement_year is not None:
        # retirement_year means "first year with no employment income" — last vest
        # is December of the prior year, consistent with salary being 0 all of retirement_year.
        cutoff = (scenario.retirement_year - 1, 12)
    else:
        cutoff = None

    combined: dict[tuple[int, int], float] = {}
    issue_month = scenario.refresh_issue_month

    for schedule in scenario.vest_grants.values():
        for key, sh in schedule.items():
            if cutoff is None or key <= cutoff:
                combined[key] = combined.get(key, 0.0) + sh

    for grant_year in projected_refresh_years(scenario):
        # Refresh grants are issued in issue_month of grant_year.  Skip if you
        # left before that month — you wouldn't have received the grant.
        # Example (March issue): leave November 2030 → March 2030 grant IS
        # included (8 months of vesting); leave February 2030 → skipped.
        if cutoff is not None and cutoff < (grant_year, issue_month):
            continue
        if grant_year in scenario.refresh_overrides:
            value = scenario.refresh_overrides[grant_year]
        else:
            value = _effective_grant_value(scenario, grant_year)
        price = _price_at_date(scenario, grant_year, issue_month)
        for key, sh in project_flat_refresh(grant_year, value, price, issue_month).items():
            if cutoff is None or key <= cutoff:
                combined[key] = combined.get(key, 0.0) + sh

    # Supplemental one-time grants (e.g. promo bonus equity). Each vests over
    # 48 months starting at the event's own (year, month).
    for ev in events_of_kind(scenario.events, "equity_grant"):
        if cutoff is not None and cutoff < (ev.year, ev.month):
            continue
        value = ev.params["value"]
        price = _price_at_date(scenario, ev.year, ev.month)
        for key, sh in project_flat_refresh(ev.year, value, price, ev.month).items():
            if cutoff is None or key <= cutoff:
                combined[key] = combined.get(key, 0.0) + sh

    return combined


def computed_refresh_values(scenario: Scenario) -> dict[int, float]:
    """Return the dollar value for each projected refresh year.

    Linear per-grant target grown from base year, unless overridden via
    ``refresh_overrides``. Supplemental ``equity_grant`` events are not
    included here — they are layered separately by ``vest_schedule``.
    """
    result: dict[int, float] = {}
    for grant_year in projected_refresh_years(scenario):
        if grant_year in scenario.refresh_overrides:
            result[grant_year] = scenario.refresh_overrides[grant_year]
        else:
            result[grant_year] = _effective_grant_value(scenario, grant_year)
    return result


def vest_gross_for_month(scenario: Scenario, year: int, month: int) -> float:
    """Dollar gross value of RSUs vesting in (year, month)."""
    schedule = vest_schedule(scenario)
    shares = schedule.get((year, month), 0.0)
    return shares * _price_at_date(scenario, year, month)


def vest_gross_for_year(scenario: Scenario, year: int) -> float:
    """Sum of RSU gross value across all months in `year`."""
    schedule = vest_schedule(scenario)
    return sum(
        shares * _price_at_date(scenario, y, m) for (y, m), shares in schedule.items() if y == year
    )
