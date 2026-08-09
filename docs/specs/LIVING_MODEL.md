# Living model spec — monthly sync, drift detection, assumption refresh

**Status:** draft for review. No code shipped, but the monthly **review** loop has been validated manually against a live **Monarch MCP** connection — see "Connector tiers" and the `review-and-report` skill. The *apply* loop (mutating a profile) is still unbuilt.

**Goal:** make this model self-updating. Once a month, pull real data, reconcile it against the scenario, surface drift, and produce a report — for a single user manually today, optionally an agent-driven routine in the future.

---

## TL;DR

A "living model" is a small pipeline with three stages:

1. **Pull** — gather fresh data from whichever **connector plugins** the user has enabled (Monarch MCP, a market-data MCP, or CSV fallback).
2. **Reconcile** — compare the snapshot against the current `Scenario`. Auto-apply safe facts (balances); flag ambiguous ones (expense baseline, price, a suspected raise) for human review.
3. **Report** — emit a brief markdown summary: what changed, projection delta vs last month, open questions.

The reconciler step is where an LLM agent earns its keep — classifying transactions, judging "drift vs noise," drafting the message, suggesting which knobs to tweak.

**Cadence:** monthly. Daily is overkill (market noise drowns signal); quarterly misses early drift in expense trajectory.

**Key design shift since the original draft:** the pipeline is now organized as a **connector-plugin model**, not a CSV importer, and the **primary implementation is the `review-and-report` skill on a recurring task** — not a Python module. If the user has the **Monarch MCP** connected (the validated default), the agent pulls data by calling Monarch read-tools live during a review session — no file wrangling. CSV is retained only as a fallback for users who don't use Monarch. Stock price comes from Monarch's implied `value/quantity`, validated with a quick `WebSearch`; no market-data connector is needed.

---

## Connector tiers (the plugin model)

A user's `sync_config.json` declares which **data-source plugins** are active. Each plugin implements one interface (`DataSource.fetch() -> partial Snapshot`) and advertises which `Scenario` fields it can populate. The pipeline merges whatever the active plugins return; nothing else in the model cares where the data came from.

| Tier | Plugin | Provides | When to use |
|---|---|---|---|
| **A — Aggregator (pick one)** | **Monarch MCP** *(validated default)* | Balances, net-worth history, categorized transactions, investment holdings (→ implied price), recurring bills | The user is on Monarch and has connected the MCP. **Recommended.** |
| | Plaid (optional) | Same shape as Monarch, official API, ~$0.30/acct/mo | Wants automated unattended syncs and isn't on Monarch |
| | CSV / `monarchmoney` lib | Same shape, manual or ToS-grey | **Fallback only** — user has no aggregator MCP |
| **B — Price check (built-in)** | **`WebSearch`** | Comp-ticker spot price (and an index level if wanted) to validate Monarch's implied price | Always available, zero config — see "Stock price" |
| **C — Out-of-band (manual, every tier)** | — | Equity grant schedules, cost basis, annual comp/grant changes | Confirmed by the user; not derivable from any feed |

The point of the table: **the only thing that changes between a Monarch user and a non-Monarch user is which Tier-A plugin is loaded.** The reconciler, report, and the rest of the pipeline are identical.

### Stock price: Monarch implied + a web-search check

**Primary:** Monarch's `GetInvestments` gives an implied spot price per holding via `value / quantity` — free, no extra connector, and refreshes whenever the holding syncs. Use it for `price_now`.

**Validation / fallback:** a plain `WebSearch` for "current <TICKER> stock price" returns the live quote and confirms the implied figure — tested, and it matched Monarch's implied price to the cent. This is the lightweight price check; it can also grab an index level if you ever want a one-line market-context note.

**Relative performance is already in Monarch.** `GetInvestments` returns `variation_percent` / `period_change_percent` per holding plus the full mix, so under/over-performance and concentration are visible without an external benchmark. For a mostly-index book, a broad index mostly restates that — at most a monthly anomaly sanity-check, not a core input.

