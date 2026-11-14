"""Opt-in qualification of the existing native host's context-delivery seam.

Only inference is static and local. No real Goal, benchmark task, credentials,
installed configuration or LoopX default changes. A successful primitive test
does not qualify a product adapter or model effectiveness.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

import pytest

from loopx.control_plane.turn_driver.codex_stdio import StdioNativeGoalTransport
from tests.control_plane.codex_checkpoint_fixture import local_inference_provider

pytestmark = pytest.mark.skipif(
    not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
    reason="Explicit native Codex binary required; inference uses loopback only",
)

# Deliberately literal before/after documents and a tiny change. This fixture
# tests native delivery, not a second implementation of the planned TS diff.
SOURCE = "\n".join(f"Requirement {i:04d}: preserve the exact original text." for i in range(900))
BASE = json.dumps({"requirements": SOURCE, "stop": "Stop before publishing."})
TARGET = json.dumps({"requirements": SOURCE, "stop": "Stop before publishing or deploying."})
DELTA = json.dumps({"changed": {"stop": "Stop before publishing or deploying."}})
HOOK = '''import json, pathlib, sys
root = pathlib.Path(__file__).parent
p = json.load(sys.stdin)
with (root / "observed.jsonl").open("a") as f:
    f.write(json.dumps({"event": p["hook_event_name"], "source": p.get("source")}) + "\\n")
event = p["hook_event_name"]
if p.get("source") == "compact":
    if (root / "fail-recovery").exists():
        sys.exit(1)
    body = (root / "target.json").read_text()
elif event == "SessionStart":
    body = (root / "base.json").read_text()
else:
    body = (root / "delta.json").read_text()
print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
    "additionalContext": "Synthetic required context data:\\n" + body}}))
'''


def _response(request, number, message):
    if number <= 2:
        return {"type": "function_call", "id": f"fc_{number}", "call_id": f"call_{number}",
                "name": "exec_command", "arguments": json.dumps({
                    "cmd": "printf synthetic-tool-result", "max_output_tokens": 100})}
    # The native host's third request is compaction. Remove the exact source
    # from the summary so recovery cannot pass by accidentally retaining it.
    message["content"][0]["text"] = "Synthetic summary without the original requirement body."
    return message


def _usage(request, number):
    count = 30000 if number == 2 else 1
    return {"input_tokens": count, "output_tokens": 1, "total_tokens": count + 1}


def _input_text(request):
    """Read actual model-input text; tools/instructions are not source delivery."""
    text = []
    for item in request["input"]:
        output = item.get("output") if item.get("type") == "function_call_output" else None
        if isinstance(output, str):
            text.append(output)
        for part in output if isinstance(output, list) else item.get("content", []):
            if isinstance(part, dict):
                text.append(part.get("text", ""))
    return "\n".join(text)


def _app_server(binary, env, workspace, home, trusted, additional_context=None, *, sandbox="read-only", resume=None,
                turn_timeout_sec=30, before_turn=None):
    with (home / "stderr.txt").open("w") as stderr, StdioNativeGoalTransport.spawn(
        [binary, "app-server", "--listen", "stdio://"], cwd=str(workspace), env=env,
        stderr=stderr, response_timeout_sec=20,
    ) as native:
        native.request("initialize", {"clientInfo": {"name": "context_boundary_fixture", "version": "0.1.0"},
                                      "capabilities": {"experimentalApi": True}})
        native.notify("initialized", {})
        params = {"cwd": str(workspace), "sandbox": sandbox, "approvalPolicy": "never"}
        if trusted:
            # Trust only the exact synthetic handlers created by this test.
            # Never read/alter the operator's hook trust or treat a global CLI
            # bypass flag as app-server trust propagation.
            listed = native.request("hooks/list", {"cwds": [str(workspace)]})
            handlers = [h for entry in listed["data"] for h in entry["hooks"]]
            fixture_hooks = json.loads((home / "hooks.json").read_text())["hooks"]
            expected_handlers = sum(len(group["hooks"]) for groups in fixture_hooks.values() for group in groups)
            assert expected_handlers > 0 and len(handlers) == expected_handlers
            assert all(h["sourcePath"] == str((home / "hooks.json").resolve()) for h in handlers)
            params["config"] = {"hooks.state": {
                h["key"]: {"trusted_hash": h["currentHash"]} for h in handlers}}
        if resume:
            params["threadId"] = resume
        source = native.request("thread/resume" if resume else "thread/start", params)["thread"]["id"]
        if before_turn is not None:
            before_turn(native)
        turn_params = {"threadId": source,
            "input": [{"type": "text", "text": "Synthetic context-delivery fixture."}]}
        if additional_context is not None:
            turn_params["additionalContext"] = additional_context
        turn = native.request("turn/start", turn_params)["turn"]["id"]
        deadline = time.monotonic() + turn_timeout_sec
        while time.monotonic() < deadline:
            event = native.next_event(timeout_sec=1)
            if event and event.get("method") == "turn/completed":
                assert event["params"]["turn"]["id"] == turn
                assert event["params"]["turn"]["status"] == "completed"
                return source
        raise AssertionError("Native context fixture did not finish")


def _native_fixture(tmp_path):
    binary = os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"]
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    env = {k: v for k, v in os.environ.items() if k in {
        "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"}}
    # Authority fixtures isolate temp files below tmp_path. Native release
    # helpers refuse a CODEX_HOME inside their temp root, which otherwise
    # breaks Linux sandbox dispatch. Keep both private, as sibling directories.
    temporary = tmp_path / "native-tmp"
    temporary.mkdir(mode=0o700)
    env.update({key: str(temporary) for key in ("TMPDIR", "TEMP", "TMP")})
    # Codex can also discover skills in the OS user's home. Isolate both
    # roots so a developer's personal skills cannot enter the measured input.
    user_home = tmp_path / "user-home"
    user_home.mkdir(mode=0o700)
    env["HOME"] = env["USERPROFILE"] = str(user_home)
    env["CODEX_HOME"] = str(home)
    return binary, env, workspace, home


def _configure_provider(home, url):
    (home / "config.toml").write_text(
        'model="fixture"\nmodel_provider="fixture"\napproval_policy="never"\nsandbox_mode="read-only"\n'
        'model_auto_compact_token_limit=20000\nmodel_context_window=100000\n'
        '[features]\ngoals=false\nplugins=false\nremote_plugin=false\nplugin_sharing=false\n'
        'responses_websockets_v2=false\n'
        f'[model_providers.fixture]\nname="Synthetic local"\nbase_url="{url}"\n'
        'wire_api="responses"\nrequires_openai_auth=false\n')


@pytest.mark.parametrize(("restore_missing", "complete_capture", "next_change"), [
    (mode, complete, None) for mode in ("original", "unobserved", "verified") for complete in (False, True)
] + [
    ("unobserved", True, change) for change in ("stop", "todo", "source", "content", "compact")
])
def test_native_code_mode_resume_requires_observed_coverage(
    tmp_path, monkeypatch, restore_missing, complete_capture, next_change,
):
    """Native Bash hooks and replayed Code Mode emissions must cover the same bytes."""
    from canonical_authority_fixture import isolate_sqlite_runtime

    from loopx.cli_commands.quota_capture import model_context_reference, native_context_capture_view
    from loopx.command_invocation import loopx_cli_route
    from loopx.control_plane.turn_driver.codex_context import prepare_codex_context
    from loopx.control_plane.turn_driver.decision_capture import CODE_MODE_EXEC_RESULT_FORWARDER
    from tests.control_plane.codex_checkpoint_fixture import last_context_page, selected_context_values
    from tests.control_plane.test_codex_context_lifecycle import project

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    binary, env, workspace, home = _native_fixture(tmp_path)
    env.update(NODE_OPTIONS="--experimental-sqlite", LOOPX_USAGE_PING="0")
    context = workspace / "context"
    context.mkdir(mode=0o700)
    program = os.environ.get("LOOPX_NATIVE_CONTEXT_CLI") or str(Path(sys.executable).with_name("loopx"))
    cli = [program, "--cli-bin", program, "--format", "json", "quota"]
    hook = shlex.join(cli + ["context-hook", "--context-delivery", "on", "--codex-app",
                             "--codex-context-dir", str(context)])
    handler = {"type": "command", "command": hook, "timeout": 10}
    (home / "hooks.json").write_text(json.dumps({"hooks": {
        **{name: [{"hooks": [handler]}] for name in ("SessionStart", "PreCompact", "PostCompact")},
        "PostToolUse": [{"matcher": "^Bash$", "hooks": [handler]}],
    }}))
    short_pointer, long_pointer = "/goal_boundary/stop_condition", "/required_work"
    payload = {"ok": True, "goal_id": "fixture",
        "goal_boundary": {"stop_condition": "Keep the original scope."},
        "required_work": "Verify the selected evidence before publishing. " * 220}
    independent_pointer = short_pointer
    narrative = "Preserve the complete original Goal context. " * 250
    if next_change:
        independent_pointer = "/interaction_contract/agent_channel/work_context/sources/0/content/text"
        payload = {"ok": True, "goal_id": "fixture", "selected_todo": {"todo_id": "todo_current"},
            "goal_boundary": {"stop_condition": "Keep the original scope."},
            "interaction_contract": {"schema_version": "loopx_interaction_contract_v0",
                "agent_channel": {"work_context": {"sources": [{"source": "goal_state", "content": {
                    "state_file": "fixture-goal.md", "full_source": True, "text": narrative,
                    "revision": "sha256:" + hashlib.sha256(narrative.encode()).hexdigest(),
                }}]}}, "user_channel": {}, "cli_channel": {}}}
    errors = []
    opening = ""
    saved_page = workspace / "first-page.json"
    initial_requests = None
    resume_state = first_read = None
    first_pages = []
    current_stop = "Stop before submitting the new result."

    def read_command(cmd):
        return "await tools.exec_command(" + json.dumps({"cmd": cmd, "max_output_tokens": 8000}) + ")"

    def resumed_response(request, number, message):
        nonlocal resume_state, first_read
        state = json.loads((context / "session.json").read_bytes())
        if number == 1:
            resume_state = state
            if next_change == "compact":
                assert independent_pointer in state.get("consumed_pointers", {})
                return "const result=" + read_command("printf synthetic-work") + ";" + CODE_MODE_EXEC_RESULT_FORWARDER
        if next_change == "compact" and number == 2:
            message["content"][0]["text"] = "Synthetic summary without the selected Goal narrative."
            return None
        if first_read:
            page = last_context_page(request, allow_selection=True)
            assert page and page["ok"]
            first_pages.append(page)
            if page["eof"]:
                return None
            command = page["next_command"]
        else:
            if next_change == "compact":
                assert "consumed_pointers" not in state
                assert narrative not in _input_text(request)
            directory = context / "next-capture"
            directory.mkdir(mode=0o700)
            with loopx_cli_route(program):
                prepared = prepare_codex_context(payload, directory=str(context), capture_directory=directory,
                    goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id="next")
                first_read = native_context_capture_view(prepared.delivery, directory,
                    context_directory=str(context), initial_read_plan=prepared.initial_read_plan)
            command = model_context_reference(first_read)["first_read_command"]
        return "const result=" + read_command(command) + ";" + CODE_MODE_EXEC_RESULT_FORWARDER

    def response(request, number, message):
        nonlocal opening
        try:
            if initial_requests is not None and next_change:
                code = resumed_response(request, number - initial_requests, message)
                if code is None:
                    return message
            elif number == 1:
                # The real SessionStart initialized the journal before inference.
                session = json.loads((context / "session.json").read_bytes())["session_id"]
                monkeypatch.setenv("CODEX_THREAD_ID", session)
                monkeypatch.chdir(workspace)
                _, wire = project(context, payload=payload)
                source = wire["recovery"]
                reader = cli + ["context-read", "--capture-file", source["path"], "--capture-sha256", source["sha256"]]
                fields = [] if complete_capture else ["--select", long_pointer]
                opening = shlex.join(reader + [*fields, "--page-bytes", "3000"])
                independent = shlex.join(reader + ["--select", independent_pointer])
                code = "{const result=" + read_command(independent) + ";" + CODE_MODE_EXEC_RESULT_FORWARDER + ";}"
                if restore_missing == "original":
                    code += "const result=" + read_command(opening) + ";" + CODE_MODE_EXEC_RESULT_FORWARDER + ";"
                else:
                    # The nested Bash hook observes the first page. Suppress
                    # its original parent emission and preserve an exact copy.
                    code += read_command("set -o pipefail\n" + opening + " | " + shlex.join(["tee", str(saved_page)])) + ";"
            elif number == 2:
                first = (last_context_page(request, allow_selection=True) if restore_missing == "original"
                         else json.loads(saved_page.read_bytes()))
                assert first and first["ok"] and not first["eof"]
                code = ("let cmd=" + json.dumps(first["next_command"]) + ";while(cmd){"
                        "const result=await tools.exec_command({cmd,max_output_tokens:8000});"
                        + CODE_MODE_EXEC_RESULT_FORWARDER +
                        ";const page=JSON.parse(result.output);if(!page.ok)throw Error('read failed');"
                        "cmd=page.next_command;}")
            elif number == 3 and restore_missing != "original":
                if restore_missing == "verified":
                    code = "const result=" + read_command(opening) + ";" + CODE_MODE_EXEC_RESULT_FORWARDER + ";"
                else:
                    # Code Mode can re-emit known bytes without a new Bash hook.
                    code = "text(" + json.dumps(saved_page.read_text()) + ");"
            else:
                return message
            return {"type": "custom_tool_call", "id": f"fixture-{number}", "call_id": f"call_{number}",
                    "name": "exec", "input": '// @exec: {"max_output_tokens":24000}\n' + code}
        except Exception as error:
            errors.append(error)
            return message

    def usage(_request, number):
        count = 30000 if next_change == "compact" and initial_requests == number - 1 else 1
        return {"input_tokens": count, "output_tokens": 1, "total_tokens": count + 1}

    with local_inference_provider(response, usage) as (url, calls, _):
        _configure_provider(home, url)
        config = home / "config.toml"
        config.write_text('tool_output_token_limit=24000\n'
            + config.read_text().replace("[features]\n", "[features]\ncode_mode=true\n"))
        session = _app_server(binary, env, workspace, home, True, sandbox="danger-full-access")
        assert not errors, errors
        before = json.loads((context / "session.json").read_bytes())
        pointers = before.get("consumed_pointers", {})
        assert set(pointers) == ({independent_pointer, ""} if complete_capture else {short_pointer, long_pointer})
        if complete_capture:
            assert before["candidate"]["capture_bytes_verified"]
        if next_change:
            payload["goal_boundary"]["stop_condition"] = current_stop
            source = payload["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]
            if next_change == "todo":
                payload["selected_todo"]["todo_id"] = "todo_other"
            elif next_change == "source":
                source["state_file"] = "replacement-goal.md"
            elif next_change == "content":
                source["text"] += " The evidence requirement changed."
                source["revision"] = "sha256:" + hashlib.sha256(source["text"].encode()).hexdigest()
        initial_requests = len(calls)
        assert _app_server(binary, env, workspace, home, True,
                           sandbox="danger-full-access", resume=session) == session
    assert not errors
    after = resume_state if next_change else json.loads((context / "session.json").read_bytes())
    if complete_capture:
        assert bool(after.get("candidate")) is (restore_missing != "unobserved")
        if restore_missing == "unobserved":
            assert after.get("consumed_pointers", {}) == {independent_pointer: pointers[independent_pointer]}
            assert after["context_epoch"] != before["context_epoch"]
            assert after["recovery"] == before["recovery"]
    else:
        assert after.get("consumed_pointers", {}) == (
            pointers if restore_missing != "unobserved" else {short_pointer: pointers[short_pointer]})

    if next_change is None:
        return
    assert first_read["mode"] == "full"
    assert first_read["target"]["context_epoch"] == after["context_epoch"]
    assert first_read["recovery"]["sha256"] != before["recovery"]["sha256"]
    assert first_pages and first_pages[-1]["eof"]
    first_page = first_pages[0]
    offset = 0
    for page in first_pages:
        assert page["offset"] == offset and page["total_bytes"] == first_page["total_bytes"]
        offset += len(page["content"].encode())
    assert offset == first_page["total_bytes"]
    values = selected_context_values("".join(page["content"] for page in first_pages))
    assert values[short_pointer] == current_stop
    reused = independent_pointer in first_page.get("previously_delivered_fields", [])
    assert reused == (next_change == "stop"), (next_change, first_page["total_bytes"])
    if reused:
        assert independent_pointer not in values
        assert first_page["total_bytes"] < len(narrative.encode())
    else:
        assert values[independent_pointer] == source["text"]


@pytest.mark.parametrize("early_yield", [False, True])
def test_native_reader_requires_complete_hook_output_even_when_model_saw_page(tmp_path, early_yield):
    """A completed process cannot turn an unobserved early page into a baseline.

    The gate, rather than elapsed time, keeps the yielding reader alive. Future
    hosts may deliver accumulated output to the hook; accept that only when its
    complete page is actually observed. Inference remains entirely local.
    """
    from loopx.control_plane.turn_driver import codex_context as context

    binary, env, workspace, home = _native_fixture(tmp_path)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    root, capture = workspace / "context", workspace / "capture"
    root.mkdir(mode=0o700)
    capture.mkdir(mode=0o700)
    gate, view_file, observations = workspace / "release", workspace / "view.json", home / "observed.jsonl"
    hook = home / "observe.py"
    hook.write_text(
        "import json, pathlib, sys\n"
        "from loopx.control_plane.turn_driver.codex_context import codex_context_hook\n"
        "event=json.load(sys.stdin)\n"
        f"result=codex_context_hook({str(root)!r},event)\n"
        f"with pathlib.Path({str(observations)!r}).open('a') as f: f.write(json.dumps(event)+'\\n')\n"
        "print(json.dumps(result))\n")
    handler = {"type": "command", "command": shlex.join([sys.executable, str(hook)]), "timeout": 10}
    (home / "hooks.json").write_text(json.dumps({"hooks": {
        name: [{"hooks": [handler]}] for name in ("SessionStart", "PostToolUse")}}))
    publish = workspace / "publish.py"
    publish.write_text(
        "import json, pathlib, time, subprocess, shlex\n"
        "from loopx.control_plane.turn_driver.codex_context import prepare_codex_context\n"
        "from loopx.cli_commands.quota_capture import native_context_capture_view\n"
        f"capture=pathlib.Path({str(capture)!r})\n"
        "body={'complete':True,'sources':[{'source':'selected_todo','content':{'todo':"
        "{'todo_id':'todo_fixture','text':'Preserve the original requirement. '*40,'status':'open'}}}]}\n"
        f"full=prepare_codex_context(body,directory={str(root)!r},capture_directory=capture,"
        "goal_id='fixture',agent_id='agent',surface='work_context',capture_id='fixture-capture').delivery\n"
        f"view=native_context_capture_view(full,capture,context_directory={str(root)!r})\n"
        # Read the returned reference through its real command. The test still
        # needs an actual page in model input to isolate complete-hook coverage.
        "view=json.loads(subprocess.run(shlex.split(view['command']),check=True,"
        "capture_output=True,text=True).stdout)\n"
        "text=json.dumps(view,separators=(',',':'))\n"
        f"pathlib.Path({str(view_file)!r}).write_text(text)\n"
        "print(text,flush=True)\n"
        + (f"while not pathlib.Path({str(gate)!r}).exists(): time.sleep(0.01)\n" if early_yield else ""))

    def response(request, number, message):
        if number == 1:
            name, args = "exec_command", {"cmd": shlex.join([sys.executable, str(publish)]),
                "yield_time_ms": 1000 if early_yield else 10000, "max_output_tokens": 5000}
        elif early_yield and number == 2:
            match = re.search(r"Process running with session ID (\d+)", _input_text(request))
            assert match, "The native reader must yield before the fixture releases its gate"
            gate.write_text("release")
            name, args = "write_stdin", {"session_id": int(match[1]), "chars": "",
                "yield_time_ms": 10000, "max_output_tokens": 5000}
        else:
            return message
        return {"type": "function_call", "id": f"fc_{number}", "call_id": f"call_{number}",
            "name": name, "arguments": json.dumps(args)}

    try:
        with local_inference_provider(response) as (url, _, requests):
            _configure_provider(home, url)
            result = subprocess.run([binary, "exec", "--dangerously-bypass-hook-trust", "--skip-git-repo-check",
                "--sandbox", "danger-full-access", "--json", "-C", str(workspace), "Synthetic reader fixture."],
                env=env, capture_output=True, text=True, timeout=40)
    finally:
        gate.write_text("release")
    assert result.returncode == 0, result.stderr
    view = json.loads(view_file.read_text())
    assert view["offset"] == 0 and view["eof"] is True
    assert view_file.read_text() in _input_text(requests[1])  # actual complete model-input page
    events = [json.loads(line) for line in observations.read_text().splitlines()]
    observed = [page for event in events if event["hook_event_name"] == "PostToolUse"
        for page in context._context_pages(event.get("tool_response"))]
    state = json.loads((root / "session.json").read_text())
    complete_hook = any(page == view for page in observed)
    assert bool(state.get("candidate")) is complete_hook
    assert bool(state.get("pending")) is not complete_hook
    if not early_yield:
        assert complete_hook


@pytest.mark.parametrize("source", ["inline", "file"])
def test_native_late_hook_discovery_does_not_deliver_session_context(tmp_path, source):
    """Discovery/trust after start is not SessionStart delivery to that thread.

    A real process resume is the positive control. Do not freeze whether future
    hosts hot-load tool hooks; only actual lifecycle delivery qualifies context.
    """
    binary, env, workspace, home = _native_fixture(tmp_path)
    marker = "Synthetic late-config requirement: stop before publishing."
    hook = home / "late-hook.py"
    hook.write_text(
        "import json,pathlib,sys\nevent=json.load(sys.stdin)\n"
        "with pathlib.Path(__file__).with_name('events.jsonl').open('a') as f:\n"
        "    f.write(json.dumps({'event':event['hook_event_name'],'source':event.get('source')})+'\\n')\n"
        "print(json.dumps({'hookSpecificOutput':{'hookEventName':'SessionStart',"
        f"'additionalContext':{marker!r}"
        "}}) if event['hook_event_name']=='SessionStart' else '{}')\n")

    def events():
        path = home / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def configure_after_start(native):
        command = shlex.join([sys.executable, str(hook)])
        if source == "inline":
            with (home / "config.toml").open("a") as config:
                for name in ("SessionStart", "PostToolUse"):
                    config.write(f"\n[[hooks.{name}]]\n[[hooks.{name}.hooks]]\n"
                                 f"type=\"command\"\ncommand={json.dumps(command)}\ntimeout=10\n")
        else:
            handler = {"type": "command", "command": command, "timeout": 10}
            (home / "hooks.json").write_text(json.dumps({"hooks": {
                name: [{"hooks": [handler]}] for name in ("SessionStart", "PostToolUse")}}))
        listed = native.request("hooks/list", {"cwds": [str(workspace)]})
        handlers = [handler for entry in listed["data"] for handler in entry["hooks"]]
        assert len(handlers) == 2
        expected_path = home / ("config.toml" if source == "inline" else "hooks.json")
        assert all(handler["sourcePath"] == str(expected_path.resolve()) for handler in handlers)
        with (home / "config.toml").open("a") as config:
            for handler in handlers:
                config.write(f"\n[hooks.state.{json.dumps(handler['key'])}]\n"
                             f"trusted_hash={json.dumps(handler['currentHash'])}\n")
        trusted = native.request("hooks/list", {"cwds": [str(workspace)]})
        assert all(handler["enabled"] and handler["trustStatus"] == "trusted"
                   for entry in trusted["data"] for handler in entry["hooks"])

    def response(request, number, message):
        if number % 2:
            return {"type": "function_call", "id": f"fc_{number}", "call_id": f"call_{number}",
                    "name": "exec_command", "arguments": json.dumps({
                        "cmd": "printf synthetic-late-hook-result", "max_output_tokens": 100})}
        return message

    with local_inference_provider(response) as (url, calls, requests):
        _configure_provider(home, url)
        config = home / "config.toml"
        config.write_text(config.read_text().replace("[features]\n", "[features]\ncode_mode_host=true\n"))
        thread = _app_server(binary, env, workspace, home, False, before_turn=configure_after_start)
        assert len(calls) == 2
        assert all(marker not in _input_text(request) for request in requests)
        assert not any(event["event"] == "SessionStart" for event in events())
        configured = config.read_bytes()
        assert _app_server(binary, env, workspace, home, False, resume=thread) == thread
    assert len(calls) == 4
    assert all(marker in _input_text(request) for request in requests[2:])
    assert {"event": "SessionStart", "source": "resume"} in events()
    assert any(event["event"] == "PostToolUse" for event in events())
    assert config.read_bytes() == configured


@pytest.mark.parametrize("host", ["exec", "app_server"])
@pytest.mark.parametrize("failed_primary", [False, True])
@pytest.mark.parametrize("surface", ["quota_packet", "turn_envelope"])
def test_native_shared_delta_with_small_compact_recovery_reference(tmp_path, host, failed_primary, surface):
    """Real native input: shared delta, small event hook, same-capture file read.

    The deterministic provider requests the necessary reads. This establishes
    transport ordering/recovery, not autonomous solver adoption or token savings.
    """
    from loopx.control_plane.quota.turn_envelope import (
        prepare_context_delivery, restore_context_delivery,
    )
    binary, env, workspace, home = _native_fixture(tmp_path)
    scope = dict(goal_id="fixture", agent_id="agent", session_id="synthetic-session", context_epoch="before-compact")
    source = {"complete": True, "sources": [{"source": "selected_todo", "content": {
        "todo": {"todo_id": "todo_fixture", "text": SOURCE, "note": "Stop before publishing.", "status": "open"}}}]}
    # A real reader must receive relocated structured provenance as well as
    # string edits. Current review conclusions remain literal on both surfaces.
    source["sources"].append({"source": "goal_acceptance", "content": {
        "goal_acceptance_contract": {"outcome_requirements": {"revision": 1, "outcomes": [{
            "id": "outcome", "requirements": [{"id": "requirement", "status": "unverified",
                "agent_review": {"conclusion": "uncertain", "evidence_refs": [
                    f"artifact:validation-{i:03d}" for i in range(60)]}}]}]}}}})
    source["sources"].append({"source": "turn_start_capability_hook", "kind": "agent_preferences",
        "hook_id": "semantic_preference.agent_context", "capability_id": "semantic-preference",
        "content": {"ok": True, "status": "read", "current": {"schema_version": "agent_preferences_v1",
            "revision": "preference-a", "authority": "advisory_only",
            "instructions": "Use the current scoped view; retired or expired preferences are not actionable.",
            "items": [{"key": "review", "state": "active", "statement": "Consult the designated reviewer.",
                "source_ref": "owner:review", "source_quote": "Keep this explicit review instruction. " * 20,
                "operation_id": "remember-review", "expires_at": "2030-01-02T00:00:00Z"}]}}})
    target_source = json.loads(json.dumps(source))
    target_source["sources"][0]["content"]["todo"]["note"] = "Stop before publishing or deploying."
    preference = target_source["sources"][-1]["content"]["current"]
    preference["revision"] = "preference-b"
    preference["items"][0].update(state="expired", statement=None)
    # Move every source; reversing three would leave the structured acceptance
    # evidence at its old index and stop exercising its relocated reference.
    target_source["sources"] = target_source["sources"][1:] + target_source["sources"][:1]
    def projection(context):
        if surface == "quota_packet":
            return {"goal_id": "fixture", "agent_id": "agent", "should_run": True,
                "interaction_contract": {"agent_channel": {"delivery_allowed": True, "work_context": context}}}
        return {"schema_version": "loopx_turn_envelope_v0", "action": {"delivery_allowed": True}, "work_context": context}
    base_source, target_source = projection(source), projection(target_source)
    base = prepare_context_delivery(base_source, scope=scope, capture_id="capture-a", surface=surface)
    target = prepare_context_delivery(target_source, scope=scope, capture_id="capture-b", surface=surface)
    delta = prepare_context_delivery(target_source, scope=scope, capture_id="capture-b", surface=surface,
        baseline=base, retained={"complete": True, "target": base["target"]})
    assert delta["mode"] == "delta"
    def structured_reference(node):
        if not isinstance(node, dict):
            return False
        if "from" in node:
            value = base["context"]
            for key in node["from"]:
                value = value[key]
            return isinstance(value, (dict, list))
        return any(structured_reference(child) for child in node.get("changed", node.get("items", {})).values())
    assert structured_reference(delta["changes"])
    assert restore_context_delivery(delta, baseline=base, current=target)["context"] == target_source
    files = {}
    for name, value in (("base", base), ("target", target), ("delta", delta)):
        files[name] = workspace / f"{name}.json"
        files[name].write_text(json.dumps(value, separators=(",", ":")))
    saved = {name: path.read_bytes() for name, path in files.items()}
    # The hook transports only a bounded invalidation/reference. A failed
    # primary handler is caught by the small recovery wrapper; no big payload
    # needs to survive the hook's default additionalContext limit.
    hook = home / "hook.py"
    hook.write_text(
        "import json, pathlib, sys\n"
        "event=json.load(sys.stdin)\n"
        "if event.get('source') == 'compact':\n"
        f"    pathlib.Path({str(home / 'epoch-invalidated')!r}).write_text('compact')\n"
        "    try:\n"
        + ("        raise RuntimeError('injected primary handler failure')\n" if failed_primary else "        pass\n")
        + "    except RuntimeError:\n        pass\n"
        f"    reference={{'mode':'full_recovery','capture_id':'capture-b','path':{str(files['target'])!r},"
        f"'context_digest':{target['target']['context_digest']!r}}}\n"
        "    print(json.dumps({'hookSpecificOutput':{'hookEventName':'SessionStart',"
        "'additionalContext':'Baseline invalidated. Read this same-capture full source before dependent work; '"
        "'do not rerun admission or ask the owner. '+json.dumps(reference)}}))\n"
    )
    handler = {"type": "command", "command": shlex.join([sys.executable, str(hook)]), "timeout": 10}
    (home / "hooks.json").write_text(json.dumps({"hooks": {
        name: [{"hooks": [handler]}] for name in ("SessionStart", "PostToolUse")}}))

    # Native tool feedback can impose its own cap even when max_output_tokens
    # is larger. Reconstruct from bounded pages, never a head/tail preview.
    page_size = 6000
    def page_count(name):
        return (len(saved[name].decode()) + page_size - 1) // page_size
    base_pages, target_pages = page_count("base"), page_count("target")
    compact_call = base_pages + 3
    final_call = compact_call + target_pages + 1

    def page_command(name, index):
        script = ("from pathlib import Path; import sys; "
            f"sys.stdout.write('SOURCE_PAGE {name} {index}\\n' + "
            f"Path({str(files[name])!r}).read_text()[{index * page_size}:{(index + 1) * page_size}])")
        return shlex.join([sys.executable, "-c", script])

    def response(request, number, message):
        if number <= base_pages:
            command = page_command("base", number - 1)
        elif number == base_pages + 1:
            command = page_command("delta", 0)
        elif number == base_pages + 2:
            command = "printf synthetic-work"
        elif compact_call < number < final_call:
            command = page_command("target", number - compact_call - 1)
        else:
            if number == compact_call:
                message["content"][0]["text"] = "Synthetic summary with the original source deliberately removed."
            return message
        return {"type": "function_call", "id": f"fc_{number}", "call_id": f"call_{number}",
            "name": "exec_command", "arguments": json.dumps({"cmd": command, "max_output_tokens": 2000})}

    def usage(request, number):
        count = 30000 if number == base_pages + 2 else 1
        return {"input_tokens": count, "output_tokens": 1, "total_tokens": count + 1}

    with local_inference_provider(response, usage) as (url, calls, requests):
        _configure_provider(home, url)
        if host == "exec":
            result = subprocess.run([binary, "exec", "--dangerously-bypass-hook-trust", "--skip-git-repo-check", "--json",
                "-C", str(workspace), "Synthetic context-delivery fixture."], env=env,
                capture_output=True, text=True, timeout=40)
            assert result.returncode == 0, result.stderr
        else:
            _app_server(binary, env, workspace, home, True)
    assert len(calls) == final_call

    def delivered_pages(request, name):
        pages = {}
        for item in request["input"]:
            output = item.get("output")
            if item.get("type") != "function_call_output" or not isinstance(output, str):
                continue
            marker = f"SOURCE_PAGE {name} "
            if marker in output:
                index, body = output.split(marker, 1)[1].split("\n", 1)
                assert "truncated output" not in output
                pages[int(index)] = body.rstrip("\n")
        return "".join(pages[i] for i in range(len(pages)))

    assert delivered_pages(requests[base_pages], "base") == saved["base"].decode()
    warm = requests[base_pages + 1]
    assert delivered_pages(warm, "base") == saved["base"].decode()
    assert delivered_pages(warm, "delta") == saved["delta"].decode()
    assert delivered_pages(warm, "target") == ""
    after_compact = requests[compact_call]
    assert (home / "epoch-invalidated").read_text() == "compact"
    assert 'Baseline invalidated.' in _input_text(after_compact)
    assert str(files['target']) in _input_text(after_compact)
    assert delivered_pages(after_compact, "base") == ""
    assert delivered_pages(requests[-1], "target") == saved["target"].decode()
    assert delivered_pages(requests[-1], "base") == ""
    assert all(path.read_bytes() == saved[name] for name, path in files.items())


@pytest.mark.parametrize("host", ["exec", "app_server"])
@pytest.mark.parametrize("case", ["intact", "spill", "untrusted", "failed_recovery"])
def test_native_hook_context_boundary(tmp_path, host, case):
    """Full -> warm change -> automatic compaction -> same-source full recovery."""
    binary, env, workspace, home = _native_fixture(tmp_path)
    for name, body in (("base", BASE), ("target", TARGET), ("delta", DELTA)):
        (home / f"{name}.json").write_text(body)
    hook = home / "hook.py"
    hook.write_text(HOOK)
    if case == "failed_recovery":
        (home / "fail-recovery").touch()
    handler = {"type": "command", "command": shlex.join([sys.executable, str(hook)]), "timeout": 10}
    if case != "spill":
        # The fixture has a strict known cap. Default spilling is deliberately
        # retained in its negative case, never mistaken for intact delivery.
        handler["additionalContextLimit"] = 0
    (home / "hooks.json").write_text(json.dumps({"hooks": {
        name: [{"hooks": [handler]}] for name in ("SessionStart", "PostToolUse")}}))
    with local_inference_provider(_response, _usage) as (url, calls, requests):
        _configure_provider(home, url)
        if host == "exec":
            flags = [] if case == "untrusted" else ["--dangerously-bypass-hook-trust"]
            result = subprocess.run([binary, "exec", *flags, "--skip-git-repo-check", "--json",
                "-C", str(workspace), "Synthetic context-delivery fixture."], env=env,
                capture_output=True, text=True, timeout=40)
            assert result.returncode == 0, result.stderr
        else:
            _app_server(binary, env, workspace, home, case != "untrusted")
    assert len(calls) == 4  # initial, warm continuation, compaction, recovery
    first, warm, _, recovered = map(_input_text, requests)
    if case == "untrusted":
        assert not (home / "observed.jsonl").exists()
        assert all(SOURCE not in text for text in (first, warm, recovered))
        return  # A configured handler is not evidence of delivery.
    events = [json.loads(line) for line in (home / "observed.jsonl").read_text().splitlines()]
    assert events[-1] == {"event": "SessionStart", "source": "compact"}
    if case == "spill":
        assert BASE not in first and TARGET not in recovered
        assert "Requirement 0000" in first and "Requirement 0899" in first
        return  # Head/tail presence must not confirm a warm baseline.
    assert first.count(BASE) == 1
    assert warm.count(BASE) == 1 and warm.count(DELTA) == 1
    assert TARGET not in warm  # warm primitive sent a change, not a hidden full resend
    if case == "failed_recovery" and TARGET not in recovered:
        pytest.xfail("Native host continues after failed compact hook without complete source; "
                     "hooks alone do not qualify automatic full fallback")
    assert recovered.count(TARGET) == 1
    assert BASE not in recovered
    # The two immutable source captures survive; no fresh admission/Goal write.
    assert (home / "base.json").read_text() == BASE
    assert (home / "target.json").read_text() == TARGET


@pytest.mark.parametrize("kind", ["untrusted", "application"])
@pytest.mark.parametrize("body", ["Complete synthetic source; stop before publishing.", BASE],
                         ids=["small", "large"])
def test_native_additional_context_boundary(tmp_path, kind, body):
    """A keyed context fragment must not be mistaken for retained source state.

    Test the native alternative to command hooks, with no recovery hook present.
    Keep failed completeness/retention qualification visible rather than asserting
    a particular preview size or freezing the current host's lossy behavior.
    """
    binary, env, workspace, home = _native_fixture(tmp_path)
    with local_inference_provider(_response, _usage) as (url, calls, requests):
        _configure_provider(home, url)
        _app_server(binary, env, workspace, home, False,
                    {"synthetic_source": {"kind": kind, "value": body}})
    assert len(calls) == 4
    first, warm, _, recovered = map(_input_text, requests)
    if body not in first:
        pytest.xfail("Native additionalContext is incomplete before first model input; "
                     "it cannot establish an intact baseline")
    assert first.count(body) == 1 and warm.count(body) == 1
    if body not in recovered:
        pytest.xfail("Native additionalContext is lost on automatic compaction; "
                     "a stable source key does not prove retained context")
    assert recovered.count(body) == 1
