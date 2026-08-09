"""Variometer — Streamlit UI for the planning model.

Run with:
    uv run streamlit run variometer/app.py    # from a checkout
    variometer ui                             # from an installed package

Without a ``user_data/`` directory the app runs on illustrative placeholder
defaults and shows a first-run banner; see README.md for making it yours.
"""

from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from variometer.cashflow import monthly_forecast
from variometer.config import (
    MODEL_EPOCH,
    PROJECTION_END_YEAR,
    Scenario,
    base_scenario,
    bracket_staleness_warning,
)
from variometer.data.social_security import (
    SS_COLA_DEFAULT,
    ss_annual_income,
)
from variometer.data.social_security import (
    estimate_monthly_benefit as ss_estimate_monthly,
)
from variometer.equity import (
    _price_at_date,
    computed_refresh_values,
    project_flat_refresh,
    projected_refresh_years,
)
from variometer.events import LifeEvent
from variometer.projection import (
    monte_carlo_nw,
    project_net_worth,
    stress_return_path,
)
from variometer.scenarios import (
    SCENARIO_REGISTRY,
    BoundKnob,
    apply_core_overrides,
    grouped_scenario_names,
    is_builtin_scenario,
    knobs_for_scenario,
    visible_scenario_names,
)
from variometer.user_data_io import (
    hide_builtin_scenarios,
    load_historical,
    load_profile,
    load_user_scenarios,
)

# Register personal factories from user_data/scenarios.py (no-op when absent).
load_user_scenarios()

# Scenario dropdown contents: user scenarios first, built-in archetypes after
# (optionally hidden via the "_ui" pref in profile.json).
SCENARIO_CHOICES = list(visible_scenario_names(hide_builtin_scenarios()))
_HAS_USER_SCENARIOS = bool(grouped_scenario_names()[0])


def scenario_label(name: str) -> str:
    """Dropdown label: mark built-ins, but only when there's a distinction to draw."""
    if _HAS_USER_SCENARIOS and name in SCENARIO_REGISTRY and is_builtin_scenario(name):
        return f"{name} · built-in"
    return name


# ─────────────────────────────────────────────
# Generic scenario knob renderer
# ─────────────────────────────────────────────
def render_knob(knob: BoundKnob, key: str):
    """Render a single BoundKnob as the matching Streamlit input.

    Returns the value coerced to the right Python type. The widget is chosen
    by ``knob.kind`` — adding a new kind only requires editing this function.
    """
    if knob.kind == "int":
        return int(
            st.number_input(
                knob.label,
                value=int(knob.default),
                min_value=int(knob.min) if knob.min is not None else None,
                max_value=int(knob.max) if knob.max is not None else None,
                step=int(knob.step) if knob.step is not None else 1,
                format="%d",
                key=key,
                help=knob.help,
            )
        )
    if knob.kind == "dollars":
        return float(
            st.number_input(
                knob.label,
                value=int(knob.default),
                min_value=int(knob.min) if knob.min is not None else None,
                max_value=int(knob.max) if knob.max is not None else None,
                step=int(knob.step) if knob.step is not None else 100,
                format="%d",
                key=key,
                help=knob.help,
            )
        )
    if knob.kind == "percent":
        # Knob values are stored as 0..1 fractions (e.g. 0.0675 for 6.75%).
        # Streamlit's `format` is printf-style and doesn't multiply by 100,
        # so we scale to percentage points for the slider and divide back on
        # read. Pick decimal precision based on step granularity.
        scale = 100.0
        display_min = float(knob.min if knob.min is not None else 0.0) * scale
        display_max = float(knob.max if knob.max is not None else 1.0) * scale
        display_step = float(knob.step if knob.step is not None else 0.01) * scale
        decimals = 2 if display_step < 0.5 else 1
        display_val = st.slider(
            knob.label,
            min_value=display_min,
            max_value=display_max,
            value=float(knob.default) * scale,
            step=display_step,
            format=f"%.{decimals}f%%",
            key=key,
            help=knob.help,
        )
        return float(display_val) / scale
    if knob.kind == "bool":
        return bool(st.checkbox(knob.label, value=bool(knob.default), key=key, help=knob.help))
    if knob.kind == "choice":
        options = list(knob.choices or ())
        idx = options.index(knob.default) if knob.default in options else 0
        return st.selectbox(knob.label, options, index=idx, key=key, help=knob.help)
    if knob.kind == "text":
        return st.text_input(knob.label, value=str(knob.default), key=key, help=knob.help)
    if knob.kind == "float":
        return float(
            st.number_input(
                knob.label,
                value=float(knob.default),
                min_value=float(knob.min) if knob.min is not None else None,
                max_value=float(knob.max) if knob.max is not None else None,
                step=float(knob.step) if knob.step is not None else 1.0,
                key=key,
                help=knob.help,
            )
        )
    raise ValueError(f"Unknown knob kind: {knob.kind!r}")


def render_scenario_knobs(scenario_name: str, key_prefix: str) -> dict:
    """Render all knobs for a scenario; return a kwargs dict for its factory."""
    knobs = knobs_for_scenario(scenario_name)
    if not knobs:
        st.caption("No tunable parameters — uses defaults.")
        return {}
    return {k.name: render_knob(k, key=f"{key_prefix}_{k.name}") for k in knobs}


# ─────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Variometer",
    page_icon="🛬",
    layout="wide",
)

# First-run banner: no profile yet → everything on screen is the placeholder
# persona, not the user's situation.
if not load_profile():
    st.info(
        "**You're looking at illustrative placeholder numbers** — a fictional "
        "senior IC at a public tech company. To model *your* situation, create "
        "`user_data/profile.json` (copy `examples/user_data/` as a starting "
        "point, or use the bootstrap-from-monarch skill). See README.md → "
        "“Make it yours”.",
        icon="👋",
    )

EXTENDED_YEARS = list(range(2026, PROJECTION_END_YEAR + 1))  # Runway + SS analysis horizon


def fmt_d(x: float) -> str:
    return f"${x:,.0f}"


def fmt_pct(x: float) -> str:
    return f"{x:.1%}"


# ─────────────────────────────────────────────
# Sidebar — Scenario inputs
#
# Widget defaults come from ``base_scenario()`` — bare Scenario() defaults
# overlaid with the user's ``user_data/profile.json`` when present. The
# sidebar is a session-local tweak layer on top of that baseline.
# ─────────────────────────────────────────────
DEF = base_scenario()

st.sidebar.title("Scenario Inputs")

with st.sidebar.expander("💼 Compensation", expanded=True):
    base_salary = st.number_input(
        "Base salary", value=int(DEF.base_salary), step=5_000, format="%d"
    )
    salary_growth_pct = st.slider(
        "Salary growth / yr", 0.0, 10.0, DEF.salary_growth * 100, 0.5, format="%.1f%%"
    )
    bonus_rate_pct = st.slider(
        "Bonus rate (% of base)", 0.0, 30.0, DEF.bonus_rate * 100, 1.0, format="%.0f%%"
    )

with st.sidebar.expander("📅 Pay schedule"):
    pay_anchor_val = st.date_input("Anchor payday (any recent Friday)", value=DEF.pay_anchor)

with st.sidebar.expander("📈 Stock / Equity"):
    price_now = st.number_input(
        "Employer stock price ($)", value=float(DEF.price_now), step=1.0, format="%.2f"
    )
    price_growth_pct = st.slider(
        "Annual price growth", -10.0, 20.0, DEF.price_growth * 100, 1.0, format="%.0f%%"
    )
    st.caption(
        "Refresh grants use a flat per-grant target that appreciates each year, "
        "independent of prior grants. Each grant vests over 48 months."
    )
    equity_refresh_value = st.number_input(
        "Refresh grant target ($)",
        value=int(DEF.equity_refresh_grant_value),
        step=5_000,
        format="%d",
        help="Dollar value of each annual refresh grant at the base year (2026).",
    )
    equity_refresh_growth_pct = st.slider(
        "Target growth / yr",
        0.0,
        8.0,
        DEF.equity_refresh_growth * 100,
        0.5,
        format="%.1f%%",
        help="Annual appreciation of the grant target (tracks inflation/salary growth).",
    )

with st.sidebar.expander("🏦 401(k) & IRA"):
    pretax_pct = st.slider("Pre-tax %", 0.0, 25.0, DEF.pretax_401k_pct * 100, 0.1, format="%.1f%%")
    roth_pct = st.slider(
        "Roth 401(k) %",
        0.0,
        25.0,
        DEF.roth_401k_pct * 100,
        0.1,
        format="%.1f%%",
        help="Shares the employee deferral limit with pre-tax. Pre-tax is "
        "allocated first (higher value at a high marginal rate).",
    )
    aftertax_pct = st.slider(
        "After-tax (mega backdoor) %", 0.0, 25.0, DEF.aftertax_401k_pct * 100, 0.1, format="%.1f%%"
    )
    roth_ira_annual = st.number_input(
        "Backdoor Roth IRA / yr ($)",
        value=int(DEF.roth_ira_annual),
        step=500,
        format="%d",
        help="Annual IRA limit; grows with IRS limit inflation. Comes from after-tax cash.",
    )

