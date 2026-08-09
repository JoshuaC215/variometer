# Design

## Philosophy

- Pragmatic over perfect. A personal tool for one user at a time — generalized just enough to share.
- Pure functions where reasonable. Easier to test, easier to wire into Streamlit.
- One dataclass (`Scenario`) carries all inputs. Functions take it (or a subset) and return result dataclasses.
- No persistence layer, no database, no logging framework. Static model data is `.py` in `data/`; personal data is JSON/CSV/Python in the gitignored `user_data/`.
- **Two layers:** the public model ships with illustrative placeholder defaults; each user's truth lives in `user_data/` and is applied through one seam — `config.base_scenario()`. See CLAUDE.md → "The two-layer architecture".
- Static bracket / IRS-limit data is hardcoded in `data/` and pinned to `MODEL_EPOCH` (2026). Updating them is a code change.

## Module responsibilities

### `config.py`
The `Scenario` dataclass — every input lives here with an **illustrative placeholder default** (a fictional senior IC at a public tech company). Includes `events: list[LifeEvent]`, `vest_grants` (explicit grant schedules), the refresh-projection knobs (`refresh_issue_month`, `refresh_projection_start_year`), `brokerage_cost_basis` (auto-derived from `brokerage_cost_basis_fraction` in `__post_init__`), `bonus_month`, `healthcare_monthly`, `rent_monthly`, `tcja_active` (SALT cap toggle), and `summary: str` (set by scenario factories). Also `MODEL_EPOCH`, `PROJECTION_END_YEAR`, and **`base_scenario()`** — `Scenario()` + the `user_data/profile.json` overlay + `user_data/grants.csv`. No other logic.

### `user_data_io.py`
The only module that knows where the private layer lives and how to parse it: `load_profile()` (JSON → validated/coerced Scenario field overrides; unknown keys and wrong types are hard errors), `load_vest_grants()` (`grants.csv` → `{grant_id: {(y,m): shares}}`), `load_historical()` (`historical.csv` → monthly actual rows), `load_user_scenarios()` (imports `user_data/scenarios.py` so its `@scenario` factories register). `VARIOMETER_USER_DATA` env var overrides the directory; `""` disables the layer entirely (used by the public test suite).

### `events.py`
`LifeEvent` frozen dataclass + helpers (`events_of_kind`, `first_event_of_kind`, `active_events_for_year`, `most_recent_event_for_year`). Event kinds: `employment_end`, `new_job`, `pay_change`, `equity_grant`, `house_purchase`, `relocation`, `expense_change`, `expense_level`, `one_time_expense`, `child`, `roth_conversion`, `ss_start`, `brokerage_deposit`. Each kind owns its own focused `params` dict — see the docstring at the top of `events.py` for the exact param shape per kind.

### `data/brackets.py`
Federal and CA bracket tables for 2026 (single filer). `brackets_for_year(year)` inflates them forward at a configurable rate. `federal_ltcg_brackets(year, inflation)` returns the `(zero_top, fifteen_top)` thresholds.

### `data/sample_grants.py`
The synthetic illustrative grant schedule behind the placeholder `Scenario.vest_grants` default. Real users supply `user_data/grants.csv`, which replaces it entirely.

### `data/social_security.py`
Rough PIA-style SS benefit estimator + COLA escalator + federal taxable percentage (85% of benefit, simplified).

### `tax.py`
Pure functions:
- `progressive_tax(income, brackets)` — bracket-stepping primitive
- `federal_income_tax(taxable, year)`, `ca_income_tax(taxable, year)`
- `fica(wages, year)` — SS / Medicare / addl Medicare / SDI breakdown
- `niit(ltcg, agi)` — 3.8% on investment income over $200k AGI
- `ltcg_federal_tax(ltcg, ordinary_taxable_income, year, inflation)` — bracketed 0/15/20% federal LTCG; LTCG stacks on top of ordinary income for bracket placement (used by `annual_summary` and the drawdown waterfall)
- `aca_premium_tax_credit(agi, year, household_size, monthly_benchmark_premium)` — ACA §36B sliding scale (original pre-enhancement rules; see ASSUMPTIONS_AND_RISKS.md)

