"""Native failure records keep their historical API and refusal ordering."""
from copy import deepcopy
import json
from types import MappingProxyType

import pytest

from loopx.control_plane.turn_driver.host_failure import (
    BuiltInHostError, build_host_failure_record, host_failure_retry_available,
    normalize_host_failure_record,
)


@pytest.mark.parametrize("attempt", [2**53 + 1, 10**40, 10**5000],
    ids=["json-safe-range-exceeded", "wide-integer", "decimal-codec-limit"])
def test_non_retryable_attempts_keep_exact_python_integer_readback(attempt):
    failure = build_host_failure_record("auth_failed", attempt=attempt)
    assert failure == {"schema_version": "loopx_turn_host_failure_v0", "kind": "auth_failed",
        "attempt": attempt, "retryable": False}
    assert type(failure["attempt"]) is int
    assert normalize_host_failure_record(failure) == failure
    assert host_failure_retry_available(failure) is False


@pytest.mark.parametrize(("changes", "message"), [
    ({"schema_version": "future", "kind": "bad", "attempt": False}, "unsupported schema"),
    ({"kind": "bad", "attempt": False}, "unsupported built-in host failure kind"),
    ({"attempt": False, "retryable": "true"}, "invalid attempt"),
    ({"retryable": "true", "retry": {}}, "invalid retryable marker"),
    ({"retryable": False, "retry": {}}, "retryability does not match its kind"),
    ({"retry": {}, "extra": "ignored only after validation"}, "invalid retry policy"),
    ({"extra": "private provider prose"}, "contains unsupported fields"),
])
def test_record_refusal_keeps_its_original_precedence_and_input(changes, message):
    record = {**build_host_failure_record("provider_capacity", attempt=1), **changes}
    before = deepcopy(record)
    with pytest.raises(ValueError, match=message):
        normalize_host_failure_record(record)
    assert record == before


def test_mapping_retry_and_equal_float_metadata_keep_the_original_record():
    raw = {"schema_version": "loopx_turn_host_failure_v0", "kind": "executor_timeout",
        "attempt": 1, "retryable": True, "retry": MappingProxyType({
            "strategy": "same_configuration", "max_attempts": 2.0, "backoff_seconds": 5.0})}
    normalized = normalize_host_failure_record(MappingProxyType(raw))
    assert normalized == {**raw, "retry": {"strategy": "same_configuration",
        "max_attempts": 2, "backoff_seconds": 5}}
    assert type(normalized["retry"]["max_attempts"]) is int
    assert normalized["retry"] is not raw["retry"]


def test_native_error_construction_does_not_need_the_effect_runtime(monkeypatch):
    from loopx.control_plane import effect_runtime

    def unavailable(*args, **kwargs):
        pytest.fail("Native protocol error creation must remain independent of the policy RPC")

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", unavailable)
    failure = BuiltInHostError("provider_unavailable", failure_kind="provider_capacity")
    assert str(failure) == "provider_unavailable" and failure.failure_kind == "provider_capacity"
    with pytest.raises(ValueError, match="unsupported built-in host failure kind"):
        BuiltInHostError("provider_unavailable", failure_kind="unregistered")


@pytest.mark.parametrize("kind", ["provider_capacity", "rate_limited", "executor_timeout"])
def test_valid_failure_kind_is_checked_before_bad_attempt(kind):
    with pytest.raises(ValueError, match="positive integer"):
        build_host_failure_record(kind, attempt=True)
    with pytest.raises(ValueError, match="unsupported built-in host failure kind"):
        build_host_failure_record("unregistered", attempt=True)


@pytest.mark.parametrize(("change", "kind", "session", "transport"), [
    ({}, "provider_capacity", "session-a", None),
    ({"timed_out": [], "error_kind": {}}, "provider_capacity", "session-a", None),
    ({"ok": True}, "unknown", "session-a", None),
    ({"session": {"binding_scope": "agent", "session_id": "session-b"}}, "unknown", "session-a", None),
    ({"session": {}}, "unknown", "session-a", None),
    ({"host_failure": {"schema_version": "future"}}, "unknown", "session-a", None),
    ({}, "unknown", "session-a", "timeout"),
])
def test_native_observation_keeps_scope_truth_encoding_and_private_receipt_fields(
    change, kind, session, transport, monkeypatch,
):
    from tests.test_external_scheduler_worker import worker
    from loopx.control_plane import effect_runtime

    receipt = {"ok": False, "timed_out": False, "error_kind": None,
        "session": {"binding_scope": "agent", "session_id": "session-a"},
        "host_failure": {"schema_version": "loopx_turn_host_failure_v0", "kind": "provider_capacity",
            "attempt": 1, "retryable": True, "retry": {"strategy": "same_configuration",
                "max_attempts": 3, "backoff_seconds": 30}},
        "private_diagnostic": "private-provider-sentinel", **change}
    attempt = {"attempt": 2, "session_id": "session-a", "scope": "original", "attempt_id": "original-attempt"}
    before = deepcopy((receipt, attempt))
    observed = []
    invoke = effect_runtime.effect_runtime_result

    def record(method, params, **kwargs):
        observed.append((method, params))
        return invoke(method, params, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", record)
    result = worker._host_result(worker.CappedProcessResult(1, json.dumps(receipt).encode(), transport), attempt, 100)
    assert result == {**attempt, "status": "failed", "session_id": session,
        "host_failure": {"schema_version": "loopx_turn_host_failure_v0", "kind": kind,
            "attempt": 2, "retryable": kind == "provider_capacity",
            **({"retry": {"strategy": "same_configuration", "max_attempts": 3, "backoff_seconds": 60}}
               if kind == "provider_capacity" else {})},
        "not_before": 160 if kind == "provider_capacity" else 100}
    assert "private-provider-sentinel" not in json.dumps(observed)
    assert (receipt, attempt) == before


def test_confirmed_native_success_does_not_require_later_failure_facts():
    from tests.test_external_scheduler_worker import worker

    receipt = {"ok": True, "session": {"binding_scope": "agent", "session_id": "session-a"}}
    attempt = {"attempt": False, "session_id": "session-a"}
    assert worker._host_result(worker.CappedProcessResult(0, json.dumps(receipt).encode(), None), attempt, 0) is None