with st.sidebar.expander("💼 Portfolio (starting balances)"):
    brokerage_balance = st.number_input(
        "Brokerage ($)", value=int(DEF.brokerage_balance), step=5_000, format="%d"
    )
    ira_balance = st.number_input(
        "Rollover / traditional IRA ($)",
        value=int(DEF.ira_balance),
        step=1_000,
        format="%d",
        help="Keep at 0 (rolled into a 401(k)) to stay clear of the pro-rata "
        "rule if you use the backdoor Roth.",
    )
    pretax_401k_balance = st.number_input(
        "Pre-tax 401(k) ($)",
        value=int(DEF.pretax_401k_balance),
        step=1_000,
        format="%d",
    )

with st.sidebar.expander("🧾 Other income & expenses"):
    other_monthly = st.number_input(
        "Other monthly income ($)", value=int(DEF.other_monthly_income), step=50, format="%d"
    )
    ltcg = st.number_input("Annual LTCG ($)", value=int(DEF.ltcg_annual), step=500, format="%d")
    expense_base = st.number_input(
        "Monthly expenses (base)", value=int(DEF.expense_base), step=500, format="%d"
    )
    expense_growth_pct = st.slider(
        "Expense growth / yr", 0.0, 8.0, DEF.expense_growth * 100, 0.5, format="%.1f%%"
    )

with st.sidebar.expander("⚙️ Advanced"):
    return_rate_pct = st.slider(
        "401k / IRA return rate", 3.0, 12.0, DEF.return_rate * 100, 0.5, format="%.1f%%"
    )
    brokerage_return_rate_pct = st.slider(
        "Brokerage after-tax return rate",
        2.0,
        10.0,
        DEF.brokerage_return_rate * 100,
        0.5,
        format="%.1f%%",
        help="Lower than 401k rate to reflect dividend tax + LTCG drag (~1% at this income level).",
    )
    bracket_inflation_pct = st.slider(
        "Bracket inflation", 0.0, 5.0, DEF.bracket_inflation * 100, 0.5, format="%.1f%%"
    )
    deduction_inflation_pct = st.slider(
        "Deduction inflation", 0.0, 5.0, DEF.deduction_inflation * 100, 0.5, format="%.1f%%"
    )
    limit_inflation_pct = st.slider(
        "IRS limit inflation", 0.0, 5.0, DEF.limit_inflation * 100, 0.5, format="%.1f%%"
    )
    itemized_excess = st.number_input(
        "Itemized excess over standard ($)",
        value=int(DEF.itemized_excess_over_standard),
        step=1_000,
        format="%d",
    )
    st.divider()
    tcja_active = st.checkbox(
        "TCJA active ($10k SALT cap)",
        value=DEF.tcja_active,
        help="Uncheck to model pre-TCJA / post-sunset scenario where SALT deduction is uncapped. Only affects housing scenarios with a mortgage.",
    )

# ─────────────────────────────────────────────
# Sidebar values → core_overrides dict
#
# Every sidebar input corresponds 1:1 to a ``Scenario`` dataclass field. The
# dict is the "base defaults" layer that gets overlaid onto any named scenario
# the user selects — see ``apply_core_overrides`` in scenario_registry.py.
# ─────────────────────────────────────────────
core_overrides: dict[str, object] = {
    "base_salary": float(base_salary),
    "salary_growth": salary_growth_pct / 100,
    "bonus_rate": bonus_rate_pct / 100,
    "pay_anchor": pay_anchor_val,
    "price_now": float(price_now),
    "price_growth": price_growth_pct / 100,
    "equity_refresh_grant_value": float(equity_refresh_value),
    "equity_refresh_growth": equity_refresh_growth_pct / 100,
    "brokerage_balance": float(brokerage_balance),
    "ira_balance": float(ira_balance),
    "pretax_401k_balance": float(pretax_401k_balance),
    "roth_ira_annual": float(roth_ira_annual),
    "pretax_401k_pct": pretax_pct / 100,
    "roth_401k_pct": roth_pct / 100,
    "aftertax_401k_pct": aftertax_pct / 100,
    "other_monthly_income": float(other_monthly),
    "ltcg_annual": float(ltcg),
    "itemized_excess_over_standard": float(itemized_excess),
    "expense_base": float(expense_base),
    "expense_growth": expense_growth_pct / 100,
    "return_rate": return_rate_pct / 100,
    "brokerage_return_rate": brokerage_return_rate_pct / 100,
    "bracket_inflation": bracket_inflation_pct / 100,
    "deduction_inflation": deduction_inflation_pct / 100,
    "limit_inflation": limit_inflation_pct / 100,
    "tcja_active": tcja_active,
}


def build_scenario(factory_name: str, factory_kwargs: dict | None = None) -> Scenario:
    """Construct a scenario from a registry factory + overlay sidebar defaults.

    Use this everywhere a tab needs the user's "current scenario" — it's the
    single point where the sidebar's global defaults meet the named scenario's
    perturbations. ``apply_core_overrides`` skips any field the factory itself
    has already customized, so scenario-specific knobs win over sidebar values.
    """
    factory = SCENARIO_REGISTRY[factory_name]
    s = factory(**(factory_kwargs or {}))
    return apply_core_overrides(s, core_overrides)


# Sidebar-only scenario for tabs that aren't tied to a named scenario (Vest
# Schedule and Monthly Forecast). Equivalent to "Baseline with sidebar
# overrides" — picks up everything the user has dialed in the sidebar.
sidebar_scenario = build_scenario("Stay put")

# ─────────────────────────────────────────────
# Tabs — the Historical tab only exists when user_data/historical.csv does
# ─────────────────────────────────────────────
historical_rows = load_historical()

_tab_names = ["🏃 Runway", "💎 Vest Schedule"]
if historical_rows:
    _tab_names.append("📜 Historical")
_tab_names.append("📅 Monthly Forecast")

tab = dict(zip(_tab_names, st.tabs(_tab_names), strict=True))


# ═══════════════════════════════════════════════
# TAB — Monthly Forecast (debugging view; pure sidebar scenario)
# ═══════════════════════════════════════════════
with tab["📅 Monthly Forecast"]:
    st.header("Monthly Forecast")
    st.caption(
        "Debugging view of the sidebar scenario's monthly cashflow. For the "
        "primary narrative (drawdown, runway, retirement), use the Runway tab."
    )

    monthly_results = monthly_forecast(sidebar_scenario, date(2026, 1, 1), date(2030, 12, 1))
    df_m = pd.DataFrame(
        [
            {
                "Month": f"{r.year}-{r.month:02d}",
                "Paydays": r.paydays_in_month,
                "Base Gross": r.base_paycheck_gross,
                "Bonus": r.bonus_gross,
                "RSU Gross": r.rsu_gross,
                "Other Income": r.other_income,
                "Est. Tax": r.estimated_tax,
                "Pre-tax 401k": r.pretax_401k,
                "Roth/AT Savings": r.roth_aftertax_savings,
                "Backdoor Roth IRA": r.backdoor_roth_contrib,
                "Net to Bank": r.net_to_bank,
                "Expenses": r.expenses,
                "Net Cash Flow": r.net_cash_flow,
                "Brokerage Balance": r.brokerage_balance,
            }
            for r in monthly_results
        ]
    )

    col1, col2 = st.columns(2)

    with col1:
        fig_income_m = px.bar(
            df_m,
            x="Month",
            y=["Base Gross", "Bonus", "RSU Gross"],
            title="Monthly Gross Income by Source",
            labels={"value": "$", "variable": "Source"},
            barmode="stack",
        )
        fig_income_m.update_xaxes(tickangle=45, nticks=20)
        st.plotly_chart(fig_income_m, width="stretch")

    with col2:
        fig_cum = go.Figure()
        fig_cum.add_trace(
            go.Scatter(
                x=df_m["Month"],
                y=df_m["Brokerage Balance"],
                mode="lines",
                name="Brokerage Balance",
                fill="tozeroy",
                line=dict(color="#2ecc71"),
            )
        )
        fig_cum.add_trace(
            go.Bar(
                x=df_m["Month"],
                y=df_m["Net to Bank"],
                name="Net to Bank",
                opacity=0.6,
                marker_color="#3498db",
            )
        )
        fig_cum.update_layout(
            title="Net to Bank & Cumulative Cash",
            yaxis_title="$",
            xaxis_tickangle=45,
        )
        st.plotly_chart(fig_cum, width="stretch")

    # Formatted display table
    money_cols = [
        "Base Gross",
        "Bonus",
        "RSU Gross",
        "Other Income",
        "Est. Tax",
        "Pre-tax 401k",
        "Roth/AT Savings",
        "Backdoor Roth IRA",
        "Net to Bank",
        "Expenses",
        "Net Cash Flow",
        "Brokerage Balance",
    ]
    df_m_display = df_m.copy()
    for col in money_cols:
        df_m_display[col] = df_m_display[col].map(fmt_d)
    st.dataframe(df_m_display, width="stretch", hide_index=True)


