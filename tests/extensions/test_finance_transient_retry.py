"""Synthetic coverage for the bounded venue-refusal retry policy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "loopx-finance-execution" / "src"))

from loopx_finance_execution.transient_retry import (  # noqa: E402
    RETRYABLE,
    TERMINAL,
    RetryPolicy,
    classify_venue_refusal,
    may_retry,
    next_wait_seconds,
)


NOW = datetime(2026, 10, 17, 4, 0, tzinfo=timezone.utc)
POLICY = RetryPolicy()


def _decide(**overrides):
    arguments = {
        "policy": POLICY,
        "attempt": 1,
        "elapsed_seconds": 0.0,
        "venue_effect_observed": False,
        "refusal": "Hyperliquid 请求过于频繁，请等待 10 秒后重试。",
    }
    arguments.update(overrides)
    return may_retry(**arguments)


def test_rate_limit_is_retryable() -> None:
    assert classify_venue_refusal("Hyperliquid 请求过于频繁，请等待 10 秒后重试。") == RETRYABLE
    assert classify_venue_refusal("rate limit exceeded") == RETRYABLE
    assert classify_venue_refusal("Request timeout after 30000ms") == RETRYABLE


def test_terminal_refusals_are_not_retried() -> None:
    for message in ("余额不足", "invalid price precision", "", None, "order rejected"):
        assert classify_venue_refusal(message) == TERMINAL
        allowed, reason = _decide(refusal=message)
        assert allowed is False and reason == "refusal_not_retryable"


def test_visible_effect_never_retries() -> None:
    allowed, reason = _decide(venue_effect_observed=True)
    assert allowed is False and reason == "venue_effect_observed"


def test_attempt_budget_is_enforced() -> None:
    allowed, reason = _decide(attempt=POLICY.max_attempts)
    assert allowed is False and reason == "attempt_budget_spent"


def test_time_budget_is_enforced() -> None:
    allowed, reason = _decide(elapsed_seconds=POLICY.max_total_seconds - 1)
    assert allowed is False and reason == "time_budget_spent"


def test_deadline_stops_retries() -> None:
    allowed, reason = _decide(deadline=NOW, now=NOW)
    assert allowed is False and reason == "operation_expired"
    allowed, reason = _decide(deadline=NOW + timedelta(minutes=1), now=NOW)
    assert allowed is True and reason == "retry_admissible"


def test_backoff_schedule() -> None:
    assert next_wait_seconds(POLICY, 1) == 0.0
    assert next_wait_seconds(POLICY, 2) == 12.0
    assert next_wait_seconds(POLICY, 3) == 25.0
    assert next_wait_seconds(POLICY, 4) == 35.0
    # The last entry repeats instead of wrapping to zero.
    assert next_wait_seconds(POLICY, 9) == 35.0


def test_invalid_policy_is_rejected() -> None:
    for kwargs in (
        {"max_attempts": 0},
        {"waits_seconds": ()},
        {"waits_seconds": (-1.0,)},
        {"max_total_seconds": 0},
    ):
        try:
            RetryPolicy(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"expected RetryPolicy to reject {kwargs}")
