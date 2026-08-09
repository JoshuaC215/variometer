# Variometer 🛬

Multi-year cash-flow and net-worth modeling for tech workers with equity comp.
Answer "what if" questions about your own money with real tax math instead of
a 4%-rule spreadsheet:

- *When can I stop working, and how safe is that date?*
- *What does a sabbatical / lower-paying pivot / early retirement actually cost?*
- *Buy or keep renting — and what does the SALT-capped itemization switch do to it?*
- *How much does a Roth conversion ladder buy me in a lean-income window?*

**What it models:** W-2 salary + bonus + RSU vesting (explicit grant schedules
plus projected annual refreshes), federal + California tax (brackets, FICA,
NIIT, LTCG with the 0% bracket, ACA premium tax credit), 401(k) with employer
match and mega-backdoor Roth, backdoor Roth IRA, mortgage amortization with
the SALT-cap itemization switch, a 5-bucket drawdown waterfall with Roth
conversion seasoning, Social Security, and Monte Carlo / deterministic stress
paths over a 35-year horizon.

> ⚠️ **Not financial or tax advice.** This is a single-maintainer planning
> tool, released as-is under the MIT license. Sanity-check any real decision
> with a professional.

**Live demo:** [variometer.streamlit.app](https://variometer.streamlit.app/) —
the UI on the placeholder persona, nothing to install.

## Run it

**Nothing to install.** With [uv](https://docs.astral.sh/uv/), `uvx` fetches
Variometer into a throwaway environment and runs it:

```bash
uvx variometer                  # configuration status + command reference
uvx variometer ui               # launch the Streamlit app on :8501
uvx variometer explain --list   # every scenario you can name
uvx variometer explain sabbatical --start-year 2030 --return-year 2033
```

Add `@latest` (`uvx variometer@latest ui`) to skip uv's cache and pick up a new
release.

**Keep it around.** `uv tool install` puts a persistent `variometer` on your
PATH, so you can drop the `uvx` prefix:

```bash
uv tool install variometer      # then: variometer ui, variometer explain --list
uv tool upgrade variometer
```

`pip install variometer` into a virtualenv works too, if that's your setup.

Either way, point `VARIOMETER_USER_DATA` at your own data directory — see
[Make it yours](#make-it-yours). It's not optional under `uvx`, which runs from
a cache directory with no repo alongside it.

**From a checkout**, for development or to work on the model itself:

```bash
uv sync                                     # install deps
uv run streamlit run variometer/app.py      # the UI on :8501
uv run variometer explain --list            # the CLI
uv run pytest                               # 460+ tests, <5s
```

Fresh out of the box you'll see **illustrative placeholder numbers** — a
fictional senior IC at a public tech company. The app tells you so with a
banner until you add your own data.

## Built to be driven by an agent

You can ask a coding agent a what-if question in English and get real tax math
back, without touching the model yourself. Four things make that work:

- **Deterministic, diffable text output.** `variometer explain` renders the
  same projection engine as the app as a plain table. Same inputs, same bytes —
  so an agent can run three variants and diff them instead of clicking through
  GUI tabs and describing screenshots.
- **`--python` ad-hoc scenarios.** A one-off question doesn't need a factory,
  a registration, or a UI change. It needs a `.py` file that assigns a
  `Scenario`, which an agent can write in one shot:
  `variometer explain --python ad_hoc/my_what_if.py`.
- **[CLAUDE.md](https://github.com/JoshuaC215/variometer/blob/main/CLAUDE.md)** — a full onboarding brief in the repo: the
  architecture, the conventions, the things that will trip an agent up. Agents
  read it automatically.
- **`.claude/skills/`** — packaged recipes for the recurring jobs (add a
  scenario, bootstrap a baseline from Monarch, run a monthly
  model-vs-actuals review).

**[docs/DEMO.md](https://github.com/JoshuaC215/variometer/blob/main/docs/DEMO.md) is a real transcript of the loop end to end** —
an English question, the file the agent writes, the actual output, and the
answer the agent gives back.

## Make it yours

All personal data lives in a gitignored `user_data/` directory — plain JSON,
CSV, and Python files designed to be easy for humans, agents, and scripts:

```
user_data/
  profile.json      your real baseline (balances, salary, price, elections…)
  grants.csv        your actual RSU vest schedule: grant_id,year,month,shares
  scenarios.py      your own scenario factories (auto-registered in the UI)
  historical.csv    optional monthly actuals — enables the Historical tab
```

1. Copy `examples/user_data/` to `user_data/` and edit `profile.json` — every
   key is a `Scenario` field (see `variometer/config.py` for the full list with
   comments). Unknown keys and wrong types are hard errors, so typos can't
   silently skew a projection.
2. Export your grant schedule from your equity portal into `grants.csv`.
3. Relaunch. The sidebar, every scenario, and the CLI now start from *your*
   baseline.

Running from `uvx` or `uv tool` rather than a checkout, there's no repo to put
`user_data/` next to: keep the directory wherever you like (the templates are
in [examples/user_data/](https://github.com/JoshuaC215/variometer/tree/main/examples/user_data))
and point `VARIOMETER_USER_DATA` at it.

```bash
export VARIOMETER_USER_DATA=~/finance/variometer_data
uvx variometer                  # confirms the private layer is on
```

Setting it to the empty string disables the private layer entirely and runs the
placeholder persona.

If you use [Monarch Money](https://www.monarchmoney.com/) with Claude Code,
the `bootstrap-from-monarch` skill can pre-fill most of `profile.json` from
your linked accounts, and `review-and-report` runs a monthly
model-vs-actuals reconciliation.

## Scenarios

Six career-shape archetypes ship in the box: **Stay put**, **Internal
promotion**, **Change jobs**, **Sabbatical / career gap**, **FIRE with Roth
ladder**, and **Buy home**. Each is a tiny factory whose knobs render
automatically in the UI.

Your own tuned compositions (specific exit years, prices, comp shapes) go in
`user_data/scenarios.py` with the same `@scenario` decorator — they load at
startup and appear alongside the built-ins. One-off explorations don't need a
factory at all: drop a file in `ad_hoc/` (or `user_data/ad_hoc/` for personal
specifics) and run
`variometer explain --python ad_hoc/my_what_if.py`.
See [docs/SCENARIOS.md](https://github.com/JoshuaC215/variometer/blob/main/docs/SCENARIOS.md).

## Using the explainer

`variometer explain` renders the projection as plain text, no browser.

```bash
variometer explain --list                                   # all scenarios
variometer explain sabbatical --start-year 2030 --return-year 2033
variometer explain fire_roth_ladder --lean-monthly 6500 --mc
variometer explain buy_home --retire-year 2056
variometer explain --python ad_hoc/my_what_if.py --mc --ss-monthly 3500
```

The positional arg resolves against `SCENARIO_REGISTRY` (factory name, display
name, or slug). Flags like `--start-year`, `--lean-monthly`, `--new-base`,
`--mega-backdoor` map onto whichever factory params match; unsupported flags
are ignored, so one flag set works across scenarios. Use `--var NAME` if a
`--python` file defines more than one scenario;
`ad_hoc/example_promo_house_fire.py` is a worked composition.

**Output:** year-by-year table (income/expenses/balances/drawdown) through
2045, key milestones (NW at 5-year marks, waterfall start, first insolvency);
`--mc` adds Monte Carlo percentile bands; `--ss-monthly` adds a retirement
sustainability table across ages 60–70 (`--claim-age` to tune); `--retire-year`
cuts off employment income at a given year. Full flags:
`variometer explain --help`.

## App layout

- **🏃 Runway** — primary view. Scenario picker + knobs, event timeline,
  year-by-year drawdown table, liquid-balances chart with compare-against
  overlay, market-risk stress paths + Monte Carlo fan, retirement
  sustainability.
- **💎 Vest Schedule** — grant-by-grant vesting, monthly/annual stacked bars.
- **📜 Historical** — model-vs-actuals reference (only if you supply
  `user_data/historical.csv`).
- **📅 Monthly Forecast** — month-by-month cashflow debugging view.

The sidebar is the base-defaults layer: its values initialize from your
profile and overlay onto whichever scenario you pick, without clobbering
anything the scenario itself sets deliberately.

## Correctness

Regression pins keep the model self-consistent, and the 2026 federal tax
quantities are additionally hand-anchored to cited primary sources (IRS, SSA,
EDD, HHS) — see [docs/GOLDEN_CASES.md](https://github.com/JoshuaC215/variometer/blob/main/docs/GOLDEN_CASES.md) for exactly what
that does and doesn't claim.

## Scope today

Single filer · California resident · no dependents · sell-at-vest · tax tables
pinned to 2026. Filing status and state are enforced with hard errors rather
than silently mismodeled; the app banners the assumptions and both the app and
CLI warn when the pinned tables go stale. Widening the envelope (married
filing jointly, more states, more income shapes) is where the roadmap goes
next. Also not modeled yet: ISOs/ESPP/AMT, HSA, rental property, holding RSUs
past vest. Details in
[docs/ASSUMPTIONS_AND_RISKS.md](https://github.com/JoshuaC215/variometer/blob/main/docs/ASSUMPTIONS_AND_RISKS.md).

## Support

Best-effort, single maintainer, no SLA. Issues and PRs are welcome and will be
read; a response — let alone a fix — is not guaranteed. The tax tables get an
annual refresh as the authorities publish (the routine is described in
[CONTRIBUTING.md](https://github.com/JoshuaC215/variometer/blob/main/CONTRIBUTING.md)); everything else happens when it happens.

This is a planning tool, not a tax engine and not an advisor. Use it to
compare shapes of decisions, not to compute a number you'll file or wire money
against, and sanity-check any real decision with a professional.

## Learn more

Design, scenario authoring, and the yearly assumptions checklist live in
[docs/](https://github.com/JoshuaC215/variometer/tree/main/docs); [CLAUDE.md](https://github.com/JoshuaC215/variometer/blob/main/CLAUDE.md) onboards agents.
[CONTRIBUTING.md](https://github.com/JoshuaC215/variometer/blob/main/CONTRIBUTING.md) covers dev setup, how the pins work, and
what a reviewable PR looks like.

MIT © Joshua Carroll