### `retirement.py`
`compute_contributions(scenario, year)` returns a `Contributions` dataclass and enforces IRS limits in this order: employee 401(k) limit (pre-tax + Roth combined) → employer match → after-tax (mega backdoor) fills the remaining annual addition limit.

### `equity.py`
Vest schedule generator:
- `vest_schedule(scenario)` returns `{(year, month): total_shares}` combining `scenario.vest_grants` (explicit schedules) and projected refreshes
- Honors `employment_end`: vests after the cutoff month are dropped; refresh grants not yet issued (issued in `refresh_issue_month` of the grant year) are skipped. The end month counts as active on both sides — vesting uses `key <= cutoff` and payroll (`cashflow._employer_months_in_year`) also counts the end month as a full worked month.
- `project_flat_refresh(grant_year, total_value, price_at_grant, issue_month)` generates a 48-consecutive-month flat vest; employers with a different projected-refresh cadence are covered by the explicit `grants.csv`, which always wins for the years it covers
- `_effective_grant_value` returns the per-grant target for a given year — a linear model anchored at `equity_refresh_grant_value` in `equity_refresh_base_year` and grown at `equity_refresh_growth`. Independent of prior grants. `pay_change` events with an `equity_refresh_grant_value` param re-anchor the target (e.g. after a promo)
- `computed_refresh_values(scenario)` exposes the per-grant dollar value per year for the UI
- `equity_grant` events layer supplemental one-time grants (e.g. a promo bonus grant) on top of the normal refresh — same 48-month flat cadence, starting at the event's own (year, month)

### `cashflow.py`
Ties income, contributions, and tax together:
- `annual_summary(scenario, year)` — full annual ladder: gross → AGI → fed/CA tax → FICA → take-home. Event-aware: `employment_end` and `new_job` blend old-employer + new-job income/match/mega-backdoor across the transition year; `roth_conversion` adds to AGI / ordinary taxable / CA taxable in the conversion year; `ss_start` adds taxable SS (85% federal, CA-exempt).
- `annual_expenses(scenario, year)` — event-aware total annual expenses. Applies `expense_level` / `expense_change` deltas, `relocation` rent overrides, and the `house_purchase` rent-for-mortgage swap.
- `monthly_forecast(scenario, start, end)` — month-by-month rows with paydays-counted base, full-month bonus in `scenario.bonus_month`, RSU income at vest, annual ETR applied monthly for tax estimation, and backdoor Roth IRA contributions deducted from net cash flow.
- `aca_monthly_subsidy_for_year(scenario, year, agi)` — computes the PTC for any `expense_change` event marked `params={"aca": True}` (the label string is display-only and never matched).

### `housing.py`
Pure-function mortgage model:
- `MortgageTerms` frozen dataclass with `loan_amount`, `down_payment`, `monthly_p_and_i`, `annual_property_tax` properties
- `amortize(terms)` — full month-by-month schedule
- `annual_interest_for_year` — calendar-year interest slice with purchase-month proration
- `home_equity_at_eoy(terms, year)` — down payment + cumulative principal paid (0 pre-purchase). Includes down payment — callers must not double-add it.
- `annual_housing_cost(terms, year)` — P&I + property tax + insurance + HOA, prorated for the purchase year
- `itemized_excess_for_year(scenario, year, standard_deduction, salt_paid)` — applies the $10k SALT cap when `tcja_active=True` (default); uncapped otherwise. With no `house_purchase` event, falls through to `scenario.itemized_excess_over_standard` (preserves baseline).

### `scenarios.py`
The **public archetype library** + `SCENARIO_REGISTRY` (name → factory). Six bare career-shape archetypes: `stay_put`, `internal_promotion`, `change_jobs`, `sabbatical`, `fire_roth_ladder`, `buy_home`. Each factory sets `s.summary` with a plain-English description with key params injected. Factories build on `base_scenario()` and append events — they never mutate declarative defaults; behavior changes flow exclusively through events. Knob metadata lives inline on each factory parameter via `Annotated[T, Knob(...)]` and is consumed by the Streamlit app to render parameter forms generically. **Personal tuned compositions belong in `user_data/scenarios.py`** — same decorator, auto-loaded at startup. See `SCENARIOS.md`.

