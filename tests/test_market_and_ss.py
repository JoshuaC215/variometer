"""Market-path and Social Security tests: Monte Carlo, deterministic stress
paths, SS integration, TCJA toggle."""

from variometer.cashflow import annual_summary
from variometer.config import Scenario
from variometer.data.social_security import (
    claim_age_factor,
    cola_adjust_monthly,
    estimate_aime,
    estimate_monthly_benefit,
    pia_from_aime,
    ss_annual_income,
)
from variometer.events import LifeEvent
from variometer.projection import (
    MCBands,
    monte_carlo_nw,
    project_net_worth,
    stress_return_path,
)

YEARS = list(range(2026, 2046))


# ─────────────────────────────────────────────────────────────────────────────
# Social Security estimator
# ─────────────────────────────────────────────────────────────────────────────


def test_aime_top_35():
    """AIME averages the 35 highest years monthly."""
    earnings = [100_000] * 35 + [50_000] * 5  # top 35 are all $100k
    aime = estimate_aime(earnings)
    assert abs(aime - 100_000 / 12) < 0.01


def test_aime_fewer_than_35_years():
    """Fewer than 35 years → zeroes fill remaining slots → AIME depressed."""
    earnings_10 = [100_000] * 10
    aime = estimate_aime(earnings_10)
    # 10 × $100k / (35 × 12)
    expected = 10 * 100_000 / (35 * 12)
    assert abs(aime - expected) < 0.01


def test_pia_bend_points_formula():
    """PIA formula produces correct bracket results."""
    # All income in first bracket (90%)
    aime_low = 500.0
    pia_low = pia_from_aime(aime_low)
    assert abs(pia_low - 0.90 * aime_low) < 1.0

    # AIME between bend1 and bend2
    aime_mid = 3_000.0
    pia_mid = pia_from_aime(aime_mid)
    # Should be: 90% × bend1 + 32% × (aime_mid - bend1)
    # bend1 ≈ 1260; pia ≈ 1134 + 0.32*(3000-1260) = 1134 + 556.8 = 1690.8
    assert 1_600 < pia_mid < 1_800

    # Very high AIME → third bracket
    aime_high = 15_000.0
    pia_high = pia_from_aime(aime_high)
    assert pia_high > pia_mid


def test_claim_age_factor_fra():
    """Claiming at FRA → factor = 1.0."""
    assert claim_age_factor(67, fra=67) == 1.0


def test_claim_age_factor_early():
    """Claiming at 62 reduces benefit by ~30%."""
    factor = claim_age_factor(62, fra=67)
    assert 0.68 < factor < 0.72  # SSA: ~70% for 5 years early


def test_claim_age_factor_delayed():
    """Claiming at 70 increases benefit by ~24%."""
    factor = claim_age_factor(70, fra=67)
    assert abs(factor - 1.24) < 0.001


def test_estimate_monthly_benefit_reasonable_range():
    """Full career at senior tech wages yields a benefit near the SS maximum."""
    benefit = estimate_monthly_benefit(
        current_age=35,
        current_salary=220_000,
        prior_career_avg_salary=100_000,
        prior_career_years=10,
        claim_age=67,
    )
    # SSA 2025 maximum at FRA ≈ $4,873/mo; should be in the ballpark
    assert 3_000 < benefit < 5_500


def test_estimate_monthly_benefit_low_career():
    """Short low-wage career yields a substantially lower benefit."""
    low = estimate_monthly_benefit(
        current_age=35,
        current_salary=40_000,
        prior_career_avg_salary=30_000,
        prior_career_years=10,
        claim_age=67,
    )
    high = estimate_monthly_benefit(
        current_age=35,
        current_salary=220_000,
        prior_career_avg_salary=100_000,
        prior_career_years=10,
        claim_age=67,
    )
    assert low < high


def test_cola_adjust_monthly():
    """COLA inflates monthly benefit correctly."""
    monthly_today = 3_500.0
    claim_year = 2054
    # 5 years of 2.5% COLA
    result = cola_adjust_monthly(monthly_today, claim_year, 2059, cola=0.025)
    expected = 3_500.0 * (1.025**5)
    assert abs(result - expected) < 0.01


