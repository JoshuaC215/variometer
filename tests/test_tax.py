"""Tax math tests.

Hand-computed reference values for known income levels (single filer, 2026).
"""

import pytest

from variometer import tax
from variometer.data.brackets import FEDERAL_SINGLE_2026


def test_progressive_tax_zero():
    assert tax.progressive_tax(0, FEDERAL_SINGLE_2026) == 0


def test_progressive_tax_first_bracket():
    # $10,000 entirely in 10% bracket
    assert tax.progressive_tax(10_000, FEDERAL_SINGLE_2026) == pytest.approx(1_000)


def test_progressive_tax_crosses_brackets():
    # $20,000 = $12,400 × 10% + $7,600 × 12% = $1,240 + $912 = $2,152
    assert tax.progressive_tax(20_000, FEDERAL_SINGLE_2026) == pytest.approx(2_152)


def test_federal_tax_100k():
    # Bracket math at $100k taxable single 2026 (Rev. Proc. 2025-32):
    # 12,400×0.10 + (50,400−12,400)×0.12 + (100,000−50,400)×0.22
    # = 1,240 + 4,560 + 10,912 = 16,712
    assert tax.federal_income_tax(100_000, 2026) == pytest.approx(16_712, abs=1)


def test_federal_tax_top_bracket():
    # $1M taxable, walk all the way up
    # Brackets: 10/12/22/24/32/35/37 (Rev. Proc. 2025-32)
    expected = (
        12_400 * 0.10
        + (50_400 - 12_400) * 0.12
        + (105_700 - 50_400) * 0.22
        + (201_775 - 105_700) * 0.24
        + (256_225 - 201_775) * 0.32
        + (640_600 - 256_225) * 0.35
        + (1_000_000 - 640_600) * 0.37
    )
    assert tax.federal_income_tax(1_000_000, 2026) == pytest.approx(expected, abs=1)


def test_ca_tax_at_real_bracket():
    # $200k CA taxable: walks 1/2/4/6/8 then sits in 9.3%
    income = 200_000
    expected = (
        11_100 * 0.01
        + (26_300 - 11_100) * 0.02
        + (41_500 - 26_300) * 0.04
        + (57_600 - 41_500) * 0.06
        + (72_800 - 57_600) * 0.08
        + (200_000 - 72_800) * 0.093
    )
    assert tax.ca_income_tax(income, 2026) == pytest.approx(expected, abs=1)


def test_fica_ss_capped():
    # At $300k wages 2026, SS should hit the $184,500 cap (SSA, Oct 2025)
    f = tax.fica(300_000, 2026)
    assert f.social_security == pytest.approx(184_500 * 0.062, abs=1)  # = $11,439 max
    # Medicare on full wages
    assert f.medicare == pytest.approx(300_000 * 0.0145, abs=1)
    # Additional Medicare 0.9% over $200k
    assert f.additional_medicare == pytest.approx((300_000 - 200_000) * 0.009, abs=1)
    # CA SDI uncapped at 1.3% (CA EDD 2026)
    assert f.ca_sdi == pytest.approx(300_000 * 0.013, abs=1)


def test_fica_under_ss_cap():
    f = tax.fica(100_000, 2026)
    assert f.social_security == pytest.approx(100_000 * 0.062, abs=1)
    assert f.additional_medicare == 0  # under $200k threshold


def test_niit_under_threshold():
    # AGI under $200k → no NIIT
    assert tax.niit(investment_income=10_000, agi=150_000) == 0


def test_niit_above_threshold():
    # AGI $250k, investment income $10k → NIIT applies to min($10k, $50k) = $10k
    assert tax.niit(investment_income=10_000, agi=250_000) == pytest.approx(10_000 * 0.038)


def test_niit_capped_by_excess():
    # AGI $205k (only $5k over threshold), investment income $10k → NIIT applies to $5k
    assert tax.niit(investment_income=10_000, agi=205_000) == pytest.approx(5_000 * 0.038)


def test_brackets_inflate_correctly():
    from variometer.data.brackets import federal_brackets

    b2026 = federal_brackets(2026)
    b2027 = federal_brackets(2027, inflation=0.03)
    # Each bound should be 3% higher
    assert b2027[1].lo == pytest.approx(b2026[1].lo * 1.03)
    # Rate unchanged
    assert b2027[1].rate == b2026[1].rate