### `scenario_registry.py`
The framework. `@scenario("Display name")` decorator + `SCENARIO_REGISTRY` + `Knob` / `BoundKnob` introspection (`knobs_for`, `knobs_for_scenario`). Also defines **`apply_core_overrides(scenario, overrides)`** — the sidebar-as-base-defaults overlay. For each (key, value) in `overrides`, the helper overwrites that field on `scenario` *only if* the field still equals the `base_scenario()` default. Scenario-specific customizations therefore win over sidebar values. The default snapshot is taken fresh per call so Streamlit hot-reload doesn't see a stale comparison. `brokerage_cost_basis` is re-derived after the overlay if the sidebar touched `brokerage_balance` or its basis fraction.

### `withdrawals.py`
Drawdown waterfall, fired when annual net surplus is negative:
- `WithdrawalState` — mutable per-year snapshot of brokerage (balance + basis), Roth IRA (balance + principal), pretax 401(k), and `{conversion_year: balance}` cohort dict (the Roth 401(k) is not a bucket — it auto-rolls into Roth IRA principal on `employment_end` before the waterfall runs)
- `WithdrawalLeg` / `YearWithdrawal` — structured result with per-bucket pulls, realized LTCG, ordinary income added, penalty, iteration count, convergence flag
- `is_cohort_seasoned(cohort_year, current_year)` — `current_year - cohort_year >= 5`
- `waterfall_withdrawal(...)` — ordered depletion across 5 buckets (see "Drawdown / runway — modeling choices" below). Mutates `state` in place. `YearWithdrawal.unfunded_shortfall` records the cash gap remaining after all buckets are exhausted.

### `projection.py`
Multi-account NW projection:
- `project_net_worth(scenario, years, return_path=None)` — year-by-year NW across pre-tax 401k, Roth 401k, Roth IRA (split principal + growth), rollover IRA, brokerage (with basis tracking), home equity, and Roth conversion cohorts. Event-aware: down-payment depletion with pro-rata basis; `roth_conversion` transfers from pretax 401k to a cohort that grows with `return_rate`; `brokerage_deposit` events land as 100%-basis cash (windfalls are net-of-tax by convention); negative net surplus invokes `waterfall_withdrawal` and records the full `YearWithdrawal` on `NetWorthYear.withdrawal`. `NetWorthYear` carries reporting fields including `take_home_income`, `total_expenses`, `aca_subsidy_monthly`, and `unfunded_shortfall`.
- `stress_return_path` — factory for deterministic return paths (`flat`, `bear`, `slow_growth`)
- `monte_carlo_nw` — N log-normal simulations → P10/P25/P50/P75/P90 NW bands

## Key modeling choices

