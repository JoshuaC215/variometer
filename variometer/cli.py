#!/usr/bin/env python
"""variometer CLI — the ``variometer`` console script and the explain engine.

Three commands:

    variometer                       configuration status + command reference
                                     (deliberately prints no dollar figures —
                                     it is safe on a screen share)
    variometer explain …             year-by-year breakdown of any scenario
    variometer ui                    launch the Streamlit app (extra flags are
                                     passed straight through to streamlit)

From a repo checkout, prefix with ``uv run`` — ``uv run variometer explain …``.

Usage examples:
    variometer explain                          # default: Stay put
    variometer explain --list                   # all registered scenarios
    variometer explain sabbatical --start-year 2030 --return-year 2033
    variometer explain change_jobs --new-base 200000 --start-year 2029
    variometer explain fire_roth_ladder --start-year 2035 --lean-monthly 6500 --mc
    variometer explain buy_home --retire-year 2056
    variometer explain stay_put --mc --ss-monthly 3500

Scenario names resolve against the registry — use the factory function name
(e.g. ``fire_roth_ladder``) or the display name. Personal factories from
``user_data/scenarios.py`` are loaded automatically and work the same way.

Knob flags (``--start-year``, ``--lean-monthly``, …) map onto the factory's
matching parameters; flags the factory doesn't have are ignored.

Ad-hoc / one-off scenario (no factory registration needed):
    variometer explain --python ad_hoc/my_what_if.py
    variometer explain --python ad_hoc/my_what_if.py --mc --ss-monthly 3500
    variometer explain --python ad_hoc/multi.py --var scenario_b   # non-default variable name

    The file must define a top-level variable of type Scenario (default name: "scenario").
    Use --var to read a different name — useful when one file defines multiple scenarios:

        # ad_hoc/my_what_if.py
        from variometer.config import base_scenario
        from variometer.events import LifeEvent

        scenario = base_scenario()
        scenario.events = [
            LifeEvent(year=2027, month=6, kind="employment_end"),
            LifeEvent(year=2028, month=1, kind="house_purchase",
                      params={"price": 1_200_000, "down_pct": 0.20,
                              "rate": 0.065, "term_years": 30}),
        ]
        scenario.summary = "Leave mid-2027, buy $1.2M house Jan 2028"

    See the worked example in ad_hoc/ for a full composition.

Monte Carlo / sustainability flags (add to any scenario):
    --mc                    Run Monte Carlo (200 sims, σ=15%) — adds ~2–3 s
    --sigma FLOAT           Volatility for MC (default 0.15)
    --ss-monthly FLOAT      SS monthly benefit in today's $ (enables SS section)
    --claim-age INT         SS claim age (default 67)
"""

import argparse
import importlib.util
import inspect
import os
import sys
import textwrap
from pathlib import Path

from variometer.config import (
    MODEL_EPOCH,
    PROJECTION_END_YEAR,
    Scenario,
    bracket_staleness_warning,
)
from variometer.data.social_security import SS_COLA_DEFAULT, ss_annual_income
from variometer.projection import monte_carlo_nw, project_net_worth
from variometer.scenarios import SCENARIO_REGISTRY, grouped_scenario_names
from variometer.user_data_io import (
    hide_builtin_scenarios,
    load_historical,
    load_user_scenarios,
    load_vest_grants,
    user_data_dir,
)

# Register personal factories from user_data/scenarios.py (no-op when absent).
load_user_scenarios()

YEARS = list(range(2026, 2046))
EXTENDED_YEARS = list(range(2026, PROJECTION_END_YEAR + 1))  # SS / sustainability horizon


def load_adhoc_scenario(path: str, var: str = "scenario") -> Scenario:
    """Load a .py file and return the named top-level Scenario variable.

    ``var`` defaults to "scenario" but can be overridden via --var to support
    files that define multiple scenarios under different names.
    """
    spec = importlib.util.spec_from_file_location("_adhoc_scenario", path)
    if spec is None or spec.loader is None:
        print(f"Error: cannot load '{path}' as a Python module.")
        sys.exit(1)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception as exc:
        print(f"Error executing '{path}': {exc}")
        sys.exit(1)
    if not hasattr(mod, var):
        defined = [n for n in dir(mod) if not n.startswith("_")]
        print(
            f"Error: '{path}' has no top-level variable named '{var}'.\n"
            f"Defined names: {defined}\n"
            f"Use --var NAME to pick a different variable, or add `{var} = ...` to the file.\n"
            "Example:\n"
            "    from variometer.config import Scenario\n"
            "    scenario_a = Scenario(); scenario_a.summary = 'Option A'\n"
            "    scenario_b = Scenario(); scenario_b.summary = 'Option B'\n"
        )
        sys.exit(1)
    sc = getattr(mod, var)
    if not isinstance(sc, Scenario):
        print(f"Error: '{path}'.{var} must be a Scenario instance, got {type(sc).__name__}.")
        sys.exit(1)
    return sc


