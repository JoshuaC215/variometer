# Assumptions & Risks

A working list of the **baked-in legal, tax, and external assumptions** that most influence the numbers the model produces — and that are most likely to change in the next ~1–3 years. Review this annually (or whenever a major item below moves).

Each item lists:
- **Where it lives** in the code,
- **What changing it would do** to results,
- **What to check** if you're revisiting.

## How to use this document

- **Annual review:** read this top-to-bottom each year. Anything with a recent legislative or regulatory change → update the cited constants in code + delete the entry.
- **Before a big decision:** scan items 1–4. They drive the largest dollar swings in scenarios that involve leaving your employer, drawing down, or buying a home.
- **When a number looks wrong:** start here, then read [DESIGN.md](DESIGN.md).

---

## Provenance of the 2026 constants (refreshed 2026-08-08)

The model is anchored at `MODEL_EPOCH = 2026`. As of the August 2026 refresh, most
of the epoch constants are **published actuals**, not projections:

| Constant | Status | Source |
| --- | --- | --- |
| Federal ordinary brackets (single) | published actual | IRS Rev. Proc. 2025-32 (reflects OBBBA) |
| Federal standard deduction ($16,100) | published actual | IRS Rev. Proc. 2025-32 |
| Federal LTCG thresholds ($49,450 / $545,500) | published actual | IRS Rev. Proc. 2025-32 |
| Social Security wage base ($184,500) | published actual | SSA announcement, 2025-10-24 |
| CA SDI rate (1.3%, uncapped) | published actual | CA EDD 2026 rates |
| 401(k) / IRA limits ($24,500 / $72,000 / $7,500) | published actual | IRS Notice 2025-67 |
| Additional Medicare (0.9% > $200k), NIIT (3.8% > $200k) | statutory, unindexed | IRC §3101(b)(2), §1411 |
| ACA applicable-percentage table | published actual for **2026 only** | IRS Rev. Proc. 2025-25 §3.01 |
| FPL baseline ($15,650 + $5,500/person) | published actual | HHS 2025 Poverty Guidelines (govern 2026 coverage) |
| **CA income-tax brackets** | **still projected** (~3% off 2025) | FTB publishes 2026 tables in fall 2026 |
| **CA standard deduction ($5,710)** | **still projected** | same |

Each of these constants is anchored by a **golden test case** whose expected
value was hand-computed from the cited source before the model was run against
it — see [GOLDEN_CASES.md](GOLDEN_CASES.md) for the cases, the arithmetic, and
exactly what the "golden" label does and does not claim. When you refresh a
constant during the annual review, redo the affected case's arithmetic and
update the expected value and its stated derivation together.

Two caveats carried deliberately:

- **CA brackets and CA standard deduction are excluded from the model's "golden"
  correctness claims** until the FTB publishes. Expect a small re-pin when they do.
- **The ACA applicable-percentage table is pinned to plan year 2026 and held flat
  for all later years.** The IRS re-indexes it annually by a premium-growth /
  income-growth ratio that does not track general inflation, so projecting it
  forward would be spurious precision. Lean-year subsidy figures beyond 2026 are
  therefore approximate.

## Tax law — top sensitivities

### 1. TCJA expiration / extension (single biggest unknown)

The Tax Cuts and Jobs Act of 2017 set the ordinary income brackets, standard deduction, $10k SALT cap, and pass-through rules that the model uses today. Significant portions were scheduled to sunset after **2025**. The 2025 reconciliation legislation extended most of the TCJA personal-income provisions; whether the **SALT cap stays at $10k**, rises, or sunsets has been the most-actively-negotiated piece and may move again.

- **Where:** `variometer/data/brackets.py` (ordinary brackets, standard deduction); `variometer/config.py::Scenario.tcja_active` (SALT cap toggle); `variometer/housing.py::itemized_excess_for_year` (uses `tcja_active`).
- **Impact if SALT cap lifts:** itemized deductions in an HCOL home-purchase scenario jump roughly **$10k–$30k/yr** (depending on CA income tax + property tax). The current model **saturates the cap with CA income tax alone**, so property tax adds zero marginal benefit — uncapping unlocks all of it. Net federal tax savings: ~$3–10k/yr in the early mortgage years.
- **Impact if brackets revert to pre-TCJA:** ordinary marginal rates climb (25% → 28%, 32% → 33%, 35% → 35%, 37% → 39.6%). Take-home in the working baseline drops several thousand per year; Roth-conversion lean-year math gets **slightly more attractive** (bigger spread vs. higher working-years brackets).
- **What to check:** look up the IRS bracket release for the current year and compare to `FEDERAL_SINGLE_2026` in `data/brackets.py`. If brackets reverted, also remove the standard deduction inflation off the TCJA-doubled base. (The 2026 tables themselves are settled — Rev. Proc. 2025-32 — so this item is now about 2027+.)

