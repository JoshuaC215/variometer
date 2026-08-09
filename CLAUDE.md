# Agent onboarding — Variometer

**Variometer** is a multi-year cash-flow / net-worth planning model for tech
workers with equity comp. Streamlit UI, pure-Python core, no DB. Read this
top-to-bottom before making changes.

## TL;DR — environment

- **Python 3.11+**, managed with **`uv`**. Never use bare `python`, `pip`, or `pytest`.
- All commands run with `uv run ...`.
- The repo's `.venv/` already exists; `uv` keeps it in sync.

```bash
uv sync                    # install deps (idempotent)
uv run pytest              # public test suite (~4s, placeholder data)
uv run pytest user_data/tests   # private pinned regressions (profile ON), if user_data/ exists
uv run pytest -q tests/test_withdrawals.py   # one file
uv run streamlit run variometer/app.py       # launch the UI on :8501
uv run variometer explain                    # CLI scenario explainer
uv run variometer explain --list             # list scenarios

# The installed console script (same engine; `uv run variometer` in a checkout)
uv run variometer                            # config status
uv run variometer ui                         # == uv run streamlit run variometer/app.py
uv build                                     # sdist + wheel into dist/

# Lint + format (Astral's ruff — same vendor as uv)
uv run ruff check           # lint (must pass with 0 errors)
uv run ruff check --fix     # auto-fix safe issues
uv run ruff format          # apply formatting
uv run ruff format --check  # check formatting without writing
```

**Run `uv run ruff check` and `uv run pytest` after every iteration.** Both
should finish clean. The `scenarios.py` files are excluded from `ruff format`
(their column-aligned `Annotated[...]` declarations are intentional);
everything else is auto-formatted.

If a `uv run` command says "no virtualenv", run `uv sync` first.

## The two-layer architecture (read this first)

The repo is **public model + gitignored private layer**:

- **Public**: the `variometer/` package, tests, docs. The `Scenario` dataclass defaults are **illustrative placeholders**
  (a fictional senior IC) — never anyone's real numbers.
- **Private (`user_data/`, gitignored)**: the user's truth. `profile.json`
  (scalar Scenario overrides), `grants.csv` (actual vest schedule),
  `scenarios.py` (personal factories), `historical.csv` (actuals),
  `REVIEW_NOTES.md`, `reports/`, `snapshots/`, `ad_hoc/`, `tests/`.

