"""Bounded retry policy for transient venue refusals.

A venue refuses an order for many reasons. Some are transient (rate limits,
request timeouts) and worth one bounded wait-and-retry. Others are terminal
(insufficient balance, invalid precision) and must stop immediately. Retrying a
submission is only safe when the executor first proves that the earlier attempt
produced no effect, so every decision helper here takes that observation.

The module is deliberately provider-neutral: it classifies a bounded set of
public refusal signals, computes the next backoff, and refuses to retry when the
operation deadline passed, the attempt budget is spent or a venue effect is
already visible. It performs no IO and grants no execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re


RETRY_POLICY_SCHEMA_VERSION = "finance_transient_retry_policy_v0"

#: Fixed patterns for transient venue refusals. Anything not matched here is
#: treated as terminal: an unknown refusal is never retried.
RETRYABLE_PATTERNS = (
    r"请求过于频繁",
    r"rate.?limit",
    r"too many requests",
    r"too frequent",
    r"timeout|timed out",
    r"超时",
    r"稍后重试",
    r"try again",
)

RETRYABLE = "retryable"
TERMINAL = "terminal"


@dataclass(frozen=True)
class RetryPolicy:
    """Attempt budget, backoff schedule and wall-clock ceiling."""

    max_attempts: int = 4
    waits_seconds: tuple[float, ...] = (12.0, 25.0, 35.0)
    max_total_seconds: float = 150.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not self.waits_seconds or any(wait < 0 for wait in self.waits_seconds):
            raise ValueError("waits_seconds must be non-negative")
        if self.max_total_seconds <= 0:
            raise ValueError("max_total_seconds must be positive")


def classify_venue_refusal(message: object) -> str:
    """Return RETRYABLE or TERMINAL for one venue refusal message."""

    text = str(message or "").strip()
    if not text:
        return TERMINAL
    for pattern in RETRYABLE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return RETRYABLE
    return TERMINAL


def next_wait_seconds(policy: RetryPolicy, attempt: int) -> float:
    """Backoff before ``attempt`` (1-based). The last entry repeats."""

    if attempt < 2:
        return 0.0
    index = min(attempt - 2, len(policy.waits_seconds) - 1)
    return float(policy.waits_seconds[index])


def may_retry(
    *,
    policy: RetryPolicy,
    attempt: int,
    elapsed_seconds: float,
    venue_effect_observed: bool,
    refusal: object,
    deadline: datetime | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Decide whether one more submission attempt is admissible.

    Returns ``(allowed, reason)``. ``reason`` is a stable public token, never a
    venue payload.
    """

    if venue_effect_observed:
        return False, "venue_effect_observed"
    if attempt >= policy.max_attempts:
        return False, "attempt_budget_spent"
    if elapsed_seconds + next_wait_seconds(policy, attempt + 1) > policy.max_total_seconds:
        return False, "time_budget_spent"
    if classify_venue_refusal(refusal) != RETRYABLE:
        return False, "refusal_not_retryable"
    if deadline is not None and now is not None:
        clock = now.astimezone(timezone.utc)
        expiry = deadline if deadline.tzinfo else deadline.replace(tzinfo=timezone.utc)
        if clock >= expiry.astimezone(timezone.utc):
            return False, "operation_expired"
    return True, "retry_admissible"
