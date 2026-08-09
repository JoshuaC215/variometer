---
name: bootstrap-from-monarch
description: Pre-fill a new user's baseline Scenario from their connected Monarch Money account. Use when a new user wants to stand up the model on their own finances and has the Monarch MCP connected ("set up my baseline from Monarch", "import my accounts", "bootstrap the model from my data"). Covers what Monarch can populate automatically, what it can only approximate, and the manual-input checklist for everything Monarch cannot see.
---

# Bootstrap a baseline from Monarch

This skill turns a connected Monarch account into a populated private layer — **`user_data/profile.json`** (scalar Scenario fields; see `variometer/config.py` for the annotated list) plus **`user_data/grants.csv`** and optionally **`user_data/historical.csv`**. Copy `examples/user_data/` to `user_data/` first if it doesn't exist. It assumes the user has connected **full** data to Monarch — all bank, brokerage, retirement, and loan accounts.

**Core principle:** Monarch is a *balance-sheet and cash-flow* source. It reliably knows what you **own, owe, and spent**. It does **not** know your **gross compensation, payroll deductions, equity vesting schedule, tax lots, or any forward-looking assumption** — those are the model's predictive core and must be entered manually. Treat Monarch as a way to eliminate the tedious data-gathering, not as a way to skip the comp/equity/assumptions conversation.

Work in three passes: (1) auto-derive, (2) infer-and-confirm, (3) manual checklist.

---

## Pass 1 — Auto-derive (trust these, confirm the totals)

Call these read tools and map the results straight onto profile.json keys (each key is a `Scenario` field; the loader hard-errors on unknown keys or wrong types, so write exactly the field names).

### `GetAccounts` → portfolio starting balances
Map each account by **type/subtype**, not name (names are user-specific):

| Account type/subtype | Scenario field |
|---|---|
| `Brokerage (Taxable)` + `Cash Management` balances (sum) | `brokerage_balance` |
| `401k` (pre-tax portion) | `pretax_401k_balance` |
| `Individual Retirement Account (IRA)` (traditional/rollover) | `ira_balance` |
| Roth IRA / Roth 401k subtypes, if present | the corresponding Roth starting balance |

Notes:
- Sum **all** taxable brokerage + cash-management/settlement accounts into `brokerage_balance` — Monarch often splits a single brokerage into a securities account and a sweep/cash account.
- A 401k balance from Monarch is the **total** plan balance. The model wants the **pre-tax** sub-balance. If the plan mixes pre-tax / Roth / after-tax, Monarch usually can't split it — flag for manual confirmation (see Pass 3).
- Checking/savings/credit-card balances are **not** model inputs (the model has no operating-cash bucket); ignore them except as a sanity check.

### `GetNetWorthHistory` → sanity check + living-model seed
Pull 12+ months. Use it to (a) confirm the balances above are current and (b) seed the historical/living-model view. Not a direct `Scenario` field, but the right source if wiring up `docs/specs/LIVING_MODEL.md`.

### `GetCashFlow` (group_by_time=month) → historical actuals reference
The monthly **income** and **expense** totals are exactly what `user_data/historical.csv` wants (`year,month,total_income,paychecks,other_income,expenses`). Auto-populate it instead of hand-transcribing bank statements — its presence enables the app's Historical tab.

### `GetRealEstate` → housing
- Empty (`has_properties: false`) → user rents; leave housing to events, set `rent_monthly` from Pass 2.
- Has a property + loan → read property value, mortgage balance, **APR**, and remaining term directly. These feed `house_purchase` event params / home-equity inputs rather than bare `Scenario` defaults.

---

## Pass 2 — Infer and confirm (derive a number, then show the user)

These are derivable but noisy. Compute a value, **state your assumption, and ask the user to confirm** before committing it.

### Expenses → `expense_base`, `rent_monthly`
- `GetSpendingByCategory` and `GetCashFlow` give total spend, but **raw categories may be polluted by transfers** (e.g. a large brokerage-funding transfer mis-categorized under an expense category). Do **not** use raw totals.
- Exclude: transfers to brokerage/retirement, credit-card payments, and any one-off lump categorized as spending. Then divide the cleaned 12-month expense total by 12 for `expense_base`.
- `rent_monthly`: read the **Housing** category group's recurring monthly figure (it's stable month-to-month), not the noisy top-level total.

