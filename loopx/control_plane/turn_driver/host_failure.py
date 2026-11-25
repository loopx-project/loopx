"""Typed, public-safe host failure and bounded retry records."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
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
    """Transport a native classification to the existing typed policy owner.

    Native error construction and the Python integer API remain local. Retry
    hints grant no launch authority; journals retain their original attempts.
    """
    from ..effect_runtime import effect_runtime_result

    normalized_kind = require_host_failure_kind(kind)
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("host failure attempt must be a positive integer")
    record = effect_runtime_result("host_failure.record", {
        "kind": normalized_kind, "attempt_text": format(Decimal(int(attempt)), "f"),
    })
    if not isinstance(record, dict):
        raise RuntimeError("Host failure policy record is unavailable")
    # Exact decimal transport avoids JSON rounding of historical Python ints.
    record["attempt"] = attempt
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
    expected = build_host_failure_record(kind, attempt=attempt)
    if retryable is not expected["retryable"]:
        raise ValueError("host failure record retryability does not match its kind")
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
    """Automatic retry remains narrower than an explicit repaired retry."""
    from ..effect_runtime import effect_runtime_result

    normalized = normalize_host_failure_record(record)
    if normalized["retryable"] is not True:
        return False
    result = effect_runtime_result("host_failure.retry", {
        "failure": normalized, "retry_failed": True, "automatic_only": True,
    })
    if not isinstance(result, dict) or type(result.get("allowed")) is not bool:
        raise RuntimeError("Host retry decision is unavailable")
    return bool(result["allowed"])


def explicit_host_failure_retry_available(record: Mapping[str, Any], *, retry_failed: bool) -> bool:
    """Share the Turn Journal's explicit repair rule; no automatic retry change."""
    from ..effect_runtime import effect_runtime_result

    if type(retry_failed) is not bool:
        raise ValueError("Host retry requires an explicit retry intent")
    result = effect_runtime_result("host_failure.retry", {
        "failure": normalize_host_failure_record(record), "retry_failed": retry_failed,
    })
    if not isinstance(result, dict) or type(result.get("allowed")) is not bool:
        raise RuntimeError("Host retry decision is unavailable")
    return bool(result["allowed"])


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
