"""Persona fixture harness — pinned regressions as reviewable data.

A *persona bundle* is a directory holding one modeled person and the numbers
their model is pinned to::

    personas/single_ca_rsu/
        persona.py      # the input spec  (behavior is Python)
        expected.json   # the pinned numbers (facts are data)

``persona.py`` defines ``scenarios() -> dict[str, Scenario]`` mapping a variant
name to the Scenario to project, and may set ``REQUIRES_USER_DATA = True`` when
it builds on the private layer (``base_scenario()`` / personal factories). It
must not build scenarios at import time — the harness imports the module first,
reads the flag, and only then calls ``scenarios()`` under the right environment.

``expected.json`` is keyed variant → check → year → value::

    {
      "baseline": {
        "net_worth":       {"2026": 413797.56, "2027": 604177.40},
        "annual_summary":  {"2026": {"gross_income": 375000.00,
                                     "total_tax":    136401.01,
                                     "take_home":    192178.99}}
      }
    }

A year's value is either a bare number (checked against the check's default
field) or an object of ``attribute name -> expected value``. An attribute name
may be a **dotted path** into a nested component (``"fica.social_security"``,
``"contributions.pretax"``), so a pin can reach one leg of a breakdown without
needing its own check kind. **Any key starting with an underscore is ignored
metadata** at every level — persona, variant, check and year — which is how a
pin carries its citation or provenance note alongside the number it pins.

Consuming it from a test file is three lines::

    from variometer.testing import case_id, persona_params, run_persona_case

    @pytest.mark.parametrize("case", persona_params(PERSONAS_DIR), ids=case_id)
    def test_persona_pin(case):
        run_persona_case(case)

which yields one test per pinned number, with ids like
``single_ca_rsu/baseline/net_worth/2028``.

Re-pinning is deliberate and reviewable: ``scripts/repin_personas.py`` rewrites
the numbers already present in an ``expected.json`` and prints what moved, so
the diff of a model change is a data diff.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from variometer.cashflow import annual_summary
from variometer.config import MODEL_EPOCH, Scenario
from variometer.projection import project_net_worth

PERSONA_MODULE_FILE = "persona.py"
PERSONA_EXPECTED_FILE = "expected.json"

__all__ = [
    "CHECKS",
    "Check",
    "Persona",
    "PersonaCase",
    "PersonaError",
    "attr_path",
    "case_id",
    "clear_user_data_caches",
    "discover_personas",
    "load_persona",
    "persona_cases",
    "persona_params",
    "run_persona_case",
    "user_data_disabled",
]


class PersonaError(Exception):
    """A persona bundle is malformed or references an unknown check."""


# ---------------------------------------------------------------------------
# Environment discipline
# ---------------------------------------------------------------------------


def clear_user_data_caches() -> None:
    """Drop every memoized read of the private layer.

    The loaders are ``lru_cache``d, so anything that changes which user_data
    directory is active must clear them or it will keep seeing the old one.
    """
    from variometer.user_data_io import (
        load_historical,
        load_profile,
        load_ui_prefs,
        load_vest_grants,
    )

    load_historical.cache_clear()
    load_profile.cache_clear()
    load_ui_prefs.cache_clear()
    load_vest_grants.cache_clear()


@contextmanager
def user_data_disabled():
    """Run a block with the private layer forced off.

    Used for personas that declare ``REQUIRES_USER_DATA = False`` so that
    building or re-pinning a *public* persona on a machine that has a populated
    ``user_data/`` can never bake private numbers into a committed file.
    """
    previous = os.environ.get("VARIOMETER_USER_DATA")
    os.environ["VARIOMETER_USER_DATA"] = ""
    clear_user_data_caches()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("VARIOMETER_USER_DATA", None)
        else:
            os.environ["VARIOMETER_USER_DATA"] = previous
        clear_user_data_caches()


def user_data_active() -> bool:
    """True when the private layer resolves to a real directory."""
    from variometer.user_data_io import user_data_dir

    try:
        return user_data_dir() is not None
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------------------
# Checks — the kinds of quantity a persona can pin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """One kind of pinnable quantity.

    ``compute`` maps a Scenario plus the pinned years to ``{year: object}``;
    ``extract`` pulls a named attribute off that object. Adding a new check
    kind (a tax case, a withdrawal ladder) is a matter of registering another
    Check — the discovery, ids, tolerances and failure messages come free.
    """

    name: str
    default_field: str
    tolerance: float
    compute: Callable[[Scenario, Sequence[int]], dict[int, Any]]
    extract: Callable[[Any, str], float]


def attr_path(obj: Any, path: str) -> Any:
    """Resolve a (possibly dotted) attribute path off a computed result object.

    A plain name behaves exactly like ``getattr``; the dotted form walks into
    nested components — ``fica.social_security``, ``contributions.pretax`` — so
    a persona can pin one leg of a breakdown without a bespoke check kind.
    """
    current = obj
    for part in path.split("."):
        try:
            current = getattr(current, part)
        except AttributeError as exc:
            raise PersonaError(
                f"{type(obj).__name__} has no field {path!r} (failed resolving {part!r})"
            ) from exc
    return current


def _compute_net_worth(scenario: Scenario, years: Sequence[int]) -> dict[int, Any]:
    """Project from MODEL_EPOCH through the last pinned year.

    The horizon starts at the epoch (not at the first pinned year) because the
    projection is path-dependent: a spot pin on 2050 has to reproduce the same
    path that produced it, whether or not the intervening years are pinned.
    """
    horizon = list(range(MODEL_EPOCH, max(years) + 1))
    return {row.year: row for row in project_net_worth(scenario, horizon)}


def _compute_annual_summary(scenario: Scenario, years: Sequence[int]) -> dict[int, Any]:
    return {year: annual_summary(scenario, year) for year in years}


CHECKS: dict[str, Check] = {
    "net_worth": Check(
        name="net_worth",
        default_field="total_nw",
        tolerance=1.0,
        compute=_compute_net_worth,
        extract=attr_path,
    ),
    "annual_summary": Check(
        name="annual_summary",
        default_field="gross_income",
        tolerance=1.0,
        compute=_compute_annual_summary,
        extract=attr_path,
    ),
}


# ---------------------------------------------------------------------------
# Persona bundles
# ---------------------------------------------------------------------------


def _public_keys(mapping: dict[str, Any], where: str) -> list[str]:
    """Keys of a mapping with ``_``-prefixed metadata dropped."""
    if not isinstance(mapping, dict):
        raise PersonaError(f"{where}: expected an object, got {type(mapping).__name__}")
    return [k for k in mapping if not k.startswith("_")]


class Persona:
    """A loaded persona bundle: its scenarios and its pinned expectations."""

    def __init__(self, name: str, path: Path, module: ModuleType, expected: dict[str, Any]):
        self.name = name
        self.path = path
        self.module = module
        self.expected = expected
        self._scenarios: dict[str, Scenario] | None = None
        self._results: dict[tuple[str, str], dict[int, Any]] = {}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Persona {self.name} at {self.path}>"

    @property
    def requires_user_data(self) -> bool:
        return bool(getattr(self.module, "REQUIRES_USER_DATA", False))

    def scenarios(self) -> dict[str, Scenario]:
        """Build (once) the persona's variant → Scenario mapping."""
        if self._scenarios is None:
            builder = getattr(self.module, "scenarios", None)
            if not callable(builder):
                raise PersonaError(f"{self.path / PERSONA_MODULE_FILE}: no scenarios() function")
            if self.requires_user_data:
                if not user_data_active():
                    raise PersonaError(
                        f"persona {self.name!r} declares REQUIRES_USER_DATA but the private "
                        f"layer is disabled (VARIOMETER_USER_DATA='')"
                    )
                clear_user_data_caches()
                built = builder()
            else:
                with user_data_disabled():
                    built = builder()
            if not isinstance(built, dict) or not built:
                raise PersonaError(
                    f"persona {self.name!r}: scenarios() must return a non-empty dict"
                )
            self._scenarios = built
        return self._scenarios

    def scenario(self, variant: str) -> Scenario:
        built = self.scenarios()
        if variant not in built:
            raise PersonaError(
                f"persona {self.name!r}: expected.json pins variant {variant!r} but "
                f"scenarios() returns {sorted(built)}"
            )
        return built[variant]

    def results(self, variant: str, check: str) -> dict[int, Any]:
        """Computed objects for one (variant, check), memoized per persona."""
        key = (variant, check)
        if key not in self._results:
            spec = CHECKS.get(check)
            if spec is None:
                raise PersonaError(
                    f"persona {self.name!r}: unknown check {check!r} (known: {sorted(CHECKS)})"
                )
            years = sorted(int(y) for y in _public_keys(self.expected[variant][check], check))
            scenario = self.scenario(variant)
            if self.requires_user_data:
                self._results[key] = spec.compute(scenario, years)
            else:
                with user_data_disabled():
                    self._results[key] = spec.compute(scenario, years)
        return self._results[key]


