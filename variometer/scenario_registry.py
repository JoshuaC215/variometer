"""Scenario registry — machinery for defining and introspecting named scenarios.

This file is the *framework*. It rarely changes. The actual scenarios live in
``scenarios.py`` and read like a config — that's the file you edit when adding
or tweaking a scenario.

What lives here
---------------
- ``Knob``     — UI metadata you attach to a factory parameter via
                 ``Annotated[type, Knob(...)]``.
- ``BoundKnob``— a ``Knob`` plus the live default pulled from the factory
                 signature (single source of truth — drift is impossible).
- ``@scenario("Display name")`` — decorator that registers a factory in
                 ``SCENARIO_REGISTRY``.
- ``SCENARIO_REGISTRY`` — display name → factory function.
- ``knobs_for(factory)`` / ``knobs_for_scenario(name)`` — introspection;
                 returns ``BoundKnob`` for each annotated parameter.

How the UI consumes it
----------------------
``app.py`` does ``for knob in knobs_for_scenario(name): render_widget(knob)``.
Adding a new scenario or knob never requires editing the app — the schema is
the factory signature.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from .config import Scenario


# ---------------------------------------------------------------------------
# Knob descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Knob:
    """UI metadata for one factory parameter.

    Pair with the parameter via ``Annotated[T, Knob(...)]``. The Streamlit app
    renders a widget chosen by ``kind`` (auto-inferred from the annotated type
    when omitted). The parameter's default is read from the function signature —
    there is no separate ``default`` field, so defaults cannot drift.

    Attributes
    ----------
    label:    Human-readable label shown in the UI.
    kind:     "int" | "float" | "dollars" | "percent" | "bool" | "choice" | "text".
              Optional; inferred from the annotated type if omitted. "dollars"
              and "percent" must be set explicitly because they're both ``float``
              to Python.
    min:      Optional lower bound (numeric kinds only).
    max:      Optional upper bound (numeric kinds only).
    step:     Optional step / increment (numeric kinds only).
    choices:  Required when ``kind == "choice"`` — sequence of options.
    help:     Optional tooltip text.
    """

    label: str
    kind: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple | None = None
    help: str | None = None


@dataclass(frozen=True)
class BoundKnob:
    """A ``Knob`` plus the live default from the factory signature."""

    name: str  # parameter name (also the factory kwarg)
    default: Any  # live default from the factory signature
    label: str
    kind: str
    min: float | None
    max: float | None
    step: float | None
    choices: tuple | None
    help: str | None


# ---------------------------------------------------------------------------
# Registry + decorator
# ---------------------------------------------------------------------------

SCENARIO_REGISTRY: dict[str, Callable[..., Scenario]] = {}


def scenario(display_name: str) -> Callable[[Callable[..., Scenario]], Callable[..., Scenario]]:
    """Decorator: register a factory under ``display_name`` in SCENARIO_REGISTRY.

    Use exactly once per scenario factory. The display name is what shows up
    in the UI dropdowns and what ``SCENARIO_REGISTRY[name]`` resolves.
    """

    def _wrap(fn: Callable[..., Scenario]) -> Callable[..., Scenario]:
        # Strict duplicate check catches accidental copy-paste at import time.
        # If this ever bites during Streamlit hot-reload (module re-imported
        # in the same process), switch to overwrite-with-warning here.
        if display_name in SCENARIO_REGISTRY:
            raise ValueError(f"Scenario {display_name!r} already registered")
        SCENARIO_REGISTRY[display_name] = fn
        return fn

    return _wrap


# ---------------------------------------------------------------------------
# Origin grouping — built-in archetypes vs user scenarios
# ---------------------------------------------------------------------------

_BUILTIN_SCENARIO_MODULE = "variometer.scenarios"


def is_builtin_scenario(display_name: str) -> bool:
    """True when the factory lives in the public archetype library.

    Origin is derived from the factory's ``__module__``: user factories load
    from ``user_data/scenarios.py`` under a different module name, so no
    registration-time bookkeeping is needed and the answer survives Streamlit
    hot-reload (where re-registration order can shuffle).
    """
    return SCENARIO_REGISTRY[display_name].__module__ == _BUILTIN_SCENARIO_MODULE


def grouped_scenario_names() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Registry display names split into ``(user, builtin)``, registration order."""
    user: list[str] = []
    builtin: list[str] = []
    for name in SCENARIO_REGISTRY:
        (builtin if is_builtin_scenario(name) else user).append(name)
    return tuple(user), tuple(builtin)