#### Known deferral: the SALT cap is modeled at $10k, not the OBBBA $40k

OBBBA **raised the SALT cap from $10,000 to roughly $40,000 for tax years 2025–2029**, with a phase-down for MAGI above ~$500,000 and a reversion to $10,000 in 2030. The model **still applies the $10,000 cap** whenever `tcja_active` is set.

- **This is a deliberate, owner-approved conservative simplification, not an oversight.** It *overstates* federal tax in any scenario where a house is owned (the itemized deduction is understated by up to ~$30k of deductible SALT), which makes home-purchase scenarios look slightly worse than they are.
- At this model's income level the phase-down bites, so the correct treatment is not simply "$40k" — it needs the MAGI-dependent phase-down and the 2030 cliff, plus a re-pin of every housing scenario. That work is **deferred to post-publication**.
- **Where:** `variometer/config.py::Scenario.tcja_active`, `variometer/housing.py::itemized_excess_for_year`.

### 2. ACA Premium Tax Credit — sunset of "enhanced" subsidies

The model implements the **post-enhancement §36B sliding scale**: the ARPA/IRA subsidy expansion expired at the end of 2025, so the 400% FPL subsidy cliff is restored. The band table is the **actual 2026 Applicable Percentage Table from IRS Rev. Proc. 2025-25 §3.01** (2.10% flat below 133% FPL, rising to 9.96% at 300–400% FPL, no subsidy above 400%).

- **Where:** `variometer/tax.py::aca_premium_tax_credit` and the `_aca_applicable_percentage` band table; FPL baseline in `_FPL_2026_COVERAGE_SINGLE`.
- **The table is pinned to plan year 2026 and held flat for later years.** The IRS re-indexes it annually by a premium-growth / income-growth ratio that does not follow general inflation, so projecting it would be spurious precision. This is a stated approximation, and it is the largest source of error in multi-year lean-window subsidy estimates.
- **FPL baseline:** PTC eligibility for coverage year Y uses the HHS guidelines published in January of Y−1. For 2026 coverage that is the **2025** HHS guidelines: $15,650 for the first person, $5,500 per additional. The model anchors there exactly and inflates at `_FPL_INFLATION = 0.03` for later years — a planning estimate; real HHS adjustments have run 2–4%. A 1pp swing moves the 400% FPL cliff by ~$600/yr.
- **Impact if enhancements are re-extended:** the 400% FPL cliff goes away (capped at 8.5% of income above 400% FPL instead). For a lean year with $40–60k AGI this is the difference between $0 subsidy and ~$200–400/mo subsidy, depending on the benchmark premium. Lean-year runway improves by ~$5–10k/yr.
- **What to check:** each year's Rev. Proc. carrying the §36B applicable-percentage table, plus the January HHS poverty guidelines release.

### 3. Federal LTCG bracket thresholds + NIIT threshold

The 0% / 15% / 20% federal LTCG brackets are the **single most leveraged tax feature in lean-year scenarios** — the lean-year drawdown waterfall is explicitly designed to land realized gains in the 0% bracket.

- **Where:** `variometer/data/brackets.py::FEDERAL_LTCG_ZERO_TOP_2026` and `FEDERAL_LTCG_FIFTEEN_TOP_2026`; `variometer/tax.py::ltcg_federal_tax`. NIIT in `variometer/tax.py::niit` (3.8% over $200k AGI single).
- **Impact:** the 0% threshold is **$49,450** single in 2026 (published actual, Rev. Proc. 2025-32; the 15% top is $545,500). A $5–10k swing changes how many years of brokerage you can drain at 0% federal cost during a lean window. The NIIT threshold of $200k AGI has **never been inflation-adjusted** since it was written into the ACA — bracket creep means more years will hit it.
- **What to check:** the annual IRS Rev. Proc. publishing LTCG threshold adjustments. The NIIT threshold is fixed in statute; only changes by Congress.

### 4. Mega backdoor Roth + after-tax 401(k) contributions

Treasury and the IRS have at times signaled scrutiny of the "mega backdoor" pathway (after-tax 401(k) contributions immediately converted to Roth IRA / in-plan Roth). Build Back Better included proposals to close it; those did not pass, but the political pressure recurs.

