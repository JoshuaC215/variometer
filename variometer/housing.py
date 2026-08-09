"""Housing model — mortgage amortization, itemized-vs-standard switch, home equity.

Pure functions, no Streamlit, no global state.

Public API
----------
MortgageTerms       — frozen dataclass describing a home purchase
amortize()          — full month-by-month amortization schedule
annual_interest_for_year()   — interest paid in a calendar year
home_equity_at_eoy()         — equity (down payment + cumulative principal) at EOY
annual_housing_cost()        — total annual outflow attributable to the house
itemized_excess_for_year()   — how much itemized deductions exceed standard deduction
terms_from_event()           — build MortgageTerms from a house_purchase LifeEvent
active_house_purchase()      — most recent house_purchase event active as of year
"""

from __future__ import annotations

from dataclasses import dataclass

# Forward reference — Scenario and LifeEvent are imported lazily to avoid
# circular imports at module level (housing is imported by cashflow which is
# imported by projection which is imported by scenarios).
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Scenario
    from .events import LifeEvent


# ---------------------------------------------------------------------------
# MortgageTerms
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MortgageTerms:
    """All the inputs needed to fully amortize a mortgage.

    Defaults reflect a typical HCOL-metro 30-year conventional purchase.
    """

    price: float
    down_pct: float
    rate: float  # annual, e.g. 0.0675
    term_years: int = 30
    property_tax_rate: float = 0.011  # fraction of price assessed annually
    insurance_monthly: float = 300.0
    hoa_monthly: float = 0.0
    purchase_year: int = 2028
    purchase_month: int = 6
    location_label: str = ""
    replaces_monthly_rent: bool = True
    # Nominal annual price appreciation. 0.0 = no appreciation (conservative).
    # Long-run HCOL-metro nominal has averaged 4–6% with high volatility;
    # 3% (roughly tracking inflation) is a reasonable central planning estimate.
    # Note: appreciation only affects home_equity_at_eoy and NW totals — it is
    # paper wealth, not cashflow. The drawdown waterfall cannot tap it.
    appreciation_rate: float = 0.0

    # Annual maintenance / capex as a fraction of purchase price.
    # Rule of thumb: 1%/yr (mix of routine repairs and amortized big-ticket
    # items like roof / HVAC). The raw dataclass stays neutral at 0.0; the
    # buy_home factory opts into the 1% rule of thumb.
    maintenance_pct: float = 0.0

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def loan_amount(self) -> float:
        return self.price * (1 - self.down_pct)

    @property
    def down_payment(self) -> float:
        return self.price * self.down_pct

    @property
    def monthly_p_and_i(self) -> float:
        """Standard mortgage amortization payment.

        P * r / (1 - (1+r)^-n)  where r = annual_rate / 12, n = term * 12.
        """
        r = self.rate / 12
        n = self.term_years * 12
        if r == 0:
            return self.loan_amount / n
        return self.loan_amount * r / (1 - (1 + r) ** -n)

    @property
    def annual_property_tax(self) -> float:
        return self.price * self.property_tax_rate


# ---------------------------------------------------------------------------
# Amortization schedule
# ---------------------------------------------------------------------------


def amortize(terms: MortgageTerms) -> list[dict]:
    """Month-by-month amortization schedule.

    Returns a list of dicts, length = term_years * 12.
    Each row: {month_index, payment, interest, principal, remaining_balance}.
    month_index starts at 1 (first month of loan).
    """
    r = terms.rate / 12
    payment = terms.monthly_p_and_i
    balance = terms.loan_amount
    schedule = []

    for i in range(1, terms.term_years * 12 + 1):
        interest = balance * r
        principal = payment - interest
        balance = max(0.0, balance - principal)
        schedule.append(
            {
                "month_index": i,
                "payment": payment,
                "interest": interest,
                "principal": principal,
                "remaining_balance": balance,
            }
        )

    return schedule


