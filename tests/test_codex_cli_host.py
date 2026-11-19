"""Shared native error semantics; no model or private transcript fixtures."""
from __future__ import annotations

import json

import pytest

from loopx.control_plane.turn_driver.codex_cli import CodexFailureObservation


@pytest.mark.parametrize("event,kind", [
    ({"type": "turn.failed", "error": {"code": "insufficient_quota", "status_code": 429}}, "quota_exhausted"),
    ({"type": "error", "code": "new_unknown_code", "message": "server overloaded"}, "unknown"),
    ({"type": "error", "codexErrorInfo": "UsageLimitExceeded"}, "quota_exhausted"),
    ({"type": "turn.failed", "error": {"status_code": 429}}, "rate_limited"),
    ({"type": "turn.failed", "error": {"code": "model_at_capacity"}}, "provider_capacity"),
    ({"type": "error", "message": "untyped private provider detail"}, "unknown"),
    ({"type": "item.completed", "item": {"type": "command_execution", "error": {"code": "insufficient_quota"}}}, "unknown"),
])
def test_shared_observer_preserves_structured_precedence(event, kind):
    observer = CodexFailureObservation()
    observer.observe_stdout(json.dumps(event))
    assert observer.failure_kind() == kind
    assert observer.failure_kind(complete=False) == "unknown"
    assert "private" not in repr(vars(observer))


def test_structured_error_wins_over_earlier_diagnostic_and_does_not_keep_text():
    observer = CodexFailureObservation()
    observer.observe_stderr("server overloaded PRIVATE-SENTINEL")
    observer.observe_event({"type": "turn.failed", "error": {"code": "insufficient_quota"}})
    assert observer.failure_kind() == "quota_exhausted"
    assert "PRIVATE-SENTINEL" not in repr(vars(observer))
