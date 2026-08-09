# Contributing

Thanks for looking. Variometer is a single-maintainer project, so this is short
and mostly about the two or three things that are easy to get wrong here.

## Dev setup

Python 3.11+, managed with [uv](https://docs.astral.sh/uv/). Everything runs
through `uv run` — don't use a bare `python`, `pip`, or `pytest`.

```bash
uv sync                  # install deps (idempotent)

uv run ruff check        # lint — must report 0 errors
uv run ruff format       # apply formatting
uv run pytest            # the public suite — 460+ tests, <5s
```

All four should be clean before you open a PR. `variometer/scenarios.py` is
excluded from `ruff format` on purpose: its column-aligned
`Annotated[..., Knob(...)]` declarations are meant to read as a config table.

Useful while working:

```bash
uv run variometer explain --list                        # scenarios you can name
uv run variometer explain sabbatical --start-year 2030  # eyeball a projection
uv run streamlit run variometer/app.py                  # the UI on :8501
```

## The one rule: no personal data in public files

The repo is a **public model plus a gitignored private layer**.

- Public — the `variometer/` package, `tests/`, `docs/`,
  `examples/`, `ad_hoc/`. The `Scenario` dataclass defaults are *illustrative
  placeholders*: a fictional senior IC. They are not anyone's real numbers, and
  they are pinned by the regression suite.
- Private — `user_data/` (gitignored): profile, grants, actuals, personal
  scenario factories, personal ad-hoc explorations.

If you are about to write a real dollar amount, balance, employer name, or
person's name into a committed file, stop — it belongs in `user_data/`. Public
test fixtures and docs use the placeholder persona only, and anything
regenerated on a machine that has a `user_data/` directory is built with the
private layer forced off so private numbers structurally cannot leak into a
committed file.

Don't "fix" a placeholder default to make a number look better, either. Defaults
are a pinned contract; deviations belong in a scenario or a `LifeEvent`.

## How the pins work

Two different kinds of pinned number live here, and they are not
interchangeable.

**Persona bundles** (`tests/personas/*/expected.json`) pin the model's own
output — self-consistency. They exist so a refactor that quietly moves a number
gets caught. When a change legitimately moves them, re-pin deliberately:

```bash
uv run python scripts/repin_personas.py --dry-run tests/personas/*
uv run python scripts/repin_personas.py tests/personas/<bundle>
```

The script only recomputes checks the bundle already pins — it never invents a
new one — and prints an old → new summary. **A re-pin must land in a commit
that says why the numbers moved**; a `HISTORY.md` next to the bundle is the
place for the long version (create one if it isn't there yet). If you can't
explain the move, it's a bug, not a re-pin.

**Golden cases** (`tests/test_golden_2026.py`, `tests/personas/golden_*`) are
the opposite: every expected value was hand-computed from a cited primary
source *before* the model was run against it. **Golden cases are never
re-pinned.** `repin_personas.py` must always report `0 moved` for them. If one
moves, the model now disagrees with a published figure — read the case's
`_`-prefixed arithmetic, re-derive it by hand, and fix whichever side is
actually wrong. See [docs/GOLDEN_CASES.md](docs/GOLDEN_CASES.md).

## Contributing a scenario

Read [docs/SCENARIOS.md](docs/SCENARIOS.md) first, and
`.claude/skills/manage-scenarios/SKILL.md` for the step-by-step recipe.

- **Public archetypes** go in `variometer/scenarios.py`. These are *shapes* —
  "sabbatical", "buy home" — parameterized by `Annotated[..., Knob(...)]`
  arguments so the UI renders their controls automatically. They must work
  sensibly against the placeholder persona.
- **Personal factories** (your specific exit year, your city, your price) go in
  `user_data/scenarios.py`. Same `@scenario` decorator, same registry, never
  committed.
- **One-off questions** need no factory at all — write a file in `ad_hoc/` and
  run it with `variometer explain --python`. See
  `ad_hoc/example_promo_house_fire.py` and [docs/DEMO.md](docs/DEMO.md).

Scenarios deviate from the baseline via `LifeEvent`s (see
`variometer/events.py`). Don't add a field to `Scenario` for something that's
only active in some scenarios.

## Pull requests

- Lint, format, and `uv run pytest` all clean.
- **Zero unexplained numeric drift.** If your diff moves a pinned number, say
  which number, why it moved, and why the new value is the correct one. A moved
  pin with no explanation will be read as a bug.
- **One concern per PR.** A refactor and a modeling change in the same diff make
  it impossible to tell which one moved a number.
- New behavior comes with a test. The suite is fast on purpose; keep it that
  way.
- Changes that widen the modeling envelope (a new filing status, a new state)
  are large by nature — open an issue to discuss shape before writing the code.

## Maintenance: the annual table refresh

Variometer is anchored at a **model epoch** (currently 2026): the year its tax
tables are pinned to, and the year all "real dollar" inputs are expressed in.
Years after the epoch are projections — the pinned constants inflated at ~3%/yr
— not published figures.

Each fall and winter, as the authorities publish, the epoch tables get
refreshed together:

| Table | Source, and when it lands |
| --- | --- |
| Federal ordinary brackets, standard deduction, LTCG ceilings | IRS Revenue Procedure, usually October |
| 401(k) / IRA contribution and annual-addition limits | IRS Notice, usually early November |
| Social Security wage base | SSA announcement, October |
| CA SDI rate | CA EDD, late in the year |
| ACA applicable-percentage table + FPL baseline | IRS Rev. Proc. (spring, for the following coverage year) + HHS poverty guidelines (January) |
| California brackets and standard deduction | FTB, typically the following year — the slowest of the set |

Bumping the epoch means swapping those constants, re-deriving each golden
case's arithmetic against the new tables, and re-pinning the regression
snapshots deliberately. The golden cases exist precisely to make that refresh
tractable and reviewable.

**In the gap**, the model keeps working and tells you what it's doing: post-epoch
years are ~3% inflation projections of the pinned epoch tables, and once the
epoch is more than a year stale, both the UI and the CLI print a warning saying
exactly how many years of assumed inflation are stacked on top of tables that
were never refreshed.

## Support posture

Best-effort review, no SLA, one maintainer. Issues and PRs are welcome and will
be read; a timely response, a merge, or any particular release cadence is not
guaranteed. If a change matters to you, a self-contained PR with tests is by
far the fastest path.

Nothing here is financial or tax advice — see the note in the
[README](README.md).
