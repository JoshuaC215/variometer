# Scenarios — author's guide

A **scenario** is a small Python factory that returns a fully populated `Scenario`. Each factory builds on the user's baseline (`base_scenario()` — placeholder defaults + the `user_data/profile.json` overlay) and appends `LifeEvent` perturbations to model a "what if" — e.g. "leave in 2028 and take a lean year off."

**Two homes for factories:**

- `variometer/scenarios.py` — the **public archetype library** (Stay put, Internal promotion, Change jobs, Sabbatical / career gap, FIRE with Roth ladder, Buy home). Bare career shapes with neutral defaults; committed.
- `user_data/scenarios.py` — **your personal compositions** (specific exit years, cities, prices, partner assumptions). Same `@scenario` decorator; auto-loaded at startup; gitignored. Display names must not collide with the built-ins.

Listings (the app's scenario dropdowns, `variometer explain --list`) show personal scenarios first, with the built-in archetypes labeled and grouped after them. Origin is derived from each factory's `__module__` (`is_builtin_scenario` / `grouped_scenario_names` / `visible_scenario_names` in `scenario_registry.py`). Setting `"_ui": {"hide_builtin_scenarios": true}` in `profile.json` hides the built-ins from listings once personal scenarios exist; they remain runnable by name.

The framework (the `@scenario` decorator, the `Knob` dataclass, the registry, signature introspection) lives in `scenario_registry.py` — you almost never need to touch it. The Streamlit UI reads parameter metadata directly from each factory's `Annotated[...]` declarations, so adding a new scenario does **not** require touching `app.py` and there is no parallel schema dict to keep in sync.

If you're an agent picking this up cold, also read [.claude/skills/manage-scenarios/SKILL.md](.claude/skills/manage-scenarios/SKILL.md) — it's a step-by-step recipe.

---

## The contract

For each named scenario you write **one function** that does two things:

1. Decorated with `@scenario("Display name")` to register it.
2. Each tunable parameter is annotated `Annotated[type, Knob("Label", ...)]` with its default in the usual place. The Streamlit app introspects this to render the parameter form.

That's it. No registry edits. No separate schema. The function signature *is* the schema.

---

## Anatomy of a factory

```python
from typing import Annotated
from variometer.scenario_registry import scenario, Knob
from variometer.config import Scenario, base_scenario
from variometer.events import LifeEvent

@scenario("Sabbatical / career gap")
def sabbatical(
    start_year:            Annotated[int,   Knob("Start year",                min=2026, max=2050, step=1)]               = 2030,
    return_year:           Annotated[int,   Knob("Return year",               min=2026, max=2055, step=1)]               = 2032,
    lean_monthly_expenses: Annotated[float, Knob("Lean budget/mo (2026 $)",   "dollars", min=2_000, max=15_000, step=250)] = 7_000,
    healthcare_monthly:    Annotated[float, Knob("Healthcare monthly ($)",    "dollars", min=0,     max=3_000,  step=50)]  = 700,
) -> Scenario:
    """No W-2 income for [start_year, return_year). Then return to work."""
    s = base_scenario()
    # ... compute expense deltas, build s.events, set s.summary ...
    return s
```

Things to notice:

- **Start from `base_scenario()`, not `Scenario()`.** `base_scenario()` applies the user's `profile.json` overlay; bare `Scenario()` is the illustrative placeholder persona.
- **Default is in the function signature only.** `start_year=2030` is the single source of truth — no risk of drift between a UI default and the factory default because there is only one default.
- **Kind is inferred from the type** when omitted: `int` → `"int"`, `bool` → `"bool"`, `str` → `"text"`. Specify the kind explicitly for `"dollars"`, `"percent"`, or `"choice"` — those aren't expressible by Python type alone.
- **Never mutate `Scenario` defaults.** Only append to `s.events`. The eventless baseline must remain canonical.
- **Don't set fields the sidebar manages unless you mean to override them.** The Streamlit sidebar overlays its values (return rate, base salary, stock price, expense base, …) onto your factory's output via `apply_core_overrides`, but only for fields still equal to the `base_scenario()` default. If your factory sets `return_rate = 0.04` it will *win* over the sidebar — which is the right behavior when the scenario is fundamentally about a low-return assumption, and the wrong behavior if you just wanted "the user's return rate." Stick to events for perturbations; touch top-level `Scenario` fields only when the scenario is supposed to dictate them.
- **Set `s.summary`** with the actual parameter values injected — it's shown in the UI captions and the CLI.
- **Hide a parameter from the UI** by *omitting* the `Annotated[...]` wrapper. The factory still accepts it programmatically; the Streamlit form just skips it. Useful for advanced overrides that don't need a widget.

---

## The `Knob` dataclass

```python
@dataclass(frozen=True)
class Knob:
    label: str
    kind: str = ""           # auto-inferred from the annotated type if omitted
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple | None = None     # required for kind="choice"
    help: str | None = None
```

| kind      | widget               | type returned     | when to specify                                 |
|-----------|----------------------|-------------------|-------------------------------------------------|
| `int`     | `st.number_input`    | `int`             | auto-inferred from `int` annotation             |
| `float`   | `st.number_input`    | `float`           | auto-inferred from `float` annotation           |
| `dollars` | `st.number_input`    | `float`           | always — formats as integer dollars             |
| `percent` | `st.slider` (0..1)   | `float`           | always — slider with 0..1 range                  |
| `bool`    | `st.checkbox`        | `bool`            | auto-inferred from `bool` annotation            |
| `choice`  | `st.selectbox`       | as in `choices`   | always — requires `choices=(...)`                |
| `text`    | `st.text_input`      | `str`             | auto-inferred from `str` annotation             |

---

## Adding a new scenario

0. **Pick the file.** A bare career shape any tech worker could use → `variometer/scenarios.py` (public). Anything with your specific years, cities, prices, or life plans → `user_data/scenarios.py` (private).

1. **Write the factory.** Decorate with `@scenario("Display name")`. Annotate each user-tunable kwarg with a `Knob`. Build on `base_scenario()`, set `s.summary` with parameter values injected.

2. **Add a test** (public factories only) in `tests/test_scenarios.py`:
   ```python
   class TestMyScenario:
       def test_summary_set(self):
           assert my_scenario().summary
       def test_event_at_chosen_year(self):
           s = my_scenario(start_year=2030)
           assert any(e.year == 2030 for e in s.events)
   ```
   Extend `test_registry_keys_match_public_library` so the registry assertion includes the new display name. Personal factories can get pins in `user_data/tests/` instead.

3. **Verify.** `uv run pytest` and `uv run variometer explain my_scenario` to eyeball a year-by-year breakdown.

That's it — the Streamlit Runway tab picks it up automatically because it iterates `SCENARIO_REGISTRY` and introspects each factory.

---

## Modifying an existing scenario

- **Change a default:** edit the function signature default. There is no parallel value to update.
- **Add a knob:** add a new annotated parameter (with default) to the factory signature.
- **Remove a knob:** drop the parameter. If the explainer references it via a `--flag` (`_FLAG_TO_PARAMS` in `variometer/cli.py`), drop that too.
- **Hide a knob from the UI temporarily:** strip just the `Annotated[...]` wrapper, keeping the parameter and default.

---

## Composing scenarios in code

Outside of the named factories you can compose freely. The model walks events year-by-year and asks each module which apply — no precedence or ordering. Two events in the same month both fire.

```python
from variometer.config import base_scenario
from variometer.events import LifeEvent
from variometer.projection import project_net_worth

s = base_scenario()
s.events = [
    LifeEvent(year=2028, month=6, kind="house_purchase",
              params={"price": 1_200_000, "down_pct": 0.20,
                      "rate": 0.0675, "term_years": 30}),
    LifeEvent(year=2030, month=3, kind="employment_end"),
    LifeEvent(year=2030, month=4, kind="new_job",
              params={"base": 160_000, "equity_annual": 60_000,
                      "healthcare_monthly": 0}),
]
results = project_net_worth(s, list(range(2026, 2046)))
```

This pattern is good for notebook / `ad_hoc/` exploration (see `variometer explain --python`). For repeated use, promote it to a named factory.

---

## Event reference (summary)

Full param shapes live in the top docstring of [variometer/events.py](variometer/events.py).

| Kind                | What it does                                                                                |
|---------------------|---------------------------------------------------------------------------------------------|
| `employment_end`    | Zeros RSU vests after this month; drops employer salary/bonus/match; rolls Roth 401(k) → Roth IRA |
| `new_job`           | Replaces salary, equity income, match, mega-backdoor, healthcare from start onward          |
| `pay_change`        | New base / bonus rate / refresh target at the same employer (promotion, re-level)            |
| `equity_grant`      | Supplemental one-time grant on top of the normal refresh (48-month flat vest)                |
| `house_purchase`    | Down payment from brokerage (with LTCG realization); P&I + tax + insurance + HOA replace rent |
| `relocation`        | Overrides rent component without buying a house                                              |
| `expense_level`     | Replaces the expense baseline (2026 $) for `[event.year/month, ends_year]`                   |
| `expense_change`    | Adds `delta_monthly` for months in `[event.year/month, ends_year]`                           |
| `one_time_expense`  | One-shot cash cost added to expenses in the event year / month                               |
| `child`             | UI timeline marker only — model childcare via `expense_change`                               |
| `roth_conversion`   | Moves $ from pre-tax 401(k) into a Roth cohort; adds ordinary income that year               |
| `ss_start`          | Begins Social Security income; 85% federal taxable, CA-exempt                                |
| `brokerage_deposit` | One-time windfall (net-of-tax cash) landing as 100%-basis brokerage money                    |

---

## Validation steps to run after any change

```bash
uv run ruff check                                   # must pass with 0 errors
uv run ruff format                                  # apply formatting (skips scenarios.py by design)
uv run pytest -q                                    # 250+ tests, <5s
uv run pytest user_data/tests                       # owner's pinned numbers (if user_data/ exists)
uv run variometer explain <changed_scenario_name>    # eyeball year-by-year
uv run streamlit run variometer/app.py              # Runway tab; confirm knobs render
```

If something breaks the persona pins (`tests/test_personas.py`, backed by `tests/personas/single_ca_rsu/expected.json`), back out — you almost certainly mutated `Scenario` defaults or changed an unrelated code path. The baseline is sacred. Editing `expected.json` to make a test green is the wrong fix; a genuine model change is re-pinned deliberately with `uv run python scripts/repin_personas.py <persona_dir>` and explained in the commit.