# ═══════════════════════════════════════════════
# TAB — Runway (primary view: scenario picker, drawdown, MC, retirement)
# ═══════════════════════════════════════════════
with tab["🏃 Runway"]:
    st.header("Runway — Drawdown Sequence")
    st.caption(
        "Year-by-year view of which accounts get tapped when cash flow goes negative, "
        "the realized LTCG, all-in tax cost, and remaining liquid runway. "
        "Sidebar inputs (return rate, employer stock price, base salary, etc.) flow into "
        "this scenario as base defaults."
    )

    # ── Modeling envelope — always visible, next to the numbers it qualifies ──
    st.info(
        "**Model assumes:** "
        f"{DEF.filing_status.replace('_', ' ')} filer · "
        f"{DEF.state} resident · "
        "no dependents · sell-at-vest · "
        f"tax tables pinned to {MODEL_EPOCH}",
        icon="📐",
    )
    _staleness = bracket_staleness_warning()
    if _staleness:
        st.warning(_staleness, icon="⚠️")

    rw_col1, rw_col2 = st.columns([2, 3])
    with rw_col1:
        rw_scenario_name = st.selectbox(
            "Scenario to inspect",
            SCENARIO_CHOICES,
            index=0,  # user scenarios sort first, so this favors them when present
            key="runway_scenario",
            format_func=scenario_label,
        )

    # ── Runway tweaks — rendered from factory signature ─────────────────────
    with st.expander("⚙ Scenario parameters", expanded=False):
        rw_kwargs = render_scenario_knobs(rw_scenario_name, key_prefix=f"rw_{rw_scenario_name}")

    rw_sc = build_scenario(rw_scenario_name, rw_kwargs)
    if rw_sc.summary:
        st.caption(f"ℹ️ {rw_sc.summary}")

    # ── Optional compare-against overlay ────────────────────────────────────
    compare_options = ["(off)"] + [n for n in SCENARIO_CHOICES if n != rw_scenario_name]
    compare_name = st.selectbox(
        "Compare against",
        compare_options,
        index=0,
        key="rw_compare_name",
        format_func=scenario_label,
        help=(
            "Overlay another scenario's Total NW on the liquid-balances chart, with "
            "delta metrics at 2035 / 2045. Uses each scenario's factory defaults "
            "(no per-scenario tweak popover) to keep this control lightweight. "
            "User-level toggles below (Social Security, retirement cutoff) apply "
            "equally to both scenarios."
        ),
    )
    # Build the comparison scenario object up-front, but apply user-level
    # overrides (SS, retirement_year) further down where rw_sc gets them — so
    # both scenarios reflect the same "when do you claim SS / retire" decision.
    compare_sc = build_scenario(compare_name) if compare_name != "(off)" else None

    # ── Social Security configuration ───────────────────────────────────────
    with st.expander("🏛 Social Security", expanded=False):
        ss_enabled = st.checkbox("Include Social Security income", value=False, key="rw_ss_enabled")
        if ss_enabled:
            ss_input_col1, ss_input_col2 = st.columns(2)
            with ss_input_col1:
                ss_claim_age = st.slider(
                    "Claim age",
                    62,
                    70,
                    67,
                    key="rw_ss_claim_age",
                    help="67 = Full Retirement Age for anyone born 1960+.",
                )
                ss_input_mode = st.radio(
                    "Benefit input",
                    ["Estimator", "Direct entry"],
                    key="rw_ss_mode",
                    horizontal=True,
                )
            with ss_input_col2:
                if ss_input_mode == "Estimator":
                    ss_prior_yrs = st.number_input(
                        "Prior career years", value=10, step=1, min_value=0, key="rw_ss_prior_yrs"
                    )
                    ss_prior_salary = st.number_input(
                        "Prior career avg salary ($)",
                        value=100_000,
                        step=5_000,
                        format="%d",
                        key="rw_ss_prior_sal",
                    )
                    ss_monthly_today = ss_estimate_monthly(
                        current_age=rw_sc.age,
                        # pre-event baseline salary (events never mutate the field)
                        current_salary=rw_sc.base_salary,
                        prior_career_avg_salary=float(ss_prior_salary),
                        prior_career_years=int(ss_prior_yrs),
                        claim_age=ss_claim_age,
                    )
                    st.metric("Estimated monthly benefit (today's $)", fmt_d(ss_monthly_today))
                else:
                    ss_monthly_today = st.number_input(
                        "Monthly benefit today's $ (from ssa.gov)",
                        value=3_500,
                        step=100,
                        format="%d",
                        key="rw_ss_direct",
                    )

            ss_claim_year = 2026 + (ss_claim_age - rw_sc.age)
            st.caption(
                f"SS starts in **{ss_claim_year}** (age {ss_claim_age}). "
                f"Benefit grows at {SS_COLA_DEFAULT:.1%}/yr COLA. "
                f"85% included in federal taxable income; CA exempts SS entirely."
            )

            # Inject ss_start event into the scenario (rw_sc is freshly constructed above).
            # Apply the same event to compare_sc so both projections see SS income.
            _ss_event = LifeEvent(
                year=ss_claim_year,
                kind="ss_start",
                params={"monthly_benefit_today_dollars": float(ss_monthly_today)},
            )
            rw_sc.events = list(rw_sc.events) + [_ss_event]
            if compare_sc is not None:
                compare_sc.events = list(compare_sc.events) + [_ss_event]
        else:
            ss_claim_year = None
            ss_monthly_today = 0.0
            ss_claim_age = 67

    # ── Retirement income cutoff ─────────────────────────────────────────────
    with st.expander("🏖 Retirement income cutoff", expanded=True):
        retire_enabled = st.checkbox(
            "Stop all employment income at a specified year",
            value=True,
            key="rw_retire_enabled",
        )
        if retire_enabled:
            # Default = the year the scenario's persona turns 65.
            rw_ret_cutoff_year = st.slider(
                "Retire in year",
                min_value=2026,
                max_value=PROJECTION_END_YEAR,
                value=min(PROJECTION_END_YEAR, 2026 + (65 - rw_sc.age)),
                key="rw_ret_cutoff_year",
            )
            rw_ret_cutoff_age = rw_sc.age + (rw_ret_cutoff_year - 2026)
            rw_sc.retirement_year = rw_ret_cutoff_year
            # Apply the same retirement cutoff to the compare scenario so the
            # two projections share the same "you retire here" assumption.
            if compare_sc is not None:
                compare_sc.retirement_year = rw_ret_cutoff_year
            st.caption(
                f"Income drops to $0 in **{rw_ret_cutoff_year}** (age **{rw_ret_cutoff_age}**). "
                "Scenarios with an earlier employment-end (sabbatical, change jobs) "
                "already stop employer income before this year — this only caps any post-transition job."
            )
        else:
            rw_ret_cutoff_year = None

    # Use extended horizon (2026-2060) so SS income and retirement sustainability are visible
    rw_nw = project_net_worth(rw_sc, EXTENDED_YEARS)

    # ── Pre-compute insolvency metrics ──────────────────────────────────────
    # Use a threshold of $1 to filter out floating-point convergence residuals
    # (the waterfall's $10 convergence cap can leave tiny residuals < $1).
    _INSOLVENCY_THRESHOLD = 1.0
    insolvency_years = [r for r in rw_nw if r.unfunded_shortfall > _INSOLVENCY_THRESHOLD]
    first_insolvency = insolvency_years[0].year if insolvency_years else None
    cumulative_unfunded = 0.0
    cumulative_unfunded_by_year: dict[int, float] = {}
    for row in rw_nw:
        cumulative_unfunded += row.unfunded_shortfall
        cumulative_unfunded_by_year[row.year] = cumulative_unfunded

    # ── Summary metrics ─────────────────────────────────────────────────────
    mc1, mc2, mc3 = st.columns(3)
    drawdown_years = [r for r in rw_nw if r.withdrawal is not None]
    with mc1:
        if first_insolvency:
            st.metric(
                "Assets exhausted",
                str(first_insolvency),
                delta=f"age {rw_sc.age + (first_insolvency - 2026)}",
                delta_color="inverse",
            )
        else:
            st.metric("Assets exhausted", "Not in window ✓")
    with mc2:
        st.metric(
            "Drawdown years",
            len(drawdown_years),
            delta=f"{drawdown_years[0].year}–{drawdown_years[-1].year}"
            if drawdown_years
            else "none",
        )
    with mc3:
        total_penalty = sum(r.withdrawal.total_penalty for r in rw_nw if r.withdrawal)
        st.metric("Total 401k penalties paid", fmt_d(total_penalty))

    # ── Insolvency warning ──────────────────────────────────────────────────
    if insolvency_years:
        st.error(
            f"⚠️ **Assets exhausted in {first_insolvency}** "
            f"(age {rw_sc.age + (first_insolvency - 2026)}). "
            f"All waterfall buckets (brokerage → Roth IRA → Roth conversions → "
            f"Roth IRA earnings → pre-tax 401k) are exhausted. "
            f"Unfunded gap grows to {fmt_d(cumulative_unfunded_by_year[EXTENDED_YEARS[-1]])} "
            f"cumulative by {EXTENDED_YEARS[-1]}."
        )
    elif drawdown_years:
        st.info(
            f"Waterfall covers all shortfalls through {EXTENDED_YEARS[-1]}. "
            f"Fires in {len(drawdown_years)} year(s): "
            + ", ".join(str(r.year) for r in drawdown_years[:8])
            + ("…" if len(drawdown_years) > 8 else ".")
        )
    else:
        st.success("No drawdown years — cash flow is positive throughout the window.")

    # ── Scenario Timeline ────────────────────────────────────────────────────
    st.subheader("Scenario Timeline")
    st.caption("Key events in this scenario. Hover any marker for details.")

    # Build timeline data
    _tl_events = []

    def _tl_add(year, month, label, category, detail, color, symbol):
        _tl_events.append(
            {
                "year": year + (month - 1) / 12,
                "year_label": f"{year}-{month:02d}",
                "label": label,
                "category": category,
                "detail": detail,
                "color": color,
                "symbol": symbol,
            }
        )

    # Always: projection start
    _tl_add(
        2026,
        1,
        "Projection start",
        "Career",
        f"Base salary: {fmt_d(rw_sc.base_salary)}/yr",
        "#3498db",
        "circle",
    )

    # Parse scenario events
    for _evt in rw_sc.events:
        if _evt.kind == "employment_end":
            _tl_add(
                _evt.year,
                _evt.month,
                "Leave job",
                "Career",
                f"Employment ends {_evt.year}-{_evt.month:02d}. "
                "RSU vests stop, employer salary/bonus/match end.",
                "#e74c3c",
                "x",
            )
        elif _evt.kind == "new_job":
            _b = _evt.params.get("base", 0)
            _eq = _evt.params.get("equity_annual", 0)
            _br = _evt.params.get("bonus_rate", 0)
            _tl_add(
                _evt.year,
                _evt.month,
                f"New job — {fmt_d(_b)}/yr",
                "Career",
                f"Base: {fmt_d(_b)}, Equity: {fmt_d(_eq)}/yr, "
                f"Bonus: {_br:.0%}, Healthcare: {fmt_d(_evt.params.get('healthcare_monthly', 0))}/mo",
                "#2ecc71",
                "circle",
            )
        elif _evt.kind == "house_purchase":
            _p = _evt.params.get("price", 0)
            _dp = _evt.params.get("down_pct", 0)
            _r = _evt.params.get("rate", 0)
            _tl_add(
                _evt.year,
                _evt.month,
                f"Buy home — {fmt_d(_p)}",
                "Housing",
                f"Price: {fmt_d(_p)}, Down: {_dp:.0%} ({fmt_d(_p * _dp)}), "
                f"Rate: {_r:.2%}, Term: {_evt.params.get('term_years', 30)}yr. "
                f"Down payment drawn from brokerage.",
                "#9b59b6",
                "square",
            )
        elif _evt.kind == "relocation":
            _rent = _evt.params.get("new_monthly_rent", 0)
            _tl_add(
                _evt.year,
                _evt.month,
                f"Relocate — {_evt.params.get('to_label', '')}",
                "Housing",
                f"New rent: {fmt_d(_rent)}/mo" if _rent else "New location",
                "#8e44ad",
                "square-open",
            )
        elif _evt.kind == "expense_change":
            _delta = _evt.params.get("delta_monthly", 0)
            _lbl = _evt.params.get("label", "")
            _ends = _evt.params.get("ends_year")
            _sign = "+" if _delta >= 0 else "−"
            _amt_str = f"{_sign}{fmt_d(abs(_delta))}/mo"
            _start_label = f"{_lbl} ({_amt_str})" if _lbl else f"Expenses {_amt_str}"
            _detail_end = (
                f" Active through {_ends}; reverts to baseline in {_ends + 1}."
                if _ends is not None
                else " No end date — permanent change."
            )
            _tl_add(
                _evt.year,
                _evt.month,
                _start_label,
                "Lifestyle",
                f"Monthly expense delta: {_amt_str} starting {_evt.year}-{_evt.month:02d}."
                + _detail_end,
                "#f39c12",
                "diamond",
            )
            if _ends is not None:
                _end_label = f"End: {_lbl} ({_amt_str})" if _lbl else f"Expenses {_amt_str} ends"
                _tl_add(
                    _ends + 1,
                    1,
                    _end_label,
                    "Lifestyle",
                    f"Expense delta of {_amt_str} ends; "
                    f"monthly expenses revert to baseline trajectory in {_ends + 1}.",
                    "#f39c12",
                    "diamond-open",
                )
        elif _evt.kind == "one_time_expense":
            _amt = _evt.params.get("amount", 0)
            _lbl = _evt.params.get("label", "One-time expense")
            _tl_add(
                _evt.year,
                _evt.month,
                _lbl,
                "Lifestyle",
                f"One-time cost: {fmt_d(_amt)}, hits cash flow in {_evt.year}-{_evt.month:02d}.",
                "#e67e22",
                "diamond-open",
            )
        elif _evt.kind == "child":
            _tl_add(
                _evt.year,
                _evt.month,
                "Child (marker)",
                "Lifestyle",
                "Timeline marker only — model childcare costs with an expense_change event.",
                "#e91e63",
                "star",
            )
        elif _evt.kind == "ss_start":
            _mo = _evt.params.get("monthly_benefit_today_dollars", 0)
            _tl_add(
                _evt.year,
                _evt.month,
                "Social Security starts",
                "Benefits",
                f"Monthly benefit: {fmt_d(_mo)} (today's $). "
                f"COLA {SS_COLA_DEFAULT:.1%}/yr. 85% federally taxable; CA exempt.",
                "#27ae60",
                "star",
            )
        elif _evt.kind == "roth_conversion":
            _amt = _evt.params.get("amount", 0)
            _tl_add(
                _evt.year,
                _evt.month,
                f"Roth conversion — {fmt_d(_amt)}",
                "Tax",
                f"Amount: {fmt_d(_amt)}, recurring: {_evt.params.get('recurring', False)}, "
                f"seasoned (penalty-free) after 5yr",
                "#16a085",
                "triangle-up",
            )
        elif _evt.kind == "brokerage_deposit":
            _amt = _evt.params.get("amount", 0)
            _lbl = _evt.params.get("label", "Brokerage deposit")
            _tl_add(
                _evt.year,
                _evt.month,
                _lbl,
                "Income",
                f"Post-tax deposit to brokerage: {fmt_d(_amt)} (100% cost basis)",
                "#2980b9",
                "triangle-up",
            )

    # Derived from projection results
    if drawdown_years:
        _tl_add(
            drawdown_years[0].year,
            1,
            "First drawdown",
            "Drawdown",
            f"Cash flow goes negative; waterfall begins tapping savings. "
            f"Total drawdown years: {len(drawdown_years)}.",
            "#c0392b",
            "triangle-down",
        )
    if first_insolvency:
        _tl_add(
            first_insolvency,
            1,
            "Assets exhausted",
            "Drawdown",
            "All liquid buckets depleted; unfunded shortfall begins.",
            "#7f0000",
            "x-open",
        )

    # Reference markers
    _tl_retire_age = ss_claim_age if ss_enabled else 65
    _tl_retire_year = 2026 + (_tl_retire_age - rw_sc.age)
    _tl_add(
        _tl_retire_year,
        1,
        f"Retirement reference (age {_tl_retire_age})",
        "Milestones",
        f"Projected NW at age {_tl_retire_age}: "
        f"{fmt_d(next((r.total_nw for r in rw_nw if r.year == _tl_retire_year), 0))}",
        "#95a5a6",
        "line-ns",
    )

    if _tl_events:
        _df_tl = pd.DataFrame(_tl_events)

        # Merge events that share the same (category, year, month) into a
        # single marker — otherwise overlapping markers hide each other's
        # tooltips. This is what happens when e.g. the sabbatical scenario
        # fires both a "lean spending" and an "ACA premium" expense event at
        # the same month in the same Lifestyle row.
        _merged_events: list[dict] = []
        for (_cat, _yl), _group in _df_tl.groupby(["category", "year_label"], sort=False):
            if len(_group) == 1:
                _merged_events.append(_group.iloc[0].to_dict())
                continue
            _first = _group.iloc[0].to_dict()
            _first["label"] = " + ".join(_group["label"].tolist())
            _first["detail"] = "<br>──────<br>".join(_group["detail"].tolist())
            # If symbols differ within the group, keep the first one — they're
            # the same shape in all current event-overlap cases.
            _merged_events.append(_first)
        _df_tl = pd.DataFrame(_merged_events)

        _cat_order = [
            "Career",
            "Housing",
            "Lifestyle",
            "Tax",
            "Benefits",
            "Income",
            "Milestones",
            "Drawdown",
        ]
        _colors = {
            "Career": "#3498db",
            "Housing": "#9b59b6",
            "Lifestyle": "#f39c12",
            "Tax": "#16a085",
            "Benefits": "#27ae60",
            "Income": "#2980b9",
            "Milestones": "#95a5a6",
            "Drawdown": "#e74c3c",
        }

        fig_tl = go.Figure()
        for _cat in _cat_order:
            _sub = _df_tl[_df_tl["category"] == _cat]
            if _sub.empty:
                continue
            fig_tl.add_trace(
                go.Scatter(
                    x=_sub["year"],
                    y=[_cat] * len(_sub),
                    mode="markers",
                    name=_cat,
                    marker=dict(
                        symbol=_sub["symbol"].tolist(),
                        size=16,
                        color=_colors.get(_cat, "#888"),
                        line=dict(width=1.5, color="white"),
                    ),
                    customdata=list(
                        zip(_sub["label"], _sub["year_label"], _sub["detail"], strict=False)
                    ),
                    hovertemplate=(
                        "<b>%{customdata[0]}</b><br>"
                        "Year: %{customdata[1]}<br>"
                        "%{customdata[2]}<extra></extra>"
                    ),
                )
            )

        fig_tl.update_layout(
            height=280,
            xaxis=dict(
                title="Year", range=[2025, EXTENDED_YEARS[-1] + 1], tickmode="linear", dtick=2
            ),
            yaxis=dict(title="", categoryorder="array", categoryarray=_cat_order[::-1]),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=20, r=20, t=40, b=20),
            hovermode="closest",
        )
        st.plotly_chart(fig_tl, width="stretch")

    # ── Drawdown table ──────────────────────────────────────────────────────
    st.subheader("Year-by-year drawdown detail")
    # Build cashflow lookup for SS income per year (from annual_summary)
    from variometer.cashflow import annual_summary as _annual_summary

    _rw_ar_cache: dict[int, object] = {}

    def _rw_ar(year: int):
        if year not in _rw_ar_cache:
            _rw_ar_cache[year] = _annual_summary(rw_sc, year)
        return _rw_ar_cache[year]

    # Only surface housing / Roth 401k columns when relevant for this scenario.
    _has_home_equity = any(r.home_equity > 0 for r in rw_nw)
    _has_roth_401k = any(r.roth_401k > 0 for r in rw_nw)

    rw_rows = []
    for row in rw_nw:
        wr = row.withdrawal
        insolvent = row.unfunded_shortfall > _INSOLVENCY_THRESHOLD
        ar = _rw_ar(row.year)
        ss_this_year = ar.ss_income if hasattr(ar, "ss_income") else 0.0
        age_label = f"{rw_sc.age + (row.year - 2026)}"
        rw_row = {
            "Year": row.year,
            "Age": age_label,
            "Income": fmt_d(row.take_home_income),
            "Expenses": fmt_d(row.total_expenses),
        }
        if ss_this_year > 0:
            rw_row["SS /mo"] = fmt_d(ss_this_year / 12)
        else:
            rw_row["SS /mo"] = "—"
        rw_row.update(
            {
                "Shortfall": fmt_d(wr.shortfall_before_tax) if wr else "—",
                "Withdrawn": fmt_d(wr.total_withdrawn) if wr else "—",
                "Unfunded ⚠": fmt_d(row.unfunded_shortfall) if insolvent else "—",
                "Realized LTCG": fmt_d(wr.total_realized_ltcg) if wr else "—",
                "Penalty": fmt_d(wr.total_penalty) if wr else "—",
                "ACA /mo": fmt_d(row.aca_subsidy_monthly) if row.aca_subsidy_monthly > 0 else "—",
                "Brokerage": fmt_d(row.brokerage),
                "Roth IRA": fmt_d(row.roth_ira),
            }
        )
        if _has_roth_401k:
            rw_row["Roth 401k"] = fmt_d(row.roth_401k)
        rw_row["Pretax 401k"] = fmt_d(row.pretax_401k)
        # Home equity sits between liquid accounts and Total NW so the table
        # reads "liquid buckets → illiquid → total". Only shown when nonzero.
        if _has_home_equity:
            cohort_total = sum(row.conversion_cohorts.values())
            liquid_total = (
                row.brokerage
                + row.roth_ira
                + row.pretax_401k
                + row.roth_401k
                + row.ira
                + cohort_total
            )
            rw_row["Liquid NW"] = fmt_d(liquid_total)
            rw_row["Home Equity 🏠"] = fmt_d(row.home_equity)
        rw_row["Total NW"] = fmt_d(row.total_nw)
        rw_rows.append(rw_row)

    st.dataframe(pd.DataFrame(rw_rows), width="stretch", hide_index=True)
    if _has_home_equity:
        roth_401k_note = " + Roth 401k (mega backdoor)" if _has_roth_401k else ""
        st.caption(
            "🏠 Home Equity is **illiquid** — the drawdown waterfall cannot tap it. "
            "It contributes to Total NW but not to runway. "
            f"*Liquid NW* = brokerage + Roth IRA{roth_401k_note} + pre-tax 401(k) + rollover IRA "
            "+ Roth conversion cohorts (the drawdown pool)."
        )

    # ── Liquid assets + cumulative deficit chart ────────────────────────────
    st.subheader("Liquid account balances over time")

    fig_liquid = go.Figure()
    fig_liquid.add_trace(
        go.Scatter(
            x=[r.year for r in rw_nw],
            y=[r.brokerage for r in rw_nw],
            name="Brokerage",
            stackgroup="one",
            fillcolor="rgba(52, 152, 219, 0.6)",
            line=dict(color="rgba(52, 152, 219, 0.8)"),
        )
    )
    fig_liquid.add_trace(
        go.Scatter(
            x=[r.year for r in rw_nw],
            y=[r.roth_ira_principal for r in rw_nw],
            name="Roth IRA Principal (accessible)",
            stackgroup="one",
            fillcolor="rgba(46, 204, 113, 0.6)",
            line=dict(color="rgba(46, 204, 113, 0.8)"),
        )
    )
    fig_liquid.add_trace(
        go.Scatter(
            x=[r.year for r in rw_nw],
            y=[r.pretax_401k for r in rw_nw],
            name="Pretax 401k (penalty if <59½)",
            stackgroup="one",
            fillcolor="rgba(230, 126, 34, 0.5)",
            line=dict(color="rgba(230, 126, 34, 0.8)"),
        )
    )
    if _has_roth_401k:
        fig_liquid.add_trace(
            go.Scatter(
                x=[r.year for r in rw_nw],
                y=[r.roth_401k for r in rw_nw],
                name="Roth 401k (mega backdoor)",
                stackgroup="one",
                fillcolor="rgba(241, 196, 15, 0.45)",
                line=dict(color="rgba(241, 196, 15, 0.8)"),
            )
        )
    _seasoned_by_year = [
        sum(v for cy, v in r.conversion_cohorts.items() if r.year - cy >= 5) for r in rw_nw
    ]
    _unseasoned_by_year = [
        sum(v for cy, v in r.conversion_cohorts.items() if r.year - cy < 5) for r in rw_nw
    ]
    _has_conversions = any(v > 0 for v in _seasoned_by_year) or any(
        v > 0 for v in _unseasoned_by_year
    )
    if _has_conversions:
        fig_liquid.add_trace(
            go.Scatter(
                x=[r.year for r in rw_nw],
                y=_seasoned_by_year,
                name="Seasoned Roth Conversions",
                stackgroup="one",
                fillcolor="rgba(155, 89, 182, 0.5)",
                line=dict(color="rgba(155, 89, 182, 0.8)"),
            )
        )
        fig_liquid.add_trace(
            go.Scatter(
                x=[r.year for r in rw_nw],
                y=_unseasoned_by_year,
                name="Roth Conversions (unseasoned, <5yr)",
                stackgroup="one",
                fillcolor="rgba(155, 89, 182, 0.25)",
                line=dict(color="rgba(155, 89, 182, 0.6)", dash="dot"),
            )
        )
    fig_liquid.add_trace(
        go.Scatter(
            x=[r.year for r in rw_nw],
            y=[max(0.0, r.roth_ira - r.roth_ira_principal) for r in rw_nw],
            name="Roth IRA Earnings (non-qualified if <59½)",
            stackgroup="one",
            fillcolor="rgba(26, 188, 156, 0.5)",
            line=dict(color="rgba(26, 188, 156, 0.8)"),
        )
    )
    # Overlay: home equity as a separate (non-stacked) line — illiquid wealth
    # that contributes to Total NW but is invisible to the waterfall. Shown
    # only when the scenario has a house so we don't draw a flat-zero line.
    if _has_home_equity:
        fig_liquid.add_trace(
            go.Scatter(
                x=[r.year for r in rw_nw],
                y=[r.home_equity for r in rw_nw],
                name="🏠 Home Equity (illiquid — not in drawdown pool)",
                mode="lines",
                line=dict(color="rgba(142, 68, 173, 0.95)", width=2.5, dash="dot"),
            )
        )

    # Overlay: cumulative unfunded deficit as a negative-going line
    if insolvency_years:
        fig_liquid.add_trace(
            go.Scatter(
                x=[r.year for r in rw_nw],
                y=[-cumulative_unfunded_by_year[r.year] for r in rw_nw],
                name="Cumulative unfunded deficit",
                mode="lines",
                line=dict(color="rgba(231, 76, 60, 0.9)", width=2, dash="dash"),
            )
        )
    # Mark employment transitions so the chart clearly shows where income changes
    for _ee_ev in [e for e in rw_sc.events if e.kind == "employment_end"]:
        fig_liquid.add_vline(
            x=_ee_ev.year,
            line_dash="dash",
            line_color="rgba(231, 76, 60, 0.6)",
            annotation_text="Work ends",
            annotation_position="top left",
        )
    for _nj_ev in [e for e in rw_sc.events if e.kind == "new_job"]:
        fig_liquid.add_vline(
            x=_nj_ev.year,
            line_dash="dash",
            line_color="rgba(46, 204, 113, 0.6)",
            annotation_text="New job",
            annotation_position="top right",
        )
    # Retirement cutoff — bold line so it's visually distinct from a mid-career break
    if retire_enabled and rw_ret_cutoff_year is not None:
        fig_liquid.add_vline(
            x=rw_ret_cutoff_year,
            line_dash="dot",
            line_color="rgba(241, 196, 15, 0.85)",
            line_width=2,
            annotation_text=f"Retirement (age {rw_ret_cutoff_age})",
            annotation_position="top left",
            annotation_font_color="rgba(241, 196, 15, 0.9)",
        )

    # Optional compare-against overlay — Total NW line for a second scenario.
    if compare_sc is not None:
        compare_nw = project_net_worth(compare_sc, EXTENDED_YEARS)
        fig_liquid.add_trace(
            go.Scatter(
                x=[r.year for r in compare_nw],
                y=[r.total_nw for r in compare_nw],
                name=f"Compare — {compare_name} (Total NW)",
                mode="lines",
                line=dict(color="rgba(231, 126, 34, 0.9)", width=2, dash="dash"),
            )
        )
        # Primary scenario's Total NW as a thin reference so the delta is legible
        fig_liquid.add_trace(
            go.Scatter(
                x=[r.year for r in rw_nw],
                y=[r.total_nw for r in rw_nw],
                name=f"{rw_scenario_name} (Total NW)",
                mode="lines",
                line=dict(color="rgba(52, 152, 219, 0.9)", width=2),
            )
        )

    _chart_title = (
        f"Liquid balances (stacked) + home equity overlay — {rw_scenario_name}"
        if _has_home_equity
        else f"Liquid account balances — {rw_scenario_name}"
    )
    fig_liquid.update_layout(
        title=_chart_title,
        xaxis_title="Year",
        yaxis_title="$",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig_liquid, width="stretch")

    # Compare-against deltas — surfaced inline so the overlay is meaningful
    if compare_sc is not None:
        primary_nw_by_year = {r.year: r.total_nw for r in rw_nw}
        compare_nw_by_year = {r.year: r.total_nw for r in compare_nw}
        cmp_cols = st.columns(3)
        for col, year_label, year in zip(
            cmp_cols,
            ("2035", "2045", str(EXTENDED_YEARS[-1])),
            (2035, 2045, EXTENDED_YEARS[-1]),
            strict=True,
        ):
            primary_val = primary_nw_by_year.get(year, 0.0)
            compare_val = compare_nw_by_year.get(year, 0.0)
            delta = compare_val - primary_val
            col.metric(
                label=f"NW @ {year_label} — {compare_name}",
                value=fmt_d(compare_val),
                delta=f"{delta:+,.0f} vs {rw_scenario_name}",
            )

    # ── Per-year bucket breakdown ───────────────────────────────────────────
    drawdown_year_opts = [r.year for r in rw_nw if r.withdrawal is not None]
    if drawdown_year_opts:
        st.subheader("Bucket breakdown for a selected year")
        selected_year = st.selectbox("Year", drawdown_year_opts, key="runway_year_detail")
        selected_row = next(r for r in rw_nw if r.year == selected_year)
        wr = selected_row.withdrawal
        if wr:

            def _pretty_bucket(source: str) -> str:
                if source == "brokerage":
                    return "Brokerage (LTCG)"
                if source == "roth_ira_principal":
                    return "Roth IRA Principal"
                if source == "roth_ira_growth":
                    return "Roth IRA Earnings (non-qualified)"
                if source == "401k_early":
                    return "Pre-tax 401k"
                if source.startswith("roth_conversion_"):
                    yr = source.split("_")[-1]
                    return f"Roth Conversion {yr}"
                return source

            leg_rows = []
            for leg in wr.legs:
                leg_rows.append(
                    {
                        "Bucket": _pretty_bucket(leg.source),
                        "Gross Pull": fmt_d(leg.amount),
                        "Realized LTCG": fmt_d(leg.taxable_ltcg),
                        "Ordinary Income": fmt_d(leg.taxable_ordinary),
                        "Penalty": fmt_d(leg.penalty),
                    }
                )
            st.dataframe(pd.DataFrame(leg_rows), width="stretch", hide_index=True)
            detail_parts = [
                f"Shortfall: {fmt_d(wr.shortfall_before_tax)}",
                f"Converged: {wr.converged}",
                f"Iterations: {wr.iterations}",
            ]
            if wr.unfunded_shortfall > _INSOLVENCY_THRESHOLD:
                detail_parts.append(f"⚠ Unfunded: {fmt_d(wr.unfunded_shortfall)}")
            st.caption(" | ".join(detail_parts))

    # ── Market Risk: deterministic stress paths + Monte Carlo ───────────────
    with st.expander("📉 Market risk: stress paths & Monte Carlo", expanded=False):
        st.caption(
            "Apply a deterministic stress path or Monte Carlo simulation to see how "
            "market uncertainty shifts the trajectory of the selected scenario."
        )

        mkt_col1, mkt_col2 = st.columns([2, 3])
        with mkt_col1:
            mkt_mode = st.radio(
                "Market conditions",
                ["Flat (baseline)", "Bear market", "Slow growth (4%)", "Monte Carlo fan"],
                index=0,
                key="rw_mkt_mode",
                help=(
                    "Flat: uses sidebar return rate every year. Bear: one severe crash "
                    "then recovery. Slow growth: persistent 4% returns. Monte Carlo: "
                    "thousands of random return sequences to show the outcome distribution."
                ),
            )
            if mkt_mode == "Bear market":
                mkt_crash_year = st.slider(
                    "Crash year",
                    min_value=EXTENDED_YEARS[0],
                    max_value=EXTENDED_YEARS[-1] - 2,
                    value=2028,
                    step=1,
                    key="rw_mkt_crash_year",
                    help=(
                        "Sequence-of-returns risk: when a crash hits relative to other "
                        "life events matters far more than the long-run average return."
                    ),
                )
                st.caption("Bear scenario: −35% crash year → +15% → +10% → base rate after.")
            else:
                mkt_crash_year = 2028
            if mkt_mode == "Monte Carlo fan":
                st.caption(
                    "Runs N simulations, each drawing annual returns from a log-normal "
                    "distribution. The fan shows the **spread of outcomes** — not a "
                    "prediction. **P50** = median. **P10/P90** = 10th/90th percentile."
                )
                mkt_n_sims = st.slider(
                    "Simulations (N)",
                    100,
                    1000,
                    300,
                    100,
                    key="rw_mkt_n_sims",
                    help="More simulations → smoother bands, slower render. 300 is a good balance.",
                )
                mkt_sigma = st.slider(
                    "Annual volatility (σ)",
                    0.05,
                    0.35,
                    0.15,
                    0.01,
                    format="%.2f",
                    key="rw_mkt_sigma",
                    help="S&P 500 historical σ ≈ 0.15–0.18. Higher σ = wider fan.",
                )
                mkt_mean_return_pct = st.slider(
                    "Expected annual return (MC mean)",
                    2.0,
                    12.0,
                    float(rw_sc.return_rate * 100),
                    0.5,
                    format="%.1f%%",
                    key="rw_mkt_mean_return",
                    help=(
                        "Mean return used for MC simulation. Defaults to the scenario's "
                        "return rate. Reduce for a more conservative stress test."
                    ),
                )
            else:
                mkt_n_sims = 300
                mkt_sigma = 0.15
                mkt_mean_return_pct = rw_sc.return_rate * 100

        with mkt_col2:
            base_rate = rw_sc.return_rate

            if mkt_mode == "Monte Carlo fan":
                import copy as _copy

                _mc_sc = _copy.copy(rw_sc)
                _mc_sc.return_rate = mkt_mean_return_pct / 100
                _mc_sc.brokerage_return_rate = max(
                    rw_sc.brokerage_return_rate, _mc_sc.return_rate - 0.01
                )
                with st.spinner(f"Running {mkt_n_sims} simulations…"):
                    bands = monte_carlo_nw(_mc_sc, EXTENDED_YEARS, n=mkt_n_sims, sigma=mkt_sigma)

                fig_mc = go.Figure()
                fig_mc.add_trace(
                    go.Scatter(
                        x=bands.years + bands.years[::-1],
                        y=bands.p90 + bands.p10[::-1],
                        fill="toself",
                        fillcolor="rgba(52, 152, 219, 0.15)",
                        line=dict(color="rgba(255,255,255,0)"),
                        name="P10–P90",
                        showlegend=True,
                        hoverinfo="skip",
                    )
                )
                fig_mc.add_trace(
                    go.Scatter(
                        x=bands.years + bands.years[::-1],
                        y=bands.p75 + bands.p25[::-1],
                        fill="toself",
                        fillcolor="rgba(52, 152, 219, 0.28)",
                        line=dict(color="rgba(255,255,255,0)"),
                        name="P25–P75",
                        showlegend=True,
                        hoverinfo="skip",
                    )
                )
                fig_mc.add_trace(
                    go.Scatter(
                        x=bands.years,
                        y=bands.p50,
                        mode="lines",
                        name="P50 (median)",
                        line=dict(color="#3498db", width=2),
                        hovertemplate="Year: %{x}<br>P50: $%{y:,.0f}<extra>Median</extra>",
                    )
                )
                # Deterministic reference (the scenario's own deterministic line)
                fig_mc.add_trace(
                    go.Scatter(
                        x=[r.year for r in rw_nw],
                        y=[r.total_nw for r in rw_nw],
                        mode="lines",
                        name=f"Deterministic ({rw_sc.return_rate:.0%} flat)",
                        line=dict(color="#e67e22", width=1.5, dash="dot"),
                        hovertemplate="Year: %{x}<br>NW: $%{y:,.0f}<extra>Deterministic</extra>",
                    )
                )
                fig_mc.update_layout(
                    title=(
                        f"Monte Carlo — {rw_scenario_name} "
                        f"(σ={mkt_sigma:.0%}, N={mkt_n_sims}, μ={mkt_mean_return_pct:.1f}%)"
                    ),
                    xaxis_title="Year",
                    yaxis_title="$",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )
                st.plotly_chart(fig_mc, width="stretch")

                mcs1, mcs2, mcs3 = st.columns(3)
                mcs1.metric(f"P10 NW @ {EXTENDED_YEARS[-1]}", fmt_d(bands.p10[-1]))
                mcs2.metric(f"P50 NW @ {EXTENDED_YEARS[-1]}", fmt_d(bands.p50[-1]))
                mcs3.metric(f"P90 NW @ {EXTENDED_YEARS[-1]}", fmt_d(bands.p90[-1]))
                st.caption(
                    f"P10: only 1-in-10 simulations end up *below* this. "
                    f"P90: only 1-in-10 end up *above* this. "
                    f"Mean return used: {mkt_mean_return_pct:.1f}%, σ={mkt_sigma:.0%}."
                )

            else:
                if mkt_mode == "Flat (baseline)":
                    path = stress_return_path("flat", EXTENDED_YEARS, base_rate)
                    path_label = f"Flat {base_rate:.0%}"
                elif mkt_mode == "Bear market":
                    path = stress_return_path(
                        "bear", EXTENDED_YEARS, base_rate, crash_year=mkt_crash_year
                    )
                    path_label = f"Bear (crash {mkt_crash_year})"
                else:
                    path = stress_return_path("slow_growth", EXTENDED_YEARS, base_rate)
                    path_label = "Slow growth (4%)"

                stress_nw = project_net_worth(rw_sc, EXTENDED_YEARS, return_path=path)

                fig_stress = go.Figure()
                fig_stress.add_trace(
                    go.Scatter(
                        x=[r.year for r in rw_nw],
                        y=[r.total_nw for r in rw_nw],
                        mode="lines",
                        name=f"Baseline ({base_rate:.0%} flat)",
                        line=dict(color="#95a5a6", width=1.5, dash="dot"),
                    )
                )
                fig_stress.add_trace(
                    go.Scatter(
                        x=[r.year for r in stress_nw],
                        y=[r.total_nw for r in stress_nw],
                        mode="lines",
                        name=path_label,
                        line=dict(color="#e74c3c" if "Bear" in path_label else "#f39c12", width=2),
                    )
                )
                fig_stress.update_layout(
                    title=f"Stress Path — {rw_scenario_name}: {path_label}",
                    xaxis_title="Year",
                    yaxis_title="$",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )
                st.plotly_chart(fig_stress, width="stretch")

                stress_final = stress_nw[-1].total_nw
                base_final = rw_nw[-1].total_nw
                delta = stress_final - base_final
                sc1, sc2 = st.columns(2)
                sc1.metric(f"Baseline NW @ {EXTENDED_YEARS[-1]}", fmt_d(base_final))
                sc2.metric(
                    f"{path_label} NW @ {EXTENDED_YEARS[-1]}",
                    fmt_d(stress_final),
                    delta=f"{delta:+,.0f}",
                )

    # ── Retirement Sustainability ───────────────────────────────────────────
    st.subheader("Retirement Sustainability")
    st.caption(
        "Given projected NW at a target age, how long do assets last? "
        "Accounts for SS income (if configured above) and investment returns on remaining assets."
    )

    rs_col1, rs_col2 = st.columns([1, 2])
    with rs_col1:
        rs_target_age = st.slider(
            "Target retirement age",
            55,
            70,
            value=ss_claim_age if ss_enabled else 65,
            key="rs_target_age",
        )
        rs_target_year = 2026 + (rs_target_age - rw_sc.age)
        rs_target_row = next((r for r in rw_nw if r.year == rs_target_year), None)

        if rs_target_row is None:
            st.warning(
                f"{rs_target_year} is outside the projection window ({EXTENDED_YEARS[0]}–{EXTENDED_YEARS[-1]})."
            )
        else:
            rs_nw = rs_target_row.total_nw
            rs_expenses_annual = rs_target_row.total_expenses

            # SS income in nominal dollars at target year
            if ss_enabled:
                rs_ss_annual = ss_annual_income(
                    monthly_benefit_today_dollars=float(ss_monthly_today),
                    claim_year=ss_claim_year,
                    projection_year=rs_target_year,
                    cola=SS_COLA_DEFAULT,
                )
            else:
                rs_ss_annual = 0.0

            rs_annual_gap = rs_expenses_annual - rs_ss_annual
            rs_monthly_gap = rs_annual_gap / 12

            st.metric("NW at target age", fmt_d(rs_nw))
            st.metric("Annual expenses", fmt_d(rs_expenses_annual))
            if ss_enabled and ss_monthly_today > 0:
                if rs_ss_annual > 0:
                    st.metric(
                        "SS income /yr",
                        fmt_d(rs_ss_annual),
                        delta=f"age {ss_claim_age} — started"
                        if ss_claim_year and ss_claim_year <= rs_target_year
                        else None,
                    )
                else:
                    # SS starts after retirement — show what it will be at claim year
                    _ss_at_start = (
                        ss_annual_income(
                            float(ss_monthly_today),
                            ss_claim_year or 9999,
                            ss_claim_year or 9999,
                            SS_COLA_DEFAULT,
                        )
                        if ss_claim_year
                        else 0.0
                    )
                    st.metric(
                        f"SS income /yr (starts age {ss_claim_age})",
                        fmt_d(_ss_at_start),
                        delta=f"not yet — starts {ss_claim_year}",
                    )
            st.metric(
                "Annual gap at retirement (expenses − SS)",
                fmt_d(rs_annual_gap),
                delta=("surplus — assets grow" if rs_annual_gap <= 0 else "shortfall from assets"),
                delta_color="normal" if rs_annual_gap <= 0 else "inverse",
            )

    with rs_col2:
        if rs_target_row is not None:
            r_ret = rw_sc.return_rate

            # ── Year-by-year simulation (handles SS starting after retirement) ──
            # This is more accurate than the closed-form annuity: it correctly
            # accounts for SS income kicking in mid-retirement and COLA growth.
            _sim_bal = rs_nw
            _sim_expense = rs_expenses_annual  # nominal at retirement year
            _sim_years = []
            _sim_nw = []
            _sim_gap = []
            _exhaust_year = None

            for _py in range(rs_target_year, rs_target_year + 55):
                # SS income in this projection year (0 before claim year, COLA after)
                if ss_enabled and ss_claim_year is not None:
                    _ss_py = ss_annual_income(
                        monthly_benefit_today_dollars=float(ss_monthly_today),
                        claim_year=ss_claim_year,
                        projection_year=_py,
                        cola=SS_COLA_DEFAULT,
                    )
                else:
                    _ss_py = 0.0

                _gap_py = _sim_expense - _ss_py

                _sim_years.append(_py)
                _sim_nw.append(max(0.0, _sim_bal))
                _sim_gap.append(_gap_py)

                # Compound and withdraw (mid-year convention)
                _sim_bal = _sim_bal * (1 + r_ret) - _gap_py
                # Grow expenses at scenario rate
                _sim_expense *= 1 + rw_sc.expense_growth

                if _sim_bal <= 0 and _exhaust_year is None:
                    _exhaust_year = _py + 1
                    _sim_nw[-1] = 0.0
                    break  # assets gone

            _years_left = (_exhaust_year - rs_target_year) if _exhaust_year else float("inf")

            # Show message
            if _years_left == float("inf"):
                # Check if truly surplus at retirement year
                _gap_at_retire = rs_expenses_annual - rs_ss_annual
                if _gap_at_retire <= 0:
                    st.success(
                        f"SS income fully covers expenses with **{fmt_d(-_gap_at_retire)}/yr surplus** "
                        f"at age {rs_target_age}. Assets of {fmt_d(rs_nw)} continue growing."
                    )
                else:
                    st.success(
                        f"Assets of {fmt_d(rs_nw)} sustain expenses indefinitely through age "
                        f"{rs_target_age + 54} (returns ≥ net gap). "
                        f"{'SS starts at age ' + str(ss_claim_age) + '.' if ss_enabled and ss_claim_year and ss_claim_year > rs_target_year else ''}"
                    )
            else:
                _exhaust_age = rs_target_age + int(_years_left)
                if _years_left < 20:
                    _ss_at_claim = (
                        ss_annual_income(
                            float(ss_monthly_today),
                            ss_claim_year or 9999,
                            ss_claim_year or 9999,
                            SS_COLA_DEFAULT,
                        )
                        if (ss_enabled and ss_claim_year)
                        else 0.0
                    )
                    st.warning(
                        f"At projected spend trajectory, assets run out in "
                        f"**{_years_left:.1f} years** (age ~{_exhaust_age}, ~{_exhaust_year}). "
                        + (
                            f"SS of {fmt_d(_ss_at_claim)}/yr starts at age {ss_claim_age} and partially offsets expenses. "
                            if ss_enabled and ss_claim_year and ss_claim_year > rs_target_year
                            else ""
                        )
                    )
                else:
                    st.info(
                        f"Assets last **{_years_left:.1f} years** "
                        f"(exhaust ~age {_exhaust_age}, ~{_exhaust_year}). "
                        + (
                            f"Includes SS income starting at age {ss_claim_age}. "
                            if ss_enabled
                            else ""
                        )
                    )

            # Chart
            _chart_color = "#e67e22" if _years_left < 25 else "#3498db"
            _fill_color = "rgba(231,76,60,0.12)" if _years_left < 25 else "rgba(52,152,219,0.12)"
            fig_ret = go.Figure()
            fig_ret.add_trace(
                go.Scatter(
                    x=_sim_years,
                    y=_sim_nw,
                    mode="lines",
                    name="Projected NW (retirement)",
                    line=dict(color=_chart_color, width=2),
                    fill="tozeroy",
                    fillcolor=_fill_color,
                    hovertemplate="Year: %{x}<br>NW: $%{y:,.0f}<extra></extra>",
                )
            )
            # Add SS income as a secondary trace if enabled and starts after retirement
            if (
                ss_enabled
                and ss_claim_year
                and ss_claim_year > rs_target_year
                and ss_claim_year in _sim_years
            ):
                fig_ret.add_vline(
                    x=ss_claim_year,
                    line_dash="dash",
                    line_color="#27ae60",
                    annotation_text=f"SS starts age {ss_claim_age}",
                    annotation_position="top right",
                )
            if _exhaust_year:
                fig_ret.add_hline(
                    y=0, line_dash="dot", line_color="#7f8c8d", annotation_text="Assets exhausted"
                )
            fig_ret.update_layout(
                title=f"Asset trajectory from age {rs_target_age}"
                + (
                    f" — SS kicks in age {ss_claim_age}"
                    if ss_enabled and ss_claim_year and ss_claim_year > rs_target_year
                    else ""
                ),
                xaxis_title="Year",
                yaxis_title="$",
            )
            st.plotly_chart(fig_ret, width="stretch")

            # Small table of first few years for sanity-check
            if len(_sim_years) > 0:
                with st.expander("Year-by-year retirement detail (first 15 years)", expanded=False):
                    _ret_rows = []
                    for _i, _py in enumerate(_sim_years[:15]):
                        _ss_py2 = (
                            ss_annual_income(
                                float(ss_monthly_today), ss_claim_year or 9999, _py, SS_COLA_DEFAULT
                            )
                            if ss_enabled and ss_claim_year
                            else 0.0
                        )
                        _ret_rows.append(
                            {
                                "Year": _py,
                                "Age": rw_sc.age + (_py - 2026),
                                "NW (start of yr)": fmt_d(_sim_nw[_i]),
                                "Expenses": fmt_d(_sim_gap[_i] + _ss_py2),
                                "SS income": fmt_d(_ss_py2) if _ss_py2 > 0 else "—",
                                "Net gap": fmt_d(_sim_gap[_i]),
                            }
                        )
                    st.dataframe(pd.DataFrame(_ret_rows), hide_index=True, width="stretch")