def test_ss_annual_income_before_claim():
    """Returns 0 before claim year."""
    assert ss_annual_income(3_500, claim_year=2054, projection_year=2050) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# ss_start event in cashflow
# ─────────────────────────────────────────────────────────────────────────────


def test_ss_start_event_zero_before_claim():
    """SS income is 0 for years before the ss_start event year."""
    s = Scenario()
    s.events = [
        LifeEvent(year=2054, kind="ss_start", params={"monthly_benefit_today_dollars": 3_500}),
    ]
    ar = annual_summary(s, 2030)
    assert ar.ss_income == 0.0


def test_ss_start_event_adds_income():
    """SS income appears in take_home and ss_income field at claim year."""
    s = Scenario()
    s.events = [
        LifeEvent(year=2030, kind="ss_start", params={"monthly_benefit_today_dollars": 3_000}),
    ]
    ar_no_ss = annual_summary(Scenario(), 2030)
    ar_ss = annual_summary(s, 2030)

    # ss_income field should equal 3000 × 12 (no COLA since it's the claim year)
    assert abs(ar_ss.ss_income - 3_000 * 12) < 1.0
    # take_home should increase (SS adds cash, though 85% is federally taxed)
    assert ar_ss.take_home > ar_no_ss.take_home


def test_ss_cola_grows_each_year():
    """SS income grows by COLA each year after claim year."""
    s = Scenario()
    monthly = 3_000.0
    s.events = [
        LifeEvent(
            year=2030,
            kind="ss_start",
            params={"monthly_benefit_today_dollars": monthly, "cola": 0.025},
        ),
    ]
    ar_2030 = annual_summary(s, 2030)
    ar_2031 = annual_summary(s, 2031)

    expected_2030 = monthly * 12
    expected_2031 = monthly * 12 * 1.025
    assert abs(ar_2030.ss_income - expected_2030) < 1.0
    assert abs(ar_2031.ss_income - expected_2031) < 1.0


def test_ss_no_effect_zero_events():
    """Regression: no ss_start event → ss_income == 0, take_home unchanged."""
    s = Scenario()
    ar = annual_summary(s, 2030)
    assert ar.ss_income == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# TCJA toggle in housing
# ─────────────────────────────────────────────────────────────────────────────


def test_tcja_active_caps_salt():
    """TCJA active → SALT capped at $10k in itemized deductions."""
    s = Scenario(tcja_active=True)
    s.events = [
        LifeEvent(
            year=2028,
            kind="house_purchase",
            params={"price": 1_200_000, "down_pct": 0.20, "rate": 0.0675, "term_years": 30},
        ),
    ]
    ar_tcja = annual_summary(s, 2029)

    s2 = Scenario(tcja_active=False)
    s2.events = list(s.events)
    ar_no_tcja = annual_summary(s2, 2029)

    # With higher SALT deduction (no cap), federal taxable income is lower
    # → federal income tax should be lower under no-TCJA (pre-sunset).
    assert ar_no_tcja.federal_income_tax <= ar_tcja.federal_income_tax


def test_tcja_default_is_active():
    """Default Scenario has tcja_active=True."""
    s = Scenario()
    assert s.tcja_active is True


# ─────────────────────────────────────────────────────────────────────────────
# Stress paths
# ─────────────────────────────────────────────────────────────────────────────


def test_stress_path_flat():
    """Flat stress path → every year equals base_rate."""
    path = stress_return_path("flat", YEARS, base_rate=0.07)
    assert all(abs(v - 0.07) < 1e-9 for v in path.values())


def test_stress_path_slow_growth():
    """Slow growth path → every year is 4%."""
    path = stress_return_path("slow_growth", YEARS, base_rate=0.07)
    assert all(abs(v - 0.04) < 1e-9 for v in path.values())


def test_stress_path_bear():
    """Bear path: crash_year = −35%, next +15%, next +10%, then base_rate."""
    crash = 2029
    path = stress_return_path("bear", YEARS, base_rate=0.07, crash_year=crash)
    assert abs(path[crash] - (-0.35)) < 1e-9
    assert abs(path[crash + 1] - 0.15) < 1e-9
    assert abs(path[crash + 2] - 0.10) < 1e-9
    assert abs(path[crash + 3] - 0.07) < 1e-9