def fmt(v: float) -> str:
    """Format a dollar value compactly."""
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.0f}k"
    return f"${v:.0f}"


# CLI flag → candidate factory parameter names, tried in order. A flag only
# applies when the resolved factory actually has one of these parameters —
# irrelevant flags are silently ignored, so the same flag set works for the
# public archetypes and any user_data/scenarios.py factory.
_FLAG_TO_PARAMS: dict[str, tuple[str, ...]] = {
    "start_year": ("start_year", "promo_year", "purchase_year", "exit_year"),
    "return_year": ("return_year",),
    "new_base": ("new_base",),
    "new_equity_annual": ("new_equity_annual",),
    "mega_backdoor": ("has_mega_backdoor",),
    "match_rate": ("new_match_rate",),
    "lean_monthly": ("lean_monthly", "lean_monthly_expenses"),
    "healthcare_monthly": ("healthcare_monthly",),
    "roth_conversion": ("roth_conversion_annual",),
}


def _slug(display_name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in display_name.lower()).strip("_")


def resolve_factory(name: str):
    """Resolve a CLI scenario name against the registry.

    Accepts the factory function name (``fire_roth_ladder``), the display
    name ("FIRE with Roth ladder"), or its slug (``fire_with_roth_ladder``).
    """
    wanted = name.lower()
    for display_name, fn in SCENARIO_REGISTRY.items():
        if wanted in (fn.__name__.lower(), display_name.lower(), _slug(display_name)):
            return fn
    print(f"Unknown scenario '{name}'. Run with --list to see options.")
    sys.exit(1)


def build_scenario(name: str, args: argparse.Namespace):
    """Build the named scenario, mapping CLI knob flags onto factory params."""
    factory = resolve_factory(name)
    params = set(inspect.signature(factory).parameters)
    kwargs = {}
    for flag, candidates in _FLAG_TO_PARAMS.items():
        value = getattr(args, flag, None)
        if value is None or value is False:
            continue
        for param in candidates:
            if param in params:
                kwargs[param] = value
                break
    return factory(**kwargs)


def print_scenario(sc, years: list[int]) -> None:
    results = project_net_worth(sc, years)

    print()
    print(f"  {sc.summary}")
    print()

    has_home_equity = any(r.home_equity > 0 for r in results)
    has_roth_401k = any(r.roth_401k > 0 for r in results)

    # Header
    hdr = (
        f"{'Year':<6} {'Income':>9} {'Expenses':>10} {'Surplus':>10} "
        f"{'Brokerage':>11} {'Roth IRA':>10}"
    )
    if has_roth_401k:
        hdr += f" {'Roth 401k':>10}"
    hdr += f" {'Pretax 401k':>11}"
    if has_home_equity:
        hdr += f" {'Home Eq':>10}"
    hdr += f" {'Total NW':>10} {'Drawdown':>10} {'Unfunded':>9}"
    print(hdr)
    print("─" * len(hdr))

    prev_nw = None
    for r in results:
        surplus = r.take_home_income - r.total_expenses
        wr = r.withdrawal
        drawn = fmt(wr.total_withdrawn) if wr else "—"
        unfunded = fmt(r.unfunded_shortfall) if r.unfunded_shortfall > 1.0 else "—"
        nw_delta = ""
        if prev_nw is not None:
            delta = r.total_nw - prev_nw
            nw_delta = f" ({'+' if delta >= 0 else ''}{fmt(delta)})"
        row_str = (
            f"{r.year:<6} {fmt(r.take_home_income):>9} {fmt(r.total_expenses):>10} "
            f"{fmt(surplus):>10} "
            f"{fmt(r.brokerage):>11} {fmt(r.roth_ira):>10}"
        )
        if has_roth_401k:
            row_str += f" {fmt(r.roth_401k):>10}"
        row_str += f" {fmt(r.pretax_401k):>11}"
        if has_home_equity:
            row_str += f" {fmt(r.home_equity):>10}"
        row_str += f" {fmt(r.total_nw):>10}{nw_delta:<12} {drawn:>10} {unfunded:>9}"
        print(row_str)
        prev_nw = r.total_nw

    print()

    # Key milestones
    insolvency = next((r for r in results if r.unfunded_shortfall > 1.0), None)
    drawdown_years = [r for r in results if r.withdrawal is not None]

    print("  Key milestones:")
    for chk_year in [2030, 2035, 2040, 2045]:
        nw_val = next((r.total_nw for r in results if r.year == chk_year), None)
        if nw_val is not None:
            print(f"    NW in {chk_year}:  {fmt(nw_val)}")
    if drawdown_years:
        print(
            f"    Waterfall fires: {drawdown_years[0].year}–{drawdown_years[-1].year} ({len(drawdown_years)} years)"
        )
    else:
        print("    Waterfall fires: never (cash flow positive throughout)")
    if insolvency:
        print(f"    ⚠  First insolvency year: {insolvency.year}")
    else:
        print("    ✓  No insolvency in window")
    print()


