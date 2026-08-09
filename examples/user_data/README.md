# user_data/ templates

Copy this directory to the repo root as `user_data/` and edit each file with
your own numbers:

```bash
cp -r examples/user_data user_data
```

`user_data/` is gitignored — nothing you put there can end up in a commit.

| File | What it is | Required? |
|---|---|---|
| `profile.json` | Scalar `Scenario` overrides — your true baseline | The one that matters |
| `grants.csv` | Your actual RSU vest schedule (one row per vest date) | If you have equity |
| `scenarios.py` | Your own scenario factories (auto-registered in the UI) | Optional |
| `historical.csv` | Monthly actuals — enables the app's Historical tab | Optional |

## profile.json

Every key must be a `Scenario` field name — see `variometer/config.py`
for the full annotated list. Unknown keys and wrong types are **hard errors**
(typo protection). Keys starting with `_` are ignored, so you can leave
yourself notes. Dates are ISO strings (`"2026-01-02"`).

Only include fields where your reality differs from the placeholder defaults;
everything you omit keeps the default from `config.py`.

One underscore key is special: `"_ui"` holds display preferences (not model
inputs). Currently just one:

```json
"_ui": {"hide_builtin_scenarios": true}
```

hides the built-in archetypes from the scenario dropdowns and
`variometer explain --list` once you have scenarios of your own (they still run by
name, and the toggle is ignored while `scenarios.py` is absent — you'd have
an empty list otherwise).

## grants.csv

One row per vest date per grant: `grant_id,year,month,shares`. `grant_id` is
the label the Vest Schedule tab groups by. Export the schedule from your
equity portal. When this file exists it **replaces** the illustrative sample
schedule entirely; projected future refreshes still come from the
`equity_refresh_*` fields in your profile.

## scenarios.py

Personal scenario factories using the same `@scenario` decorator as the
public library — see `docs/SCENARIOS.md`. Display names must not collide
with the built-ins.

Your scenarios always list first — in the app's dropdowns and in
`variometer explain --list` — with the built-in archetypes labeled and grouped after
them (or hidden entirely via the `"_ui"` pref above).

## historical.csv

Monthly actuals: `year,month,total_income,paychecks,other_income,expenses`.
`paychecks` and `other_income` may be left blank. Its presence enables the
📜 Historical tab. The `bootstrap-from-monarch` skill can auto-populate it.
