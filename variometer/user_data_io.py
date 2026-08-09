"""I/O for the gitignored ``user_data/`` private layer.

The public repo carries illustrative placeholder defaults; everything personal
lives in ``user_data/`` next to the repo root:

    user_data/
      profile.json      scalar Scenario overrides — the user's true baseline
      grants.csv        actual RSU vest schedule (grant_id,year,month,shares)
      scenarios.py      personal scenario factories (@scenario-decorated)
      historical.csv    monthly actuals (gates the Historical tab)

This module is the only place that knows where those files live and how to
parse them. ``config.base_scenario()`` is the consumer — everything else in
the model stays a pure function of ``Scenario``.

Facts are data files (JSON/CSV — easy for humans, agents, and scripts);
behavior (scenario factories) is Python.

Env override: ``VARIOMETER_USER_DATA=/path/to/dir`` points at an
alternate user_data directory; the empty string disables user-data loading
entirely (used by the public test suite so tests are deterministic
placeholder math).
"""

from __future__ import annotations

import csv
import json
import os
import types
import typing
from dataclasses import fields as dataclass_fields
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

# Repo root = parent of the variometer package directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Scenario fields that cannot be expressed in profile.json. Complex/derived
# fields come from other sources (grants.csv, scenario factories) or are
# recomputed (__post_init__ derives brokerage_cost_basis).
_NON_PROFILE_FIELDS = frozenset({"events", "summary", "vest_grants"})


def user_data_dir() -> Path | None:
    """Resolve the active user_data directory, honoring the env override.

    Returns None when user-data loading is disabled (env set to ''). An
    explicit env path that doesn't exist is an error — a silently ignored
    typo'd path would be worse.
    """
    env = os.environ.get("VARIOMETER_USER_DATA")
    if env is not None:
        if env == "":
            return None
        p = Path(env)
        if not p.is_dir():
            raise FileNotFoundError(f"VARIOMETER_USER_DATA points at missing dir: {p}")
        return p
    return _REPO_ROOT / "user_data"


def _user_file(name: str) -> Path | None:
    d = user_data_dir()
    if d is None:
        return None
    p = d / name
    return p if p.exists() else None


def profile_path() -> Path | None:
    return _user_file("profile.json")


def _coerce(name: str, value: Any, target: Any) -> Any:
    """Coerce a JSON value to a Scenario field type; raise on mismatch."""
    origin = typing.get_origin(target)

    # Optional[X] / X | None — unwrap; None passes through.
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(target) if a is not type(None)]
        if value is None:
            return None
        return _coerce(name, value, args[0])

    if target is date:
        if isinstance(value, str):
            return date.fromisoformat(value)
        raise TypeError(f"profile field {name!r}: expected ISO date string, got {value!r}")
    if target is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise TypeError(f"profile field {name!r}: expected number, got {value!r}")
    if target is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise TypeError(f"profile field {name!r}: expected integer, got {value!r}")
    if target is bool:
        if isinstance(value, bool):
            return value
        raise TypeError(f"profile field {name!r}: expected boolean, got {value!r}")
    if target is str:
        if isinstance(value, str):
            return value
        raise TypeError(f"profile field {name!r}: expected string, got {value!r}")
    if origin is dict:
        key_t, val_t = typing.get_args(target)
        if not isinstance(value, dict):
            raise TypeError(f"profile field {name!r}: expected object, got {value!r}")
        # JSON object keys are strings; coerce to the declared key type.
        return {key_t(k): _coerce(f"{name}[{k}]", v, val_t) for k, v in value.items()}
    raise TypeError(f"profile field {name!r}: unsupported field type {target!r}")


@lru_cache(maxsize=1)
def load_profile() -> dict[str, Any]:
    """Parse and validate profile.json → {Scenario field: coerced value}.

    Unknown field names are a hard error (typo protection). Values are
    type-checked against the Scenario dataclass annotations. Returns {} when
    no profile is active. Cached — call load_profile.cache_clear() after
    editing the file within a process.
    """
    path = profile_path()
    if path is None:
        return {}
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise TypeError(f"{path}: profile must be a JSON object")

    from .config import Scenario  # local import avoids a module cycle

    hints = typing.get_type_hints(Scenario)
    valid = {f.name for f in dataclass_fields(Scenario)} - _NON_PROFILE_FIELDS

    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue  # reserved for comments / metadata, e.g. "_note"
        if key not in valid:
            raise KeyError(
                f"{path}: {key!r} is not a Scenario field (or is not settable from a profile)"
            )
        out[key] = _coerce(key, value, hints[key])
    return out


# Recognized keys inside profile.json's reserved "_ui" object: display
# preferences, not model inputs — they never touch Scenario. Maps pref name
# to its required type.
_UI_PREFS: dict[str, type] = {"hide_builtin_scenarios": bool}


