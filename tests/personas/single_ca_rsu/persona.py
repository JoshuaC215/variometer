"""Persona: the placeholder senior IC baked into ``Scenario()`` defaults.

Single filer, California, salary + bonus + RSU refreshes, no house, no events.
This is the fictional persona the public test suite is deterministic against —
never anyone's real numbers. Its pins are what stop a refactor from silently
shifting the model: if a change moves these, it moved the model.

The bundle is loaded by ``tests/test_personas.py`` through
``variometer.testing``; ``REQUIRES_USER_DATA`` stays False, so the harness
builds the scenario with the private layer forced off.

Pin history
-----------
- ``net_worth`` 2026–2035 came from
  ``tests/test_withdrawals.py::TestFullYearScenarios::test_no_drawdown_regression_bit_identical``.
  Baseline's surplus stays positive across this window because projected
  refresh grants extend past 2030 — equity income doesn't fall off a cliff
  while employment continues.
- ``annual_summary`` 2026–2030 came from ``_SNAPSHOT`` in
  ``tests/test_events.py`` (the zero-events regression: an empty events list
  must reproduce the pre-events baseline exactly).
Both were migrated verbatim in the persona-harness workstream — values
unchanged, only their home moved.
"""

from variometer.config import Scenario

REQUIRES_USER_DATA = False


def scenarios() -> dict[str, Scenario]:
    return {"baseline": Scenario()}
