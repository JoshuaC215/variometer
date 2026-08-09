# Demo: asking a what-if in English

This is a real session, lightly trimmed. It shows the loop Variometer is built
for: you ask a question in English, an agent writes a small scenario file, the
model answers with actual tax math, and the agent reads the result back to you.

Everything below was produced with the private layer switched off —

```bash
VARIOMETER_USER_DATA="" uv run variometer explain --python ad_hoc/demo_quit_2029.py
```

— so every dollar figure belongs to the **illustrative placeholder persona** (a
fictional mid-30s senior IC: $220k base, 15% bonus, $150k brokerage, $100k
pre-tax 401(k), $8.5k/mo spend). None of it is anyone's real money.

Trimmed sections are marked `[… trimmed …]`. Nothing else is edited.

---

## The question

> **What if I quit my job in June 2029, take two lean years at $5.5k/month,
> then take a lower-paying job in 2031?**

## What the agent does

No factory, no registration, no UI. It writes one file in `ad_hoc/` that
assigns a `Scenario` — this is
[`ad_hoc/demo_quit_2029.py`](../ad_hoc/demo_quit_2029.py), committed alongside
this doc:

```python
from variometer.config import base_scenario
from variometer.events import LifeEvent

s = base_scenario()

QUIT_YEAR, QUIT_MONTH = 2029, 6
LEAN_END_YEAR = 2030          # last lean year; normal budget resumes in 2031
NEW_JOB_YEAR = 2031

LEAN_MONTHLY = 5_500          # total monthly burn during the gap, 2026 real dollars
ACA_MONTHLY = 650             # marketplace premium before the tax credit
NEW_BASE = 165_000            # 2026 real dollars — a deliberate step down

s.events = [
    LifeEvent(year=QUIT_YEAR, month=QUIT_MONTH, kind="employment_end"),
    LifeEvent(year=QUIT_YEAR, month=QUIT_MONTH, kind="expense_level",
              params={"monthly": LEAN_MONTHLY, "label": "Lean years",
                      "ends_year": LEAN_END_YEAR}),
    LifeEvent(year=QUIT_YEAR, month=QUIT_MONTH, kind="expense_change",
              params={"delta_monthly": ACA_MONTHLY, "label": "ACA marketplace premium",
                      "aca": True, "ends_year": LEAN_END_YEAR}),
    LifeEvent(year=NEW_JOB_YEAR, month=1, kind="new_job",
              params={"base": NEW_BASE, "bonus_rate": 0.10, "equity_annual": 60_000,
                      "healthcare_monthly": 0, "match_rate": 0.50,
                      "match_cap": 5_000, "mega_backdoor": False}),
]

scenario = s   # `variometer explain --python` reads this variable
```

Four events, each a small composable patch on the baseline: employment ends
(salary, bonus, match and every unvested RSU stop), the budget drops for a
bounded window, an ACA premium appears with `aca=True` so the premium tax
credit nets against it, and a new job starts with its own comp shape.

Then it runs the model:

```console
$ uv run variometer explain --python ad_hoc/demo_quit_2029.py

  Quit 6/2029, $5,500/mo lean through 2030 (+$650/mo ACA), new job 2031 at $165,000 base (2026 $), 10% bonus, $60,000/yr equity.

Year      Income   Expenses    Surplus   Brokerage   Roth IRA  Roth 401k Pretax 401k   Total NW   Drawdown  Unfunded
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
2026       $191k      $102k       $89k       $243k        $8k       $23k       $139k      $413k                      —         —
2027       $209k      $105k      $103k       $357k       $16k       $48k       $182k      $602k (+$189k)             —         —
2028       $229k      $108k      $121k       $494k       $26k       $75k       $228k      $823k (+$220k)             —         —
2029       $149k       $93k       $56k       $573k      $135k         $0       $261k      $969k (+$147k)             —         —
2030         $2k       $82k      $-80k       $534k      $145k         $0       $279k      $958k ($-12k)           $80k         —
2031       $164k      $118k       $46k       $604k      $164k         $0       $326k     $1.09M (+$136k)             —         —
2032       $168k      $122k       $46k       $679k      $184k         $0       $377k     $1.24M (+$146k)             —         —
2033       $172k      $125k       $46k       $759k      $206k         $0       $431k     $1.40M (+$156k)             —         —
2034       $176k      $129k       $47k       $843k      $230k         $0       $491k     $1.56M (+$167k)             —         —
2035       $180k      $133k       $47k       $932k      $256k         $0       $555k     $1.74M (+$179k)             —         —
[… 2036–2044 trimmed …]
2045       $228k      $179k       $49k      $2.18M      $655k         $0      $1.56M     $4.39M (+$352k)             —         —

  Key milestones:
    NW in 2030:  $958k
    NW in 2035:  $1.74M
    NW in 2040:  $2.85M
    NW in 2045:  $4.39M
    Waterfall fires: 2030–2030 (1 years)
    ✓  No insolvency in window
```

## How the agent answers

Not a wall of numbers — the table is for checking its work. What comes back in
the conversation looks like this:

> **The plan holds.** The two-year gap costs one drawdown year, not the
> trajectory. Headlines:
>
> - **Only 2030 needs your portfolio.** 2029 mostly funds itself — five months
>   of salary plus the vests that land before the June cutoff bring in $149k
>   against a $93k year. In 2030 income is ~$2k, so the model pulls **$80k from
>   brokerage**; net worth dips $12k, then resumes climbing.
> - **Health insurance is nearly free in the gap.** With income near zero, the
>   ACA premium tax credit covers most of the $650/mo marketplace premium —
>   that's why 2030's expenses land at $82k, not $90k+.
> - **Your Roth money moves, it doesn't vanish.** At separation the Roth
>   401(k)/mega-backdoor balance (~$135k) rolls into the Roth IRA. That's
>   accessible principal sitting behind brokerage — the backup bucket if the
>   gap ran longer than planned.
> - **The real long-term cost is the lower savings rate, not the gap.** At the
>   new job, surplus settles around $46–49k/yr versus $121k in 2028. Even so,
>   compounding on a ~$1M base carries you to **$4.4M by 2045** with no
>   insolvency anywhere in the window.
> - **Total cost of the gap ≈ the $80k drawdown plus two forgone high-surplus
>   years.** Whether that trade is worth two years of your time is your call,
>   not the model's.
>
> Worth checking next: how it looks if the market drops 30% the year you quit
> (`--mc` and the stress paths), and how sensitive 2030 is to the lean budget —
> at $7k/mo the drawdown grows by roughly the difference.

Ask a follow-up and the loop repeats: another small file, another table,
diffable against this one.

## With your own numbers

Everything above ran on the placeholder persona. Once `user_data/profile.json`
and `grants.csv` exist, `base_scenario()` picks them up and the exact same file
answers the same question about *your* balances, comp, and vest schedule — no
edits. Personal explorations belong in `user_data/ad_hoc/` (gitignored) rather
than `ad_hoc/`:

```bash
uv run variometer explain --python user_data/ad_hoc/my_what_if.py --mc --ss-monthly 3500
```
