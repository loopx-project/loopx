"""Deterministic local inference for real Codex checkpoint owner tests."""
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

@contextmanager
def real_native_source(root, binary, workspace, *, close_source=False, response_builder=None, sandbox="read-only"):
    """Real native writer/readback; a local static stream replaces only the model.

    No paid model, user credential or benchmark cell. Explicit responses may
    request synthetic tools inside the isolated fixture. Fixtures
    become completed Turns through the native protocol, not copied fake receipts.
    """
    from loopx.control_plane.turn_driver.codex_stdio import (
        NativeGoalProtocolError, StdioNativeGoalTransport,
    )
    home = root / "home"
    home.mkdir(parents=True)
    calls = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            calls.append(self.path)
            message = {"type": "message", "id": "msg_" + uuid.uuid4().hex, "role": "assistant",
                       "content": [{"type": "output_text", "text": "Synthetic source result."}]}
            if response_builder is not None:
                message = response_builder(request, len(calls), message)
            result = {"id": "resp_" + uuid.uuid4().hex, "status": "completed", "output": [message],
                      "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
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
    (home / "config.toml").write_text(
        'model="fixture"\nmodel_reasoning_effort="high"\nmodel_provider="fixture"\napproval_policy="never"\n'
        f'sandbox_mode={json.dumps(sandbox)}\n'
        + ('[sandbox_workspace_write]\nnetwork_access=true\n' if sandbox == "workspace-write" else "") +
        '[features]\ngoals=false\nresponses_websockets_v2=false\nplugins=false\nremote_plugin=false\nplugin_sharing=false\n'
        '[model_providers.fixture]\nname="Synthetic local"\n'
        f'base_url="http://127.0.0.1:{server.server_port}/v1"\nwire_api="responses"\n'
        'env_key="SYNTHETIC_PROVIDER_KEY"\n')
    # No ambient provider credentials or app integrations enter this fixture.
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL",
                      "SYSTEMROOT", "PYTHONPATH", "VIRTUAL_ENV"}}
    env.update(CODEX_HOME=str(home), CODEX_BIN=binary, LOOPX_PROJECT=str(workspace),
               SYNTHETIC_PROVIDER_KEY="synthetic-local-only", LOOPX_USAGE_PING="0")
    try:
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
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
