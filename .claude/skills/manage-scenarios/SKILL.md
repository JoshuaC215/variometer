---
name: manage-scenarios
description: Add, modify, or remove a named scenario in variometer. Use when the user says "add a scenario for X", "tweak the lean year defaults", "expose a new knob in the UI", or any similar request that touches variometer/scenarios.py. Encapsulates the full recipe — factory + tests + verification — so the agent doesn't have to deeply grok the project first.
---

# Manage scenarios

A scenario is a Python factory that returns a fully populated `Scenario`. The Streamlit UI renders parameter controls **directly from the factory's signature** via `Annotated[type, Knob(...)]`, so adding a new scenario only requires editing **one file**: `variometer/scenarios.py`. The framework (the `@scenario` decorator, `Knob` dataclass, registry, introspection) lives in `scenario_registry.py` and rarely changes. There is no parallel schema dict to maintain. Tests live next to it; verification is fast.

## Step 0 — triage: exploration or permanent scenario?

Before writing a factory, decide which path fits the request:

| Situation | Path |
|---|---|
| One-off "what if" — user wants to see numbers, not a reusable scenario | **Ad-hoc** (see below) |
| Recurring scenario that belongs in the UI / can be tweaked via knobs | **Named factory** (see The recipe) |

Named factories have two homes: bare career-shape archetypes with neutral defaults → `variometer/scenarios.py` (public, committed); anything with the user's specific years, cities, prices, or life plans → `user_data/scenarios.py` (private, gitignored, auto-loaded — same decorator, display names must not collide with the built-ins).

When in doubt, start ad-hoc. It's trivially promotable later.

### Ad-hoc exploration with `--python`

Write a `.py` file in `ad_hoc/` that defines a `scenario` variable, then pass it to `variometer explain`:

```python
# ad_hoc/my_what_if.py  (gitignored — .gitignore excludes ad_hoc/* except the canonical example)
from variometer.config import base_scenario   # NOT Scenario() — applies user_data/profile.json
from variometer.events import LifeEvent

scenario = base_scenario()
scenario.events = [
    LifeEvent(year=2027, month=6, kind="employment_end"),
    LifeEvent(year=2028, month=1, kind="house_purchase",
              params={"price": 1_200_000, "down_pct": 0.20,
                      "rate": 0.065, "term_years": 30}),
]
scenario.summary = "Leave mid-2027, buy $1.2M house Jan 2028"
```

```bash
uv run variometer explain --python ad_hoc/my_what_if.py
uv run variometer explain --python ad_hoc/my_what_if.py --mc --ss-monthly 3500
```

You get the full explainer output pipeline — year-by-year table, key milestones, sustainability table, Monte Carlo — without touching `scenarios.py`, registering anything, or writing tests.

**Multiple scenarios in one file** — use `--var` to pick which variable to run:

```python
# ad_hoc/compare_options.py
scenario_a = base_scenario(); scenario_a.events = [...]; scenario_a.summary = "Option A"
scenario_b = base_scenario(); scenario_b.events = [...]; scenario_b.summary = "Option B"
```

```bash
uv run variometer explain --python ad_hoc/compare_options.py --var scenario_a
uv run variometer explain --python ad_hoc/compare_options.py --var scenario_b
```

**Reference example:** `ad_hoc/example_promo_house_fire.py` is a full worked example (promotion + house purchase + FIRE + Roth ladder). Copy it as a starting point for complex multi-event scenarios. Ad-hoc files with personal specifics belong in `user_data/ad_hoc/` instead.

**Other conventions:**
- Always save ad-hoc files to `ad_hoc/`, never to the repo root.
- Top of the file should have a `# Run:` comment showing the exact `uv run` command.
- The variable name defaults to `scenario`; use descriptive names (`scenario_lean`, `scenario_promo`) when comparing multiple options in one file.
- You can also set top-level `Scenario` fields directly when the scenario calls for a different assumption (e.g. `scenario.price_growth = 0.05`).

**When to promote to a named factory:** if the user wants to tweak the knobs in the Streamlit UI, compare it against other scenarios in the Runway tab, or reuse it across multiple conversations, promote it using the factory recipe below.

## Before you start (named-factory path)

Read these in order (only once per session):
- `docs/SCENARIOS.md` — author's guide; explains the `@scenario` + `Annotated[Knob]` contract.
- `variometer/events.py` top docstring — full param shape per event kind.
- `variometer/scenarios.py` — existing factories you'll mirror.

## The recipe

### Adding a new scenario

