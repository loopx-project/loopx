from __future__ import annotations

import pytest

from loopx.capabilities.decision_context.packets import _score
from loopx.capabilities.explore.result_log import _safe_confidence


@pytest.mark.parametrize(
    ("validator",),
    [
        ("decision-context score",),
        ("explore confidence",),
    ],
)
def test_score_validators_reject_integer_overflow_as_value_error(validator: str) -> None:
    with pytest.raises(ValueError):
        if validator == "decision-context score":
            _score(10**400, field="score")
        else:
            _safe_confidence(10**400)
