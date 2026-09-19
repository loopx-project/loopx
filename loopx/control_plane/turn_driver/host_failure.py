"""Typed, public-safe host failure and bounded retry records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .session_recovery import require_host_recovery_kind


HOST_FAILURE_SCHEMA_VERSION = "loopx_turn_host_failure_v0"
HOST_FAILURE_KINDS = frozenset(
    {
        "auth_failed",
        "contract_rejected",
        "executor_timeout",
        "output_budget_exhausted",
        "provider_capacity",
        "provider_overloaded",
        "quota_exhausted",
        "rate_limited",
        "session_missing",
        "transport_lost",
        "unknown",
    }
)
HOST_RETRY_STRATEGY = "same_configuration"

# These are scheduling hints, not an in-process sleep policy. The Turn journal
# remains the authority for the attempt count and the outer controller decides
# when to wake the exact same Turn again.
_RETRY_POLICIES = {
    "executor_timeout": (2, 5),
    "provider_capacity": (3, 30),
    "provider_overloaded": (3, 30),
    "rate_limited": (3, 60),
    "transport_lost": (3, 10),
}


class BuiltInHostError(RuntimeError):
    """A public-safe built-in host failure classification."""

    def __init__(
        self,
        reason: str,
        *,
        failure_kind: str = "unknown",
        recovery_kind: str | None = None,
    ) -> None:
        if recovery_kind is not None:
            require_host_recovery_kind(recovery_kind)
        require_host_failure_kind(failure_kind)
        super().__init__(reason)
        self.reason = reason
        self.failure_kind = failure_kind
        self.recovery_kind = recovery_kind


def require_host_failure_kind(value: str) -> str:
    if value not in HOST_FAILURE_KINDS:
        raise ValueError("unsupported built-in host failure kind")
    return value


def build_host_failure_record(kind: str, *, attempt: int) -> dict[str, Any]:
    """Build one content-free failure record from an adapter classification."""

    normalized_kind = require_host_failure_kind(kind)
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("host failure attempt must be a positive integer")
    record: dict[str, Any] = {
        "schema_version": HOST_FAILURE_SCHEMA_VERSION,
        "kind": normalized_kind,
        "attempt": attempt,
        "retryable": normalized_kind in _RETRY_POLICIES,
    }
    policy = _RETRY_POLICIES.get(normalized_kind)
    if policy is not None:
        max_attempts, base_backoff_seconds = policy
        record["retry"] = {
            "strategy": HOST_RETRY_STRATEGY,
            "max_attempts": max_attempts,
            "backoff_seconds": min(
                base_backoff_seconds * (2 ** max(0, attempt - 1)),
                300,
            ),
        }
    return record


def normalize_host_failure_record(value: Any) -> dict[str, Any]:
    """Validate a record before it can influence controller recovery."""

    if not isinstance(value, Mapping):
        raise ValueError("host failure record must be one object")
    record = dict(value)
    if record.get("schema_version") != HOST_FAILURE_SCHEMA_VERSION:
        raise ValueError("host failure record has an unsupported schema")
    kind = require_host_failure_kind(str(record.get("kind") or ""))
    attempt = record.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("host failure record has an invalid attempt")
    retryable = record.get("retryable")
    if not isinstance(retryable, bool):
        raise ValueError("host failure record has an invalid retryable marker")
    expected_retryable = kind in _RETRY_POLICIES
    if retryable is not expected_retryable:
        raise ValueError("host failure record retryability does not match its kind")
    expected = build_host_failure_record(kind, attempt=attempt)
    if retryable:
        retry = record.get("retry")
        if not isinstance(retry, Mapping) or dict(retry) != expected["retry"]:
            raise ValueError("host failure record has an invalid retry policy")
    elif "retry" in record:
        raise ValueError("non-retryable host failure must not declare retry policy")
    if set(record) != set(expected):
        raise ValueError("host failure record contains unsupported fields")
    return expected


def host_failure_retry_available(record: Mapping[str, Any]) -> bool:
    normalized = normalize_host_failure_record(record)
    if normalized["retryable"] is not True:
        return False
    retry = dict(normalized["retry"])
    return int(normalized["attempt"]) < int(retry["max_attempts"])


def record_host_failure(
    journal: dict[str, Any],
    *,
    kind: str,
) -> None:
    journal["host_failure"] = build_host_failure_record(
        kind,
        attempt=int(journal["host_attempt_count"]),
    )


def project_host_failure(journal: Mapping[str, Any]) -> dict[str, Any]:
    failure = journal.get("host_failure")
    return (
        {"host_failure": normalize_host_failure_record(failure)}
        if isinstance(failure, Mapping)
        else {}
    )
