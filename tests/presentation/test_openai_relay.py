"""Real loopback HTTP and private store, with synthetic upstream transport."""
import importlib
import http.client
import io
import json
import os
from pathlib import Path
import socketserver
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
    def post(token="example-loopback-token", origin=None, *, return_response=False):
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/responses", data=json.dumps(body).encode(), headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=10)
            if return_response:
                return response
            with response:
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
    with pytest.raises(http.client.IncompleteRead) as caught:
        post()
    assert caught.value.partial == partial and len(calls) == 1


def test_bad_upstream_headers_return_one_unknown_failure(relay):
    _, keys, calls, outcomes, post, _ = relay
    outcomes.append(http.client.LineTooLong("upstream header"))
    status, response = post()
    assert status == 502 and json.loads(response)["error"]["code"] == "upstream_unavailable"
    assert len(calls) == 1
    assert all(row["runtime"] is None for row in keys.status()["keys"])


@pytest.mark.parametrize("oversized", [False, True])
def test_unverified_rejection_body_cannot_trigger_another_key(relay, oversized):
    _, keys, calls, outcomes, post, _ = relay

    class TruncatedRejection(io.BytesIO):
        def read(self, *_args):
            raise http.client.IncompleteRead(b'{"error":')

    outcomes.extend([
        urllib.error.HTTPError(
            "https://api.openai.com/v1/responses", 429, "fixture", {},
            io.BytesIO(b"x" * 65537) if oversized else TruncatedRejection(),
        ),
        Response(b"must-not-be-used"),
    ])
    status, response = post()
    assert status == 502 and json.loads(response)["error"]["code"] == "upstream_unavailable"
    assert len(calls) == 1
    assert all(row["runtime"] is None for row in keys.status()["keys"])


def test_unavailable_key_observation_does_not_discard_admitted_response(relay, monkeypatch):
    _, keys, calls, outcomes, post, _ = relay

    def unavailable(*_args, **_kwargs):
        raise BlockingIOError("fixture observation lock")

    monkeypatch.setattr(type(keys), "record", unavailable)
    stream = b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
    outcomes.append(Response(stream))
    assert post() == (200, stream) and len(calls) == 1
    assert all(row["runtime"] is None for row in keys.status()["keys"])


@pytest.mark.parametrize(
    ("framing", "complete"), [
        ("length", False), ("chunked", False), ("length", True), ("chunked", True),
    ],
)
def test_loopback_truncation_framing_and_exact_tool_prefix(relay, framing, complete):
    _, _, calls, outcomes, post, body = relay
    prefix = b'data: {"type":"response.output_item.added","item":{"type":"function_call","call_id":"fixture"}}\n\n'
    terminal = b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
    upstream_calls = []
    upstream_finished = threading.Event()
    downstream_saw_prefix = threading.Event()

    class Upstream(socketserver.StreamRequestHandler):
        def handle(self):
            self.rfile.readline(4097)
            headers = http.client.parse_headers(self.rfile)
            upstream_calls.append(self.rfile.read(int(headers["Content-Length"])))
            self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n")
            if framing == "length":
                self.wfile.write(f"Content-Length: {len(prefix + terminal)}\r\n\r\n".encode())
                self.wfile.write(prefix)
            else:
                self.wfile.write(b"Transfer-Encoding: chunked\r\n\r\n")
                self.wfile.write(f"{len(prefix):x}\r\n".encode() + prefix + b"\r\n")
            self.wfile.flush()
            try:
                if complete:
                    # The relay must send the first tool item before the
                    # upstream sends a terminal item or closes the stream.
                    if not downstream_saw_prefix.wait(timeout=5):
                        return
                    if framing == "length":
                        self.wfile.write(terminal)
                    else:
                        self.wfile.write(f"{len(terminal):x}\r\n".encode() + terminal + b"\r\n0\r\n\r\n")
            finally:
                upstream_finished.set()

    with socketserver.TCPServer(("127.0.0.1", 0), Upstream) as server:
        serve = threading.Thread(target=server.serve_forever, daemon=True)
        serve.start()
        try:
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                connection.request("POST", "/v1/responses", json.dumps(body).encode())
                outcomes.append(connection.getresponse())
                if complete:
                    with post(return_response=True) as response:
                        assert response.status == 200
                        assert response.read(len(prefix)) == prefix
                        assert not upstream_finished.is_set()
                        downstream_saw_prefix.set()
                        assert response.read() == terminal
                else:
                    with pytest.raises(http.client.IncompleteRead) as caught:
                        post()
                    assert caught.value.partial == prefix
                assert len(calls) == len(upstream_calls) == 1
                assert calls[0].data == upstream_calls[0] == json.dumps(body).encode()
            finally:
                downstream_saw_prefix.set()
                connection.close()
        finally:
            server.shutdown()
            serve.join(timeout=5)


def test_valid_quota_json_in_short_content_length_cannot_rotate(relay):
    _, keys, calls, outcomes, post, _ = relay
    payload = json.dumps({"error": {"code": "insufficient_quota"}}).encode()
    with socketserver.TCPServer(("127.0.0.1", 0), RejectedQuota) as server:
        server.wire_response = (
            f"HTTP/1.1 429 Too Many Requests\r\nContent-Length: {len(payload) + 1}\r\n"
            "Content-Type: application/json\r\nConnection: close\r\n\r\n"
        ).encode() + payload
        serve = threading.Thread(target=server.serve_forever, daemon=True)
        serve.start()
        try:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/", timeout=5)
            except urllib.error.HTTPError as rejection:
                outcomes.extend([rejection, Response(b"must-not-be-used")])
            else:
                pytest.fail("fixture did not return HTTP 429")
            status, response = post()
            assert status == 502 and json.loads(response)["error"]["code"] == "upstream_unavailable"
            assert len(calls) == 1
            assert all(row["runtime"] is None for row in keys.status()["keys"])
        finally:
            server.shutdown()
            serve.join(timeout=5)


class RejectedQuota(socketserver.StreamRequestHandler):
    def handle(self):
        self.rfile.readline(4097)
        http.client.parse_headers(self.rfile)
        self.wfile.write(self.server.wire_response)


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