# ---------------------------------------------------------------------------
# Calendar-year helpers
# ---------------------------------------------------------------------------


def _loan_month_range_for_year(terms: MortgageTerms, year: int) -> tuple[int, int]:
    """Return (first_loan_month_index, last_loan_month_index) active in `year`.

    Loan month 1 = purchase_month of purchase_year.
    Returns (None, None) if the loan is not active in `year`.
    """
    if year < terms.purchase_year:
        return None, None  # type: ignore[return-value]

    payoff_year = terms.purchase_year + terms.term_years
    # If we're past the payoff year the loan is paid off
    if year > payoff_year:
        return None, None  # type: ignore[return-value]

    # Calendar month in terms of loan-month index
    # Loan month 1 = (purchase_year, purchase_month)
    def _loan_month(cal_year: int, cal_month: int) -> int:
        return (cal_year - terms.purchase_year) * 12 + (cal_month - terms.purchase_month) + 1

    total_months = terms.term_years * 12

    # First calendar month active in `year`
    if year == terms.purchase_year:
        first_cal_month = terms.purchase_month
    else:
        first_cal_month = 1

    # Last calendar month active in `year`
    last_cal_month = 12

    first_idx = _loan_month(year, first_cal_month)
    last_idx = _loan_month(year, last_cal_month)

    # Clamp to valid range [1, total_months]
    first_idx = max(1, first_idx)
    last_idx = min(total_months, last_idx)

    if first_idx > last_idx:
        return None, None  # type: ignore[return-value]

    return first_idx, last_idx


def annual_interest_for_year(terms: MortgageTerms, year: int) -> float:
    """Total interest paid in calendar `year`.

    Returns 0 before the purchase year or after payoff.
    """
    first_idx, last_idx = _loan_month_range_for_year(terms, year)
    if first_idx is None:
        return 0.0
    schedule = amortize(terms)
    return sum(schedule[i - 1]["interest"] for i in range(first_idx, last_idx + 1))


def home_equity_at_eoy(terms: MortgageTerms, year: int) -> float:
    """Total home equity at end of `year`.

    Equity = down_payment + cumulative_principal_paid + appreciation_gain.

    Appreciation: ``price × ((1 + appreciation_rate)^years_since_purchase − 1)``,
    compounded annually from the purchase year. With ``appreciation_rate = 0``
    (the neutral dataclass default) this term drops out and equity is just
    down payment + principal.

    Caveats worth knowing:
    - Appreciation is **paper wealth only**. It increases NW totals but does
      not produce cashflow until the home is sold. The drawdown waterfall
      ignores home equity entirely.
    - The down_payment component is included here so callers can use
      ``home_equity_at_eoy(terms, year)`` directly as the home_equity balance.

    Returns 0 before the purchase year.
    """
    if year < terms.purchase_year:
        return 0.0

    schedule = amortize(terms)
    first_idx, last_idx = _loan_month_range_for_year(terms, year)

    if first_idx is None:
        # Past payoff — all principal paid
        cumulative_principal = terms.loan_amount
    else:
        cumulative_principal = sum(schedule[i - 1]["principal"] for i in range(1, last_idx + 1))

    # Years-of-ownership for appreciation. Year-end of purchase_year counts as
    # one full year of growth from the purchase price — a small simplification
    # vs. partial-year compounding, but fine at planning resolution.
    years_owned = max(0, year - terms.purchase_year + 1)
    if terms.appreciation_rate != 0.0:
        appreciation_gain = terms.price * ((1 + terms.appreciation_rate) ** years_owned - 1)
    else:
        appreciation_gain = 0.0

    return terms.down_payment + cumulative_principal + appreciation_gain