- **RSU refreshes vest flat over 48 months** (1/48 each month). Explicit grants keep whatever schedule `vest_grants` says (front-loads, cliffs — it's just data).
- **Refresh sizing is linear.** Each refresh grant is valued at `equity_refresh_grant_value` grown at `equity_refresh_growth` from `equity_refresh_base_year` — independent of how much existing equity is vesting. Rationale: forecasts should reflect a steady target, not a band-targeted top-up that over-couples to past stock moves. Manual overrides available via `refresh_overrides`. Promo / re-leveling effects flow through `pay_change` events (re-anchor the target) and `equity_grant` events (supplemental one-time grants).
- **Projected refreshes vest straight through.** No per-employer cadence quirks are modeled for projected grants; employers with quarterly or otherwise exotic cadences supply the explicit `grants.csv` — it always wins for the years it covers.
- **Tax math uses annual computation** with effective-rate proration to monthly. Real IRS practice (supplemental withholding on bonus/RSU, true-up at filing) doesn't change annual totals.
- **Brokerage uses a lower after-tax return rate** (`brokerage_return_rate`, default 6%) than tax-deferred accounts (`return_rate`, default 7%). The ~1% drag reflects dividend tax + incidental rebalancing LTCG on a mostly-index portfolio.
- **Brokerage accumulation** = prior balance × (1 + brokerage_return_rate/12) + monthly net surplus. Net surplus = net_to_bank − expenses − backdoor_roth_contrib. RSU proceeds net of tax are implicitly included via the ETR applied to gross income.
- **Backdoor Roth IRA** (`roth_ira_annual`, grows with `limit_inflation`). Treated as after-tax dollars leaving brokerage and entering a separate Roth IRA bucket. No tax effect on contribution; non-deductible basis = contribution. Capped at the year's W-2 earned income (IRA contributions require compensation), so no-income years — FIRE, gap years — stop contributing automatically. Keep `ira_balance` at 0 (rolled into a 401(k)) to stay clear of the pro-rata rule.
- **One-time windfalls** (a prior-employer liquidity event, inheritance, asset sale) are `brokerage_deposit` events — net-of-tax cash by convention, landing as 100%-basis brokerage money.
- **SS cap is annual.** Monthly take-home doesn't model the "January smaller checks, then bump" pattern explicitly — smoothed via annual ETR.
- **CA SDI uncapped** as of 2024, at the 2026 rate of 1.3% (CA EDD).
- **LTCG** uses the bracketed 0/15/20% federal function everywhere — the baseline `scenario.ltcg_annual` signal and drawdown-driven LTCG alike stack on ordinary income and exploit the 0% bracket automatically in lean years.
- **AMT not modeled** — doesn't bite W-2 RSU income at this scale.

## Housing — modeling choices

- **Mortgage amortization** uses the standard formula `P * r / (1 - (1+r)^-n)`, full month-by-month schedule.
- **Down payment** is deducted from brokerage in the purchase year as a one-shot before compounding. Basis depletes pro-rata so subsequent withdrawals see the correct ratio. LTCG tax on the realized portion is routed through `net_surplus` so lean-year + house composites fund it correctly.
- **Rent-for-mortgage swap.** `Scenario.rent_monthly` is the rent portion of `expense_base`. When a `house_purchase` event with `replaces_monthly_rent=True` is active, that rent is subtracted from `expense_base` for the months under mortgage and replaced by `annual_housing_cost` (P&I + property tax + insurance + HOA + maintenance). `expense_base` itself stays as the canonical total burn.
- **Maintenance.** `MortgageTerms.maintenance_pct` (default 0 raw, 1%/yr in the factory — the standard rule of thumb covering routine repairs and amortized big-ticket items like roof / HVAC) flows into `annual_housing_cost` prorated for the purchase year. Post-mortgage-payoff years still include maintenance + tax + insurance + HOA (only P&I drops to zero).
- **Itemized vs standard switch.** Federal deduction = `standard + max(0, mortgage_interest + min(salt_paid, 10000) - standard)` when `tcja_active`. SALT cap lifts when `tcja_active=False`. At high CA W-2 income the cap is saturated by state income tax alone, so property tax adds zero marginal deduction with TCJA on — pinned by `test_itemized_excess_property_tax_irrelevant_at_high_income`.
- **CA always uses standard deduction.** Two-pass tax computation: compute CA tax with standard, then use that CA tax as part of SALT in the federal itemized calculation. Avoids a circular dependency without iterating.
- **Home equity** = down payment + cumulative principal paid + appreciation gain. Appreciation is `price × ((1 + appreciation_rate) ^ years_owned − 1)`, compounded annually from the purchase year. `appreciation_rate` is a per-event param (3%/yr in the `buy_home` factory; the raw `MortgageTerms` dataclass stays neutral at 0%). Appreciation is **paper wealth** — it lifts NW totals but produces no cashflow. The drawdown waterfall ignores home equity entirely. Set the knob to 0 to stress-test a flat-price decade.

## Drawdown / runway — modeling choices

- **Auto-triggers.** Whenever `project_net_worth` computes negative net surplus, `waterfall_withdrawal` is invoked. No explicit "drawdown mode" event.
- **Waterfall order (5 buckets):** brokerage (pro-rata basis, LTCG via bracketed function) → Roth IRA contribution principal (tax-free, penalty-free) → seasoned conversion cohorts FIFO oldest-first (tax-free, penalty-free) → Roth IRA earnings/growth (non-qualified: ordinary + 10% penalty if age < 60; qualified + tax-free at 60+) → pre-tax 401(k) early withdrawal (ordinary + 10% penalty if age < 60). Brokerage-first is optimal in lean years (0% LTCG bracket); sub-optimal in high-income drawdown years — the spec accepts this trade-off because lean years are the primary use case.
- **Fixed-point iteration for brokerage tax self-financing.** The brokerage withdrawal must cover both the cash shortfall and the LTCG tax that the withdrawal itself creates. Up to 8 iterations; converges in 3–5 for all tested scenarios. Failure sets `WithdrawalResult.converged = False`.
- **0% federal LTCG bracket** is exploited automatically because `ltcg_federal_tax` stacks LTCG on top of ordinary income for bracket placement. In a no-W-2 year with ~$0 ordinary taxable, the first ~$55k (2031, inflated from $50k 2026) of realized LTCG lands at 0% federal. CA still taxes at its ordinary brackets.
- **Brokerage cost basis tracking.** Positive surplus adds to both balance and basis (new savings are 100% basis). Returns compound the balance only. Withdrawals deplete basis pro-rata.
- **Roth IRA principal vs growth split.** Principal = cumulative backdoor contributions + Roth 401(k) rollover at separation. Growth = total − principal. Before age 60: non-qualified distribution on growth (ordinary + 10% penalty); at 60+: qualified, tax-free.
- **Roth 401(k) rollover on `employment_end`.** Entire Roth 401(k) balance auto-rolls into Roth IRA principal.
- **Roth conversion cohorts.** Each `roth_conversion` event (or year of a recurring event) creates a cohort `{conversion_year: amount}`. Cashflow adds the amount to ordinary income; projection moves it from pretax 401k to the cohort. Each cohort grows at `return_rate` each year. Seasoned when `current_year - conversion_year >= 5`. FIFO order.
- **401(k) early withdrawal gross-up.** Last-resort bucket. Uses combined federal+CA marginal rate at current ordinary income to gross up the pull so the net (after tax + 10% penalty) covers the residual shortfall. Single-pass estimate.
- **ACA premium tax credit.** Computed post-waterfall using effective AGI = ordinary income + realized LTCG (scenario + waterfall). This ensures lean-year drawdown LTCG is included in the AGI for the subsidy calc when otherwise near-zero. The subsidy reduces effective monthly expenses (credited as brokerage inflow with 100% basis).
- **What's NOT modeled:** 72(t) / SEPP (out of scope — conflicts with "willing to return to work"); auto-fill conversions to the 12% bracket (always user-driven via events).

## Market path & Monte Carlo

- **`stress_return_path(shape, years, base_rate, crash_year=None)`** returns a `{year: rate}` dict. Shapes: `flat`, `bear` (−35%, +15%, +10%, base after), `slow_growth` (4% forever).
- **`monte_carlo_nw(scenario, years, n, sigma, seed)`** runs N log-normal simulations, returns P10/P25/P50/P75/P90 NW bands. Returns are clipped at −50% to bound extreme tail draws. Brokerage maintains its historical drag (~1pp) vs. the tax-deferred rate.
- **Sequence-of-returns risk** is visible because `bear` accepts a `crash_year` slider — the same crash at different years produces visibly different long-run NW depending on which life events it lands near.

## Regression invariants

Pinned numbers live in **persona bundles**, not in test code. A bundle is a directory with `persona.py` (the inputs — `scenarios() -> {variant: Scenario}`) and `expected.json` (the pinned values, keyed variant → check → year). The shared harness is `variometer/testing.py`; each suite has a three-line `test_personas.py` that parametrizes over it, producing one test per pinned number with ids like `single_ca_rsu/baseline/net_worth/2028`.

- `tests/personas/single_ca_rsu/` — the placeholder persona (bare `Scenario()`, no events): `net_worth` total_nw 2026–2035 and `annual_summary` gross_income / total_tax / take_home 2026–2030.
- `user_data/tests/personas/personal/` — with a private layer present, the owner's `base_scenario()` trajectory plus spot pins on the most-used personal factory. Its re-pin history is in the bundle's `HISTORY.md`.
- `tests/personas/golden_2026_w2/`, `tests/personas/golden_2026_investment/` — **golden cases**, a different kind of pin (see below).
- Any `_`-prefixed key in `expected.json` is ignored metadata, so a pin can carry its provenance or citation next to the number.
- A field name may be a dotted path (`fica.social_security`, `contributions.pretax`) to pin one leg of a nested breakdown without a bespoke check kind.

A legitimate model change requires re-pinning **both** deliberately: `uv run python scripts/repin_personas.py <persona_dir>` recomputes only what the file already pins and prints an old → new summary, so the review is a data diff.

### Golden cases vs. regression pins

The pins above prove *self-consistency* — the model still produces what it produced yesterday. **Golden cases** prove *correctness*: their expected values were hand-computed from cited primary sources (IRS Rev. Proc. 2025-32, Notice 2025-67, Rev. Proc. 2025-25, SSA, CA EDD, HHS, and statute) before the model was ever run against them. They live in `tests/test_golden_2026.py` (function-level anchors) and the two `tests/personas/golden_2026_*` bundles (end-to-end through `annual_summary`), pin 2026 only, and deliberately exclude every CA-bracket-derived quantity because those tables are still projected.

**A golden case that moves is a finding, not a re-pin** — `repin_personas.py` must always report 0 moved for those bundles. Full claims, non-claims and the source list: [GOLDEN_CASES.md](GOLDEN_CASES.md).

## Out of scope

- **Social Security expansion / replacement income beyond §36B-style estimates** (no detailed AIME calc)
- **House sale event** (no `house_sale` event kind yet — appreciation is modeled but never realized as cash)
- **HSA, ISOs, ESPP, AMT, ESG-screened return assumptions**
- **Charitable / DAF strategies**
- **Multi-state mid-year tax, spouse income / MFJ, 72(t) / SEPP** — explicitly out of scope (see the v1 scope in README.md)

## Streamlit app

Run with `uv run streamlit run variometer/app.py`. Tabs:

- **🏃 Runway** — the primary view. Scenario picker with parameter expander (rendered from each factory's `Knob`-annotated signature). Optional **Compare against** selector overlays a second scenario's Total NW on the liquid-balances chart and shows delta metrics. Sections: scenario summary banner, optional Social Security inputs, retirement income cutoff, summary metrics (first insolvency, drawdown years, total penalties), insolvency / drawdown banner, scenario timeline, year-by-year drawdown table, stacked liquid-assets chart with home-equity overlay + cumulative unfunded line, per-year bucket breakdown, **Market risk** expander (deterministic stress paths or Monte Carlo fan), Retirement Sustainability simulation.
- **💎 Vest Schedule** — grant-by-grant vesting + modeled refresh values; monthly and annual stacked bars. Reads the sidebar scenario.
- **📜 Historical** — model-vs-actuals reference; only rendered when `user_data/historical.csv` exists.
- **📅 Monthly Forecast** — debugging view of the sidebar scenario's monthly cashflow (paydays, gross income by source, net to bank, brokerage trajectory).

**The sidebar is the base-defaults layer.** Every sidebar widget maps 1:1 to a `Scenario` dataclass field and initializes from `base_scenario()` (i.e. the user's profile when present). They're collected into a `core_overrides` dict and overlaid onto whichever named scenario the user picks via `apply_core_overrides` (see `scenario_registry.py`). A factory that intentionally sets a field wins over the sidebar — the overlay only fills fields still at the `base_scenario()` default.

**First-run banner.** Without `user_data/profile.json` the app shows a banner explaining that everything on screen is the placeholder persona and pointing at `examples/user_data/`.

**`variometer explain`** (`variometer/cli.py`) — CLI scenario explainer. Prints summary, year-by-year table, key milestones. Registry-driven: resolves scenario names against factory names, display names, or slugs (personal factories included). Run: `uv run variometer explain [scenario] [--flags]`. Use `--mc` for a Monte Carlo percentile table and `--ss-monthly $$$` for a retirement sustainability table.
