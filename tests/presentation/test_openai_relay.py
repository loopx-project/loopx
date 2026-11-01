"""Real loopback HTTP and private store, with synthetic upstream transport."""
import importlib
import io
import json
import os
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Native policy uses POSIX locks")


class Response(io.BytesIO):
    status = 200
    headers = {"Content-Type": "text/event-stream", "x-ratelimit-remaining-tokens": "1234"}


@pytest.fixture
def relay(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "packages/loopx-codex-provider-routing/src"))
    native = importlib.import_module("loopx_codex_provider_routing.native")
    module = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    owner = native.NativeAccounts(tmp_path, clock=lambda: 1000)
    keys = owner.api_keys()
    for index in range(2):
        keys.add(expected_revision=index, label=f"Key {index}", api_key="sk-" + "synthetic-key-" * 3 + str(index))
    state = owner._load()
    state["policy"].update(enabled=True, api_fallback=True)
    state["native"] = {"active_slot": "openai-api"}
    native.atomic(owner.path, state)
    calls, outcomes = [], []
    class Upstream:
        def open(self, request, timeout):
            calls.append(request)
            assert request.full_url == "https://api.openai.com/v1/responses"
            assert timeout == 300
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
    server = module.RelayServer(owner, {"port": 0, "token": "example-loopback-token"}, opener=Upstream())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    body = {"model": "unchanged-selected-model", "input": "synthetic prompt", "stream": True, "tools": []}
    def post(token="example-loopback-token", origin=None):
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/responses", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
    yield owner, keys, calls, outcomes, post, body
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def rejected(status=429, code="insufficient_quota"):
    return urllib.error.HTTPError("https://api.openai.com/v1/responses", status, "private upstream text", {},
                                  io.BytesIO(json.dumps({"error": {"code": code, "message": "private upstream text"}}).encode()))


def test_real_http_rotates_only_rejected_quota_and_preserves_model_body_and_stream(relay):
    owner, keys, calls, outcomes, post, body = relay
    stream = b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
    outcomes.extend([rejected(), Response(stream)])
    status, result = post()
    assert status == 200 and result == stream and len(calls) == 2
    assert calls[0].data == calls[1].data == json.dumps(body).encode()
    assert calls[0].get_header("Authorization") != calls[1].get_header("Authorization")
    rows = keys.status()["keys"]
    assert rows[0]["runtime"]["state"] == "quota_exhausted"
    assert rows[1]["runtime"]["remaining_tokens"] == 1234
    assert keys.status()["balance_available"] is False
    assert "synthetic prompt" not in keys.path.read_text()


def test_network_unknown_effects_never_try_another_key(relay):
    _, keys, calls, outcomes, post, _ = relay
    outcomes.append(OSError("connection lost after dispatch"))
    status, _ = post()
    assert status == 502 and len(calls) == 1
    assert all(row["runtime"] is None for row in keys.status()["keys"])


def test_started_stream_never_replays_or_synthesizes_completion(relay):
    _, _, calls, outcomes, post, _ = relay
    class Interrupted(Response):
        def read1(self, _size):
            if self.tell():
                raise OSError("upstream disconnected")
            return self.read()
    partial = b'data: {"type":"response.created"}\n\n'
    outcomes.append(Interrupted(partial))
    status, result = post()
    assert status == 200 and result == partial and len(calls) == 1
    assert b"response.completed" not in result


def test_pool_exhaustion_is_bounded_and_does_not_probe_in_background(relay):
    _, _, calls, outcomes, post, _ = relay
    outcomes.extend([rejected(), rejected()])
    status, result = post()
    assert status == 429 and len(calls) == 2
    assert b"private upstream text" not in result
    status, _ = post()
    assert status == 429 and len(calls) == 2, "Cooling keys must not be retried on each request"


def test_default_off_origin_and_token_guards_precede_upstream_access(relay):
    owner, _, calls, _, post, _ = relay
    assert post(token="example-wrong-token")[0] == 401
    assert post(origin="https://outside.example")[0] == 401
    native = importlib.import_module("loopx_codex_provider_routing.native")
    state = owner._load()
    state["policy"]["api_fallback"] = False
    native.atomic(owner.path, state)
    assert post()[0] == 503
    assert calls == []


def test_enabled_api_pool_cannot_outrank_an_active_subscription(relay):
    owner, _, calls, _, post, _ = relay
    native = importlib.import_module("loopx_codex_provider_routing.native")
    state = owner._load()
    state["native"]["active_slot"] = "subscription-a"
    native.atomic(owner.path, state)
    assert post()[0] == 503 and calls == []
