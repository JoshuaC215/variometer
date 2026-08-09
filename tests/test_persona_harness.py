"""The persona harness itself — discovery, metadata, ids, failure behavior.

These tests build throwaway persona bundles in tmp_path and pin them against
values computed on the spot, so they exercise ``variometer.testing`` without
duplicating any real pin. The real pins live in ``tests/personas/``.
"""

import json

import pytest

from variometer.cashflow import annual_summary
from variometer.config import Scenario
from variometer.testing import (
    PersonaError,
    load_persona,
    persona_params,
    run_persona_case,
)

_PERSONA_PY = """
from variometer.config import Scenario

REQUIRES_USER_DATA = False


def scenarios():
    return {"baseline": Scenario()}
"""


def _bundle(root, expected: dict, name: str = "demo"):
    """Write a persona bundle into ``root`` and return the personas dir."""
    directory = root / "personas" / name
    directory.mkdir(parents=True)
    (directory / "persona.py").write_text(_PERSONA_PY)
    (directory / "expected.json").write_text(json.dumps(expected))
    return root / "personas"


def _gross_2026() -> float:
    return round(annual_summary(Scenario(), 2026).gross_income, 2)


def test_cases_are_discovered_and_pass(tmp_path):
    personas = _bundle(
        tmp_path,
        {"baseline": {"annual_summary": {"2026": {"gross_income": _gross_2026()}}}},
    )
    cases = persona_params(personas)
    assert len(cases) == 1
    assert cases[0].id == "demo/baseline/annual_summary/2026/gross_income"
    run_persona_case(cases[0])


def test_underscore_keys_are_ignored_metadata(tmp_path):
    """Metadata at every level — persona, variant, check, year — makes no cases."""
    personas = _bundle(
        tmp_path,
        {
            "_persona": "notes about the persona",
            "baseline": {
                "_source": "notes about the variant",
                "annual_summary": {
                    "_citation": "notes about the check",
                    "2026": {"_why": "notes about the year", "gross_income": _gross_2026()},
                },
            },
        },
    )
    cases = persona_params(personas)
    assert [c.id for c in cases] == ["demo/baseline/annual_summary/2026/gross_income"]


def test_scalar_leaf_uses_the_check_default_field(tmp_path):
    """A bare number pins the check's default field and drops it from the id."""
    from variometer.projection import project_net_worth

    total_nw = round(project_net_worth(Scenario(), [2026])[0].total_nw, 2)
    personas = _bundle(tmp_path, {"baseline": {"net_worth": {"2026": total_nw}}})
    (case,) = persona_params(personas)
    assert case.id == "demo/baseline/net_worth/2026"
    assert case.field == "total_nw"
    run_persona_case(case)


def test_dotted_field_reaches_a_nested_component(tmp_path):
    """``fica.social_security`` pins one leg of a breakdown, no new check kind."""
    ss = round(annual_summary(Scenario(), 2026).fica.social_security, 2)
    personas = _bundle(
        tmp_path,
        {"baseline": {"annual_summary": {"2026": {"fica.social_security": ss}}}},
    )
    (case,) = persona_params(personas)
    assert case.id == "demo/baseline/annual_summary/2026/fica.social_security"
    run_persona_case(case)


def test_unresolvable_dotted_field_names_the_path(tmp_path):
    personas = _bundle(
        tmp_path,
        {"baseline": {"annual_summary": {"2026": {"fica.nonexistent": 1.0}}}},
    )
    (case,) = persona_params(personas)
    with pytest.raises(PersonaError, match="fica.nonexistent"):
        case.actual()


def test_failure_message_names_persona_variant_check_year_and_field(tmp_path):
    personas = _bundle(
        tmp_path,
        {"baseline": {"annual_summary": {"2026": {"gross_income": _gross_2026() + 5_000}}}},
    )
    (case,) = persona_params(personas)
    with pytest.raises(AssertionError) as exc:
        run_persona_case(case)
    message = str(exc.value)
    for fragment in [
        "demo",
        "baseline",
        "annual_summary",
        "2026",
        "gross_income",
        "repin_personas",
    ]:
        assert fragment in message


def test_tolerance_override_is_honored(tmp_path):
    personas = _bundle(
        tmp_path,
        {
            "baseline": {
                "annual_summary": {
                    "_tolerance": 10_000,
                    "2026": {"gross_income": _gross_2026() + 5_000},
                }
            }
        },
    )
    (case,) = persona_params(personas)
    assert case.tolerance == 10_000
    run_persona_case(case)  # inside the widened tolerance


def test_unknown_check_raises(tmp_path):
    personas = _bundle(tmp_path, {"baseline": {"vibes": {"2026": 1.0}}})
    with pytest.raises(PersonaError, match="unknown check 'vibes'"):
        persona_params(personas)


def test_variant_missing_from_persona_module_raises(tmp_path):
    personas = _bundle(tmp_path, {"nonexistent": {"net_worth": {"2026": 1.0}}})
    (case,) = persona_params(personas)
    with pytest.raises(PersonaError, match="pins variant 'nonexistent'"):
        case.actual()


def test_bundle_without_expected_json_raises(tmp_path):
    directory = tmp_path / "personas" / "broken"
    directory.mkdir(parents=True)
    (directory / "persona.py").write_text(_PERSONA_PY)
    with pytest.raises(PersonaError, match="no expected.json"):
        load_persona(directory)


def test_missing_personas_dir_yields_no_cases(tmp_path):
    assert persona_params(tmp_path / "nope") == []