> **Footnote — why not a market-data connector.** FMP was evaluated with a free API key: index quotes work, but **all single-equity endpoints (`quote`, `quote-short`, `chart`) are plan-gated even authenticated**. Direct Stooq/Yahoo web fetches returned empty (JS-blocked). So a dedicated market-data MCP buys nothing over `WebSearch` + Monarch's implied price for this use case. Skip it unless you later want programmatic, headless index history.

---

## Architecture

**Primary implementation is the skill, not a module.** The monthly loop runs today as the `review-and-report` **skill** driven by a Cowork **recurring task**: the agent calls the MCP tools live, runs `monthly_forecast`, and writes a dated markdown report into `user_data/reports/`. Archived reports are themselves the history — the agent reads prior months to spot drift. No `sync/` package is required for this to work.

The deterministic `sync/` module below is **optional hardening**, worth building only if you later need headless/no-LLM runs, strict reproducibility, or a structured time-series to chart (see "Do you even need the module?" in Phasing). The plugin shape is documented here so that, if built, it stays connector-agnostic.

```
variometer/sync/                         OPTIONAL module (only if hardening beyond the skill)
  __init__.py
  snapshot.py                            data model: Snapshot, AccountSnapshot, TransactionSummary
  sources/                               connector plugins (was: fetchers/)
    __init__.py
    base.py                              DataSource interface: fetch() -> partial Snapshot; declares fields it fills
    monarch_mcp.py                       Tier A default — adapts Monarch read-tools into Snapshot
    websearch_price.py                   Tier B — comp-ticker price check via WebSearch
    csv_aggregator.py                    Tier C fallback — generic brokerage / Monarch CSV export
    plaid_client.py                      Tier A optional — official Plaid SDK
  reconciler.py                          merge active sources, diff vs Scenario, classify changes
  report.py                              render markdown / send
  cli.py                                 `uv run python -m variometer.sync run`

user_data/
  sync_config.json                       which plugins are active + their params (ticker, benchmark, csv paths)
  snapshots/                             one file per run: 2026-05.json, …
  reports/                               generated 2026-05-review.md, …
```

### `DataSource` plugin interface

```python
class DataSource(Protocol):
    name: str
    fills: frozenset[str]                # Scenario fields / snapshot sections this source can populate
    def fetch(self, period_start: date, period_end: date) -> "PartialSnapshot": ...
```

The reconciler loads the plugins named in `sync_config.json`, calls `fetch()` on each, and merges. A field claimed by two sources resolves by tier order (a Tier-A balance beats a CSV balance). This is the whole "plugin model": adding Plaid, a different aggregator, or a new market-data provider is one file implementing `DataSource`.

### Snapshot data model

```python
@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str              # stable ID from the source
    institution: str
    account_type: str            # "brokerage" | "401k" | "ira" | "roth_ira" | "checking" | …
    balance: float
    cost_basis: float | None     # taxable only; usually None (Monarch can't supply it)
    as_of: date

@dataclass(frozen=True)
class TransactionSummary:
    period_start: date
    period_end: date
    net_paychecks: float                     # sum of Paycheck-category credits (NET pay)
    vest_proceeds: float                     # equity-provider credits (broker "Other Income")
    other_income: float
    total_expenses: float
    expense_by_category: dict[str, float]    # transfers already excluded
    contributions: dict[str, float] | None   # pretax/roth/aftertax/match — only if investment txns are tracked
    reimbursements: list[Reimbursement]      # e.g. a shared expense reimbursed within the month
    one_offs: list[OneOff]

@dataclass(frozen=True)
class Snapshot:
    captured_at: datetime
    accounts: list[AccountSnapshot]
    transactions: TransactionSummary | None
    market: MarketSnapshot | None            # comp-ticker price + benchmark level + period return
    metadata: dict
```

One immutable JSON blob per run in `user_data/snapshots/YYYY-MM.json`. The reconciler compares N to N-1 (or to the scenario baseline if N=1).

### Reconciler logic

For each `Scenario` field with a snapshot equivalent, classify the delta:

