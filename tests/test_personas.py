"""Pinned regressions for the public persona bundles in ``tests/personas/``.

One test per pinned number. The values live in each persona's
``expected.json``, the inputs in its ``persona.py``, and the machinery in
``variometer.testing`` — so a deliberate re-pin shows up as a reviewable data
diff instead of an edit buried in test code.

Re-pin with:  uv run python scripts/repin_personas.py tests/personas/<name>
"""

from pathlib import Path

import pytest

from variometer.testing import case_id, persona_params, run_persona_case

PERSONAS_DIR = Path(__file__).parent / "personas"


@pytest.mark.parametrize("case", persona_params(PERSONAS_DIR), ids=case_id)
def test_persona_pin(case):
    run_persona_case(case)