The single seam is **`config.base_scenario()`** = `Scenario()` + profile
overlay + grants.csv. Scenario factories, the sidebar defaults, and the CLI
all start from it. `VARIOMETER_USER_DATA` env var overrides the dir;
`""` disables the private layer (the public test suite does this in
`tests/conftest.py` so it's deterministic for everyone).

**Rules for agents:**
- Personal facts (balances, comp, grants, quirks) go in `user_data/`, never
  in committed files. If you're about to write a real dollar amount or a
  person/employer name into the repo, stop — it belongs in the private layer.
- When modeling for the repo's owner, build on `base_scenario()`, not
  `Scenario()`. Bare `Scenario()` is the placeholder persona.
- `user_data/tests/personas/personal/` pins the owner's exact trajectories,
  the same way `tests/personas/single_ca_rsu/` pins the placeholder's. If a
  model change legitimately shifts numbers, both the public pins and the
  private pins need deliberate re-pinning — say so explicitly.

## Layout

```
variometer/                  the installable package
  app.py                     Streamlit UI — single file (the real one). Tabs:
                               🏃 Runway (primary), 💎 Vest Schedule,
                               📜 Historical (only with user_data/historical.csv),
                               📅 Monthly Forecast (debug).
  cli.py                     the `variometer` console script: bare = config
                             status (never prints a dollar figure — tests pin
                             that), `explain` = year-by-year breakdown of any
                             registered scenario, `ui` = launch Streamlit
  (everything below is the pure-Python core — no UI / no Streamlit imports)
  config.py                  Scenario dataclass (placeholder defaults), MODEL_EPOCH,
                             base_scenario()
  user_data_io.py            user_data/ loaders: profile.json, grants.csv,
                             historical.csv, load_user_scenarios()
  events.py                  LifeEvent — perturbations layered on Scenario
  tax.py                     fed + CA brackets, FICA, NIIT, ACA PTC, LTCG (bracketed)
  retirement.py              401(k) limits and contribution allocator
  equity.py                  RSU vest schedule + projected refresh grants
  cashflow.py                annual tax ladder + monthly forecast
  housing.py                 mortgage amortization, itemized switch, home equity
  withdrawals.py             drawdown waterfall (5 buckets)
  projection.py              multi-account NW projection + Monte Carlo + stress paths
  scenarios.py               the PUBLIC archetype library (the file you edit for
                             public scenarios; personal ones → user_data/scenarios.py)
  scenario_registry.py       Knob / BoundKnob / @scenario decorator / SCENARIO_REGISTRY /
                             introspection + apply_core_overrides. Framework; rarely changes.
  testing.py                 persona-fixture harness: loads persona bundles
                             (persona.py + expected.json), one test per pinned number
  data/
    brackets.py              federal + CA bracket tables 2026 (the MODEL_EPOCH)
    sample_grants.py         synthetic illustrative grant schedule
    social_security.py       SS estimator + COLA + federal taxable %
examples/user_data/          committed templates for the private layer
user_data/                   gitignored — the owner's private layer (may be absent)
tests/                       pytest — 280+ tests, fast, placeholder-deterministic
  personas/single_ca_rsu/    the placeholder persona's pinned numbers (data, not code)
  test_personas.py           runs the harness over tests/personas/
scripts/
  repin_personas.py          deliberate re-pin: recompute a bundle's expected.json
docs/
  DESIGN.md                  architecture and modeling choices
  SCENARIOS.md               how scenarios work + how to add/edit one
  ASSUMPTIONS_AND_RISKS.md   legal/tax assumptions worth revisiting yearly
  GOLDEN_CASES.md            externally-anchored 2026 tax cases: what they claim,
                             the source list, and why they must never be re-pinned
  specs/                     LIVING_MODEL.md (living-model sync design)
.claude/skills/              manage-scenarios, bootstrap-from-monarch, review-and-report
```

## Tool preferences (saves you time and permission prompts)

- **Use `Edit` / `MultiEdit` / `Write` instead of `sed` / `awk` / `perl -i`.** The shell-based mutation tools require a permission prompt every invocation and the user is often AFK. The harness-native edit tools don't. The same applies to `cat > file <<EOF` style writes — use `Write` instead.
- **Use `Read` instead of `cat` / `head` / `tail`** for file inspection — `Read` is allowlisted and shows line numbers needed for precise edits.
- **Use `Bash` for things that genuinely need a shell**: running tests, `uv` commands, `git` commands, `grep` / `find` for code search.
- If you find yourself reaching for `sed` to do a bulk replace, prefer `Edit` with `replace_all=true`, or a small number of targeted `Edit` calls.

## Things that will trip up an agent

- **Use `uv run pytest`** — `python -m pytest` will fail because the venv may not be activated.
- **Lint + tests on every iteration.** `uv run ruff check` and `uv run pytest` should both finish clean. CI-style discipline; takes <5s combined.
- **`Scenario` defaults are placeholders, and they're pinned — in data, not in test code.** Pins live in *persona bundles*: `tests/personas/single_ca_rsu/` (the placeholder trajectory) and `user_data/tests/personas/personal/` (the owner's). Each bundle is `persona.py` (inputs: `scenarios() -> {variant: Scenario}`, plus `REQUIRES_USER_DATA` when it builds on the private layer) + `expected.json` (values, keyed variant → check → year; `_`-prefixed keys are ignored metadata). One shared harness — `variometer/testing.py` — turns each pinned number into its own test via the three-line `tests/test_personas.py` / `user_data/tests/test_personas.py`. Don't mutate defaults to "fix" a failing pin, and don't hand-edit `expected.json` to make it green; add an event instead. A *genuine* model change is re-pinned deliberately with `uv run python scripts/repin_personas.py <persona_dir>` (recomputes only what's already pinned, prints old → new, forces the private layer off for public personas) — and both suites need re-pinning, with the reason recorded (public: the commit; private: the bundle's `HISTORY.md`).
- **Events are how scenarios deviate from baseline.** See `variometer/events.py` — `LifeEvent(year, month, kind, params)`. Composing scenarios = appending events. Don't add new fields to `Scenario` for something that's only active in some scenarios. One-time windfalls are `brokerage_deposit` events (net-of-tax cash by convention).
- **The model is anchored at `MODEL_EPOCH` (2026).** Bracket tables, `year - 2026` growth arithmetic, "2026 real dollars" — all pinned to it. It is not a per-user setting; updating it means new bracket tables + re-pinned tests.
- **The Streamlit app is one file.** `app.py` builds the tabs in order with per-tab state. The Runway tab renders scenario knobs by introspecting each factory's `Annotated[..., Knob(...)]` parameters — adding a scenario knob is a scenarios-file change only. See docs/SCENARIOS.md.
- **The sidebar is the base-defaults layer.** Sidebar widgets initialize from `base_scenario()` and produce a `core_overrides` dict overlaid onto every named scenario via `apply_core_overrides`. A factory's customizations win over sidebar values (the overlay only touches fields still at the `base_scenario()` default).
- **RSU vest math and payroll both treat the `employment_end` cutoff month as active.** A vest landing in that month counts, and that month's salary counts as a full worked month too — see the docstring in `equity.vest_schedule`.
- **`itemized_excess_over_standard`** is applied only when no `house_purchase` event is active. Housing math overrides it (with the SALT cap when `tcja_active`).
- **Drawdown waterfall is path-dependent.** Order matters: brokerage → Roth IRA principal → seasoned conversion cohorts → Roth IRA earnings → pretax 401k. Don't reorder lightly.
- **Brokerage uses a lower return rate** (`brokerage_return_rate`, default 6%) than tax-deferred (`return_rate`, default 7%) to reflect dividend tax + occasional realization drag.