def _module_name_for(directory: Path) -> str:
    digest = hashlib.sha1(str(directory).encode()).hexdigest()[:8]
    return f"variometer_persona_{directory.name}_{digest}"


_PERSONA_CACHE: dict[Path, Persona] = {}


def load_persona(directory: str | Path) -> Persona:
    """Load one persona bundle directory (memoized by resolved path)."""
    directory = Path(directory).resolve()
    if directory in _PERSONA_CACHE:
        return _PERSONA_CACHE[directory]

    module_path = directory / PERSONA_MODULE_FILE
    expected_path = directory / PERSONA_EXPECTED_FILE
    if not module_path.exists():
        raise PersonaError(f"persona bundle {directory} has no {PERSONA_MODULE_FILE}")
    if not expected_path.exists():
        raise PersonaError(f"persona bundle {directory} has no {PERSONA_EXPECTED_FILE}")

    try:
        expected = json.loads(expected_path.read_text())
    except json.JSONDecodeError as exc:
        raise PersonaError(f"{expected_path}: invalid JSON — {exc}") from exc

    mod_name = _module_name_for(directory)
    spec = importlib.util.spec_from_file_location(mod_name, module_path)
    if spec is None or spec.loader is None:
        raise PersonaError(f"cannot import {module_path} as a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[mod_name]
        raise

    persona = Persona(directory.name, directory, module, expected)
    _PERSONA_CACHE[directory] = persona
    return persona


def discover_personas(personas_dir: str | Path) -> list[Persona]:
    """Load every persona bundle directly under ``personas_dir``, name-sorted."""
    root = Path(personas_dir)
    if not root.is_dir():
        return []
    found = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / PERSONA_MODULE_FILE).exists():
            found.append(load_persona(child))
    return found