def annual_housing_cost(terms: MortgageTerms, year: int) -> float:
    """Total annual cash outflow attributable to the house in ``year``.

    Includes: P&I + property tax + insurance + HOA + maintenance.
    Returns 0 before the purchase year. All components are prorated for the
    purchase year by months owned.

    Maintenance is `price × maintenance_pct` per year (1% rule of thumb in
    the factories). It's a real cashflow drain on a real-house comparison —
    leaving it out systematically understates the cost of ownership and
    biases rent-vs-buy toward buy.
    """
    if year < terms.purchase_year:
        return 0.0

    first_idx, last_idx = _loan_month_range_for_year(terms, year)

    # After the mortgage is paid off, P&I = 0 but the house still costs
    # property tax + insurance + HOA + maintenance for every month owned.
    # (year >= purchase_year here, so a None range can only mean past payoff.)
    if first_idx is None:
        return (
            terms.annual_property_tax
            + 12 * terms.insurance_monthly
            + 12 * terms.hoa_monthly
            + terms.price * terms.maintenance_pct
        )

    months_owned = last_idx - first_idx + 1

    p_and_i = months_owned * terms.monthly_p_and_i
    property_tax = terms.annual_property_tax * months_owned / 12
    insurance = terms.insurance_monthly * months_owned
    hoa = terms.hoa_monthly * months_owned
    maintenance = terms.price * terms.maintenance_pct * months_owned / 12

    return p_and_i + property_tax + insurance + hoa + maintenance


# ---------------------------------------------------------------------------
# Itemized deduction excess
# ---------------------------------------------------------------------------

_SALT_CAP_TCJA = 10_000.0  # TCJA-era cap (2018–present under current law)
_SALT_CAP_PRE_TCJA = None  # pre-TCJA: effectively unlimited (use total SALT paid)


def itemized_excess_for_year(
    scenario: Scenario,
    year: int,
    standard_deduction: float,
    salt_paid: float,
) -> float:
    """Return how much itemized deductions exceed the standard deduction this year.

    Computes: mortgage_interest_this_year + min(salt_paid, salt_cap) − standard_deduction.
    Returns max(0, that) so callers can add it as an additive adjustment to the
    existing federal-deduction calculation in cashflow.

    ``salt_paid`` is state income tax + property tax paid this year.  The TCJA
    $10k cap applies when ``scenario.tcja_active`` is True (default).  When
    False (pre-TCJA / post-sunset), the full SALT amount is deductible.

    With no house_purchase event: returns scenario.itemized_excess_over_standard.
    """
    ev = active_house_purchase(scenario, year)
    if ev is None:
        return scenario.itemized_excess_over_standard

    salt_cap = _SALT_CAP_TCJA if getattr(scenario, "tcja_active", True) else None
    terms = terms_from_event(ev)
    mortgage_interest = annual_interest_for_year(terms, year)
    capped_salt = min(salt_paid, salt_cap) if salt_cap is not None else salt_paid
    itemized = mortgage_interest + capped_salt
    return max(0.0, itemized - standard_deduction)


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------


def terms_from_event(ev: LifeEvent) -> MortgageTerms:
    """Build MortgageTerms from a `house_purchase` LifeEvent."""
    p = ev.params
    return MortgageTerms(
        price=p["price"],
        down_pct=p["down_pct"],
        rate=p["rate"],
        term_years=p.get("term_years", 30),
        property_tax_rate=p.get("property_tax_rate", 0.011),
        insurance_monthly=p.get("insurance_monthly", 300.0),
        hoa_monthly=p.get("hoa_monthly", 0.0),
        purchase_year=ev.year,
        purchase_month=ev.month,
        location_label=p.get("location_label", ""),
        replaces_monthly_rent=p.get("replaces_monthly_rent", True),
        appreciation_rate=p.get("appreciation_rate", 0.0),
        maintenance_pct=p.get("maintenance_pct", 0.0),
    )


def active_house_purchase(scenario: Scenario, year: int) -> LifeEvent | None:
    """Most recent house_purchase event with year <= `year`, or None."""
    from .events import most_recent_event_for_year

    return most_recent_event_for_year(scenario.events, year, "house_purchase")