def print_monte_carlo(sc, years: list[int], sigma: float = 0.15, n: int = 200) -> None:
    """Print a percentile table at 5-year milestones from a Monte Carlo run."""
    print(f"  Monte Carlo (σ={sigma:.0%}, N={n}, seed=42) — NW percentiles at 5-yr marks:")
    print(
        f"  {'Year':<6} {'P10':>9} {'P25':>9} {'P50':>9} {'P75':>9} {'P90':>9}  {'P50 vs det':>11}"
    )

    # Deterministic for comparison
    det = {r.year: r.total_nw for r in project_net_worth(sc, years)}

    sys.stdout.write("  running simulations…")
    sys.stdout.flush()
    bands = monte_carlo_nw(sc, years, n=n, sigma=sigma, seed=42)
    sys.stdout.write(f"\r{' ' * 30}\r")  # clear the spinner line

    milestone_years = [y for y in [2030, 2035, 2040, 2045, 2050, 2055, 2060] if y in bands.years]
    print("  " + "─" * 68)
    for yr in milestone_years:
        idx = bands.years.index(yr)
        p10, p25, p50, p75, p90 = (
            bands.p10[idx],
            bands.p25[idx],
            bands.p50[idx],
            bands.p75[idx],
            bands.p90[idx],
        )
        det_val = det.get(yr, 0.0)
        diff_pct = (p50 - det_val) / det_val * 100 if det_val else 0.0
        diff_str = f"{diff_pct:+.1f}%" if det_val else "—"
        print(
            f"  {yr:<6} {fmt(p10):>9} {fmt(p25):>9} {fmt(p50):>9} {fmt(p75):>9} {fmt(p90):>9}  {diff_str:>11}"
        )

    # Final year summary
    last_idx = len(bands.years) - 1
    p10f, p50f, p90f = bands.p10[last_idx], bands.p50[last_idx], bands.p90[last_idx]
    print(f"\n  P10–P90 range at {bands.years[-1]}: {fmt(p10f)} – {fmt(p90f)}  (P50: {fmt(p50f)})")
    print(
        f"  Fan width (P90−P10): {fmt(p90f - p10f)}  |  σ={sigma:.0%} compounds to wide uncertainty over {len(years)} yrs"
    )
    print()