def visible_scenario_names(hide_builtins: bool = False) -> tuple[str, ...]:
    """Display names for listings: user scenarios first, then built-ins.

    ``hide_builtins`` drops the built-in archetypes, but only when at least
    one user scenario exists — the toggle can never produce an empty list.
    Hiding affects listings only; a hidden scenario still resolves by name.
    """
    user, builtin = grouped_scenario_names()
    if hide_builtins and user:
        return user
    return user + builtin


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def _infer_kind(annotation: Any) -> str:
    """Best-effort kind inference from a parameter's type annotation."""
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is str:
        return "text"
    return "text"


def _extract_knob(resolved_annotation: Any) -> Knob | None:
    """Return the ``Knob`` metadata attached via ``Annotated``, or None."""
    if typing.get_origin(resolved_annotation) is not Annotated:
        return None
    for meta in typing.get_args(resolved_annotation)[1:]:
        if isinstance(meta, Knob):
            return meta
    return None


def _base_type(resolved_annotation: Any) -> Any:
    """Strip ``Annotated`` wrapper and return the underlying type."""
    if typing.get_origin(resolved_annotation) is Annotated:
        return typing.get_args(resolved_annotation)[0]
    return resolved_annotation


def knobs_for(factory: Callable[..., Scenario]) -> tuple[BoundKnob, ...]:
    """Walk the factory signature; return ``BoundKnob`` for every annotated param.

    Unannotated parameters are skipped — useful for hiding a parameter from
    the UI without removing it from the factory signature.

    Uses ``typing.get_type_hints(include_extras=True)`` so ``Annotated``
    metadata survives ``from __future__ import annotations`` lazy evaluation.
    """
    sig = inspect.signature(factory)
    # Narrow except: NameError / TypeError cover the realistic failure modes
    # (unresolved forward reference, malformed annotation). Anything broader
    # would swallow real bugs and silently drop the scenario's knobs from the
    # UI. We also emit a warning so dev workflows surface it loudly.
    try:
        hints = typing.get_type_hints(factory, include_extras=True)
    except (NameError, TypeError) as e:
        import warnings

        warnings.warn(
            f"knobs_for({factory.__qualname__}): type hint resolution failed "
            f"({type(e).__name__}: {e}); scenario will render with zero knobs.",
            stacklevel=2,
        )
        hints = {}
    out: list[BoundKnob] = []
    for name, p in sig.parameters.items():
        resolved = hints.get(name, p.annotation)
        knob = _extract_knob(resolved)
        if knob is None:
            continue
        kind = knob.kind or _infer_kind(_base_type(resolved))
        out.append(
            BoundKnob(
                name=name,
                default=p.default,
                label=knob.label,
                kind=kind,
                min=knob.min,
                max=knob.max,
                step=knob.step,
                choices=knob.choices,
                help=knob.help,
            )
        )
    return tuple(out)


def knobs_for_scenario(display_name: str) -> tuple[BoundKnob, ...]:
    """Convenience: introspect the factory registered under ``display_name``."""
    return knobs_for(SCENARIO_REGISTRY[display_name])


# ---------------------------------------------------------------------------
# Core overrides — sidebar-as-base-defaults overlay
# ---------------------------------------------------------------------------


def apply_core_overrides(scenario: Scenario, overrides: dict[str, Any]) -> Scenario:
    """Overlay sidebar-managed ``Scenario`` defaults onto a factory output.

    The Streamlit sidebar collects user-tweaked values for the "global" model
    inputs (return rate, employer stock price, base salary, expenses, …). Each value
    corresponds 1:1 to a ``Scenario`` field. We want those to propagate into
    every named scenario the user picks — *unless* the scenario itself
    intentionally set that field (e.g. ``sabbatical`` setting
    ``healthcare_monthly`` for the gap-year ACA premium).

    Rule: for each (key, value) in ``overrides``, if the factory output's
    current value still matches the ``base_scenario()`` default (bare
    ``Scenario()`` + the user's profile overlay), copy the sidebar value over
    the top; otherwise leave it alone.

    The default snapshot is taken **fresh on every call** rather than cached
    at import time, so it survives Streamlit's hot-reload cycle during
    development where ``Scenario`` dataclass defaults may have been edited.
    Constructing a default ``Scenario`` is cheap.

    Mutates and returns the same ``scenario`` object for chaining.
    """
    from .config import base_scenario as _base  # local import to avoid cycle

    default = _base()
    for key, value in overrides.items():
        if not hasattr(scenario, key):
            raise AttributeError(f"core override {key!r} not a Scenario field")
        if getattr(scenario, key) == getattr(default, key):
            setattr(scenario, key, value)
    # ``brokerage_cost_basis`` is auto-derived from balance × fraction in
    # __post_init__; if the sidebar changed either, recompute so the override
    # actually takes effect for the projection.
    if {"brokerage_balance", "brokerage_cost_basis_fraction"} & overrides.keys():
        scenario.brokerage_cost_basis = (
            scenario.brokerage_balance * scenario.brokerage_cost_basis_fraction
        )
    return scenario
