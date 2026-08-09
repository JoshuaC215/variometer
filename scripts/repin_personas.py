#!/usr/bin/env python3
"""Re-pin a persona bundle's expected.json from the current model.

    uv run python scripts/repin_personas.py tests/personas/single_ca_rsu
    uv run python scripts/repin_personas.py user_data/tests/personas/personal
    uv run python scripts/repin_personas.py --dry-run tests/personas/*

This is a *deliberate* operation. It recomputes only the checks and years the
file already pins — it never invents a new pin — rewrites the file preserving
key order and every ``_``-prefixed metadata key, and prints an old → new
summary of what moved. A re-pin should always land in a commit that explains
why the numbers moved; ``HISTORY.md`` next to the persona is the place for the
long form.

Environment discipline: a persona that does not declare
``REQUIRES_USER_DATA = True`` is built with the private layer forced off (see
``variometer.testing.user_data_disabled``), so regenerating a public persona on
a machine with a populated ``user_data/`` can never bake private numbers into a
committed file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from variometer.testing import (  # noqa: E402
    PERSONA_EXPECTED_FILE,
    PersonaError,
    load_persona,
    persona_cases,
)

MAX_INLINE_WIDTH = 100


# ---------------------------------------------------------------------------
# JSON rendering — stable, diff-friendly, 2-decimal money
# ---------------------------------------------------------------------------


def _scalar(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return json.dumps(value, ensure_ascii=False)


def _render(value: Any, indent: int = 0) -> str:
    """Pretty-print JSON with small all-scalar objects kept on one line."""
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [
            f"{json.dumps(k, ensure_ascii=False)}: {_render(v, indent + 2)}"
            for k, v in value.items()
        ]
        if all(not isinstance(v, (dict, list)) for v in value.values()):
            inline = "{" + ", ".join(items) + "}"
            if indent + len(inline) <= MAX_INLINE_WIDTH:
                return inline
        body = ",\n".join(f"{pad}  {item}" for item in items)
        return "{\n" + body + "\n" + pad + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        items = [_render(v, indent + 2) for v in value]
        inline = "[" + ", ".join(items) + "]"
        if indent + len(inline) <= MAX_INLINE_WIDTH and all(
            not isinstance(v, (dict, list)) for v in value
        ):
            return inline
        body = ",\n".join(f"{pad}  {item}" for item in items)
        return "[\n" + body + "\n" + pad + "]"
    return _scalar(value)


def dumps(data: Any) -> str:
    return _render(data) + "\n"


# ---------------------------------------------------------------------------
# Re-pin
# ---------------------------------------------------------------------------


def repin(directory: Path, dry_run: bool = False) -> int:
    """Recompute one persona's pins. Returns the number of values that moved."""
    persona = load_persona(directory)
    expected_path = persona.path / PERSONA_EXPECTED_FILE
    data = json.loads(expected_path.read_text())

    layer = "private layer ON" if persona.requires_user_data else "private layer OFF"
    cwd = Path.cwd()
    shown = expected_path.relative_to(cwd) if expected_path.is_relative_to(cwd) else expected_path
    print(f"\n{persona.name}  ({shown})  [{layer}]")

    moved = 0
    for case in persona_cases(persona):
        new_value = round(case.actual(), 2)
        old_value = round(case.expected, 2)
        target = data[case.variant][case.check][case.year_key]
        if case.named_field:
            target[case.field] = new_value
        else:
            data[case.variant][case.check][case.year_key] = new_value
        if abs(new_value - old_value) >= 0.005:
            moved += 1
            delta = new_value - old_value
            print(f"  ~ {case.id:<58} {old_value:>15,.2f} → {new_value:>15,.2f}  ({delta:+,.2f})")

    if moved == 0:
        print("  = no change")

    if dry_run:
        print("  (dry run — file not written)")
        return moved

    rendered = dumps(data)
    if rendered != expected_path.read_text():
        expected_path.write_text(rendered)
        print(f"  wrote {expected_path.name}")
    else:
        print(f"  {expected_path.name} already up to date")
    return moved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recompute the pinned values in one or more persona bundles.",
    )
    parser.add_argument("persona_dir", nargs="+", type=Path, help="persona bundle directory")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would move without rewriting expected.json",
    )
    args = parser.parse_args(argv)

    total = 0
    for directory in args.persona_dir:
        try:
            total += repin(directory, dry_run=args.dry_run)
        except PersonaError as exc:
            print(f"\nerror: {exc}", file=sys.stderr)
            return 2

    print(f"\n{total} pinned value(s) moved.")
    if total:
        print("Re-pins are deliberate: record why in the persona's HISTORY.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
