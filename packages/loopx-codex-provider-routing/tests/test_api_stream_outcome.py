"""Stream failures through the existing real HTTP relay and private key store."""
import importlib
import json
from pathlib import Path
import runpy

import pytest

# Share the canonical real loopback fixture, rather than introduce another server.
_fixture = runpy.run_path(str(Path(__file__).resolve().parents[3] / "tests/presentation/test_openai_relay.py"))
Response, relay = _fixture["Response"], _fixture["relay"]


def event(value):
    return b"data: " + json.dumps(value).encode() + b"\n\n"


@pytest.mark.parametrize("failure", [
    {"type": "error", "code": "usage_limit_exceeded", "message": "private quota detail"},
    {"type": "response.failed", "response": {"status": "failed", "error": {
        "code": "insufficient_quota", "message": "private quota detail"}}},
])
def test_stream_failure_is_forwarded_once_and_only_next_request_uses_next_key(relay, failure):
    _, keys, calls, outcomes, post, body = relay
    stream = event({"type": "response.created"}) + event(failure)
    outcomes.append(Response(stream))
    assert post() == (200, stream)
    assert len(calls) == 1, "An admitted stream is never retried, even on a known quota failure"
    assert keys.status()["keys"][0]["runtime"]["state"] == "quota_exhausted"
    success = event({"type": "response.completed", "response": {"status": "completed"}})
    outcomes.append(Response(success))
    assert post() == (200, success) and len(calls) == 2
    assert calls[0].get_header("Authorization") != calls[1].get_header("Authorization")
    assert calls[0].data == calls[1].data == json.dumps(body).encode()
    saved = keys.path.read_text()
    assert "synthetic prompt" not in saved and "private quota detail" not in saved
    assert keys.status()["balance_available"] is False


@pytest.mark.parametrize("stream", [
    event({"type": "response.output_text.delta", "delta": '{"type":"error","code":"insufficient_quota"}'}),
    event({"type": "error", "message": "Quota exceeded"}),
    event({"type": "error", "code": "server_error"}),
    b'data: {"type":"error","code":"insufficient_quota"}',  # Truncated frame.
])
def test_unknown_prose_and_partial_errors_do_not_rotate_or_replay(relay, stream):
    _, keys, calls, outcomes, post, _ = relay
    outcomes.append(Response(stream))
    assert post() == (200, stream) and len(calls) == 1
    assert keys.status()["keys"][0]["runtime"]["state"] == "accepted"


def test_fragmented_crlf_and_multiline_error_observation_is_bounded(relay, monkeypatch):
    module = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    observer = module._StreamOutcome()
    oversized = b"data: " + b"x" * 100000 + b"\r\n\r\n"
    stream = oversized + b'event: error\r\ndata: {"type":"error",\r\ndata: "code":"rate_limit_exceeded"}\r\n\r\n'
    results = []
    for offset in range(0, len(stream), 17):
        value = observer.feed(stream[offset:offset + 17])
        if value is not None:
            results.append(value)
        assert len(observer.buffer) <= 65536
    assert results == [{"status": 429, "code": "rate_limit_exceeded"}]
    assert observer.feed(event({"type": "error", "code": "insufficient_quota"})) is None
    observer = module._StreamOutcome()
    monkeypatch.setattr(module, "api_policy", lambda *_: pytest.fail("Ordinary tokens must not spawn a policy process"))
    assert observer.feed(event({"type": "response.output_text.delta", "delta": "ordinary text"})) is None


def test_failed_observer_does_not_change_original_stream(relay, monkeypatch):
    module = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    def unavailable(*_):
        raise OSError("policy unavailable")
    monkeypatch.setattr(module, "api_policy", unavailable)
    _, _, calls, outcomes, post, _ = relay
    stream = event({"type": "error", "code": "insufficient_quota"})
    outcomes.append(Response(stream))
    assert post() == (200, stream) and len(calls) == 1
