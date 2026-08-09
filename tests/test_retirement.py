"""Retirement contribution allocation tests."""

import pytest

from variometer.config import Scenario
from variometer.retirement import compute_contributions, limits_for


def _baseline_4pct() -> Scenario:
    """A Scenario with an unoptimized 4% pretax / no Roth / no aftertax election."""
    return Scenario(pretax_401k_pct=0.04, roth_401k_pct=0.0, aftertax_401k_pct=0.0)


def test_limits_2026():
    lim = limits_for(2026)
    assert lim.employee_401k == 24_500
    assert lim.total_401k == 72_000


def test_limits_inflate():
    lim_2027 = limits_for(2027, inflation=0.025)
    assert lim_2027.employee_401k == pytest.approx(24_500 * 1.025)


def test_baseline_4pct_pretax_only():
    """Unoptimized election: 4% pre-tax, no Roth, no after-tax."""
    s = _baseline_4pct()
    c = compute_contributions(s, 2026)
    expected_pretax = s.base_salary * 0.04
    assert c.pretax == pytest.approx(expected_pretax)
    assert c.roth == 0
    assert c.aftertax == 0
    # Match: 50% of pre-tax, under the cap at this contribution level
    assert c.employer_match == pytest.approx(min(expected_pretax * 0.50, s.match_cap))
    assert c.annual_additions == pytest.approx(c.pretax + c.employer_match)


def test_default_pretax_hits_ee_limit():
    """Default Scenario's pre-tax election essentially maxes the EE limit."""
    s = Scenario()
    c = compute_contributions(s, 2026)
    assert c.pretax == pytest.approx(24_500, abs=300)
    # Match: 50% of pre-tax, capped at the scenario match_cap
    assert c.employer_match == pytest.approx(s.match_cap, abs=1)
    # After-tax fills toward the annual addition limit per aftertax_401k_pct
    expected_aftertax = min(
        s.base_salary * s.aftertax_401k_pct, 72_000 - c.pretax - c.employer_match
    )
    assert c.aftertax == pytest.approx(expected_aftertax, abs=1)


def test_match_caps_at_limit():
    """Even if user contributed >$24,500 pre-tax (impossible per rules,
    but model should still cap match)."""
    s = Scenario()
    s.pretax_401k_pct = 0.50  # would try $110k — should clamp
    c = compute_contributions(s, 2026)
    assert c.pretax <= 24_500
    assert c.employer_match <= s.match_cap


def test_aftertax_respects_total_limit():
    """If user requests more after-tax than fits, clamp to remaining headroom."""
    s = Scenario()
    s.aftertax_401k_pct = 1.0  # would try 100% of $240k
    c = compute_contributions(s, 2026)
    # Should fill exactly to $72k total
    assert c.annual_additions == pytest.approx(72_000, abs=1)


def test_roth_competes_with_pretax_for_ee_limit():
    """Pre-tax + Roth share the $24,500 cap."""
    s = Scenario()
    s.pretax_401k_pct = 0.05  # $11,000
    s.roth_401k_pct = 0.05  # $11,000 — total $22k, under cap
    c = compute_contributions(s, 2026)
    assert c.pretax == pytest.approx(s.base_salary * 0.05)
    assert c.roth == pytest.approx(s.base_salary * 0.05)


def test_roth_clamped_by_remaining_ee_cap():
    """If pre-tax already maxes the EE limit, Roth only gets the residual."""
    s = Scenario()
    s.pretax_401k_pct = 0.111  # ≈ $24,420 — basically maxed
    s.roth_401k_pct = 0.10  # would want $22k but no room left
    c = compute_contributions(s, 2026)
    assert c.roth == pytest.approx(24_500 - s.base_salary * 0.111, abs=10)
