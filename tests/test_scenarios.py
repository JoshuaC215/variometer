"""Scenario factory functions.

Key invariants:
- stay_put() produces no events and identical cashflow to Scenario() defaults.
- Each archetype factory produces the expected income profile.
- sabbatical models the off years with no W-2 and returns to work correctly.
- SCENARIO_REGISTRY contains exactly the public archetype library (user_data
  scenarios are not loaded in the test suite).
"""

import pytest

from variometer.cashflow import annual_summary
from variometer.config import Scenario
from variometer.projection import project_net_worth
from variometer.scenarios import (
    SCENARIO_REGISTRY,
    apply_core_overrides,
    buy_home,
    change_jobs,
    fire_roth_ladder,
    grouped_scenario_names,
    internal_promotion,
    is_builtin_scenario,
    sabbatical,
    scenario,
    stay_put,
    visible_scenario_names,
)

# ---------------------------------------------------------------------------
# stay_put()
# ---------------------------------------------------------------------------


def test_stay_put_is_eventless_baseline():
    """stay_put() has no events and matches the bare Scenario() cashflow."""
    s = stay_put()
    assert s.events == []
    for year in [2026, 2027, 2028, 2029, 2030]:
        ar_factory = annual_summary(s, year)
        ar_default = annual_summary(Scenario(), year)
        assert ar_factory.gross_income == pytest.approx(ar_default.gross_income, abs=0.01)
        assert ar_factory.take_home == pytest.approx(ar_default.take_home, abs=0.01)


# ---------------------------------------------------------------------------
# internal_promotion()
# ---------------------------------------------------------------------------


def test_internal_promotion_raises_income_from_promo_year():
    """Base salary rises from the promo year on; pre-promo years match baseline."""
    s = internal_promotion(promo_year=2028)
    base = stay_put()
    assert annual_summary(s, 2027).base_salary == pytest.approx(
        annual_summary(base, 2027).base_salary, abs=1
    )
    assert annual_summary(s, 2028).base_salary > annual_summary(base, 2028).base_salary
    assert annual_summary(s, 2029).base_salary > annual_summary(base, 2029).base_salary


def test_internal_promotion_emits_pay_change_and_one_time_grant():
    s = internal_promotion()
    kinds = [e.kind for e in s.events]
    assert kinds.count("pay_change") == 1
    assert kinds.count("equity_grant") == 1


def test_internal_promotion_zero_grant_pct_omits_equity_grant():
    s = internal_promotion(one_time_grant_pct=0.0)
    assert not any(e.kind == "equity_grant" for e in s.events)


def test_internal_promotion_bumps_refresh_target():
    """The pay_change event re-anchors the refresh grant target upward."""
    s = internal_promotion(promo_year=2028, equity_bump=0.50)
    pc = next(e for e in s.events if e.kind == "pay_change")
    pre_promo_target = s.equity_refresh_grant_value * (
        (1 + s.equity_refresh_growth) ** (2028 - s.equity_refresh_base_year)
    )
    assert pc.params["equity_refresh_grant_value"] == pytest.approx(pre_promo_target * 1.5)


# ---------------------------------------------------------------------------
# change_jobs()
# ---------------------------------------------------------------------------


def test_change_jobs_reduces_gross_after_start():
    """Gross income in 2028 (transition year) is lower than 2027 gross."""
    s = change_jobs()
    gross_2027 = annual_summary(s, 2027).gross_income
    gross_2028 = annual_summary(s, 2028).gross_income
    assert gross_2028 < gross_2027, (
        f"Expected 2028 gross ({gross_2028:,.0f}) < 2027 gross ({gross_2027:,.0f})"
    )


def test_change_jobs_no_mega_backdoor_default():
    """Default factory has has_mega_backdoor=False → after-tax 401k is 0 in 2029."""
    s = change_jobs()
    contribs = annual_summary(s, 2029).contributions
    assert contribs.aftertax == pytest.approx(0.0), (
        f"Expected zero after-tax contributions in 2029, got {contribs.aftertax:,.2f}"
    )