### `GetRecurring` → fixed bills (informational)
Lists insurance, utilities, subscriptions with amounts and cadence. Useful to validate the `expense_base` build and to spot fixed costs, but it does **not** map to a single `Scenario` field. Note: paychecks frequently do **not** appear as recurring income streams, so don't rely on this for salary.

### `pay_anchor`
Pull a recent paycheck transaction date via `GetTransactions` (filter to the payroll merchant) and use it as `pay_anchor`. The bi-weekly cadence derives from it.

### `ltcg_annual`
Estimate annual dividends + realized gains from `GetInvestments` holdings (tickers × typical yields) or from dividend/realized-gain transactions. Approximate only — confirm with the user.

---

## Pass 3 — Manual checklist (Monarch cannot see these)

**Stop and collect these from the user directly.** This is where the original setup conversation actually lived. Monarch provides *none* of them reliably.

**Compensation (from offer letter / payslip, NOT bank data):**
- `base_salary` — Monarch sees **net** deposits, not W-2 gross. Do not back into it from cash flow.
- `bonus_rate`, `salary_growth`
- `pretax_401k_pct`, `roth_401k_pct`, `aftertax_401k_pct`, `match_rate`, `match_cap` — payroll deductions are invisible to Monarch. Pull from the paystub / plan docs.
- `healthcare_monthly` — $0 if employer-paid; otherwise the premium.

**Equity (from the equity portal / grant PDFs — the single biggest manual chunk):**
- Per-grant vesting schedules → `user_data/grants.csv` (one row per vest date: `grant_id,year,month,shares`). Monarch sees vested shares *after* they land, never the forward schedule — export from the equity portal.
- `equity_refresh_grant_value`, `equity_refresh_growth`, `equity_refresh_base_year`, `refresh_projection_start_year` (first year NOT covered by grants.csv), `refresh_issue_month`
- `price_now` / `price_now_date` (public — can fetch), `price_growth` (assumption)

**Tax lots / basis:**
- `brokerage_cost_basis_fraction` — tax-lot data is Plus-gated and not exposed by the MCP. Ask, or keep the default.

**Personal facts:**
- `age` (Monarch household member returns name/role only, not age). Note the model is single-filer / CA-resident only (see README scope) — confirm that fits before bootstrapping.

**Forward-looking assumptions (model defaults, not personal data — keep unless the user has a view):**
- `return_rate`, `brokerage_return_rate`, `expense_growth`, `bracket_inflation`, `deduction_inflation`, `limit_inflation`, `roth_ira_annual`
- Scenario-specific: one-time windfalls (`brokerage_deposit` events in `user_data/scenarios.py`), `retirement_year`, `events`

---

## Coverage summary (set expectations up front)

- **Auto (Pass 1):** account balances, net-worth history, historical cash-flow actuals, owned-property value/mortgage. ~8–10 fields, but the *tedious* ones.
- **Infer + confirm (Pass 2):** `expense_base`, `rent_monthly`, `pay_anchor`, dividend/LTCG estimate. Noisy; always confirm.
- **Manual (Pass 3):** gross comp, payroll/401k splits, equity vesting, cost basis, age/filing/state, off-platform loans, all growth/return assumptions. The majority of fields *and* most of the model's forecasting power.

Net: Monarch removes the balance-sheet and expense-gathering grind and keeps balances fresh over time (synced balances auto-correct stale hand-entered figures). It does **not** shortcut the compensation and equity conversation.

## After bootstrapping

1. Write `user_data/profile.json` with auto + confirmed values (and `grants.csv` if the schedule was collected); leave un-collected manual fields out of the profile (placeholder defaults apply) and **list what's still assumed**.
2. Run `uv run variometer explain` on the baseline and eyeball the year-one numbers against the user's lived experience (take-home, monthly burn).
3. `uv run pytest` still passes untouched (user_data/ is invisible to the public suite); relaunch the app — the first-run banner should be gone and the sidebar should show the user's numbers.
