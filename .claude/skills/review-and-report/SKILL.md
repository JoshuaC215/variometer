---
name: review-and-report
description: Run the monthly Variometer sync — pull the latest actuals from Monarch (balances, transactions, holdings), validate the stock price, compare against the model's forecast for the just-closed month, surface drift, and write the dated review report. Use at the start of each month or when the user asks to reconcile or review the model ("run my monthly review", "how's the model tracking"). Read-only — proposes changes, never edits model numbers.
---

# Monthly review & report

Reconcile the Variometer model against reality once a month. Pull actuals from the **Monarch MCP**, validate the comp-ticker price with a quick `WebSearch`, run the model's forecast for the just-closed month, and write `user_data/reports/YYYY-MM-review.md` plus a one-line append to `user_data/snapshots/history.jsonl`. The steps below are self-contained — everything needed to run is here.

**Hard rule:** this is a *review*. Do **not** edit `user_data/profile.json`, `config.py`, or any scenario number. Surface flags as proposals; the user decides. Applying changes (usually a `price_now` or balance update in `user_data/profile.json`) is a separate, explicitly-confirmed action.

## Step 0 — orient

- Today's date → the just-closed month is the reconciliation target (e.g. early July → reconcile **June**; show the current month as month-to-date).
- Active scenario: the user's baseline — `base_scenario()` (placeholder defaults + `user_data/profile.json`).
- **Read `user_data/REVIEW_NOTES.md` if it exists** — household-specific reconciliation quirks (unusual cash flow, account naming, merchant mappings). Apply them throughout.
- Confirm the Monarch MCP is connected. If it isn't, stop and tell the user.

## Step 1 — pull actuals

**Monarch:**
- `GetAccounts` → current balances by type.
- `GetNetWorthHistory(start_date=<2 months back>, include_account_breakdown=true)` → weekly per-account snapshots **and** `newly_added_accounts`.
- `GetCashFlow(group_by_time="month", group_by_entity="category_group")` → per-category income/expense.
- `GetTransactions(filters={"transaction_type":"Credit"})` for the closed month → decompose income (Step 3). This is the call that removes any paystub dependency.
- `GetInvestments` → holdings; **implied price = value / quantity** for the comp ticker.

**Stock price:**
- Primary: Monarch implied price (above).
- Validate with `WebSearch("current <TICKER> stock price")` and use the agreement as a confidence check.

## Step 2 — run the model for the closed month

From the repo root (always `uv run` — see CLAUDE.md):

```bash
cd <repo> && uv run python -c "
from datetime import date
from variometer.config import base_scenario
from variometer.cashflow import monthly_forecast
for r in monthly_forecast(base_scenario(), start=date(YYYY,MM,1), end=date(YYYY,MM+1,1)):
    print(r.year, r.month, round(r.expenses), round(r.net_cash_flow), round(r.brokerage_balance))
"
```

`MonthlyResult.brokerage_balance` = modeled end-of-month taxable balance; `.expenses` = modeled burn. Use `monthly_forecast` — don't write a custom projection script.

## Step 3 — reconcile

**Income — decompose from transactions (no paystub needed).** In `GetTransactions` credits:
- **Net paychecks** = employer merchant + "Paychecks" category. Sum them; compare to the model's `take_home`-implied monthly net.
- **Vest proceeds** = equity-provider merchant + "Other Income". Compare to the model's vest math for the month.
- Ignore **Transfer** and **Credit Card Payment** credits — not income.

**Balances:**
1. **Taxable brokerage** — model `brokerage_balance` vs **all taxable brokerage accounts (including any sweep/cash-management and vested-share holding accounts)** at month-end. Cleanest signal.
2. **Net worth** — diff a **constant account set**; pull `newly_added_accounts` out and report separately (a newly-linked account is not growth).
3. **401(k)** — balance delta ≈ contributions + market growth; note it alongside the income signal.

**Expenses:** model `.expenses` vs Monarch category total, after the netting in Step 4. Flag genuine step-changes, not timing spikes.

**Price:** Monarch implied price vs the profile's `price_now`. A large gap usually explains most balance drift — attribute drift to price before flagging behavior. Monarch's per-holding `period_change_percent` gives relative performance if a position looks off.

## Step 4 — netting & artifacts (before flagging anything)

- **Newly-linked accounts:** exclude from growth; report under a separate "account coverage" note.
- **Flickering syncs:** if an account reads $0 in a weekly snapshot but was non-zero before and after, use the non-zero month-end value.

## Step 5 — classify each delta

- **Fact (state as a proposed balance update):** `brokerage_balance`, `pretax_401k_balance`, `ira_balance`.
- **Assumption (flag for confirmation):** `expense_base` drift, `price_now`, a suspected contribution change, any new recurring cost.
- **Out-of-band (note, don't try to derive):** exact W-2 gross & withholding, exact vest share count, cost basis. Income *net* is readable and the model reconciles on net, so this rarely blocks. Annual equity grants and salary changes are confirmed by the user separately.

## Step 6 — write the report

`user_data/reports/YYYY-MM-review.md`, in this order:
1. **Headline** — did the model track; the dominant drift driver.
2. **Brokerage balance** — model vs actual (+ MTD).
3. **Net worth** — with the constant-account-set caveat.
4. **Market context** — implied price vs `price_now`; per-holding performance if notable.
5. **Expenses** — model vs actual, anomaly flags.
6. **Income** — net paycheck + vest reconciliation; contribution-change signal.
7. **Drift detection** — ⚠️ / ✓ bullets.
8. **Needs manual input this cycle** — only the blind spots actually in play.
9. **Suggested actions (none applied)** — ranked; usually `price_now` is the highest-leverage edit.

## Step 7 — append history & read the past

- **Read prior context first:** skim the last 1–2 `user_data/reports/*.md` and `user_data/snapshots/history.jsonl` so the report can call out trends ("third month running under budget", "projection NW@target drifting down").
- **Append one row** to `user_data/snapshots/history.jsonl`: date, NW (ex newly-linked accounts), brokerage, 401(k), IRA, expense actual, implied price, projection NW@target. This is the running history that powers drift-over-time and a future Streamlit "Sync history" tab.

## Common pitfalls

- **Comparing Monarch's "Income" total to the model's gross.** Don't. Decompose credits (paychecks + vest) and reconcile on net.
- **Reporting a newly-linked account as growth.** Constant account set only.
- **Trusting raw category totals for `expense_base`.** Strip transfers and reimbursements first.
- **Treating one month's market move as drift to fix.** Attribute it via the implied/searched price first.
- **Editing model numbers during the review.** Review proposes; the user disposes.

## When done

Present the report file and give a 2–3 sentence summary: tracking quality, the single biggest flag, and whether any manual input is needed this cycle.

---

<!--
NOTE FOR SKILL DEVELOPMENT ONLY — DO NOT LOAD DURING LIVE / SCHEDULED RUNS.
The steps above are self-contained. A monthly run should not open docs/specs/LIVING_MODEL.md
or read this block; it only adds context the live run doesn't need.

Open questions / maintenance:
- If Monarch investment-transaction tracking is enabled, verify which line items actually appear (contributions split into
  pre-tax / Roth / after-tax / match; vest events; dividends). If contribution line items sync
  cleanly, upgrade Step 3 from the net-paycheck inference to a direct read of contribution
  amounts, and let the reconciler decompose each 401(k) balance change into contributions vs
  market growth.
- Cost basis and exact W-2 gross remain out-of-band; revisit only if a basis source appears.
- Full design rationale, connector tiers, and phasing live in docs/specs/LIVING_MODEL.md.
-->
