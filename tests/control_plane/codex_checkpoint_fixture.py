"""Deterministic local inference for real Codex checkpoint owner tests."""
import json
import os
from pathlib import Path
import shlex
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def delivered_context(request, digest):
    """Independent byte-map oracle over actual native model input."""
    import hashlib

    delivered = {}
    for item in request["input"]:
        output = item.get("output")
        texts = ([part["text"] for part in output if part.get("type") == "input_text"]
                 if item.get("type") == "custom_tool_call_output" and isinstance(output, list)
                 else [output])
        for output in texts:
            if not isinstance(output, str):
                continue
            try:
                page = json.loads(output.split("\nOutput:\n", 1)[-1])
                if isinstance(page, dict) and page.get("exit_code") == 0 and isinstance(page.get("output"), str):
                    page = json.loads(page["output"])
            except (ValueError, TypeError):
                continue
            if not isinstance(page, dict) or page.get("schema_version") != "context_capture_page_v1" or page.get("sha256") != digest:
                continue
            assert page["ok"]
            data = page["content"].encode()
            assert len(data) == (page["total_bytes"] if page["eof"] else page["next_offset"]) - page["offset"]
            for offset, value in enumerate(data, page["offset"]):
                assert offset not in delivered or delivered[offset] == value
                delivered[offset] = value
    if not delivered:
        return ""
    assert set(delivered) == set(range(len(delivered)))
    raw = bytes(delivered[i] for i in range(len(delivered)))
    assert hashlib.sha256(raw).hexdigest() == digest
    return raw.decode().rstrip("\n")


def last_context_page(request):
    for item in reversed(request["input"]):
        output = item.get("output")
        parts = [p.get("text", "") for p in output] if isinstance(output, list) else [output]
        for part in reversed(parts):
            if not isinstance(part, str):
                continue
            try:
                value = json.loads(part.split("\nOutput:\n", 1)[-1])
                if isinstance(value, dict) and value.get("exit_code") == 0:
                    value = json.loads(value["output"])
                if isinstance(value, dict) and value.get("schema_version") == "context_capture_page_v1":
                    return value
            except (ValueError, KeyError):
                continue
    return None


class NativeContextReader:
    """Static inference which follows only model-visible native reader cursors.

    Expected files are inspected after complete input coverage, never to seed a
    baseline or choose a page. Native hooks remain the production continuity owner.
    """

    def __init__(self):
        self.pending = None
        self.deliveries = []
        self.retained = {}
        self.calls = 0
        self.identity = uuid.uuid4().hex

    def tool(self, command):
        self.calls += 1
        return {"type": "function_call", "id": f"fc_context_{self.identity}_{self.calls}",
                "call_id": f"call_context_{self.identity}_{self.calls}", "name": "exec_command",
                "arguments": json.dumps({"cmd": command, "yield_time_ms": 10000,
                                         "max_output_tokens": 30000})}

    def begin(self, command, label):
        assert self.pending is None
        self.pending = {"label": label, "descriptor": None, "page_calls": 0}
        return self.tool(command)

    def follow(self, request):
        if self.pending is None:
            return None
        page = last_context_page(request)
        assert page and page["ok"], "Native reader output must reach the model intact"
        self.pending["page_calls"] += 1
        if self.pending["descriptor"] is None:
            assert page["offset"] == 0 and page["mode"] in {"full", "delta"}
            self.pending["descriptor"] = page
        first = self.pending["descriptor"]
        assert page["sha256"] == first["sha256"]
        if not page["eof"]:
            return self.tool(page["next_command"])
        raw = delivered_context(request, first["sha256"])
        assert len(raw.encode()) in {page["total_bytes"], page["total_bytes"] - 1}
        # The original reader owns digest verification. This separate oracle
        # checks the actual complete model input against its declared source.
        assert raw == Path(first["path"]).read_text().rstrip("\n")
        packet = json.loads(raw)
        if first["mode"] == "delta":
            base = self.retained[packet["base"]["capture_id"]]
            assert delivered_context(request, base["sha256"]) == base["raw"]
        self.retained[first["target"]["capture_id"]] = {"raw": raw, "sha256": first["sha256"]}
        self.deliveries.append({"label": self.pending["label"], "mode": first["mode"],
            "body_bytes": page["total_bytes"], "page_calls": self.pending["page_calls"],
            "sha256": first["sha256"], "recovery": first["recovery"],
            "target": first["target"]})
        self.pending = None
        return None