@lru_cache(maxsize=1)
def load_ui_prefs() -> dict[str, Any]:
    """Parse the reserved ``"_ui"`` object from profile.json.

    Display preferences (e.g. ``{"hide_builtin_scenarios": true}``), kept
    separate from the Scenario-field overlay: ``load_profile`` skips
    underscore keys, so this is the one reader of ``"_ui"``. Unknown pref
    names or wrong types are a hard error (typo protection, same ethos as
    ``load_profile``). Returns {} when no profile is active or the key is
    absent. Cached — call ``load_ui_prefs.cache_clear()`` after editing the
    file within a process.
    """
    path = profile_path()
    if path is None:
        return {}
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise TypeError(f"{path}: profile must be a JSON object")
    prefs = raw.get("_ui", {})
    if not isinstance(prefs, dict):
        raise TypeError(f'{path}: "_ui" must be a JSON object')
    for key, value in prefs.items():
        expected = _UI_PREFS.get(key)
        if expected is None:
            raise KeyError(f"{path}: {key!r} is not a recognized _ui pref")
        if not isinstance(value, expected):
            raise TypeError(
                f"{path}: _ui pref {key!r}: expected {expected.__name__}, got {value!r}"
            )
    return dict(prefs)


def hide_builtin_scenarios() -> bool:
    """Convenience: the ``hide_builtin_scenarios`` display pref (default False)."""
    return bool(load_ui_prefs().get("hide_builtin_scenarios", False))


def load_user_scenarios() -> bool:
    """Import ``user_data/scenarios.py`` so its ``@scenario`` factories register.

    Call once at app/CLI startup, AFTER ``variometer.scenarios`` has been
    imported (so the built-in library registers first and dropdown ordering is
    stable). Returns True when a user scenario module was loaded. Repeat calls
    are no-ops (the module stays in ``sys.modules``); the registry's
    duplicate-name guard catches collisions with built-in display names.
    """
    import importlib.util
    import sys

    path = _user_file("scenarios.py")
    if path is None:
        return False
    mod_name = "variometer_user_scenarios"
    if mod_name in sys.modules:
        return True
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path} as a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[mod_name]
        raise
    return True


@lru_cache(maxsize=1)
def load_historical() -> list[dict] | None:
    """Parse user_data/historical.csv → monthly actuals for the Historical tab.

    Schema (header required): ``year,month,total_income,paychecks,
    other_income,expenses``. ``paychecks`` / ``other_income`` may be blank
    (unknown). Returns None when the file is absent — the app hides the
    Historical tab entirely in that case.
    """
    path = _user_file("historical.csv")
    if path is None:
        return None
    out: list[dict] = []
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"year", "month", "total_income", "paychecks", "other_income", "expenses"}
        if reader.fieldnames is None or not required <= set(reader.fieldnames):
            raise ValueError(f"{path}: expected header with columns {sorted(required)}")
        for i, row in enumerate(reader, start=2):
            try:
                out.append(
                    {
                        "year": int(row["year"]),
                        "month": int(row["month"]),
                        "total_income": float(row["total_income"]),
                        "paychecks": float(row["paychecks"]) if row["paychecks"] else None,
                        "other_income": float(row["other_income"]) if row["other_income"] else None,
                        "expenses": float(row["expenses"]),
                    }
                )
            except ValueError as e:
                raise ValueError(f"{path} line {i}: {e}") from e
    out.sort(key=lambda r: (r["year"], r["month"]))
    return out


@lru_cache(maxsize=1)
def load_vest_grants() -> dict[str, dict[tuple[int, int], float]] | None:
    """Parse user_data/grants.csv → {grant_id: {(year, month): shares}}.

    Schema (header required): ``grant_id,year,month,shares``. One row per
    vest date per grant; grant_id is the display label the Vest Schedule tab
    groups by. Returns None when the file is absent (Scenario keeps its
    illustrative default schedule).
    """
    path = _user_file("grants.csv")
    if path is None:
        return None
    out: dict[str, dict[tuple[int, int], float]] = {}
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"grant_id", "year", "month", "shares"}
        if reader.fieldnames is None or not required <= set(reader.fieldnames):
            raise ValueError(f"{path}: expected header with columns {sorted(required)}")
        for i, row in enumerate(reader, start=2):
            try:
                grant = row["grant_id"].strip()
                key = (int(row["year"]), int(row["month"]))
                shares = float(row["shares"])
            except (ValueError, AttributeError) as e:
                raise ValueError(f"{path} line {i}: {e}") from e
            if not 1 <= key[1] <= 12:
                raise ValueError(f"{path} line {i}: month must be 1-12, got {key[1]}")
            sched = out.setdefault(grant, {})
            sched[key] = sched.get(key, 0.0) + shares
    return out
