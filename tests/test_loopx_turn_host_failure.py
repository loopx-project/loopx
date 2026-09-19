from __future__ import annotations

import pytest

from loopx.control_plane.turn_driver.host_failure import (
    build_host_failure_record,
    host_failure_retry_available,
    normalize_host_failure_record,
    project_host_failure,
)


@pytest.mark.parametrize(
    ("kind", "base_backoff_seconds"),
    [
        ("provider_capacity", 30),
        ("provider_overloaded", 30),
        ("rate_limited", 60),
    ],
)
def test_transient_provider_failure_uses_bounded_exponential_backoff(
    kind: str,
    base_backoff_seconds: int,
) -> None:
    first = build_host_failure_record(kind, attempt=1)
    second = build_host_failure_record(kind, attempt=2)
    exhausted = build_host_failure_record(kind, attempt=3)

    assert first["retry"] == {
        "strategy": "same_configuration",
        "max_attempts": 3,
        "backoff_seconds": base_backoff_seconds,
    }
    assert second["retry"]["backoff_seconds"] == base_backoff_seconds * 2
    assert host_failure_retry_available(first) is True
    assert host_failure_retry_available(exhausted) is False


def test_host_failure_record_rejects_caller_authored_retry_policy() -> None:
    forged = build_host_failure_record("provider_capacity", attempt=1)
    forged["retry"]["max_attempts"] = 99

    with pytest.raises(ValueError, match="invalid retry policy"):
        normalize_host_failure_record(forged)


def test_non_retryable_failure_has_no_automatic_retry_policy() -> None:
    failure = build_host_failure_record("auth_failed", attempt=1)

    assert failure == {
        "schema_version": "loopx_turn_host_failure_v0",
        "kind": "auth_failed",
        "attempt": 1,
        "retryable": False,
    }
    assert host_failure_retry_available(failure) is False


def test_exhausted_quota_requires_repair_instead_of_timed_retry() -> None:
    failure = build_host_failure_record("quota_exhausted", attempt=1)

    assert failure["retryable"] is False
    assert "retry" not in failure
    assert host_failure_retry_available(failure) is False


def test_output_budget_exhaustion_cannot_blindly_retry() -> None:
    failure = build_host_failure_record("output_budget_exhausted", attempt=1)

    assert failure["retryable"] is False
    assert "retry" not in failure
    assert host_failure_retry_available(failure) is False


def test_public_projection_rejects_unallowlisted_failure_fields() -> None:
    failure = build_host_failure_record("provider_capacity", attempt=1)
    failure["provider_message"] = "private provider prose"

    with pytest.raises(ValueError, match="unsupported fields"):
        project_host_failure({"host_failure": failure})