- **Where:** `variometer/retirement.py::compute_contributions` (uses the full annual-addition limit headroom for after-tax fills). `variometer/config.py::Scenario.aftertax_401k_pct`.
- **Impact:** Tens of thousands per year of after-tax → Roth conversions in a mega-backdoor-maxing baseline. If closed, that becomes ordinary taxable savings going to brokerage instead — small near-term hit (you still save the money), but ~$1–2M lifetime opportunity cost on **tax-free growth** in the Roth umbrella.
- **What to check:** Treasury / IRS guidance + any Roth-related provisions in current tax legislation.

### 5. Backdoor Roth IRA itself

Same risk shape as mega backdoor — periodically proposed for closure, has survived to date.

- **Where:** `variometer/config.py::Scenario.roth_ira_annual` (added to Roth IRA principal each year, treated as accessible after-tax dollars).
- **Impact if closed:** $7,500/yr (growing with the IRA contribution limit) gets stuck in taxable brokerage instead of Roth. Modest tax drag (~1% of $7.5k = $75/yr the first year), but compounds: a 20-year delta is ~$50–80k in tax-free vs. taxable terminal value.

### 6. 401(k) annual limits & catch-up

Employee deferral limit ($24,500 in 2026), employer-plus-employee combined limit ($72,000 in 2026), and IRA limit ($7,500) — all **published actuals** from IRS Notice 2025-67. SECURE 2.0's age-50 / age-60–63 catch-up structure is not modeled. Later years are simple inflation-projected versions of these 2026 baselines.

- **Where:** `variometer/retirement.py` (`limits_for`, the `*_LIMIT_2026` constants). `Scenario.limit_inflation` (default 2.5%).
- **Impact:** missing the 50+ catch-up matters from the placeholder persona's age-50 year (2041) onward but not the next 5 years. SECURE 2.0's age-60–63 "super catch-up" ($11,250 vs $7,500) is **not modeled** — would add ~$3,750/yr during the persona's age-60–63 window (2051–2054 for the placeholder).

### 7. Social Security — projected COLA, taxable %, full retirement age

The retirement-sustainability analysis in `variometer explain` and the Runway tab uses:
- **COLA** of 2.5%/yr (`SS_COLA_DEFAULT` in `variometer/data/social_security.py`).
- **Federal taxability** of 85% (the legal max), via `ss_federal_taxable`.
- **Full Retirement Age** = 67 (correct for anyone born 1960+; **wrong** if FRA is changed by Congress, which is occasionally proposed).
- **Benefit estimate formula** is a rough PIA approximation, not the actual 35-year-AIME calculation.

- **Impact:** the SS line is "directionally right." Estimator may be ±20% vs. an actual ssa.gov projection. The "Direct entry" mode in the UI exists specifically to bypass this — use it for any decision that hinges on the SS number.

### 8. State of California — SDI, brackets, mental-health surtax

- **CA SDI uncapped at 1.3%** for 2026 (`CA_SDI_RATE = 0.013` in `tax.py`, per CA EDD; the wage ceiling was removed effective 2024). This rate has moved every couple of years and applies to *every* W-2 dollar, so it is the single most impactful CA constant — the 1.1% → 1.3% refresh alone cost the pinned personas ~$750–1,700/yr. If California reintroduces a wage cap, high-W-2-income years would over-state SDI by a few thousand dollars.
- **CA brackets and CA standard deduction are still projected**, not published: `CALIFORNIA_SINGLE_2026` (with the 1% mental-health surcharge over $1M) and `CALIFORNIA_STANDARD_DEDUCTION_2026 = 5,710` are 2025 actuals grown ~3%. The FTB publishes official 2026 tables in fall 2026. **These are excluded from the model's "golden" correctness claims** and should be refreshed (with a re-pin) when published.
- **No itemized deductions on CA side** by design — see [DESIGN.md](DESIGN.md). This is a known simplification (CA does allow itemized; we always take CA standard).
- **What to check:** CA FTB annual bracket release; CA EDD's annual SDI rate notice. CA brackets have run ~3%/yr in line with federal.

---

## Modeling value choices (not just simplifications)

These are places where the model encodes a *preference* alongside the math. Worth knowing because changing the preference would meaningfully shift outputs without changing any tax law.

### Drawdown waterfall — Roth IRA earnings before pre-tax 401(k)

The waterfall taps **Roth IRA earnings (bucket 4) before pre-tax 401(k) (bucket 5)** in pre-60 drawdown years. Both buckets incur ordinary income + 10% penalty at the same marginal rate, so terminal value is identical bucket-for-bucket.