def test_change_jobs_events_have_correct_kinds():
    """Factory emits exactly one employment_end and one new_job event."""
    s = change_jobs()
    kinds = [e.kind for e in s.events]
    assert kinds.count("employment_end") == 1
    assert kinds.count("new_job") == 1


def test_change_jobs_start_year_parametrised():
    """start_year=2029 shifts the transition — 2027 is unaffected by either date.

    We can't compare 2028 gross between hop(2028) and hop(2029) to Scenario()
    directly because the equity auto-refresh model builds the vest schedule
    sequentially and truncates based on employment_end.  The 2028 RSU refresh
    grant is sized differently when the model knows employment ends in 2029 vs
    2028.  Instead, check 2027 — a year before either hop date — which is
    always fully under the current employer with the same refresh schedule.
    """
    s = change_jobs(start_year=2029)
    ar_2027 = annual_summary(s, 2027)
    ar_baseline_2027 = annual_summary(Scenario(), 2027)
    assert ar_2027.base_salary == pytest.approx(ar_baseline_2027.base_salary, rel=0.01)
    assert ar_2027.bonus == pytest.approx(ar_baseline_2027.bonus, rel=0.01)
    # 2030 should be fully under the new job (hop started in 2029)
    ar_2030 = annual_summary(s, 2030)
    assert ar_2030.rsu_gross == pytest.approx(0.0, abs=1)  # old-employer equity gone


# ---------------------------------------------------------------------------
# sabbatical()
# ---------------------------------------------------------------------------


def test_sabbatical_zero_w2_during_window():
    """During the off-year window, base_salary should be near zero.

    2030 is start_year (default). The employment_end fires in March, and the
    end month counts as active (Jan-Mar = 3 months of base). April onwards
    has no W-2. We allow for the small partial-year base but confirm it's a
    fraction of a normal year.
    """
    s = sabbatical()
    ar_2030 = annual_summary(s, 2030)
    full_year_base = annual_summary(Scenario(), 2030).base_salary
    # At most 3/12 of base (Jan-Mar) should remain; no new_job until 2032
    assert ar_2030.base_salary < full_year_base * (3 / 12 + 0.01), (
        f"2030 base_salary ({ar_2030.base_salary:,.0f}) should be < ~25% of "
        f"full-year baseline ({full_year_base:,.0f})"
    )


def test_sabbatical_2031_essentially_passive_income():
    """2031 is a full off year — gross should be just LTCG + other_income (no W-2)."""
    s = sabbatical()
    ar_2031 = annual_summary(s, 2031)
    expected_passive = Scenario().other_monthly_income * 12 + Scenario().ltcg_annual
    assert ar_2031.base_salary == pytest.approx(0.0, abs=1), (
        f"2031 base_salary should be 0, got {ar_2031.base_salary:,.2f}"
    )
    assert ar_2031.bonus == pytest.approx(0.0, abs=1)
    assert ar_2031.rsu_gross == pytest.approx(0.0, abs=1)
    assert ar_2031.gross_income == pytest.approx(expected_passive, abs=1)


def test_sabbatical_returns_to_work():
    """2032 gross (return_year) should be higher than 2031 gross (full off year)."""
    s = sabbatical()
    gross_2031 = annual_summary(s, 2031).gross_income
    gross_2032 = annual_summary(s, 2032).gross_income
    assert gross_2032 > gross_2031, (
        f"Return year 2032 gross ({gross_2032:,.0f}) should exceed "
        f"off year 2031 gross ({gross_2031:,.0f})"
    )


def test_sabbatical_return_comp_knobs_flow_into_new_job():
    """return_base / return_equity_annual land in the new_job event params."""
    s = sabbatical(return_base=120_000, return_equity_annual=25_000)
    nj = next(e for e in s.events if e.kind == "new_job")
    assert nj.params["base"] == pytest.approx(120_000)
    assert nj.params["equity_annual"] == pytest.approx(25_000)


