# Golden tax cases

Most of the pinned numbers in this repo prove **self-consistency**: the model
still produces what it produced yesterday, so a refactor that moves a number
gets caught. That is useful and it is not the same thing as being *right*.

The golden cases are the other half. Each expected value was **hand-computed
from a cited primary source and written down before the model was run against
it**. None of them is model output. If one moves, the model disagrees with a
published figure or with statute — which is a finding to investigate, not a
number to re-pin.

Where they live:

| | |
| --- | --- |
| `tests/test_golden_2026.py` | function-level anchors: bracket walks, LTCG rate ceilings, FICA legs, the §1411 threshold, the ACA applicable-percentage table, IRS contribution limits |
| `tests/personas/golden_2026_w2/` | end-to-end W-2 cases: standard-deduction pass-through, the §402(g) deferral cap, FICA composition through `annual_summary` |
| `tests/personas/golden_2026_investment/` | end-to-end wages + capital gain: LTCG stacking, NIIT in both binding regimes |

The persona bundles use the same harness as every other pin
([DESIGN.md § Regression invariants](DESIGN.md#regression-invariants)). Each
pinned value carries a `_`-prefixed sibling key holding its citation and the
hand arithmetic, so `expected.json` reads as the derivation, not as a table of
magic numbers.

## What "golden" claims

- **2026 federal quantities, hand-computed from the cited source.** Ordinary
  brackets, the standard deduction, LTCG rate ceilings, the Social Security
  wage base, the Additional Medicare and NIIT thresholds, the ACA
  applicable-percentage table and FPL baseline, and the 401(k)/IRA limits.
- **The arithmetic is shown.** Every case states the bracket walk or formula
  that produced its expected value, so a reviewer can check it against the
  source without re-running anything.
- **CA SDI is anchored** — it is a published flat rate (1.3%, uncapped), not a
  bracket table.

## What "golden" does NOT claim

- **California brackets and the CA standard deduction are excluded.** The 2026
  FTB tables are not published as of this writing, so `data/brackets.py`
  projects them from 2025 actuals at ~3%. No golden case pins `ca_taxable`,
  `ca_income_tax`, or anything downstream of them — which means `total_tax`,
  `take_home` and `effective_tax_rate` are deliberately absent from the golden
  bundles. Expect a small re-pin of the *ordinary* personas when the FTB
  publishes.
- **Post-2026 years are projections, not anchors.** Every golden case pins
  2026 and only 2026. Later years multiply the epoch constants by assumed
  inflation (`bracket_inflation`, `deduction_inflation`, `limit_inflation`) —
  a modeling assumption, not a published figure.
- **The ACA applicable-percentage table is held flat after 2026.** The IRS
  re-indexes it annually by a premium-growth / income-growth ratio that does
  not track general inflation, so projecting it forward would be spurious
  precision. Only the 2026 table is anchored.
- **There are no official IRS worked examples for tax year 2026.** Those don't
  exist until the instructions and tax tables publish in early 2027. The
  anchors here are therefore the Rev. Proc. / Notice figures and the statute
  itself, cited as such — not IRS-computed example returns.
- **These are not a full audit of the model.** They anchor the tax primitives
  and their composition through `annual_summary`. Housing, drawdown ordering,
  Roth seasoning, equity vesting and the projection engine are covered by the
  ordinary test suite, not by external anchors.
- **Nothing here is tax advice.** See the disclaimer in the README.

## Sources

The maintainer verifies each of these against its source before a release. Nothing on
this list is taken second-hand from a calculator or an article.

| Source | Used for |
| --- | --- |
| [IRS Rev. Proc. 2025-32](https://www.irs.gov/pub/irs-drop/rp-25-32.pdf) | 2026 single ordinary brackets (10% to 12,400 / 12% to 50,400 / 22% to 105,700 / 24% to 201,775 / 32% to 256,225 / 35% to 640,600 / 37% above); standard deduction $16,100; LTCG ceilings — 0% top $49,450, 15% top $545,500 |
| [IRS Notice 2025-67](https://www.irs.gov/pub/irs-drop/n-25-67.pdf) | 2026 §402(g) elective deferral $24,500; §415(c) annual additions $72,000; IRA $7,500 |
| [IRS Rev. Proc. 2025-25 §3.01](https://www.irs.gov/pub/irs-drop/rp-25-25.pdf) | 2026 ACA Applicable Percentage Table: <133% FPL 2.10 flat; 133–150% 3.14→4.19; 150–200% 4.19→6.60; 200–250% 6.60→8.44; 250–300% 8.44→9.96; 300–400% 9.96 flat; >400% no credit |
| SSA announcement, 2025-10-24 | 2026 Social Security wage base $184,500; maximum employee SS tax $11,439 (6.2%) |
| [CA EDD 2026 rates](https://edd.ca.gov/en/payroll_taxes/rates_and_withholding) | SDI 1.3%, uncapped |
| [HHS 2025 Poverty Guidelines](https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines) | FPL for the 2026 coverage year: $15,650 first person + $5,500 each additional (48 states + DC) |
| IRC §1411 | NIIT: 3.8% × min(net investment income, MAGI − $200,000 single). Statutory, unindexed |
| IRC §3101(b)(2) | Additional Medicare Tax: 0.9% on wages over $200,000 single. Statutory, unindexed |

## Working with them

Run just the golden cases:

```bash
uv run pytest tests/test_golden_2026.py
uv run pytest tests/test_personas.py -k golden
```

**Do not re-pin a golden case.** `scripts/repin_personas.py` will happily
rewrite the golden bundles because it treats every `expected.json` alike — it
must always report `0 moved` for them. If it reports a move, something in the
model changed such that it no longer matches a cited source. Read the failing
case's `_`-prefixed arithmetic, re-derive it by hand, and fix whichever side is
actually wrong.

Adding a case? Compute the expected value from the source first, write the
arithmetic into the `arithmetic=` field (or the `_`-prefixed metadata key),
*then* run it. A value copied out of model output and captioned with a citation
is a pin wearing a costume.

## Annual refresh

When `MODEL_EPOCH` advances, these cases are the reason the refresh is
tractable: swap the constants in `data/brackets.py`, `tax.py` and
`retirement.py` for the new Rev. Proc. / Notice figures, redo each case's
arithmetic against the new tables, and update the expected values *and* their
stated arithmetic together. See
[ASSUMPTIONS_AND_RISKS.md](ASSUMPTIONS_AND_RISKS.md) for the surrounding
checklist.
