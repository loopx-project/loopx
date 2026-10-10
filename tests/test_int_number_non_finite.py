import pytest

from loopx.control_plane.quota.ledger_readback import _int_number as ledger_int_number
from loopx.orchestration import _int_number as orchestration_int_number
from loopx.quota import _int_number as quota_int_number


@pytest.mark.parametrize(
    "value",
    [float("inf"), float("-inf"), float("nan"), "inf", "-inf", "nan", "1e400"],
)
@pytest.mark.parametrize(
    "int_number",
    [quota_int_number, orchestration_int_number, ledger_int_number],
)
def test_int_number_returns_default_for_non_finite_values(int_number, value):
    assert int_number(value, default=7) == 7