def test_sabbatical_healthcare_event_present():
    """At least one expense_change event carries the healthcare premium delta."""
    s = sabbatical(healthcare_monthly=700)
    hc_events = [
        e
        for e in s.events
        if e.kind == "expense_change" and e.params.get("delta_monthly", 0) == pytest.approx(700)
    ]
    assert len(hc_events) >= 1, "Expected at least one expense_change event with delta_monthly=700"


def test_sabbatical_healthcare_event_window():
    """Healthcare expense_change ends in the year before return_year."""
    s = sabbatical(start_year=2030, return_year=2032, healthcare_monthly=700)
    hc_events = [
        e
        for e in s.events
        if e.kind == "expense_change" and e.params.get("delta_monthly", 0) == 700
    ]
    assert len(hc_events) == 1
    assert hc_events[0].params.get("ends_year") == 2031  # return_year - 1


def test_sabbatical_expense_events_end_before_return():
    """All temporary expense events (expense_level + expense_change) expire before return_year."""
    start_year, return_year = 2030, 2032
    s = sabbatical(start_year=start_year, return_year=return_year)

    level_events = [e for e in s.events if e.kind == "expense_level"]
    assert len(level_events) == 1, "Expected exactly one expense_level event for lean spending"
    assert level_events[0].params.get("ends_year") == return_year - 1

    for e in [ev for ev in s.events if ev.kind == "expense_change"]:
        ends = e.params.get("ends_year")
        assert ends is not None and ends <= return_year - 1, (
            f"expense_change ends in {ends}, should be <= {return_year - 1}: {e}"
        )


# ---------------------------------------------------------------------------
# fire_roth_ladder()
# ---------------------------------------------------------------------------


def test_fire_roth_ladder_employment_ends_at_exit():
    s = fire_roth_ladder(exit_year=2033)
    end = next(e for e in s.events if e.kind == "employment_end")
    assert (end.year, end.month) == (2033, 1)


def test_fire_roth_ladder_has_roth_conversions_from_exit():
    """Roth conversion ladder starts at the exit year and continues for decades."""
    s = fire_roth_ladder(exit_year=2033)
    conv_years = sorted({e.year for e in s.events if e.kind == "roth_conversion"})
    assert conv_years[0] == 2033, f"First conversion should be at exit (2033), got {conv_years[0]}"
    assert conv_years[-1] >= 2055


def test_fire_roth_ladder_zero_conversion_omits_events():
    s = fire_roth_ladder(roth_conversion_annual=0)
    assert not any(e.kind == "roth_conversion" for e in s.events)


def test_fire_roth_ladder_has_aca_premium():
    s = fire_roth_ladder(healthcare_monthly=650)
    aca = [e for e in s.events if e.kind == "expense_change" and e.params.get("aca") is True]
    assert len(aca) == 1
    assert aca[0].params["delta_monthly"] == pytest.approx(650)


def test_fire_roth_ladder_receives_aca_subsidy_in_lean_years():
    """Regression: the ACA premium event must be recognized by the PTC calc.

    (The factory once used a label the subsidy matcher didn't recognize, so
    FIRE lean years silently got $0 subsidy — hence the explicit aca param.)
    """
    s = fire_roth_ladder(exit_year=2030)
    rows = project_net_worth(s, list(range(2026, 2040)))
    assert any(r.aca_subsidy_monthly > 0 for r in rows if r.year > 2030), (
        "FIRE lean years should qualify for a nonzero ACA premium tax credit"
    )


# ---------------------------------------------------------------------------
# buy_home()
# ---------------------------------------------------------------------------