def native_context_command(request, *, todo_id=None):
    """Use the actual worker prompt, including its original wake/transport pins."""
    latest = [item for item in request["input"] if item.get("role") == "user"][-1]
    texts = [part.get("text", "") for part in latest.get("content", []) if isinstance(part, dict)]
    text = "\n".join(texts)
    projects = [line for line in text.splitlines() if " quota context-project " in line]
    if projects:
        return projects[-1]
    command = [line for line in text.splitlines()
               if " quota should-run " in line and "--codex-context-dir" in line][-1]
    assignment = [line for line in text.splitlines() if line.startswith("LOOPX_TURN=")][-1]
    turn = shlex.split(assignment)[0].split("=", 1)[1]
    argv = [turn if arg == "${LOOPX_TURN:?}" else arg for arg in shlex.split(command)]
    if todo_id:
        argv += ["--todo-id", todo_id]
    return shlex.join(argv)

@contextmanager
def local_inference_provider(response_builder=None, usage_builder=None):
    """Static loopback Responses stream, retaining the actual native model input."""
    calls = []
    requests = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            calls.append(self.path)
            requests.append(request)
            message = {"type": "message", "id": "msg_" + uuid.uuid4().hex, "role": "assistant",
                       "content": [{"type": "output_text", "text": "Synthetic source result."}]}
            if response_builder is not None:
                message = response_builder(request, len(calls), message)
            result = {"id": "resp_" + uuid.uuid4().hex, "status": "completed", "output": [message],
                      "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
            if usage_builder is not None:
                result["usage"] = usage_builder(request, len(calls))
            events = [{"type": "response.created", "response": {**result, "status": "in_progress", "output": []}},
                      {"type": "response.output_item.done", "output_index": 0, "item": message},
                      {"type": "response.completed", "response": result}]
            raw = "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
                          for event in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextmanager
def real_native_source(root, binary, workspace, *, close_source=False, response_builder=None, sandbox="read-only"):
    """Real native writer/readback; a local static stream replaces only the model.

    No paid model, user credential or benchmark cell. Explicit responses may
    request synthetic tools inside the isolated fixture. Fixtures
    become completed Turns through the native protocol, not copied fake receipts.
    """
    with local_inference_provider(response_builder) as (base_url, calls, _):
        with _native_source(root, binary, workspace, base_url, calls, close_source, sandbox) as source:
            yield source


@contextmanager
def _native_source(root, binary, workspace, base_url, calls, close_source, sandbox):
    from loopx.control_plane.turn_driver.codex_stdio import (
        NativeGoalProtocolError, StdioNativeGoalTransport,
    )
    home = root / "home"
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        'model="fixture"\nmodel_reasoning_effort="high"\nmodel_provider="fixture"\napproval_policy="never"\n'
        f'sandbox_mode={json.dumps(sandbox)}\n'
        + ('[sandbox_workspace_write]\nnetwork_access=true\n' if sandbox == "workspace-write" else "") +
        '[features]\ngoals=false\nresponses_websockets_v2=false\nplugins=false\nremote_plugin=false\nplugin_sharing=false\n'
        '[model_providers.fixture]\nname="Synthetic local"\n'
        f'base_url="{base_url}"\nwire_api="responses"\n'
        'env_key="SYNTHETIC_PROVIDER_KEY"\n')
    # No ambient provider credentials or app integrations enter this fixture.
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL",
                      "SYSTEMROOT", "PYTHONPATH", "VIRTUAL_ENV"}}
    env.update(CODEX_HOME=str(home), CODEX_BIN=binary, LOOPX_PROJECT=str(workspace),
               SYNTHETIC_PROVIDER_KEY="synthetic-local-only", LOOPX_USAGE_PING="0")
    with (root / "stderr.txt").open("w") as stderr, StdioNativeGoalTransport.spawn(
        [binary, "-c", "features.goals=false", "app-server", "--listen", "stdio://"],
        cwd=str(workspace), env=env, stderr=stderr, response_timeout_sec=20,
    ) as native:
        native.request("initialize", {"clientInfo": {"name": "checkpoint_qualification", "version": "0.1.0"},
                                      "capabilities": {"experimentalApi": True}})
        native.notify("initialized", {})
        source = native.request("thread/start", {"cwd": str(workspace), "sandbox": sandbox,
            "approvalPolicy": "never", "persistExtendedHistory": True})["thread"]["id"]
        def complete_turn():
            expected_turn = native.request("turn/start", {"threadId": source,
                "input": [{"type": "text", "text": "Synthetic storage fixture. Return its source result."}]})["turn"]["id"]
            stop = time.monotonic() + 30
            while time.monotonic() < stop:
                try:
                    page = native.request("thread/turns/list", {"threadId": source,
                        "sortDirection": "asc", "itemsView": "full", "limit": 100})
                except NativeGoalProtocolError:
                    # A first live Turn may not have a persisted page yet.
                    time.sleep(.05)
                    continue
                if (page.get("data") and page["data"][-1].get("id") == expected_turn and
                        page["data"][-1].get("status") == "completed"):
                    return page["data"][-1]["id"]
                time.sleep(.05)
            raise AssertionError("Real native fixture did not complete its source Turn")
        complete_turn()
        if close_source:
            native.close()
        yield env, source, complete_turn, calls