# ---------------------------------------------------------------------------
# Cases — one pinned number each
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class PersonaCase:
    """One pinned number: persona / variant / check / year / field."""

    persona: Persona
    variant: str
    check: str
    year: int
    field: str
    expected: float
    tolerance: float
    named_field: bool = False
    year_key: str = ""  # the literal key in expected.json, for write-back

    @property
    def id(self) -> str:
        parts = [self.persona.name, self.variant, self.check, str(self.year)]
        if self.named_field:
            parts.append(self.field)
        return "/".join(parts)

    def __str__(self) -> str:
        return self.id

    def actual(self) -> float:
        spec = CHECKS[self.check]
        results = self.persona.results(self.variant, self.check)
        if self.year not in results:
            raise PersonaError(
                f"{self.id}: {self.check} produced no row for {self.year} (got {sorted(results)})"
            )
        return float(spec.extract(results[self.year], self.field))


def persona_cases(persona: Persona) -> list[PersonaCase]:
    """Every pinned number in one persona's expected.json, in file order."""
    cases: list[PersonaCase] = []
    for variant in _public_keys(persona.expected, f"{persona.name}/expected.json"):
        variant_block = persona.expected[variant]
        for check in _public_keys(variant_block, f"{persona.name}/{variant}"):
            spec = CHECKS.get(check)
            if spec is None:
                raise PersonaError(
                    f"{persona.name}/{variant}: unknown check {check!r} (known: {sorted(CHECKS)})"
                )
            check_block = variant_block[check]
            tolerance = float(check_block.get("_tolerance", spec.tolerance))
            for year_key in _public_keys(check_block, f"{persona.name}/{variant}/{check}"):
                year = int(year_key)
                leaf = check_block[year_key]
                if isinstance(leaf, dict):
                    for field_name in _public_keys(
                        leaf, f"{persona.name}/{variant}/{check}/{year_key}"
                    ):
                        cases.append(
                            PersonaCase(
                                persona=persona,
                                variant=variant,
                                check=check,
                                year=year,
                                field=field_name,
                                expected=float(leaf[field_name]),
                                tolerance=tolerance,
                                named_field=True,
                                year_key=year_key,
                            )
                        )
                else:
                    cases.append(
                        PersonaCase(
                            persona=persona,
                            variant=variant,
                            check=check,
                            year=year,
                            field=spec.default_field,
                            expected=float(leaf),
                            tolerance=tolerance,
                            year_key=year_key,
                        )
                    )
    return cases


def persona_params(personas_dir: str | Path) -> list[PersonaCase]:
    """Every pinned number under ``personas_dir``, one PersonaCase each.

    Personas that declare ``REQUIRES_USER_DATA = True`` are skipped when the
    private layer is switched off, so a public run never trips over them.
    """
    cases: list[PersonaCase] = []
    for persona in discover_personas(personas_dir):
        if persona.requires_user_data and not user_data_active():
            continue
        cases.extend(persona_cases(persona))
    return cases


def case_id(case: PersonaCase) -> str:
    """pytest id for a case: ``persona/variant/check/year[/field]``."""
    return case.id


def run_persona_case(case: PersonaCase) -> None:
    """Assert one pinned number, with a failure message that names everything."""
    got = case.actual()
    if abs(got - case.expected) > case.tolerance:
        raise AssertionError(
            f"{case.id}: {case.check}.{case.field} for {case.year} — "
            f"expected {case.expected:,.2f}, got {got:,.2f} "
            f"(drift {got - case.expected:+,.2f}, tolerance ±{case.tolerance:,.2f})\n"
            f"  persona: {case.persona.path}\n"
            f"  If this move is intended, re-pin deliberately:\n"
            f"    uv run python scripts/repin_personas.py {case.persona.path}"
        )
