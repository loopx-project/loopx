"""Deterministic local inference for real Codex checkpoint owner tests."""
import json
import os
from pathlib import Path
import re
import shlex
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def resolve_summary_row_references(presentation):
    """Recover same-view Todo rows for comparison with an independent capture."""
    if "context_reference_instruction" not in presentation:
        return presentation
    resolved = deepcopy(presentation)
    for rows in resolved["agent_todo_summary"].values():
        if not isinstance(rows, list):
            continue
        for i, row in enumerate(rows):
            if not isinstance(row, dict) or "context_ref" not in row:
                continue
            pointer = row["context_ref"]
            assert pointer.startswith("/agent_todo_summary/")
            target = presentation
            for token in pointer.split("/")[1:]:
                key = token.replace("~1", "/").replace("~0", "~")
                target = target[int(key)] if isinstance(target, list) else target[key]
            assert "context_ref" not in target and target["todo_id"] == row["todo_id"]
            rows[i] = deepcopy(target)
    resolved.pop("context_reference_instruction")
    return resolved


def selected_context_values(content):
    """Address selected facts independently of complete parent packaging."""
    values = {}

    def visit(pointer, value):
        values[pointer] = value
        if isinstance(value, (dict, list)):
            children = value.items() if isinstance(value, dict) else enumerate(value)
            for key, child in children:
                visit(pointer + "/" + str(key).replace("~", "~0").replace("/", "~1"), child)

    for item in json.loads(content)["selections"]:
        visit(item["pointer"], item["value"])
    return values


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
    assert set(delivered) == set(range(len(delivered))), (
        f"Non-contiguous native context: received {len(delivered)} bytes; "
        f"first offset {min(delivered, default=None)}, last offset {max(delivered, default=None)}")
    raw = bytes(delivered[i] for i in range(len(delivered)))
    assert hashlib.sha256(raw).hexdigest() == digest
    return raw.decode().rstrip("\n")


def last_context_page(request, *, allow_reference=False, allow_selection=False):
    """Observe the latest tool result; a saved-file reference is not a page."""
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
                if isinstance(value, dict) and (
                    value.get("schema_version") == "context_capture_page_v1"
                    or (allow_selection and value.get("schema_version") == "context_capture_selection_page_v1")
                    or (allow_reference and value.get("schema_version") == "context_capture_navigation_v1")
                    or (allow_reference and value.get("mode") in {"full", "delta"}
                        and all(key in value for key in ("path", "sha256", "command", "target", "recovery")))
                ):
                    return value
            except (ValueError, KeyError):
                return None
        if output is not None:
            # An earlier valid response cannot repair a missing or malformed
            # current result in the model's input.
            return None
    return None


def native_process_continuation(request, identity, *, code_mode=False):
    """Follow a yielded native command, using only its latest visible output."""
    for item in reversed(request["input"]):
        if (output := item.get("output")) is None:
            continue
        parts = [p.get("text", "") for p in output] if isinstance(output, list) else [output]
        for part in reversed(parts):
            if not isinstance(part, str):
                continue
            pending_session = None
            try:
                wrapper = json.loads(part)
                if (isinstance(wrapper, dict) and type(wrapper.get("session_id")) is int
                        and wrapper.get("exit_code") is None):
                    pending_session = wrapper["session_id"]
                if isinstance(wrapper, dict) and isinstance(wrapper.get("output"), str):
                    part = wrapper["output"]
            except ValueError:
                pass
            match = re.search(r"(?m)^Process running with session ID (\d+)\s*$", part)
            if match or pending_session is not None:
                options = {"session_id": int(match[1]) if match else pending_session, "chars": "", "yield_time_ms": 1000,
                           "max_output_tokens": 30000}
                base = {"id": f"fc_process_{identity}", "call_id": f"call_process_{identity}"}
                if code_mode:
                    from loopx.control_plane.turn_driver.decision_capture import CODE_MODE_EXEC_RESULT_FORWARDER
                    options["max_output_tokens"] = 22000
                    return {**base, "type": "custom_tool_call", "name": "exec",
                            "input": '// @exec: {"max_output_tokens":24000}\nconst result=await tools.write_stdin('
                                + json.dumps(options) + ');' + CODE_MODE_EXEC_RESULT_FORWARDER + ';'}
                return {**base, "type": "function_call", "name": "write_stdin", "arguments": json.dumps(options)}
        return None  # Never borrow a session from an older result.
    return None


def reference_metadata_oracle(view):
    """Inspect complete metadata as an oracle, never as a model read receipt."""
    if view.get("schema_version") == "context_capture_selection_page_v1":
        view = view["navigation"]
    if view.get("schema_version") == "context_capture_navigation_v1":
        from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference

        return read_context_read_reference(view["reference_file"])[1]["navigation"]
    return view


