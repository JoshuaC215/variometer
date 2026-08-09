"""Example personal scenario factories.

Copied to ``user_data/scenarios.py``, this file is auto-imported at app/CLI
startup and its ``@scenario`` factories appear in the UI next to the public
archetypes. Build on the public library — compose events, don't copy code.
Display names must not collide with the built-ins.
"""

from typing import Annotated

from variometer.config import Scenario, base_scenario
from variometer.events import LifeEvent
from variometer.scenario_registry import Knob, scenario


@scenario("My plan — promo then house")
def my_promo_then_house(
    promo_year: Annotated[int, Knob("Promotion year", min=2026, max=2035, step=1)] = 2027,
    house_price: Annotated[
        float, Knob("House price ($)", "dollars", min=200_000, max=3_000_000, step=25_000)
    ] = 750_000,
) -> Scenario:
    """A personal composition: promotion, then buy a house two years later."""
    s = base_scenario()
    salary_at_promo = s.base_salary * (1 + s.salary_growth) ** (promo_year - 2026)
    s.events = [
        LifeEvent(
            year=promo_year,
            month=4,
            kind="pay_change",
            params={"new_base": salary_at_promo * 1.12, "bonus_rate": 0.18},
        ),
        LifeEvent(
            year=promo_year + 2,
            month=6,
            kind="house_purchase",
            params={
                "price": house_price,
                "down_pct": 0.20,
                "rate": 0.0675,
                "term_years": 30,
                "replaces_monthly_rent": True,
                "appreciation_rate": 0.03,
                "maintenance_pct": 0.01,
            },
        ),
    ]
    s.summary = (
        f"Promo in {promo_year} (+12% base, 18% bonus), "
        f"buy a ${house_price:,.0f} house in {promo_year + 2}."
    )
    return s
