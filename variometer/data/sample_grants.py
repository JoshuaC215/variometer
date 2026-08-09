"""Illustrative RSU grant schedule for the placeholder persona.

A single new-hire grant: $480k over 4 years at the $100 placeholder share
price → 4,800 shares vesting 100/month, Jan 2026 – Dec 2029.
Exists only so a fresh checkout renders a plausible projection; real users
supply their actual schedule via ``user_data/grants.csv`` (one row per vest:
``grant_id,year,month,shares``), which replaces this entirely.
"""


def _flat_monthly(start_year: int, start_month: int, months: int, per_month: float):
    out: dict[tuple[int, int], float] = {}
    year, month = start_year, start_month
    for _ in range(months):
        out[(year, month)] = per_month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


SAMPLE_INITIAL_GRANT: dict[tuple[int, int], float] = _flat_monthly(2026, 1, 48, 100.0)

SAMPLE_GRANTS: dict[str, dict[tuple[int, int], float]] = {
    "Initial grant (sample)": SAMPLE_INITIAL_GRANT,
}
