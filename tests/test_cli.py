"""Tests for the ``variometer`` console script.

The load-bearing one is the privacy contract on the bare command: it is what
someone types on a screen share, so it must report configuration only — never
a balance, salary, share count or any other financial figure.
"""

import json
import re

import pytest

from variometer.cli import _display_path, explain_main, main, render_status
from variometer.config import MODEL_EPOCH, PROJECTION_END_YEAR
from variometer.user_data_io import (
    load_historical,
    load_profile,
    load_ui_prefs,
    load_vest_grants,
)


def _clear_caches():
    load_profile.cache_clear()
    load_ui_prefs.cache_clear()
    load_vest_grants.cache_clear()
    load_historical.cache_clear()


@pytest.fixture
def populated_user_data(tmp_path, monkeypatch):
    """A private layer stuffed with money, to prove none of it can leak out."""
    monkeypatch.setenv("VARIOMETER_USER_DATA", str(tmp_path))
    (tmp_path / "profile.json").write_text(
        json.dumps(
            {
                "base_salary": 412_345.0,
                "brokerage": 1_234_567.0,
                "pretax_401k": 987_654.0,
                "monthly_expenses": 13_579.0,
            }
        )
    )
    (tmp_path / "grants.csv").write_text(
        "grant_id,year,month,shares\n"
        "Acme Refresh 2025,2026,3,1250\n"
        "Acme Refresh 2025,2026,6,1250\n"
        "Acme New-hire 2023,2026,3,4321\n"
    )
    (tmp_path / "historical.csv").write_text(
        "year,month,total_income,paychecks,other_income,expenses\n2026,1,54321,40000,14321,13579\n"
    )
    _clear_caches()
    yield tmp_path
    _clear_caches()


# ---------------------------------------------------------------------------
# The privacy contract
# ---------------------------------------------------------------------------

# Anything that is plausibly a year in the model's horizon is allowed to appear
# as a bare number; everything else numeric must be a small count.
_ALLOWED_YEARS = set(range(MODEL_EPOCH - 1, PROJECTION_END_YEAR + 1))


def _assert_no_financial_figures(text: str) -> None:
    assert "$" not in text, f"status output contains a dollar sign:\n{text}"

    # Comma- or underscore-grouped numbers are money-shaped by construction.
    grouped = re.findall(r"\d[\d,_]*[,_]\d\d\d", text)
    assert not grouped, f"status output contains grouped numbers {grouped}:\n{text}"

    # Filesystem paths are the one thing the status view is allowed to echo
    # verbatim, and their digits are incidental (a pytest tmp dir is
    # ".../pytest-1234/..."), so drop path-shaped tokens before scanning.
    scannable = " ".join(w for w in text.split() if "/" not in w and "\\" not in w)

    # Every remaining integer must be a small count or an in-horizon year.
    for tok in re.findall(r"(?<![\w.])\d+(?![\w.])", scannable):
        value = int(tok)
        assert value < 1000 or value in _ALLOWED_YEARS, (
            f"status output contains suspicious number {value}:\n{text}"
        )


def test_status_prints_no_financial_figures_without_user_data():
    _assert_no_financial_figures(render_status())


def test_status_prints_no_financial_figures_with_populated_user_data(populated_user_data):
    out = render_status()
    _assert_no_financial_figures(out)

    # Nor may any *label* from the private layer appear.
    assert "Acme" not in out
    # ...but the layer is genuinely active and counted: 2 grants, 3 vest dates.
    assert "private layer     on" in out
    assert "2 grants, 3 vest dates" in out
    assert "historical.csv    present" in out
    assert "profile.json      loaded" in out


def test_status_reports_disabled_private_layer():
    # conftest sets VARIOMETER_USER_DATA="" for the whole public suite.
    out = render_status()
    assert "private layer     off" in out
    assert "illustrative placeholder data" in out


def test_status_reports_missing_user_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VARIOMETER_USER_DATA", str(tmp_path / "nope"))
    _clear_caches()
    try:
        out = render_status()
        assert "misconfigured" in out
        _assert_no_financial_figures(out)
    finally:
        _clear_caches()


def test_status_mentions_epoch_and_commands():
    out = render_status()
    assert str(MODEL_EPOCH) in out
    for cmd in ("variometer explain", "variometer explain --list", "variometer ui"):
        assert cmd in out


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _run_main(monkeypatch, argv: list[str]) -> None:
    monkeypatch.setattr("sys.argv", ["variometer", *argv])
    main()


def test_bare_command_prints_status(monkeypatch, capsys):
    _run_main(monkeypatch, [])
    out = capsys.readouterr().out
    assert "Variometer" in out
    assert "Commands" in out
    _assert_no_financial_figures(out)


@pytest.mark.parametrize("flag", ["-h", "--help", "help"])
def test_help_prints_status(monkeypatch, capsys, flag):
    _run_main(monkeypatch, [flag])
    assert "Commands" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["-V", "--version", "version"])
def test_version_flag(monkeypatch, capsys, flag):
    _run_main(monkeypatch, [flag])
    assert capsys.readouterr().out.startswith("variometer ")


def test_explain_subcommand_reaches_the_explainer(monkeypatch, capsys):
    _run_main(monkeypatch, ["explain", "--list"])
    out = capsys.readouterr().out
    assert "stay_put" in out


def test_explain_subcommand_passes_positional_scenario(monkeypatch, capsys):
    _run_main(monkeypatch, ["explain", "sabbatical"])
    out = capsys.readouterr().out
    assert "Key milestones" in out


def test_unknown_command_suggests_explain(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["variometer", "stay_put"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "Unknown command 'stay_put'" in out
    assert "variometer explain stay_put" in out


def test_ui_is_dispatched_before_argparse(monkeypatch):
    """`ui` must be peeled off before the explainer's positional arg sees it."""
    seen = {}

    def fake_run_ui(argv=None):
        seen["argv"] = argv

    monkeypatch.setattr("variometer.cli.run_ui", fake_run_ui)
    _run_main(monkeypatch, ["ui", "--server.port", "8599"])
    assert seen["argv"] == ["--server.port", "8599"]


# ---------------------------------------------------------------------------
# The explainer entry contract
# ---------------------------------------------------------------------------


def test_explain_main_takes_a_bare_scenario_name(capsys):
    """explain_main takes a bare scenario name — no subcommand in front."""
    explain_main(["--list"])
    assert "stay_put" in capsys.readouterr().out


def test_display_path_is_relative_when_under_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _display_path(tmp_path / "user_data") == "user_data"
