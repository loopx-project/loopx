"""Structured provider failures survive collection without private content."""

import json
import os

import pytest

from benchmark.runtime.provider_failure import observe_terminal_provider_failure


def _event(info, *, message="PRIVATE-SENTINEL"):
    return {"type": "event_msg", "payload": {"type": "task_complete", "error": {
        "message": message, **info,
    }}}


@pytest.mark.parametrize("info", [
    {"codexErrorInfo": {"responseTooManyFailedAttempts": {"httpStatusCode": 429}}},
    {"codex_error_info": {"response_too_many_failed_attempts": {"http_status_code": 429}}},
])
def test_structured_terminal_429_is_reduced_without_message_or_path(tmp_path, info):
    session = tmp_path / "sessions" / "rollout.jsonl"
    session.parent.mkdir()
    session.write_text(json.dumps(_event(info)) + "\n")

    receipt = observe_terminal_provider_failure(tmp_path / "sessions")

    assert receipt == {
        "schema_version": "codex_terminal_provider_failure_v0",
        "kind": "response_too_many_failed_attempts",
        "http_status_code": 429,
    }
    assert "PRIVATE-SENTINEL" not in json.dumps(receipt)
    assert str(tmp_path) not in json.dumps(receipt)


@pytest.mark.parametrize("event", [
    _event({"codexErrorInfo": {"futureProviderError": {"httpStatusCode": 429}}}),
    _event({"codexErrorInfo": {"responseTooManyFailedAttempts": {"httpStatusCode": "429"}}}),
    {"type": "event_msg", "payload": {"type": []}},
    {"type": "event_msg", "payload": {"type": "task_complete",
        "error": {"message": "HTTP 429 rate limit"}}},
    {"type": "response_item", "payload": {"text": "HTTP 429 rate limit"}},
])
def test_unknown_or_free_text_failures_fail_closed(tmp_path, event):
    session = tmp_path / "sessions" / "rollout.jsonl"
    session.parent.mkdir()
    session.write_text(json.dumps(event) + "\n")
    assert observe_terminal_provider_failure(tmp_path / "sessions") is None


def test_bounded_tail_drops_truncated_first_line_and_uses_complete_terminal_event(tmp_path):
    session = tmp_path / "sessions" / "rollout.jsonl"
    session.parent.mkdir()
    ignored = _event({"codexErrorInfo": {
        "responseTooManyFailedAttempts": {"httpStatusCode": 503}}})
    expected = _event({"codex_error_info": {
        "response_too_many_failed_attempts": {"http_status_code": 429}}})
    session.write_bytes(
        json.dumps(ignored).encode() + b"\n" + b"x" * 200 + b"\n" + json.dumps(expected).encode() + b"\n"
    )

    receipt = observe_terminal_provider_failure(tmp_path / "sessions", tail_bytes=256)

    assert receipt and receipt["http_status_code"] == 429


def test_newest_session_and_latest_terminal_event_win(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    old = root / "old.jsonl"
    new = root / "new.jsonl"
    old.write_text(json.dumps(_event({"codexErrorInfo": {
        "responseTooManyFailedAttempts": {"httpStatusCode": 503}}})) + "\n")
    new.write_text("\n".join(json.dumps(_event({"codexErrorInfo": {
        "responseTooManyFailedAttempts": {"httpStatusCode": status}}}))
        for status in (502, 429)) + "\n")
    old.touch()
    new.touch()
    newer = old.stat().st_mtime_ns + 1_000_000
    os.utime(new, ns=(newer, newer))

    assert observe_terminal_provider_failure(root)["http_status_code"] == 429


def test_later_success_does_not_replay_an_earlier_provider_failure(tmp_path):
    session = tmp_path / "sessions" / "rollout.jsonl"
    session.parent.mkdir()
    failure = _event({"codexErrorInfo": {
        "responseTooManyFailedAttempts": {"httpStatusCode": 429}}})
    success = {"type": "event_msg", "payload": {"type": "task_complete"}}
    session.write_text(json.dumps(failure) + "\n" + json.dumps(success) + "\n")

    assert observe_terminal_provider_failure(tmp_path / "sessions") is None


def test_scan_bounds_validate_before_reading(tmp_path):
    with pytest.raises(ValueError, match="positive"):
        observe_terminal_provider_failure(tmp_path, tail_bytes=0)
    with pytest.raises(ValueError, match="positive"):
        observe_terminal_provider_failure(tmp_path, max_files=0)