def print_retirement_sustainability(
    sc,
    extended_results,
    ss_monthly_today: float = 0.0,
    ss_claim_age: int = 67,
    current_year: int = 2026,
) -> None:
    """Print a retirement sustainability table across a range of retirement ages."""
    current_age = sc.age  # age at MODEL_EPOCH — the scenario is the source of truth
    ss_enabled = ss_monthly_today > 0
    ss_claim_year = current_year + (ss_claim_age - current_age) if ss_enabled else None

    if ss_enabled:
        print(
            f"  Retirement sustainability  (SS {fmt(ss_monthly_today * 12)}/yr at age {ss_claim_age}, "
            f"COLA {SS_COLA_DEFAULT:.1%}/yr):"
        )
    else:
        print("  Retirement sustainability  (no SS — pass --ss-monthly to include):")

    hdr2 = f"  {'Age':<5} {'Year':<6} {'NW':>9} {'Exp/yr':>9} {'SS/yr':>8} {'Gap/yr':>9} {'Runway':>18}"
    print(hdr2)
    print("  " + "─" * (len(hdr2) - 2))

    nw_by_year = {r.year: r for r in extended_results}

    for ret_age in [60, 62, 65, 67, 70]:
        ret_year = current_year + (ret_age - current_age)
        row = nw_by_year.get(ret_year)
        if row is None:
            continue

        nw = row.total_nw
        exp_annual = row.total_expenses
        r_ret = sc.return_rate

        # Year-by-year simulation (mirrors app.py fix)
        bal = nw
        exp_now = exp_annual
        exhaust_year = None
        for py in range(ret_year, ret_year + 60):
            ss_py = (
                ss_annual_income(ss_monthly_today, ss_claim_year, py, SS_COLA_DEFAULT)
                if ss_enabled and ss_claim_year
                else 0.0
            )
            gap_py = exp_now - ss_py
            bal = bal * (1 + r_ret) - gap_py
            exp_now *= 1 + sc.expense_growth
            if bal <= 0:
                exhaust_year = py + 1
                break

        # SS at ret_year (may be 0 if not yet claimed)
        ss_at_ret = (
            ss_annual_income(ss_monthly_today, ss_claim_year, ret_year, SS_COLA_DEFAULT)
            if ss_enabled and ss_claim_year
            else 0.0
        )
        gap_at_ret = exp_annual - ss_at_ret

        if exhaust_year:
            years_left = exhaust_year - ret_year
            runway_str = f"{years_left} yr → age {ret_age + years_left} (~{exhaust_year})"
        else:
            runway_str = "∞ (returns ≥ gap)"

        print(
            f"  {ret_age:<5} {ret_year:<6} {fmt(nw):>9} {fmt(exp_annual):>9} "
            f"{fmt(ss_at_ret):>8} {fmt(gap_at_ret):>9} {runway_str}"
        )

    if ss_enabled and ss_claim_year:
        ss_nominal_at_claim = ss_annual_income(
            ss_monthly_today, ss_claim_year, ss_claim_year, SS_COLA_DEFAULT
        )
        print(
            f"\n  SS note: {fmt(ss_monthly_today * 12)}/yr in today's $ → "
            f"{fmt(ss_nominal_at_claim)}/yr nominal at age {ss_claim_age} ({ss_claim_year}) "
            f"after COLA. Not yet received before age {ss_claim_age}."
        )
    print()