1. **Write the factory** (in `variometer/scenarios.py` for public archetypes, `user_data/scenarios.py` for personal ones). Pattern:

   ```python
   @scenario("My scenario")
   def my_scenario(
       start_year:     Annotated[int,   Knob("Start year",          min=2026, max=2050, step=1)]              = 2028,
       monthly_amount: Annotated[float, Knob("Monthly amount ($)",  "dollars", min=0, max=20_000, step=100)]   = 5_000,
   ) -> Scenario:
       """One-line description."""
       s = base_scenario()
       s.events = [
           LifeEvent(year=start_year, month=1, kind="<event_kind>",
                     params={"<key>": monthly_amount, ...}),
       ]
       s.summary = (
           f"Plain-English description with ${monthly_amount:,.0f}/mo "
           f"starting {start_year}."
       )
       return s
   ```

   Critical rules:
   - **`@scenario("Display name")`** auto-registers the factory in `SCENARIO_REGISTRY`. The string is what shows up in UI dropdowns.
   - **`Annotated[type, Knob(...)]`** on each tunable parameter — that's the UI metadata. Type is inferred for `int`/`bool`/`str`/`float`; explicitly specify `"dollars"`, `"percent"`, or `"choice"`.
   - **The default lives in the function signature only.** No separate "default" field — drift is impossible by construction.
   - **Hide a parameter from the UI** by omitting `Annotated`. It still works programmatically; the form just skips it.
   - **Start from `base_scenario()`** (placeholder defaults + the user's profile overlay), and **never mutate declarative defaults** — only append to `s.events`. The eventless baseline must remain canonical.
   - **Set `s.summary`** with the actual parameter values injected. Shown in the UI and CLI.
   - **For expense events,** compute deltas against the inflated baseline:
     ```python
     yrs = year - 2026
     inflated = s.expense_base * (1 + s.expense_growth) ** yrs
     delta = target_monthly - inflated
     ```

2. **Add a test** in `tests/test_scenarios.py`:

   ```python
   class TestMyScenario:
       def test_summary_set(self):
           assert my_scenario().summary

       def test_event_at_chosen_year(self):
           s = my_scenario(start_year=2030)
           assert any(e.year == 2030 for e in s.events)
   ```

   Also extend `test_registry_keys_match_public_library` so the registry assertion includes the new display name. (Public factories only — personal factories can get pins in `user_data/tests/` instead.)

### Modifying an existing scenario

- **Change a default:** edit the function signature default. There is no parallel value to update.
- **Add a knob:** add a new `Annotated[type, Knob(...)]` parameter to the factory signature.
- **Remove a knob:** drop the parameter. If the explainer references it via a `--flag` (`_FLAG_TO_PARAMS` in `variometer/cli.py`), drop that too.
- **Hide a knob from the UI:** strip the `Annotated[...]` wrapper but keep the parameter and default.

### Removing a scenario

1. Delete the factory function (the `@scenario` decorator already registered it; removing the function removes it from `SCENARIO_REGISTRY`).
2. Delete its test class in `tests/test_scenarios.py`.
3. Update `test_registry_keys_match_public_library` to drop the removed display name.
4. The explainer is registry-driven — nothing to update there.

## Verification

### Ad-hoc scenario
```bash
uv run variometer explain --python ad_hoc/my_what_if.py
uv run variometer explain --python ad_hoc/my_what_if.py --var scenario_b   # if using --var
uv run variometer explain --python ad_hoc/my_what_if.py --mc               # optional Monte Carlo
```
No lint or test run needed for a throwaway file.

### Named factory — run all four
```bash
# 1. Lint clean
uv run ruff check
uv run ruff format    # apply formatting (skips scenarios.py by design)

# 2. All tests pass
uv run pytest -q

# 3. The new scenario produces a sensible year-by-year breakdown
uv run variometer explain my_scenario --start-year 2030

# 4. The UI renders the knobs correctly
uv run streamlit run variometer/app.py
# → Runway tab → pick "My scenario" from the dropdown → expand "Scenario parameters"
# → verify the knobs render with correct labels and defaults
```

If a persona pin fails (`tests/test_personas.py`, e.g. `single_ca_rsu/baseline/net_worth/2028`): you mutated `Scenario` defaults somewhere. Revert and channel the change through events — don't edit `tests/personas/single_ca_rsu/expected.json` to match.

## Common pitfalls

- **Knobs don't render in the UI.** You probably forgot the `Annotated[type, Knob(...)]` wrapper, or the file is missing `from __future__ import annotations` (it should already have it — `typing.get_type_hints(include_extras=True)` resolves the strings).
- **`ValueError: Unknown event kind`.** The `kind` string isn't in `EVENT_KINDS` (see `variometer/events.py`). Use one of the listed kinds; don't invent new ones — event kinds are part of the model contract.
- **`child` events don't affect cash flows.** They're UI timeline markers only — model childcare costs via `expense_change`. (`one_time_expense` *does* hit cash flow: its full `amount` lands in the event year/month.)
- **Adding new fields to `Scenario` instead of using events.** Don't. New `Scenario` fields are for things that are *always* present (e.g. `return_rate`). Anything scenario-specific belongs in an event's `params` dict.
- **Skipping `s.summary`.** The UI's info captions and the CLI read this. Empty summary → blank caption.
- **Wrong widget for a numeric.** `int`/`float` annotations infer the kind; "dollars"/"percent"/"choice" must be set explicitly because Python type alone can't distinguish them.
- **Ad-hoc file saved to repo root.** Always save to `ad_hoc/`.

## When you're done

**Ad-hoc:** Show the user the explainer output and interpret the key numbers. Offer to promote to a named factory if they want to tune knobs in the UI.

**Named factory:** Report back:
- The display name (matches the `@scenario(...)` argument) so they can pick it in the UI.
- A one-line summary of what the factory does and the knobs you exposed.
- Output of `uv run pytest -q` (all passing) and `uv run variometer explain <new_scenario>` showing that the year-by-year shape is sensible.