def read_cli_policy_payload(stdout, *, returncode):
    """Read the current policy capture for tests, without claiming model retention.

    Preserve failed commands and inline/off output. Successful native full/delta
    navigation uses its complete current recovery, never a cached or delta body.
    """
    from loopx.control_plane.turn_driver.decision_capture import (
        read_context_capture_file,
        read_context_read_reference,
    )

    payload = json.loads(stdout)
    if returncode == 0 and payload.get("schema_version") == "context_capture_selection_page_v1":
        payload = payload["navigation"]
    if returncode == 0 and payload.get("mode") in {"full", "delta"} and isinstance(payload.get("recovery"), dict):
        recovery = payload["recovery"]
        if "reference_file" in recovery:
            recovery = read_context_read_reference(recovery["reference_file"])[1]
        payload = read_context_capture_file(recovery["path"], recovery["sha256"], private=True)[2]
    if returncode == 0 and payload.get("schema_version") == "loopx_turn_envelope_v1":
        details = payload.get("context_details", {})
        command = details.get("command_template") if isinstance(details, dict) else None
        if isinstance(command, str):
            # A required view can reuse Todo rows while retaining the original
            # binding. Inspect its declared immutable source for policy tests;
            # the display itself is not the signed decision or model retention.
            argv = shlex.split(command)
            for flag in ("--capture-file", "--capture-sha256"):
                if argv.count(flag) != 1:
                    raise ValueError("policy view requires one original capture and digest")
            original = read_context_capture_file(
                argv[argv.index("--capture-file") + 1],
                argv[argv.index("--capture-sha256") + 1], private=True,
            )[2]
            if original.get("contract_binding") != payload.get("contract_binding"):
                raise ValueError("policy view original capture binding mismatch")
            payload = original
    return payload


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
        if continuation := native_process_continuation(request, f"{self.identity}_{self.calls + 1}"):
            self.calls += 1
            return continuation
        page = last_context_page(request, allow_reference=True, allow_selection=True)
        assert page and page["ok"], "Native reader output must reach the model intact"
        if page.get("schema_version") == "context_capture_selection_page_v1":
            # The initial read carries a selected view and a separate navigation
            # reference. Only complete delivery of the latter earns byte coverage.
            page = page["navigation"]
            assert page["ok"] and page["schema_version"] == "context_capture_navigation_v1"
        if page.get("schema_version") != "context_capture_page_v1":
            assert self.pending["descriptor"] is None and "reference" not in self.pending
            # This fixture voluntarily reads the complete saved input for its
            # byte oracle. A model-visible reference alone proves no coverage.
            self.pending["reference"] = page
            return self.tool(page["command"])
        self.pending["page_calls"] += 1
        if self.pending["descriptor"] is None:
            assert page["offset"] == 0
            if reference := self.pending.get("reference"):
                if "sha256" in reference:
                    assert page["sha256"] == reference["sha256"]
                # The generic saved-file reader returns bytes and cursors;
                # delivery identity stays in the original visible reference.
                self.pending["descriptor"] = {**reference, **page}
            else:
                assert page["mode"] in {"full", "delta"}
                self.pending["descriptor"] = page
        first = self.pending["descriptor"]
        assert page["sha256"] == first["sha256"]
        if not page["eof"]:
            return self.tool(page["next_command"])
        raw = delivered_context(request, first["sha256"])
        assert len(raw.encode()) in {page["total_bytes"], page["total_bytes"] - 1}
        if self.pending.get("reference", {}).get("schema_version") == "context_capture_navigation_v1":
            # Resolve oracle metadata only after complete model-visible byte
            # coverage; a reference never seeds the retained-input baseline.
            navigation = reference_metadata_oracle(self.pending["reference"])
            assert navigation["sha256"] == first["sha256"]
            first = {**first, **navigation}
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

def native_capture_wire(descriptor, *, recovery=None):
    """Independent saved-byte oracle; full delivery needs no duplicate wire file.

    Native requests must still consume every returned page. This fixture reads
    the exact capture separately to verify those actual model-visible bytes.
    """
    import hashlib
    from pathlib import Path
    from loopx.control_plane.quota.turn_envelope import prepare_context_delivery

    raw = Path(descriptor["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
    if descriptor["mode"] != "full":
        return json.loads(raw)
    target = descriptor["target"]
    wire = prepare_context_delivery(json.loads(raw),
        scope={key: target[key] for key in ("goal_id", "agent_id", "session_id", "context_epoch", "continuity_generation")
            if key in target}, capture_id=target["capture_id"], surface=target["surface"])
    assert wire["target"] == target
    wire["recovery"] = recovery if recovery is not None else descriptor["recovery"]
    return wire


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