# ═══════════════════════════════════════════════
# TAB — Vest Schedule
# ═══════════════════════════════════════════════
with tab["💎 Vest Schedule"]:
    st.header("RSU Vest Schedule")

    def _build_vest_df(s: Scenario) -> pd.DataFrame:
        rows = []
        for label, schedule in s.vest_grants.items():
            for (y, m), shares in schedule.items():
                price = _price_at_date(s, y, m)
                rows.append(
                    {
                        "Year": y,
                        "Month": m,
                        "Period": f"{y}-{m:02d}",
                        "Source": label,
                        "Gross": shares * price,
                        "Shares": shares,
                    }
                )
        refresh_vals = computed_refresh_values(s)
        for grant_year in projected_refresh_years(s):
            value = refresh_vals.get(grant_year, 0.0)
            price_at_grant = _price_at_date(s, grant_year, s.refresh_issue_month)
            sched = project_flat_refresh(grant_year, value, price_at_grant, s.refresh_issue_month)
            for (y, m), sh in sched.items():
                price = _price_at_date(s, y, m)
                rows.append(
                    {
                        "Year": y,
                        "Month": m,
                        "Period": f"{y}-{m:02d}",
                        "Source": f"Refresh {grant_year}",
                        "Gross": sh * price,
                        "Shares": sh,
                    }
                )
        # Supplemental one-time grants (equity_grant events, e.g. promo bonus)
        for ev in s.events:
            if ev.kind != "equity_grant":
                continue
            value = ev.params["value"]
            price_at_grant = _price_at_date(s, ev.year, ev.month)
            label = ev.params.get("label") or f"Grant {ev.year}"
            sched = project_flat_refresh(ev.year, value, price_at_grant, ev.month)
            for (y, m), sh in sched.items():
                price = _price_at_date(s, y, m)
                rows.append(
                    {
                        "Year": y,
                        "Month": m,
                        "Period": f"{y}-{m:02d}",
                        "Source": label,
                        "Gross": sh * price,
                        "Shares": sh,
                    }
                )
        return pd.DataFrame(rows)

    # Show computed grant values so you can see what the model decided.
    # With ~34 refresh years a metric-column grid is unusable; render as a table.
    refresh_vals = computed_refresh_values(sidebar_scenario)
    st.subheader("Modeled Refresh Grants")
    st.caption(
        "Linear per-grant target growing each year. "
        "Adjust target parameters in the sidebar to explore scenarios."
    )
    _rg_rows = []
    for yr in projected_refresh_years(sidebar_scenario):
        val = refresh_vals.get(yr, 0.0)
        price_at_grant = _price_at_date(sidebar_scenario, yr, sidebar_scenario.refresh_issue_month)
        shares = val / price_at_grant if price_at_grant > 0 else 0
        _rg_rows.append(
            {
                "Year": yr,
                "Grant value": fmt_d(val),
                "Shares": f"{shares:,.0f}",
                "Price @ grant": fmt_d(price_at_grant),
            }
        )
    st.dataframe(pd.DataFrame(_rg_rows), hide_index=True, width="stretch")

    df_vest = _build_vest_df(sidebar_scenario)

    # Monthly stacked bar
    df_vest_m = (
        df_vest.groupby(["Period", "Source"])["Gross"].sum().reset_index().sort_values("Period")
    )
    fig_vest_m = px.bar(
        df_vest_m,
        x="Period",
        y="Gross",
        color="Source",
        title="Monthly RSU Gross Value by Grant Source",
        labels={"Gross": "$", "Period": "Month"},
        barmode="stack",
    )
    fig_vest_m.update_xaxes(tickangle=45, nticks=24)
    st.plotly_chart(fig_vest_m, width="stretch")

    # Annual rollup
    df_vest_a = df_vest.groupby(["Year", "Source"])["Gross"].sum().reset_index()
    fig_vest_a = px.bar(
        df_vest_a,
        x="Year",
        y="Gross",
        color="Source",
        title="Annual RSU Gross Value by Grant Source",
        labels={"Gross": "$"},
        barmode="stack",
    )

    col1, col2 = st.columns([2, 1])
    with col1:
        st.plotly_chart(fig_vest_a, width="stretch")
    with col2:
        df_vest_summary = (
            df_vest.groupby("Year")["Gross"]
            .sum()
            .reset_index()
            .rename(columns={"Gross": "Annual RSU Gross"})
        )
        df_vest_summary["Annual RSU Gross"] = df_vest_summary["Annual RSU Gross"].map(fmt_d)
        st.dataframe(df_vest_summary, width="stretch", hide_index=True)