def test_buy_home_emits_house_purchase_with_params():
    s = buy_home(purchase_year=2029, price=900_000, location="Springfield")
    house = next(e for e in s.events if e.kind == "house_purchase")
    assert house.year == 2029
    assert house.params["price"] == pytest.approx(900_000)
    assert house.params["location_label"] == "Springfield"
    assert house.params["replaces_monthly_rent"] is True


def test_buy_home_default_has_nonzero_appreciation_and_maintenance():
    s = buy_home()
    house = next(e for e in s.events if e.kind == "house_purchase")
    assert house.params["appreciation_rate"] > 0
    assert house.params["maintenance_pct"] > 0


# ---------------------------------------------------------------------------
# SCENARIO_REGISTRY
# ---------------------------------------------------------------------------


def test_registry_keys_match_public_library():
    """SCENARIO_REGISTRY contains exactly the public archetypes.

    (user_data/scenarios.py is not loaded in the test suite — the conftest
    disables the private layer.)
    """
    expected = {
        "Stay put",
        "Internal promotion",
        "Change jobs",
        "Sabbatical / career gap",
        "FIRE with Roth ladder",
        "Buy home",
    }
    assert set(SCENARIO_REGISTRY.keys()) == expected


def test_registry_values_are_callable():
    """Every value in SCENARIO_REGISTRY is callable and returns a Scenario."""
    for label, factory in SCENARIO_REGISTRY.items():
        s = factory()
        assert isinstance(s, Scenario), f"Factory for '{label}' did not return a Scenario"


def test_every_archetype_projects_without_error():
    """Smoke test: every public archetype survives a 20-year projection."""
    for label, factory in SCENARIO_REGISTRY.items():
        nw = project_net_worth(factory(), list(range(2026, 2046)))
        assert len(nw) == 20, f"{label}: unexpected projection length"


# ---------------------------------------------------------------------------
# Knob introspection contract
# ---------------------------------------------------------------------------


def test_every_scenario_has_well_formed_knobs():
    """Every registered scenario must produce well-formed BoundKnobs.

    Pins the ``Annotated[type, Knob(...)]`` contract: each knob has a
    non-empty label, a kind from the valid set, and (for ``choice``) a
    default that's actually in ``choices``. Catches typos like a missing
    label or a misspelled kind.

    Also catches **silent skips** from ``typing.get_type_hints`` failing
    (e.g. a forward reference that can't be resolved): every parameter that
    looks Annotated in the raw signature must appear in the introspected
    BoundKnob list.
    """
    import inspect
    import typing
    from typing import Annotated

    from variometer.scenarios import knobs_for_scenario

    VALID_KINDS = {"int", "float", "dollars", "percent", "bool", "choice", "text"}

    for name, factory in SCENARIO_REGISTRY.items():
        sig = inspect.signature(factory)
        knobs = knobs_for_scenario(name)

        for knob in knobs:
            assert knob.label, f"{name}/{knob.name}: empty label"
            assert knob.kind in VALID_KINDS, (
                f"{name}/{knob.name}: kind={knob.kind!r} not in {VALID_KINDS}"
            )
            if knob.kind == "choice":
                assert knob.choices, f"{name}/{knob.name}: kind=choice but no choices"
                assert knob.default in knob.choices, (
                    f"{name}/{knob.name}: default {knob.default!r} not in choices {knob.choices}"
                )

        # No silent skips: every annotated param in the raw signature must
        # surface as a BoundKnob. `get_type_hints` resolves Annotated strings;
        # if it fails, the param disappears and the UI shows nothing.
        try:
            resolved_hints = typing.get_type_hints(factory, include_extras=True)
        except Exception as e:
            raise AssertionError(f"{name}: typing.get_type_hints() failed — {e!r}") from e
        annotated_param_names = {
            p_name
            for p_name in sig.parameters
            if typing.get_origin(resolved_hints.get(p_name)) is Annotated
        }
        bound_param_names = {k.name for k in knobs}
        missing = annotated_param_names - bound_param_names
        assert not missing, (
            f"{name}: annotated params silently skipped during introspection: {missing}"
        )