## Conventions

- **Dollars are always nominal** (not real / inflation-adjusted) unless a function name says otherwise. "2026 real dollars" inputs are inflated forward from `MODEL_EPOCH`.
- **All years are calendar years.** Roth conversion 5-year seasoning uses `current_year - conversion_year >= 5`.
- **Pure functions** wherever possible. Modules import `Scenario` and compute; no module mutates a `Scenario`.
- **Use Edit (not Write)** for surgical changes — files are dense and small typos break the snapshot regression.
- **Facts are data files, behavior is Python** in `user_data/`: scalars in JSON, schedules in CSV, compositions as factories.

## Modeling scenarios — quickstart

When the user asks a "what if" question, use `variometer explain` as the output engine. Two paths:

**Ad-hoc exploration** (one-off, no factory needed): write a `.py` file in `ad_hoc/` (or `user_data/ad_hoc/` if it contains personal specifics) that defines a `scenario` variable and pass it via `--python`. This is the right default for anything that won't become a permanent UI scenario.

```python
# ad_hoc/my_what_if.py
from variometer.config import base_scenario   # NOT Scenario() — see two-layer notes
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

# Multiple scenarios in one file — use --var to pick which variable to run:
uv run variometer explain --python ad_hoc/my_what_if.py --var scenario_b
```

`ad_hoc/example_promo_house_fire.py` is the worked example (promotion + house + FIRE + Roth ladder) — copy it as a starting point for complex compositions.

**Named factory** (permanent, shows up in the Streamlit UI): public archetypes go in `variometer/scenarios.py` with `@scenario("Display name")` and `Annotated[type, Knob(...)]` parameters; the user's personal compositions go in `user_data/scenarios.py` with the same decorator. See `.claude/skills/manage-scenarios/SKILL.md` for the full recipe, including which file a new scenario belongs in.

**Do not write custom projection scripts** — `print_scenario()` and `print_monte_carlo()` in `variometer/cli.py` already produce the right output. Import and call them directly if you need to drive them from Python rather than the CLI.

## After making changes

1. `uv run ruff check` — must report "All checks passed!" (0 errors).
2. `uv run ruff format` — apply formatting (no-op if already clean).
3. `uv run pytest` — must pass.
4. If `user_data/` exists: `uv run pytest user_data/tests` — the owner's pinned numbers must also pass.
5. If you touched scenarios or projection: `uv run variometer explain <changed_scenario>` to eyeball the year-by-year output.
6. If you touched the UI: `uv run streamlit run variometer/app.py` and click through the affected tab.

## Where to read next

- **docs/DESIGN.md** — architecture, modeling choices, what's deliberately simplified.
- **docs/SCENARIOS.md** — how scenarios work, how to add/edit one, the Knob schema.
- **docs/ASSUMPTIONS_AND_RISKS.md** — legal/tax assumptions to re-check yearly.
- **docs/ROADMAP.md** — the public future sketch (one line per item) plus what's deliberately out of scope.
- **docs/specs/** — design docs (living-model sync).
- **`.claude/skills/manage-scenarios/SKILL.md`** — step-by-step recipe for adding or modifying a scenario.
