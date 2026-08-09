"""The modeling envelope — what the model claims to support, enforced.

The tax code is single-filer federal + CA state with no dependent credits.
``Scenario`` carries ``filing_status`` / ``state`` so an out-of-envelope
profile fails loudly instead of being silently mismodeled, and the bracket
tables announce their own staleness once they're more than a year past the
model epoch.
"""

from datetime import date

import pytest

from variometer.config import (
    MODEL_EPOCH,
    SUPPORTED_FILING_STATUS,
    SUPPORTED_STATE,
    Scenario,
    base_scenario,
    bracket_staleness_warning,
)

# ---------------------------------------------------------------------------
# filing_status / state fields
# ---------------------------------------------------------------------------


def test_defaults_are_the_supported_envelope():
    s = Scenario()
    assert s.filing_status == SUPPORTED_FILING_STATUS == "single"
    assert s.state == SUPPORTED_STATE == "CA"


def test_explicit_supported_values_are_accepted():
    s = Scenario(filing_status="single", state="CA")
    assert s.filing_status == "single"
    assert s.state == "CA"


@pytest.mark.parametrize(
    "status", ["married_filing_jointly", "married_filing_separately", "head_of_household", ""]
)
def test_unsupported_filing_status_raises(status):
    with pytest.raises(ValueError, match="filing_status"):
        Scenario(filing_status=status)


@pytest.mark.parametrize("state", ["TX", "NY", "WA", "ca"])
def test_unsupported_state_raises(state):
    with pytest.raises(ValueError, match="state"):
        Scenario(state=state)


def test_error_message_names_the_envelope():
    """The message has to tell the reader what IS supported, not just what isn't."""
    with pytest.raises(ValueError) as exc:
        Scenario(state="TX")
    msg = str(exc.value)
    assert "single" in msg
    assert "California" in msg


def test_base_scenario_is_inside_the_envelope():
    """The public suite runs with the profile overlay off, so this is the
    placeholder persona — but the same constructor validates a real profile."""
    s = base_scenario()
    assert s.filing_status == SUPPORTED_FILING_STATUS
    assert s.state == SUPPORTED_STATE


def test_envelope_fields_do_not_affect_computation():
    """They are declarative only: nothing downstream reads them."""
    from variometer.projection import project_net_worth

    years = [2026, 2030, 2035]
    baseline = project_net_worth(Scenario(), years)
    explicit = project_net_worth(Scenario(filing_status="single", state="CA"), years)
    assert [r.total_nw for r in baseline] == [r.total_nw for r in explicit]


# ---------------------------------------------------------------------------
# bracket_staleness_warning()
# ---------------------------------------------------------------------------


def test_no_warning_in_the_epoch_year():
    assert bracket_staleness_warning(date(MODEL_EPOCH, 6, 15)) is None


def test_no_warning_one_year_past_the_epoch():
    assert bracket_staleness_warning(date(MODEL_EPOCH + 1, 12, 31)) is None


def test_warns_three_years_past_the_epoch():
    warning = bracket_staleness_warning(date(MODEL_EPOCH + 3, 1, 1))
    assert warning is not None
    assert str(MODEL_EPOCH) in warning
    assert str(MODEL_EPOCH + 3) in warning
    assert "3 years" in warning


def test_warning_grows_with_distance_from_the_epoch():
    assert "2 years" in (bracket_staleness_warning(date(MODEL_EPOCH + 2, 1, 1)) or "")
    assert "10 years" in (bracket_staleness_warning(date(MODEL_EPOCH + 10, 1, 1)) or "")


def test_default_today_is_accepted():
    """No-arg call uses date.today() and returns the right shape."""
    result = bracket_staleness_warning()
    assert result is None or isinstance(result, str)