def explain_main(argv: list[str] | None = None, prog: str | None = None):
    """The explainer: ``variometer explain …``.

    ``prog`` overrides the name argparse prints in usage/help so the console
    script reports ``variometer explain`` rather than just ``variometer``.
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Explain a scenario year-by-year. Use `variometer ui` for the Streamlit app.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        default=None,
        help="Scenario name (default: stay_put). Ignored when --python is used.",
    )
    parser.add_argument("--list", action="store_true", help="List available scenarios and exit")
    parser.add_argument(
        "--python",
        metavar="FILE",
        dest="python_file",
        default=None,
        help=(
            "Path to a .py file (typically in ad_hoc/) that defines a top-level Scenario variable. "
            "Runs it through the standard output pipeline without needing a named factory."
        ),
    )
    parser.add_argument(
        "--var",
        metavar="NAME",
        dest="python_var",
        default="scenario",
        help=(
            "Name of the top-level Scenario variable to read from the --python file "
            "(default: 'scenario'). Useful when one file defines multiple scenarios."
        ),
    )

    # Shared knobs
    parser.add_argument("--start-year", type=int, default=None)
    parser.add_argument("--return-year", type=int, default=None)
    parser.add_argument("--new-base", type=float, default=None, dest="new_base")
    parser.add_argument("--new-equity-annual", type=float, default=None, dest="new_equity_annual")
    parser.add_argument("--mega-backdoor", action="store_true", default=False, dest="mega_backdoor")
    parser.add_argument("--match-rate", type=float, default=None, dest="match_rate")
    parser.add_argument("--lean-monthly", type=float, default=None, dest="lean_monthly")
    parser.add_argument("--healthcare-monthly", type=float, default=None, dest="healthcare_monthly")
    parser.add_argument("--roth-conversion", type=float, default=None, dest="roth_conversion")

    # Monte Carlo / sustainability flags
    parser.add_argument(
        "--mc",
        action="store_true",
        default=False,
        help="Run Monte Carlo simulation and print percentile table (~2–3 s extra)",
    )
    parser.add_argument(
        "--sigma",
        type=float,
        default=0.15,
        metavar="FLOAT",
        help="Annual return volatility for MC (default 0.15)",
    )
    parser.add_argument(
        "--n-sims",
        type=int,
        default=200,
        dest="n_sims",
        metavar="N",
        help="Number of MC simulations (default 200)",
    )
    parser.add_argument(
        "--ss-monthly",
        type=float,
        default=0.0,
        dest="ss_monthly",
        metavar="DOLLARS",
        help="SS monthly benefit in today's $ — enables sustainability table",
    )
    parser.add_argument(
        "--claim-age",
        type=int,
        default=67,
        dest="claim_age",
        help="SS claim age (default 67 = FRA)",
    )
    parser.add_argument(
        "--retire-year",
        type=int,
        default=None,
        dest="retire_year",
        metavar="YEAR",
        help="Stop all employment income at this year (same logic as UI retirement cutoff). "
        "Example: --retire-year 2056  (age 65 for the placeholder persona)",
    )

    args = parser.parse_args(argv)

    if args.list:
        user_names, builtin_names = grouped_scenario_names()
        col = max(len(fn.__name__) for fn in SCENARIO_REGISTRY.values()) + 2

        def _print_rows(names):
            for display_name in names:
                fn = SCENARIO_REGISTRY[display_name]
                doc = (fn.__doc__ or "").strip().splitlines()[0].rstrip(".")
                print(f"  {fn.__name__:<{col}}  {display_name:<30}  {doc}")

        if not user_names:
            print("\nAvailable scenarios:\n")
            _print_rows(builtin_names)
        else:
            print("\nYour scenarios (user_data/scenarios.py):\n")
            _print_rows(user_names)
            if hide_builtin_scenarios():
                print(
                    f"\n  [{len(builtin_names)} built-in archetypes hidden — "
                    '"_ui": {"hide_builtin_scenarios": true} in profile.json; '
                    "they still run by name]"
                )
            else:
                print("\nBuilt-in archetypes:\n")
                _print_rows(builtin_names)
        print()
        return

    if args.python_file:
        sc = load_adhoc_scenario(args.python_file, var=args.python_var)
    else:
        sc = build_scenario(args.scenario or "stay_put", args)

    staleness = bracket_staleness_warning()
    if staleness:
        print()
        print("  " + "!" * 76)
        for line in textwrap.wrap(staleness, width=72):
            print(f"  !!  {line}")
        print("  " + "!" * 76)

    if args.retire_year:
        sc.retirement_year = args.retire_year
        retire_age = sc.age + (args.retire_year - 2026)
        print(f"  [retirement cutoff: income stops in {args.retire_year} (age {retire_age})]")

    # Basic year-by-year table (2026–2045)
    print_scenario(sc, YEARS)

    # Monte Carlo section (opt-in via --mc)
    if args.mc:
        mc_years = EXTENDED_YEARS if args.ss_monthly else YEARS
        print_monte_carlo(sc, mc_years, sigma=args.sigma, n=args.n_sims)

    # Retirement sustainability (always shown if --ss-monthly given, otherwise shown without SS)
    extended_results = project_net_worth(sc, EXTENDED_YEARS)
    print_retirement_sustainability(
        sc,
        extended_results,
        ss_monthly_today=args.ss_monthly,
        ss_claim_age=args.claim_age,
    )


# ---------------------------------------------------------------------------
# Status / help (the bare `variometer` command)
# ---------------------------------------------------------------------------


def _version() -> str:
    """Installed distribution version, falling back to the package constant."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("variometer")
    except PackageNotFoundError:
        from variometer import __version__

        return __version__
    except Exception:  # pragma: no cover - importlib.metadata is stdlib
        from variometer import __version__

        return __version__


def _display_path(p: Path) -> str:
    """Show a path relative to the cwd when possible, else absolute.

    Only ever a *path* — the status view never reads the files' contents out
    loud, so nothing here can be a financial figure.
    """
    try:
        return str(p.relative_to(Path.cwd()))
    except ValueError:
        return str(p)


