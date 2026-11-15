"""Original Chat request reaches the existing repair checker and Task owner.

The production HTTP/Chat/Core/File/SQLite owners execute; model interpretation
and editing are scripted. The six preexisting repair checks remain independent.
"""
import hashlib
import http.client
import json
import runpy
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from test_new_goal_storage_defaults import environment as environment
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore
from loopx.capabilities.native_chat.project_context import ChatProjectContexts
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.goals.acceptance import inspect_goal_acceptance


@pytest.fixture
def cart_host(environment, monkeypatch):
    _, bootstrap, _, project, runtime = environment
    bootstrap("workspace")
    exercise = Path(__file__).parents[2] / "examples/bugfix-loop"
    for name in ("checks.py", "cart.py", "README.md"):
        shutil.copy2(exercise / name, project / name)
    capture, binary = runtime / "requests.jsonl", runtime / "synthetic-codex"
    from loopx.control_plane.turn_driver import host_candidate
    extract = host_candidate.extract_turn_authority
    def capture_authority(*args, **kwargs):
        authority = extract(*args, **kwargs)
        (runtime / "native-authority.json").write_text(json.dumps(authority))
        return authority
    monkeypatch.setattr(host_candidate, "extract_turn_authority", capture_authority)
    source = runpy.run_path(str(Path(__file__).parents[2] / "examples/loopx-chat-runtime-smoke.py"))["FAKE_CODEX"]
    source = source.replace('    method = request.get("method")',
        f'    with open({str(capture)!r}, "a") as stream:\n        stream.write(json.dumps(request) + "\\n")\n'
        '    method = request.get("method")')
    # The model proposes a same-workspace existing checker, with no absolute interpreter,
    # source hash, Task id or Goal/Agent routing supplied as execution authority.
    document = {"outcome_id": "goal-creation", "criteria": [{"id": "completion",
        "validation_argv": ["python3", "checks.py"], "validation_timeout_seconds": 5,
        "oracle": {"kind": "owner_pinned_oracle", "source_id": "bugfix-cart",
            "input_refs": [], "output_refs": ["cart.py"]}}]}
    source = source.replace('    elif method == "turn/start":',
        '    elif request_id == 900 and method is None:\n'
        '        evidence = json.loads(request["result"]["contentItems"][0]["text"])\n'
        '        response = "<loopx-review-json>" + json.dumps({"schema_version": "loopx_chat_agent_response_v0",\n'
        '            "message": "The existing six-check repair plan is ready for owner review.", "proposals": [], "gate": None}) + "</loopx-review-json>"\n'
        '        print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": response}}), flush=True)\n'
        '        print(json.dumps({"method": "turn/completed", "params": {"threadId": "durable-thread", "turn": {"id": active_turn}}}), flush=True)\n'
        '        turn_status[active_turn] = "completed"\n'
        '        continue\n'
        '    elif method == "turn/start":')
    source = source.replace("        response = 'Visible runtime response.",
        f'        if not __import__("pathlib").Path({str(project / "execute-native")!r}).exists():\n'
        '            print(json.dumps({"id": 900, "method": "item/tool/call", "params": {\n'
        '                "threadId": "durable-thread", "turnId": active_turn, "tool": "loopx_collaboration",\n'
        '                "arguments": ' + repr({"action": "prepare_check", "operation_id": "original-cart-check", "document": document}) + '}}), flush=True)\n'
        '            continue\n'
        f'        mode = __import__("pathlib").Path({str(project / "execute-native")!r}).read_text()\n'
        '        if mode == "valid":\n'
        f'            assert request["params"].get("sandboxPolicy") == {{"type": "workspaceWrite", "writableRoots": [{str(project.resolve())!r}], "networkAccess": False, "excludeTmpdirEnvVar": True, "excludeSlashTmp": True}}\n'
        f'            assert request["params"].get("runtimeWorkspaceRoots") == [{str(project.resolve())!r}]\n'
        f'            path = __import__("pathlib").Path({str(project / "cart.py")!r})\n'
        '            cart = path.read_text().replace("    if subtotal > 10_000:", "    if not prices or subtotal >= 10_000:")\n'
        '            path.write_text(cart)\n'
        "        response = 'Visible runtime response.")
    candidate = {"result_kind": "validated_completion", "classification": "implementation",
        "summary": "Repaired the empty-cart and free-shipping boundaries.",
        "next_action": "Read back the original cart artifact and independent checks.",
        "delivery_batch_scale": "implementation", "delivery_outcome": "outcome_progress",
        "vision_unchanged_reason": "The original six-check cart repair remains the current task.",
        "work_evidence": ["cart.py"], "validation_evidence": ["Unchanged checks.py verifies six original behaviors"]}
    source = source.replace('"message": "Runtime response."', '"message": ' + repr(json.dumps(candidate)))
    binary.write_text(source)
    compile(source, str(binary), "exec")
    binary.chmod(0o700)
    store = ChatSessionStore(runtime / "chat")
    registry = project / ".loopx/registry.json"
    controller = ChatRuntimeController(store=store, codex_bin=str(binary), registry_path=registry,
        project_contexts=ChatProjectContexts([project]))
    actions = ChatActionStore(runtime / "chat/actions")
    service = ChatActionService(store=actions, registry_path=registry,
        runtime_controller=controller, chat_store=store)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.registry_path, server.runtime_root = registry, runtime
    server.runtime_controller, server.action_store, server.action_service = controller, actions, service
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=30)
        try:
            connection.request("GET" if body is None else "POST", path,
                None if body is None else json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        yield actions, request, controller, capture, project, runtime
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        controller.close()


def create_cart(cart_host):
    _, request, controller, _, project, runtime = cart_host
    status, preview = request("/api/actions/preview", {"action_kind": "goal.create",
        "summary": "Repair the cart using its existing checks", "idempotency_key": "cart-repair-request",
        "context": {"kind": "goal_channel", "goal_id": "workspace"}, "normalized_parameters": {
            "goal_id": "cart-repair", "title": "Cart repair",
            "objective": "Repair cart.py in this exercise workspace according to README.md and unchanged checks.py.",
            "completion_criteria": "The six existing checks pass without changing checks.py.",
            "agent_id": "codex", "workspace_ref": "current", "heartbeat": {"enabled": False},
            "initial_todos": ["Repair cart.py to pass the six existing behavioral checks."]}})
    assert status == 201, preview
    proposal = preview["proposal"]
    status, applied = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert status == 202, applied
    ids = applied["proposal"]["receipt"]["resource_ids"]
    first = controller.wait_for_turn(session_id=ids["session_id"], turn_id=ids["turn_id"], timeout_sec=15)
    assert first["status"] == "completed", first
    messages = controller.store.messages(ids["session_id"])
    assert messages[0]["text"] == proposal["normalized_parameters"]["objective"]
    assert first["message"].startswith("开始推进 Goal"), "execution identity retains its original internal request"
    basis = inspect_goal_acceptance(registry_path=controller.registry_path, runtime_root=str(runtime),
        goal_id="cart-repair", agent_id="codex")
    assert not basis.get("completion_requirements")
    return proposal, ids, first


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["valid", "unconfirmed", "changed-source", "failed", "read-only"])
def test_original_http_task_reuses_requested_checker_and_session(environment, cart_host, provider, mode):
    configure, *_ = environment
    configure(provider)
    actions, request, controller, capture, project, runtime = cart_host
    _, ids, first = create_cart(cart_host)
    sid, todo = ids["session_id"], ids["todo_ids"][0]
    session, history = controller.store.load_session(sid), controller.store.messages(sid)
    checker_hash = hashlib.sha256((project / "checks.py").read_bytes()).hexdigest()
    proposals = actions.list(goal_id="cart-repair", context_kind="goal")
    checks = [row for row in proposals if row["action_kind"] == "goal.acceptance.check"]
    assert len(checks) == 1, {"first": first, "proposals": proposals}
    check = checks[0]
    criterion = check["normalized_parameters"]["document"]["criteria"][0]
    assert criterion["validation_argv"] == ["python3", "checks.py"]
    assert Path(shutil.which("python3")).resolve() == Path(sys.executable).resolve()
    assert criterion["validation_files"] == [{"path": "checks.py", "sha256": checker_hash}]
    assert check["status"] == "preview_ready" and not first.get("loopx_execution")
    calls_before = capture.read_bytes()
    status, unavailable = request(f"/api/chat/sessions/{sid}/native-turn?original_task=true&agent_id=codex")
    assert status == 200 and unavailable["task"]["state"] == "unbound", unavailable
    if mode != "unconfirmed":
        status, adopted = request(f"/api/actions/{check['proposal_id']}/apply", {})
        assert status == 200 and adopted["proposal"]["receipt"]["checks_passed"] is False, adopted
        assert capture.read_bytes() == calls_before, "check confirmation cannot launch a model/Task"
        status, observed = request(f"/api/chat/sessions/{sid}/native-turn?original_task=true&agent_id=codex")
        assert status == 200 and observed["task"]["state"] == "ready", observed
        assert observed["task"]["todo_id"] == todo and not observed["task"]["canonical_done"]
        assert "effects" not in observed["task"]
    if mode == "changed-source":
        (project / "checks.py").write_text("raise SystemExit(0)\n")
    if mode in {"valid", "failed"}:
        # An explicit, existing owner approval is separate from check adoption
        # and native Task selection. Default new Goals do not gain write rights.
        approved = subprocess.run([sys.executable, "-m", "loopx.entrypoint",
            "--registry", str(controller.registry_path), "--runtime-root", str(runtime), "--format", "json",
            "configure-goal", "--goal-id", "cart-repair", "--write-scope", "**",
            "--boundary-authority-scope", "**", "--boundary-authority-source", "owner-approved-exercise",
            "--boundary-authority-decision-id", "cart-workspace", "--execute"],
            capture_output=True, text=True, timeout=20)
        assert approved.returncode == 0, approved.stdout + approved.stderr
    (project / "execute-native").write_text(mode)
    status, accepted = request(f"/api/chat/sessions/{sid}/native-turn", {
        "agent_id": "codex", "operation_id": "original-cart-execution", "todo_id": todo})
    assert status == 202, accepted
    turn = controller.wait_for_turn(session_id=sid, turn_id=accepted["turn"]["turn_id"], timeout_sec=20)
    result = turn["response"]["native_loopx"]
    assert result["status"] == {"valid": "committed", "unconfirmed": "stopped",
        "changed-source": "stopped", "failed": "failed", "read-only": "failed"}[mode], result
    assert result["effects"]["quota_spent"] is (mode == "valid")
    assert result["effects"]["state_written"] is (mode == "valid")
    authority = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="cart-repair", include_leases=True)
    assert next(row for row in authority["todos"] if row["todo_id"] == todo)["status"] == ("done" if mode == "valid" else "open")
    calls = [json.loads(line) for line in capture.read_bytes()[len(calls_before):].splitlines()]
    native_calls = [row for row in calls if row.get("method") == "turn/start"]
    assert len(native_calls) == (1 if mode in {"valid", "failed", "read-only"} else 0)
    if native_calls:
        assert native_calls[0]["params"]["sandboxPolicy"]["type"] == ("readOnly" if mode == "read-only" else "workspaceWrite")
        if mode == "read-only":
            assert "runtimeWorkspaceRoots" not in native_calls[0]["params"]
        else:
            assert native_calls[0]["params"]["runtimeWorkspaceRoots"] == [str(project.resolve())]
    assert controller.adapters[sid].session.sandbox == "read-only"
    assert controller.adapters[sid].session.execution_mode is False
    assert not any(row.get("method") == "thread/start" for row in calls)
    assert controller.store.load_session(sid)["upstream_thread_id"] == session["upstream_thread_id"]
    assert controller.store.messages(sid)[:len(history)] == history
    after = capture.read_bytes()
    status, replay = request(f"/api/chat/sessions/{sid}/native-turn", {
        "agent_id": "codex", "operation_id": "original-cart-execution", "todo_id": todo})
    assert status == 202 and not replay["created"] and replay["turn"]["turn_id"] == turn["turn_id"]
    assert capture.read_bytes() == after
    if mode == "valid":
        (project / "execute-native").write_text("ordinary")
        followup, created = controller.submit_turn(session_id=sid, client_turn_id="ordinary-after-task",
            message="Explain the repaired behavior without changing the files.", work_dir=project,
            objective="Retain the original cart repair.")
        assert created
        answered = controller.wait_for_turn(session_id=sid, turn_id=followup["turn_id"], timeout_sec=10)
        assert answered["status"] == "completed" and "native_loopx" not in answered["response"]
        later = [json.loads(line) for line in capture.read_bytes()[len(after):].splitlines()]
        next_calls = [row for row in later if row.get("method") == "turn/start"]
        assert len(next_calls) == 1 and next_calls[0]["params"]["sandboxPolicy"] == {
            "type": "readOnly", "networkAccess": False}
        assert not any(row.get("method") == "thread/start" for row in later)
        check_run = subprocess.run([sys.executable, "checks.py"], cwd=project, capture_output=True, text=True, timeout=10)
        assert check_run.returncode == 0 and "Ran 6 tests" in check_run.stderr
        assert hashlib.sha256((project / "checks.py").read_bytes()).hexdigest() == checker_hash