| Field | Snapshot source | Auto-apply if … |
|---|---|---|
| `brokerage_balance` | sum(taxable brokerage + cash-mgmt) | always — it's a fact |
| `pretax_401k_balance` | 401k account balance | always |
| `ira_balance` | rollover IRA balance | always |
| `brokerage_cost_basis` | broker-reported basis | only when supplied (Monarch does **not**) |
| `price_now` | market-data spot, else implied `value/quantity` | flag (assumption-shaped, but low-friction to accept) |
| `pretax_401k_pct` / `aftertax_401k_pct` | **investment-txn contributions** if tracked, else inferred from net-paycheck delta + balance inflow | flag → confirm with the user (a rate change is a discrete event the user knows about) |
| `base_salary` (net check) | `net_paychecks / pay_periods` | compare-only — reconcile take-home, don't overwrite gross |
| `expense_base` | trailing-3–6-mo expense avg (transfers + reimbursements netted) | flag — "is this the new normal?" |
| events (`pay_change`, `child`, …) | suspicious transaction patterns | propose an event; user accepts/rejects |

**What "compare-only" means for income:** Monarch transactions cleanly separate **net paychecks** (employer merchant → "Paychecks") from **vest proceeds** (equity provider → "Other Income"). So the reconciler reconstructs net pay and net vest without a paystub, and compares them against the model's `take_home` / vest math. It does **not** try to overwrite `base_salary` (that's W-2 gross, still out-of-band) — it just confirms the net is consistent and flags a surprise.

**Detecting a 401(k) contribution change without a paystub:** a contribution-rate bump shows up two ways the reconciler can see — the **net paycheck drops** (more is deferred) and the **401k balance inflow accelerates**. Neither is proof on its own, but together with a one-line user confirmation ("yes, I moved to the optimized election in July") it's enough. If the user enables Monarch **investment-transaction tracking** (see Open Questions), the actual contribution line items become visible and this stops being an inference at all.

