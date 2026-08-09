"""Life events — perturbations applied to the baseline Scenario at specific months.

Each LifeEvent is a small, composable patch to the simulation.  The base Scenario
holds time-invariant (or slowly-drifting) parameters; events override or augment
those values at a specific point in time.

Usage:
    from variometer.events import LifeEvent
    scenario.events = [
        LifeEvent(year=2028, month=6, kind="house_purchase",
                  params={"price": 1_200_000, "down_pct": 0.20,
                          "rate": 0.0675, "term_years": 30}),
    ]

With an empty events list the model produces identical output to a bare
``Scenario()`` — that invariant is pinned by the regression tests in
``tests/test_events.py`` and ``tests/test_withdrawals.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Event kinds — each string maps to a focused params dict (see docstring below)
# ---------------------------------------------------------------------------
EVENT_KINDS = (
    "employment_end",  # params: {}  — zeros RSU vests after this month,
    #              drops employer salary/bonus/match
    "new_job",  # params: {base, bonus_rate=0, equity_annual=0,
    #          healthcare_monthly=0, match_rate=0, match_cap=0,
    #          mega_backdoor=False, ends_year=None}
    "house_purchase",  # params: {price, down_pct, rate, term_years,
    #          property_tax_rate=0.011, insurance_monthly=300,
    #          hoa_monthly=0, location_label="",
    #          replaces_monthly_rent=True}
    "relocation",  # params: {to_label, new_monthly_rent=None}
    "expense_change",  # params: {delta_monthly, label="", ends_year=None,
    #              aca=False}
    #              aca=True marks the delta as an ACA marketplace premium so
    #              projection.py computes the premium tax credit against it.
    "expense_level",  # params: {monthly, label="", ends_year=None}
    #              Set a new monthly spending baseline in 2026 real dollars.
    #              The model inflates to nominal via expense_growth from 2026,
    #              exactly like expense_base.
    #              ends_year: if set, reverts to expense_base after that year.
    "one_time_expense",  # params: {amount, label=""}
    #              One-shot cash cost: added to expenses in the event year
    #              (annual view) / the event month (monthly view).
    "child",  # params: {label=""}
    #              UI timeline marker ONLY — does not affect cash flow. Model
    #              childcare costs with an expense_change event.
    "roth_conversion",  # params: {amount, recurring=False, ends_year=None}
    "ss_start",  # params: {monthly_benefit_today_dollars}
    "brokerage_deposit",  # params: {amount, label=""}  — post-tax lump sum deposited
    #              directly into brokerage (e.g. illiquid equity liquidity events)
    "pay_change",  # params: {new_base, bonus_rate=None, equity_refresh_grant_value=None}
    #              Internal compensation change (e.g. promo / merit increase) while
    #              staying put.  new_base is the absolute annual base salary
    #              from this event's month forward.  Optional keys override the
    #              scenario-level bonus_rate and refresh grant target from this
    #              month forward without touching the vest schedule.
    "equity_grant",  # params: {value, label=""}
    #              Supplemental one-time RSU grant (e.g. promo equity bonus) added on
    #              top of the normal annual refresh.  Vests flat over 48 months
    #              starting at the event's own (year, month).
)


@dataclass(frozen=True)
class LifeEvent:
    """An immutable life event that perturbs the simulation at (year, month).

    The ``params`` dict is mutable (dict), so LifeEvent instances are not
    hashable — that's intentional: events are stored in lists, not sets.
    """

    year: int
    month: int = 1
    kind: str = ""
    params: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"Unknown event kind {self.kind!r}. Valid kinds: {EVENT_KINDS}")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def events_of_kind(events: list[LifeEvent], kind: str) -> list[LifeEvent]:
    """All events of a specific kind, in chronological order."""
    return sorted(
        (e for e in events if e.kind == kind),
        key=lambda e: (e.year, e.month),
    )


def first_event_of_kind(events: list[LifeEvent], kind: str) -> LifeEvent | None:
    """Earliest event of the given kind, or None."""
    matches = events_of_kind(events, kind)
    return matches[0] if matches else None


def active_events_for_year(
    events: list[LifeEvent],
    year: int,
    kind: str | None = None,
) -> list[LifeEvent]:
    """Events that started at or before the end of ``year``.

    Optionally filter by kind.
    """
    return [e for e in events if e.year <= year and (kind is None or e.kind == kind)]


def most_recent_event_for_year(
    events: list[LifeEvent],
    year: int,
    kind: str,
) -> LifeEvent | None:
    """The most recent event of the given kind active as of ``year``, or None."""
    candidates = active_events_for_year(events, year, kind)
    if not candidates:
        return None
    return max(candidates, key=lambda e: (e.year, e.month))