# ═══════════════════════════════════════════════
# TAB — Historical (only rendered when user_data/historical.csv exists)
# ═══════════════════════════════════════════════
if historical_rows:
    _hist_first = f"{historical_rows[0]['year']}-{historical_rows[0]['month']:02d}"
    _hist_last = f"{historical_rows[-1]['year']}-{historical_rows[-1]['month']:02d}"
    with tab["📜 Historical"]:
        st.header(f"Historical Actuals ({_hist_first} – {_hist_last})")

        df_hist = pd.DataFrame(
            [
                {
                    "Month": f"{h['year']}-{h['month']:02d}",
                    "Total Income": h["total_income"],
                    "Paychecks": h["paychecks"] or 0.0,
                    "Other Income": h["other_income"] or 0.0,
                    "Expenses": h["expenses"],
                    "Net Cash Flow": h["total_income"] - h["expenses"],
                }
                for h in historical_rows
            ]
        )

        # Headline stats
        s1, s2, s3 = st.columns(3)
        s1.metric("Avg Monthly Income", fmt_d(df_hist["Total Income"].mean()))
        s2.metric("Avg Monthly Expenses", fmt_d(df_hist["Expenses"].mean()))
        s3.metric("Avg Net Cash Flow", fmt_d(df_hist["Net Cash Flow"].mean()))

        fig_hist = go.Figure()
        fig_hist.add_trace(
            go.Bar(
                x=df_hist["Month"],
                y=df_hist["Total Income"],
                name="Income",
                marker_color="#2ecc71",
                opacity=0.8,
            )
        )
        fig_hist.add_trace(
            go.Bar(
                x=df_hist["Month"],
                y=df_hist["Expenses"],
                name="Expenses",
                marker_color="#e74c3c",
                opacity=0.8,
            )
        )
        fig_hist.add_trace(
            go.Scatter(
                x=df_hist["Month"],
                y=df_hist["Net Cash Flow"],
                name="Net Cash Flow",
                mode="lines+markers",
                line=dict(color="#3498db", width=2),
            )
        )
        fig_hist.update_layout(
            title="Monthly Income vs Expenses — Actuals",
            barmode="group",
            xaxis_tickangle=45,
            yaxis_title="$",
        )
        st.plotly_chart(fig_hist, width="stretch")

        df_hist_display = df_hist.copy()
        for col in ["Total Income", "Paychecks", "Other Income", "Expenses", "Net Cash Flow"]:
            df_hist_display[col] = df_hist_display[col].map(fmt_d)
        st.dataframe(df_hist_display, width="stretch", hide_index=True)