**Reimbursement handling (don't flag timing artifacts):** some expenses are paid in full then reimbursed within the same month (a split bill, an expensed work purchase). These show as a mid-month category spike that nets out by EOM. The reconciler must **net same-category reimbursement inflows against the expense** and use the **month-end** figure — never flag the mid-month spike as overspending. (Household-specific patterns belong in user_data/REVIEW_NOTES.md.)

### Report shape

One markdown file per run in `user_data/reports/YYYY-MM-review.md`. Section order (validated in the first manual run):

1. **Headline** — did the model track; dominant driver of any drift.
2. **Brokerage balance** — model vs actual for the closed month (+ MTD).
3. **Net worth** — diffed on a **constant account set**; newly-linked accounts reported separately, never as growth.
4. **Market context** *(optional)* — implied price vs `price_now`, plus per-holding `period_change_percent` from Monarch (and a `WebSearch` index level if wanted); separates stock-specific moves from market-wide ones.
5. **Expenses** — model vs actual (reimbursements netted), anomaly flags.
6. **Income** — net paycheck + vest reconciliation; note any contribution-change signal.
7. **Drift detection** — ⚠️/✓ bullets.
8. **Needs manual input this cycle** — only the blind spots actually in play.
9. **Suggested actions (none applied)** — ranked; usually `price_now` is the highest-leverage edit.

---

## Agent integration

The reconciler and reporter are where LLM augmentation pays off. Two integration shapes:

### Shape A — "report drafter" (lightweight, recommended for v1)

The pipeline runs deterministically end-to-end. At the end, it passes the raw structured diff to Claude and asks it to draft the markdown. The diff is small JSON; Claude returns a friendlier summary. ~50 extra lines, ~$0.01/run. Zero risk of hallucinated numbers (the LLM only rewrites a structured input it can't fabricate).

### Shape B — "reconciler agent" (heavier, needs guardrails)

The reconciler itself becomes an agent: given the snapshot + current `Scenario`, it decides what to flag, auto-apply, or ask about. **This is in fact how the Monarch-MCP review is run today** — the agent calls the read-tools live, reconciles in-context, and writes the report. Pros: handles ambiguity, catches patterns no heuristic would ("rent looks doubled — but there's a matching reimbursement, so it's a timing artifact"). Cons: needs strict guardrails so it never silently mutates `Scenario`. **All mutations require human confirmation, shaped as structured proposals (`propose_field_update(field, current, suggested)`), never free-text.**

**Recommendation:** the live-agent flow (Shape B, review-only) is already useful and is what the `review-and-report` skill encodes. Add the deterministic `sync/` module (Shape A) when you want unattended scheduled runs.

### Scheduling

- **Personal:** a Claude Code / Cowork **recurring task** on the 1st of each month runs the `review-and-report` skill and drops the report in `user_data/reports/` (or messages it to you). No cron, no server.
- **Deterministic module:** once `sync/` exists, `cron`/`launchd` calling `uv run python -m variometer.sync run`, or a monthly GitHub Action.

---

## Risks, downsides, and what to watch for

1. **Aggregator dependence.** Monarch's MCP could change or deauthorize. *Mitigation:* the plugin model keeps CSV (Tier C) as a first-class fallback; switching tiers is a config change, not a rewrite.
2. **Market noise dominates short windows.** A single month's balance move is mostly price, not behavior. *Mitigation:* the benchmark comparison (Tier B) and implied-price attribution separate "the market moved" from "you changed."
3. **Account-coverage changes.** A newly-linked account inflates the NW delta. *Mitigation:* always diff a constant account set; surface additions separately.
4. **Intermittent syncing.** Accounts can flicker $0 between weekly snapshots. *Mitigation:* use month-end readings; sanity-check against the prior non-zero value.
5. **Categorization / transfer pollution.** Brokerage-funding transfers can land in expense categories; vest proceeds in income. *Mitigation:* exclude transfers and net reimbursements before computing `expense_base`; trust per-merchant/category transactions over top-line totals.
6. **Silent drift from over-auto-applying.** *Mitigation:* auto-apply balances only; every assumption change is a logged, confirmed proposal.
7. **Market-data cost/limits.** Free tiers (FMP/Alpha Vantage) are rate-limited. Fine for one monthly call; document the key in `user_data/secrets.toml` (never committed).
8. **You stop reading the reports.** *Mitigation:* the monthly run nags on ignored prior open items.

### Privacy

- **Default local-only.** Data stays in `user_data/`; reports are local markdown.
- **LLM calls** ship balance + scenario JSON to Anthropic; documented, disable-able via config.
- **Cloud/scheduled** surfaces (Slack, email, Actions) are opt-in and require explicit credential setup.

### What it doesn't solve

- Doesn't make decisions for you (flags "expenses up 6%," can't say whether to cut, work longer, or accept).
- Doesn't validate future-year assumptions — only flags forecast error to date.
- Doesn't replace the annual deep-review of big assumptions (retirement age, target lifestyle).

---

## Phasing (skill-first)

The earlier draft front-loaded a Python `sync/` module. That ordering is wrong for a single user who already has the MCP and a skill: the agent-driven loop covers nearly all the value with no code to maintain. Reordered accordingly.

**Phase 1 — review-only via the skill (done, manual).** The `review-and-report` skill: agent pulls Monarch read-tools, runs `monthly_forecast`, writes a dated report. No new code. **Usable today.**

**Phase 2 — enable Monarch investment-transaction tracking (5 min).** A settings toggle, not code. Unlocks contribution/vest/dividend line items → contribution verification becomes direct rather than inferred. Highest value-per-effort step by far.

**Phase 3 — schedule it (15 min).** A Cowork **recurring task** on the 1st of each month that runs the skill and writes the report to `user_data/reports/`. This is the step that makes it a *living* model, and it's nearly free — promoted from the bottom of the old list. (GitHub Action is the alternative only if you want it running without your machine on.)

**Phase 4 — lightweight history append (½ hr, optional but recommended).** Have the skill append ~8 key numbers per run (date, NW ex-new-accounts, brokerage, 401k, expense actual, implied price, projection NW@target) to a single `user_data/snapshots/history.jsonl`. This is the cheap substitute for the deterministic module: it gives drift-over-time and a future Streamlit "Sync history" tab without building a package. The agent reads it (plus archived reports) for trend context.

### Do you even need the deterministic module? (mostly no)

The old Phase 3 (`sync/` package) and Phase 4 (LLM report drafter) are **optional and probably skippable.** What they'd add over *skill + recurring task + archived reports + Phase-4 JSONL*:

| Capability | Skill-only path | Needs the module? |
|---|---|---|
| Monthly pull + reconcile + report | ✓ (agent live) | no |
| Scheduled/automated runs | ✓ (recurring task) | no |
| Browsable history / drift-over-time | ✓ (archived reports + `history.jsonl`) | no |
| Streamlit "Sync history" chart | ✓ (reads `history.jsonl`) | no |
| Hallucination safety | ✓ (agent reads tool output, doesn't invent; numbers are quoted, not computed by the LLM) | no |
| **Run headless with no LLM / no agent** | ✗ | **yes** |
| **Bit-reproducible, unit-tested reconciliation** | ✗ | **yes** |
| **Zero per-run API cost** | ✗ | **yes** |

So build the module **only** if you later want unattended runs with no agent in the loop, or you want the reconciliation logic version-controlled and unit-tested like the rest of `variometer/`. For a personal user comfortable with the agent loop, that's a "maybe someday," not a near-term phase. The LLM "report drafter" (old Phase 4) is moot — if the agent is writing the report, it's already the drafter.

**Phase 5 (optional, someday) — deterministic `sync/` module.** Only for headless/no-LLM or reproducibility needs, per the table above.

**Phase 6 (optional) — CSV/Plaid plugins.** Only for the generalized release / non-Monarch users.

**Total:** useful today; **~20 minutes** (Phases 2–4) to a scheduled, self-archiving living loop. The module is a someday-maybe, not a requirement.

---

## Open questions for the user

1. **Enable Monarch investment/401(k) transaction tracking.** Monarch *can* track investment-account transactions (contributions, vests, dividends), but the setting is often off. Turning it on lets the review read **actual contribution line items** — directly verifying `pretax_401k_pct` / `aftertax_401k_pct` and decomposing each 401k balance change into contributions vs market growth, rather than inferring from net-paycheck deltas. It also surfaces vest and dividend transactions explicitly. **This is probably the single highest-quality-per-effort data improvement available.** *(Action: enable in Monarch account settings, then re-test a review.)*
2. **Stock price — settled.** Monarch implied price + a `WebSearch` validation (confirmed accurate). No market-data connector needed. *(Resolved.)*
3. **Schedule cadence + surface.** Recurring task on the 1st → report to `user_data/reports/` only, or also messaged to a chat surface?
4. **Build the deterministic module, ever?** Per "Do you even need the module?", only if you want headless/no-LLM runs or unit-tested reconciliation. Default: skip for now.
5. **Sync-history tab in Streamlit?** Reads the Phase-4 `history.jsonl` to show projection drift over time ("in Jan I projected retire-at-55; by June it had drifted to 57"). ~½ day if you want the visual.

---

## Verdict

High-leverage for low effort — the hard part (model, scenario plumbing, projection math) is done. The right shape is **skill + recurring task + archived reports**, not a Python sync package: that combination already does the monthly pull, reconcile, report, scheduling, and history, with no code to maintain and no hallucination risk (the agent quotes tool output, it doesn't compute the numbers). The deterministic module is a someday-maybe for headless/no-LLM or unit-tested reconciliation — not a near-term need.

Two cheap, high-value moves: **enable Monarch investment-transaction tracking** (5-min toggle — closes the contribution-verification gap, surfaces vests/dividends directly) and **schedule the skill** on the 1st of each month (~15 min). Stock price is settled via Monarch's implied price plus a `WebSearch` check — no market-data connector. Net: ~20 minutes of setup takes the monthly review from "good" to a self-running living loop with almost nothing left that needs a paystub.