The reason Roth earnings are pulled first: a pre-tax 401(k) dollar carries a **future ordinary-income tax burden** (taxed when withdrawn in retirement, even if no penalty applies). A Roth IRA earnings dollar, once non-qualified, has already paid both penalty and tax; no future obligation. So pulling Roth earnings first preserves the larger remaining tax shield in the 401(k).

**The value tension:** Roth earnings are otherwise *tax-free* growth at qualified-distribution time. Pulling them pre-60 trades a future tax-free dollar for a currently-taxable-once dollar. For a user who is genuinely "willing to return to work" — i.e. who plans to eventually fund retirement from regular contributions again — that tradeoff is fine: the depleted Roth earnings will get rebuilt and the preserved 401(k) shield is more valuable. For a user who is "willing to return to work in principle but actually won't," the math flips: Roth earnings preserved would have grown tax-free for decades, while the 401(k) was going to be taxed eventually anyway.

If you want to reverse the order, swap the bucket-4 and bucket-5 calls in `variometer/withdrawals.py::waterfall_withdrawal`. The 5-bucket structure and the per-bucket tax math don't change.

## Modeling simplifications worth knowing

These aren't "external risks" but they affect what the numbers mean.

- **AMT not modeled.** Pure W-2/RSU income doesn't trigger AMT at these scales. Would matter if a large ISO exercise entered the picture.
- **LTCG on `scenario.ltcg_annual` uses the bracketed 0/15/20% function**, stacked on ordinary income — the same function the drawdown waterfall uses. At high working-years income this is equivalent to a flat 15% (unless income pushes into the 20% band, which the bracketed function also handles correctly); in lean years the 0% bracket applies to the baseline signal too.
- **RSU vest income** is taxed as ordinary W-2 wages with no held-shares capital-gains layer — assumes sell-at-vest. Real holding decisions would create a separate LTCG/STCG path.
- **House price appreciation defaults to 3%/yr in `buy_home`.** Long-run HCOL-metro nominal has averaged 4–6%/yr but with high volatility; 3% is conservative-central. **Paper wealth only** — appreciation lifts NW totals but the drawdown waterfall cannot tap home equity. Set the knob to 0 in the UI to stress-test a flat-price decade.
- **Mortgage rate is an input, not a forecast.** Default 6.75% reflects mid-2025 market. Re-shop annually.
- **Brokerage drag of 1%/yr** (return_rate − brokerage_return_rate) is a stand-in for dividend tax + occasional realization. At higher AGI it's closer to 1.2–1.5%.
- **Single filer, CA resident, no dependents.** Hard-coded throughout. MFJ / out-of-state / kids would each require non-trivial work.
- **No HSA**, no ISOs/ESPP, no DAF/charitable, no 72(t)/SEPP.

---

## External / non-tax risks to revisit annually

- **Employer refresh policy.** The linear refresh model (`equity_refresh_grant_value`, grown at `equity_refresh_growth`) is a *plausible inference* about a steady-state grant target, not any employer's policy. Real grants vary with level, performance, and stock price at issuance; if actual sizing diverges materially from the target, equity income forecasts drift.
- **Employer-stock concentration.** The model treats the employer stock as flat by default (`price_growth = 0`). Single-stock 50% drawdown stress is one click in the Market Risk UI; the linear refresh model deliberately does *not* assume the employer writes a bigger next grant after a drawdown — a band-targeted top-up model would embed that behavioral assumption, which is why one isn't used here.
- **Index-fund returns of 7% nominal / 6% after-tax.** Forward-looking equity premiums in late 2025 / early 2026 are debated; some research argues for **4–5% real / 5–6% nominal** going forward. Monte Carlo at σ=15% with μ=5% (toggleable in the UI) tells the conservative story.
- **Healthcare costs in lean years.** $700/mo gross ACA premium is a planning placeholder; California marketplace silver-plan premiums for a single adult in their 30s have ranged $450–900/mo. Recheck Covered California pricing if a lean-year scenario is being seriously considered.
- **HCOL-metro property tax + insurance.** 1.1% effective property tax and $300/mo insurance are reasonable for an HCOL-metro CA home in 2026; **insurance especially has been climbing 10–20%/yr** in CA wildfire-exposed areas.
- **Maintenance defaults to 1%/yr of price** (the standard rule of thumb). This averages routine repairs ($) with amortized big-ticket items (roof, HVAC, foundation). For older homes or wildfire-exposed properties the realistic range is 1.5–2%. The model treats it as a smooth monthly expense; in reality it's lumpy (lean years where almost nothing happens, then a $30k roof). Set the knob higher to stress-test pessimistic-realistic scenarios.