def test_factory_accepts_introspected_kwargs():
    """The kwargs dict produced from defaults can be passed back to the factory.

    Ensures the introspection round-trips: every BoundKnob.name is a real
    factory kwarg, and the factory accepts the defaults without error.
    """
    from variometer.scenarios import knobs_for_scenario

    for name, factory in SCENARIO_REGISTRY.items():
        kwargs = {k.name: k.default for k in knobs_for_scenario(name)}
        s = factory(**kwargs)
        assert isinstance(s, Scenario)


# ---------------------------------------------------------------------------
# apply_core_overrides — sidebar-as-base-defaults overlay
# ---------------------------------------------------------------------------


def test_apply_core_overrides_overwrites_default_field():
    """A field still at its baseline default is overwritten by the override."""
    s = stay_put()
    assert s.return_rate == 0.07  # default
    apply_core_overrides(s, {"return_rate": 0.05})
    assert s.return_rate == 0.05


def test_apply_core_overrides_skips_field_already_customized():
    """If the factory (or caller) set a non-default value, the override is ignored."""
    s = Scenario()
    s.healthcare_monthly = 750.0  # simulate factory customization
    apply_core_overrides(s, {"healthcare_monthly": 1_000.0})
    assert s.healthcare_monthly == 750.0, "override should NOT clobber a customized field"


def test_apply_core_overrides_recomputes_brokerage_cost_basis():
    """Changing brokerage_balance via override recomputes cost basis to
    balance × fraction so projection LTCG math reflects the new balance."""
    s = stay_put()
    default = Scenario()
    assert s.brokerage_cost_basis == pytest.approx(
        default.brokerage_balance * default.brokerage_cost_basis_fraction, abs=1
    )
    apply_core_overrides(s, {"brokerage_balance": 500_000.0})
    assert s.brokerage_balance == 500_000.0
    assert s.brokerage_cost_basis == pytest.approx(
        500_000.0 * Scenario().brokerage_cost_basis_fraction, abs=1
    )


def test_apply_core_overrides_unknown_field_raises():
    s = stay_put()
    with pytest.raises(AttributeError):
        apply_core_overrides(s, {"not_a_field": 42})


def test_apply_core_overrides_returns_same_scenario_for_chaining():
    s = stay_put()
    out = apply_core_overrides(s, {"base_salary": 300_000.0})
    assert out is s
    assert s.base_salary == 300_000.0


# ---------------------------------------------------------------------------
# Origin grouping (user vs built-in)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_user_scenario():
    """Register a factory that looks like it came from user_data/scenarios.py."""
    name = "Test user scenario"

    @scenario(name)
    def _user_factory() -> Scenario:
        """A pretend personal composition."""
        return Scenario()

    _user_factory.__module__ = "variometer_user_scenarios"
    yield name
    del SCENARIO_REGISTRY[name]


def test_public_registry_is_all_builtin():
    user, builtin = grouped_scenario_names()
    assert user == ()
    assert builtin == tuple(SCENARIO_REGISTRY)
    assert all(is_builtin_scenario(n) for n in SCENARIO_REGISTRY)


def test_visible_names_hide_guard_without_user_scenarios():
    # hide_builtins must be a no-op when there's nothing else to show
    assert visible_scenario_names(hide_builtins=True) == tuple(SCENARIO_REGISTRY)


def test_user_scenarios_sort_first(fake_user_scenario):
    assert not is_builtin_scenario(fake_user_scenario)
    user, builtin = grouped_scenario_names()
    assert user == (fake_user_scenario,)
    assert fake_user_scenario not in builtin
    # Registered last, listed first
    assert visible_scenario_names()[0] == fake_user_scenario
    assert set(visible_scenario_names()) == set(SCENARIO_REGISTRY)


def test_hide_builtins_with_user_scenarios(fake_user_scenario):
    assert visible_scenario_names(hide_builtins=True) == (fake_user_scenario,)