def render_status() -> str:
    """Render the bare-command view: what is configured, and what to run next.

    Privacy contract — this output is what someone types on a screen share, so
    it reports *counts and paths only*. No balance, salary, share count, grant
    label or scenario name from the private layer may appear here; the public
    test suite asserts that no dollar figure can reach this string.
    """
    out: list[str] = []
    out.append(f"Variometer {_version()} — multi-year cash-flow and net-worth planning")
    out.append("")
    out.append("Configuration")

    # Private layer. user_data_dir() raises when VARIOMETER_USER_DATA points at
    # a missing directory — that is a misconfiguration worth showing plainly.
    try:
        d = user_data_dir()
        dir_error = None
    except FileNotFoundError as exc:
        d, dir_error = None, str(exc)

    if dir_error:
        out.append(f"  private layer     misconfigured — {dir_error}")
    elif d is None:
        disabled = os.environ.get("VARIOMETER_USER_DATA") == ""
        why = "disabled via VARIOMETER_USER_DATA" if disabled else "not configured"
        out.append(f"  private layer     off ({why}) — running on illustrative placeholder data")
    elif not d.is_dir():
        # Common for an installed wheel: the default location is resolved
        # relative to the package, so it lands next to site-packages and does
        # not exist. Point at the env var rather than at a repo-only directory.
        out.append("  private layer     off — running on illustrative placeholder data")
        out.append(f"                    looked in {_display_path(d)}")
        out.append("                    set VARIOMETER_USER_DATA to your own directory to")
        out.append("                    use it (templates: examples/user_data/ in the repo)")
    else:
        out.append(f"  private layer     on  {_display_path(d)}")

    if d is not None and d.is_dir():
        out.append(f"  profile.json      {'loaded' if (d / 'profile.json').exists() else 'absent'}")

        try:
            grants = load_vest_grants()
        except Exception:
            grants = None
            out.append("  grants.csv        unreadable — see the file for the parse error")
        else:
            if grants is None:
                out.append("  grants.csv        absent — using the illustrative sample schedule")
            else:
                vests = sum(len(sched) for sched in grants.values())
                out.append(f"  grants.csv        {len(grants)} grants, {vests} vest dates")

        try:
            historical = load_historical()
        except Exception:
            out.append("  historical.csv    unreadable — see the file for the parse error")
        else:
            out.append(f"  historical.csv    {'present' if historical else 'absent'}")

    user_names, builtin_names = grouped_scenario_names()
    out.append(f"  scenarios         {len(user_names)} personal, {len(builtin_names)} built-in")
    out.append(f"  model epoch       {MODEL_EPOCH} (tax tables pinned to this year)")

    staleness = bracket_staleness_warning()
    if staleness:
        out.append("")
        for line in textwrap.wrap(staleness, width=72):
            out.append(f"  !!  {line}")

    out.append("")
    out.append("Commands")
    out.append("  variometer explain              year-by-year breakdown (default scenario)")
    out.append("  variometer explain --list       every scenario you can name")
    out.append("  variometer explain NAME [opts]  one scenario; --help for all flags")
    out.append("  variometer ui                   open the Streamlit app in a browser")
    out.append("")
    return "\n".join(out)


def run_ui(argv: list[str] | None = None) -> None:
    """Launch the packaged Streamlit app (`variometer ui`).

    Resolves ``variometer/app.py`` inside the *installed* package rather than
    the cwd, so this works from anywhere once the wheel is installed. Extra
    argv is forwarded verbatim to ``streamlit run`` (e.g. ``--server.port``).
    """
    from importlib.resources import as_file, files

    try:
        from streamlit.web import cli as stcli
    except ImportError as exc:  # pragma: no cover - streamlit is a hard dep
        print(f"Cannot launch the UI: streamlit is not importable ({exc}).")
        raise SystemExit(1) from exc

    with as_file(files("variometer").joinpath("app.py")) as app_path:
        if not app_path.is_file():  # pragma: no cover - would mean a broken install
            print(f"Cannot launch the UI: app module missing at {app_path}.")
            raise SystemExit(1)
        sys.argv = ["streamlit", "run", str(app_path), *(argv or [])]
        raise SystemExit(stcli.main())


def main() -> None:
    """Entry point for the ``variometer`` console script.

    Subcommands are dispatched by hand, before the explainer's argparse ever
    sees argv: the explainer takes a bare positional scenario name, so
    ``explain`` / ``ui`` have to be peeled off first or they'd be read as
    scenario names.
    """
    argv = sys.argv[1:]
    cmd = argv[0] if argv else None

    if cmd is None or cmd in ("-h", "--help", "help"):
        print(render_status())
        return
    if cmd in ("-V", "--version", "version"):
        print(f"variometer {_version()}")
        return
    if cmd == "ui":
        run_ui(argv[1:])
        return
    if cmd == "explain":
        explain_main(argv[1:], prog="variometer explain")
        return

    print(f"Unknown command '{cmd}'.\n")
    if not cmd.startswith("-"):
        print(f"Did you mean:  variometer explain {cmd}\n")
    print(render_status())
    sys.exit(2)


if __name__ == "__main__":
    main()