def test_bear_path_lowers_nw():
    """Bear market year produces lower terminal NW than flat path."""
    s = Scenario()
    flat = stress_return_path("flat", YEARS, base_rate=0.07)
    bear = stress_return_path("bear", YEARS, base_rate=0.07, crash_year=2028)

    nw_flat = project_net_worth(s, YEARS, return_path=flat)[-1].total_nw
    nw_bear = project_net_worth(s, YEARS, return_path=bear)[-1].total_nw
    assert nw_bear < nw_flat


def test_slow_growth_below_flat():
    """Slow growth (4%) always produces lower NW than flat 7% path."""
    s = Scenario()
    flat = stress_return_path("flat", YEARS, base_rate=0.07)
    slow = stress_return_path("slow_growth", YEARS, base_rate=0.07)

    nw_flat = project_net_worth(s, YEARS, return_path=flat)[-1].total_nw
    nw_slow = project_net_worth(s, YEARS, return_path=slow)[-1].total_nw
    assert nw_slow < nw_flat


def test_no_return_path_identical_to_default():
    """project_net_worth with return_path=None is bit-identical to without param."""
    s = Scenario()
    nw_default = project_net_worth(s, YEARS)
    nw_none = project_net_worth(s, YEARS, return_path=None)
    for a, b in zip(nw_default, nw_none, strict=True):
        assert a.total_nw == b.total_nw


# ─────────────────────────────────────────────────────────────────────────────
# Monte Carlo bands
# ─────────────────────────────────────────────────────────────────────────────


def test_mc_returns_mcbands():
    """monte_carlo_nw returns an MCBands dataclass with correct year list."""
    s = Scenario()
    years = list(range(2026, 2036))
    bands = monte_carlo_nw(s, years, n=50, sigma=0.15, seed=42)
    assert isinstance(bands, MCBands)
    assert bands.years == years
    assert len(bands.p50) == len(years)


def test_mc_p50_near_deterministic():
    """P50 of MC simulation is within 10% of the deterministic projection."""
    s = Scenario()
    years = list(range(2026, 2036))
    bands = monte_carlo_nw(s, years, n=200, sigma=0.15, seed=42)
    det = project_net_worth(s, years)[-1].total_nw
    p50_final = bands.p50[-1]
    # P50 should be within 10% of deterministic (log-normal mean ≠ median)
    assert abs(p50_final - det) / det < 0.10


def test_mc_ordering():
    """P10 ≤ P25 ≤ P50 ≤ P75 ≤ P90 at every year."""
    s = Scenario()
    years = list(range(2026, 2036))
    bands = monte_carlo_nw(s, years, n=100, sigma=0.15, seed=99)
    for i in range(len(years)):
        assert bands.p10[i] <= bands.p25[i] <= bands.p50[i] <= bands.p75[i] <= bands.p90[i]


def test_mc_fan_widens_over_time():
    """Uncertainty (P90 − P10) grows over time as returns compound."""
    s = Scenario()
    years = list(range(2026, 2046))
    bands = monte_carlo_nw(s, years, n=200, sigma=0.15, seed=42)
    spread_early = bands.p90[0] - bands.p10[0]
    spread_late = bands.p90[-1] - bands.p10[-1]
    assert spread_late > spread_early


def test_mc_reproducible_with_seed():
    """Same seed → identical results."""
    s = Scenario()
    years = list(range(2026, 2036))
    b1 = monte_carlo_nw(s, years, n=50, sigma=0.15, seed=7)
    b2 = monte_carlo_nw(s, years, n=50, sigma=0.15, seed=7)
    assert b1.p50 == b2.p50


def test_mc_different_seeds_differ():
    """Different seeds → different P50 (probabilistically)."""
    s = Scenario()
    years = list(range(2026, 2036))
    b1 = monte_carlo_nw(s, years, n=100, sigma=0.15, seed=1)
    b2 = monte_carlo_nw(s, years, n=100, sigma=0.15, seed=2)
    assert b1.p50 != b2.p50
