"""Ordinary workspace Chat uses native Sessions without Goal/manager authority."""

import http.client
import json
from pathlib import Path
import runpy
import threading

import pytest

from loopx.capabilities.native_chat.project_context import ChatProjectContexts
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore


@pytest.fixture
def ordinary(tmp_path, monkeypatch):
    workspace = tmp_path / "notes"
    workspace.mkdir()
    capture = tmp_path / "requests.jsonl"
    source = runpy.run_path(str(Path(__file__).parents[1] / "examples/loopx-chat-runtime-smoke.py"))["FAKE_CODEX"]
    source = source.replace('    method = request.get("method")',
        f'    with open({str(capture)!r}, "a") as output:\n        output.write(json.dumps(request) + "\\n")\n'
        '    method = request.get("method")')
    source = source.replace('    elif method in {"thread/start", "thread/resume"}:',
        '    elif method == "config/read":\n'
        '        import pathlib\n'
        '        config = pathlib.Path(request["params"]["cwd"]) / "effective-config.json"\n'
        '        result = {"config": json.loads(config.read_text()) if config.exists() else {}}\n'
        '    elif method in {"thread/start", "thread/resume"}:')
    source = source.replace('        result = {"thread": {"id": "durable-thread"}}',
        '        result = {"thread": {"id": "durable-thread"},\n'
        '                  "model": request["params"].get("model", "old-model"),\n'
        '                  "reasoningEffort": request["params"].get("config", {}).get("model_reasoning_effort", "xhigh")}')
    fake = tmp_path / "codex"
    fake.write_text(source)
    fake.chmod(0o700)
    contexts = ChatProjectContexts([workspace], workspace_grant="workspace_read")
    store = ChatSessionStore(tmp_path / "runtime")
    runtime = ChatRuntimeController(store=store, codex_bin=str(fake), project_contexts=contexts,
                                    registry_path=tmp_path / "no-registry.json")
    import loopx.chat_manager_context as manager
    monkeypatch.setattr(manager, "collect_manager_turn_context", lambda *_, **__: pytest.fail("ordinary Chat must not read portfolio"))
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller = store, runtime
    server.selected_goal_id, server.verbose = None, False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request("GET" if body is None else "POST", path,
                None if body is None else json.dumps(body), headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    yield store, runtime, contexts, request, capture, fake, workspace
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_http_ordinary_project_continues_native_session_without_goal_or_portfolio(ordinary):
    store, runtime, contexts, request, capture, fake, workspace = ordinary
    status, projects = request("/api/chat/projects")
    assert status == 200 and len(projects["projects"]) == 1
    project = projects["projects"][0]
    assert project["title"] == "notes" and "workspace_path" not in project
    body = {"context_kind": "project", "project_ref": project["project_ref"]}
    status, opened = request("/api/chat/sessions", body)
    assert status == 201, opened
    sid = opened["session_id"]
    assert opened["goal_id"] is None and opened["session"]["manager_runtime"] is None
    assert store.load_session(sid)["project_context"]["workspace_path"] == str(workspace)
    for message, rid in [("Name the directory", "first"), ("Repeat the previous name", "follow-up")]:
        status, accepted = request(f"/api/chat/sessions/{sid}/turns", {"message": message, "client_turn_id": rid})
        assert status == 202, accepted
        assert runtime.wait_for_turn(session_id=sid, turn_id=accepted["turn_id"], timeout_sec=10)["status"] == "completed"
    assert request("/api/chat/sessions", body)[1]["session_id"] == sid
    original = store.load_session(sid)["upstream_thread_id"]
    runtime.close()
    restarted = ChatRuntimeController(store=ChatSessionStore(store.root.parent), codex_bin=str(fake), project_contexts=contexts)
    try:
        resumed, was_resumed = restarted.open_session(goal_id=None, agent_id="codex", work_dir=workspace,
            objective="do not substitute caller authority", mode="resume_latest", project_ref=project["project_ref"])
        assert was_resumed and resumed["session_id"] == sid and resumed["upstream_thread_id"] == original
    finally:
        restarted.close()
    requests = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len([row for row in requests if row.get("method") == "thread/start"]) == 1
    resumes = [row for row in requests if row.get("method") == "thread/resume"]
    assert len(resumes) == 1 and resumes[0]["params"]["threadId"] == original
    assert len([row for row in requests if row.get("method") == "turn/start"]) == 2
    assert not any(row.get("method", "").startswith("thread/goal") for row in requests)
    assert "Fresh Core evidence" not in capture.read_text() and "None:" not in capture.read_text()
    # The real host context gets answer guidance even on the structured-output
    # project lane, which does not use the older Chat review prompt.
    assert "A simple question needs no report template" in capture.read_text()
    assert "Preserve requested substantive detail" in capture.read_text()
    assert not (workspace / "ACTIVE_GOAL_STATE.md").exists()


def test_project_grants_cannot_be_forged_widened_or_reused_after_revocation(ordinary):
    store, runtime, contexts, request, _, _, workspace = ordinary
    ref = contexts.available()[0]["project_ref"]
    base = {"context_kind": "project", "project_ref": ref}
    for forged in [dict(base, goal_id="hidden"), dict(base, goal_id=[]), dict(base, project_ref="b" * 24),
                   dict(base, workspace_path=str(workspace.parent)), dict(base, grant="write")]:
        assert request("/api/chat/sessions", forged)[0] == 400
    sid = request("/api/chat/sessions", base)[1]["session_id"]
    with pytest.raises(ValueError, match="external audience"):
        runtime.enqueue_turn(session_id=sid, client_turn_id="foreign", message="private", work_dir=workspace,
                             objective="ignored", origin="lark")
    runtime.project_contexts = ChatProjectContexts([])
    assert request(f"/api/chat/sessions/{sid}/turns", {"message": "read", "client_turn_id": "revoked"})[0] == 400
    assert store.turn_for_client(sid, "revoked") is None
    with pytest.raises(ValueError):
        store.create_session(goal_id=None, agent_id="codex", adapter_kind="codex_app_server", upstream_thread_id="forged")


def test_project_configuration_change_reaches_resumed_native_turn(ordinary):
    store, runtime, contexts, request, capture, fake, workspace = ordinary
    ref = contexts.available()[0]["project_ref"]
    _, opened = request("/api/chat/sessions", {"context_kind": "project", "project_ref": ref})
    sid = opened["session_id"]
    original = store.load_session(sid)["upstream_thread_id"]
    assert runtime.adapters[sid].session.reasoning_effort == "xhigh"
    runtime.close()
    # Independent host observation changes between processes. The original
    # thread still reports xhigh unless the adapter applies that observation.
    (workspace / "effective-config.json").write_text(json.dumps({
        "model": "current-project-model", "model_reasoning_effort": "high",
        "sandbox_mode": "danger-full-access",
    }))
    restarted = ChatRuntimeController(store=ChatSessionStore(store.root.parent),
        codex_bin=str(fake), project_contexts=contexts)
    try:
        resumed, was_resumed = restarted.open_session(goal_id=None, agent_id="codex",
            work_dir=workspace, objective="continue", mode="resume_latest", project_ref=ref)
        assert was_resumed and resumed["session_id"] == sid
        assert resumed["upstream_thread_id"] == original
        turn, _ = restarted.submit_turn(session_id=sid, client_turn_id="after-config-change",
            message="Continue the original request.", work_dir=workspace, objective="continue")
        assert restarted.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=10)["status"] == "completed"
    finally:
        restarted.close()
    requests = [json.loads(line) for line in capture.read_text().splitlines()]
    assert sum(r.get("method") == "thread/start" for r in requests) == 1
    resume = next(r for r in requests if r.get("method") == "thread/resume")["params"]
    assert resume["threadId"] == original and resume["sandbox"] == "read-only"
    turn = next(r for r in requests if r.get("method") == "turn/start")["params"]
    assert turn["threadId"] == original
    assert turn["model"] == "current-project-model" and turn["effort"] == "high"
    assert len(store.list_sessions()) == 1 and store.load_session(sid)["goal_id"] is None


@pytest.mark.parametrize("messages", [
    ("Read the linked source and collect it using the project skill.", "Keep the previous source and correct its summary."),
    ("Organize this document in the existing notes.", "Preserve the original text and verify your edits."),
])
def test_writable_project_turns_use_project_skills_without_manager_work_selection(ordinary, messages):
    from loopx.chat_agent import CHAT_REVIEW_CLOSE_TAG, CHAT_REVIEW_OPEN_TAG

    store, runtime, contexts, request, capture, _, workspace = ordinary
    contexts.workspace_grant = "workspace_write"
    ref = contexts.available()[0]["project_ref"]
    status, opened = request("/api/chat/sessions", {"context_kind": "project", "project_ref": ref})
    assert status == 201
    sid = opened["session_id"]
    for index, message in enumerate(messages):
        status, accepted = request(f"/api/chat/sessions/{sid}/turns", {"message": message, "client_turn_id": f"project-{index}"})
        assert status == 202, accepted
        assert runtime.wait_for_turn(session_id=sid, turn_id=accepted["turn_id"], timeout_sec=10)["status"] == "completed"
    requests = [json.loads(line) for line in capture.read_text().splitlines()]
    starts = [row for row in requests if row.get("method") == "thread/start"]
    turns = [row for row in requests if row.get("method") == "turn/start"]
    assert len(starts) == 1 and starts[0]["params"]["sandbox"] == "workspace-write"
    assert len(turns) == 2 and len({row["params"]["threadId"] for row in turns}) == 1
    for turn, message in zip(turns, messages):
        prompt = turn["params"]["input"][0]["text"]
        assert "project's AGENTS.md and applicable skills" in prompt
        assert "Use applicable skills and permitted tools" in prompt
        assert "existing typed owners" in prompt and "public/private rules" in prompt
        assert "do not create a hidden Goal" in prompt
        assert "supplied Goal directory" not in prompt
        assert "Before preparing a new Goal" not in prompt
        assert "context_delegation catalog" not in prompt
        assert "A protected_action is only an untrusted proposal" in prompt
        assert prompt.endswith(f"Operator user message:\n{message}")
        envelope = json.loads(prompt.split(CHAT_REVIEW_OPEN_TAG, 1)[1].split(CHAT_REVIEW_CLOSE_TAG, 1)[0])
        assert envelope["schema_version"] == "loopx_chat_agent_response_v0"
        assert envelope["proposals"] == []
        assert all(envelope[field] is None for field in ("protected_action", "goal_draft", "context_handoff", "gate"))
    session = store.load_session(sid)
    assert session["goal_id"] is None and session.get("manager_runtime") is None
    assert session["project_context"]["grant"] == "workspace_write"
    assert len(store.list_sessions()) == 1
    assert not (workspace / "ACTIVE_GOAL_STATE.md").exists()
def test_read_only_project_rejects_executor_with_workspace_write_scope(ordinary, monkeypatch):
    store, runtime, contexts, request, _, _, _ = ordinary

    class FakeWriteScopedAdapter:
        upstream_thread_id = "write-scoped-project-agent"

        def close_session(self):
            pass

    monkeypatch.setattr(
        "loopx.chat_endpoint_catalog.shutil.which",
        lambda executable: executable if executable == "kiro-cli" else None,
    )
    kiro_capability = next(row for row in runtime.capabilities() if row["agent_id"] == "kiro-cli")
    assert kiro_capability["trust_scope"] == "workspace_write"
    runtime._start_adapter = lambda **_: FakeWriteScopedAdapter()
    project_ref = contexts.available()[0]["project_ref"]
    status, result = request("/api/chat/sessions", {
        "context_kind": "project", "project_ref": project_ref, "agent_id": "kiro-cli",
    })

    assert status == 400, result
    assert "read-only" in result.get("error", "").lower()
    assert store.latest_session(goal_id=None, agent_id="kiro-cli", channel_id=f"project.{project_ref}") is None

    project_context = contexts.available()[0]
    legacy = store.create_session(
        goal_id=None, agent_id="kiro-cli", adapter_kind="acp",
        upstream_thread_id="pre-guard-write-scoped-session",
        channel_id=f"project.{project_ref}", project_context=project_context,
    )
    status, result = request(
        f"/api/chat/sessions/{legacy['session_id']}/turns",
        {"message": "read this workspace", "client_turn_id": "must-not-be-accepted"},
    )
    assert status == 400, result
    assert store.turn_for_client(legacy["session_id"], "must-not-be-accepted") is None


def test_retargeted_symlink_does_not_rebind_a_project_grant(tmp_path):
    first, second = tmp_path / "first", tmp_path / "private"
    first.mkdir()
    second.mkdir()
    link = tmp_path / "workspace"
    link.symlink_to(first, target_is_directory=True)
    contexts = ChatProjectContexts([link])
    ref = contexts.available()[0]["project_ref"]
    link.unlink()
    link.symlink_to(second, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        contexts.resolve(ref)


def test_default_project_host_grant_is_write_and_read_only_launch_is_enforced(ordinary):
    from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings

    store, runtime, contexts, request, capture, _, workspace = ordinary
    # The fixture intentionally launched read-only. The ordinary product default
    # is write-capable, and both observed settings and actual host must agree.
    assert ChatProjectContexts([workspace]).available()[0]["grant"] == "workspace_write"
    contexts.workspace_grant = "workspace_write"
    status, projects = request("/api/chat/projects")
    assert status == 200 and projects["projects"][0]["grant"] == "workspace_write"
    _, opened = request("/api/chat/sessions", {"context_kind": "project", "project_ref": projects["projects"][0]["project_ref"]})
    assert opened["goal_id"] is None
    assert runtime.adapters[opened["session_id"]].session.sandbox == "workspace-write"
    row = json.loads(capture.read_text().splitlines()[-1])
    assert row["method"] == "thread/start" and row["params"]["sandbox"] == "workspace-write"
    proof = {"transport_ref": "notes-app", "provider_ref": "c" * 24, "operator_ref": "d" * 24, "verified": True}
    bindings = ChatConversationBindings(root=store.root, project_contexts=contexts, observe=lambda _: proof)
    contexts.conversation_bindings = bindings
    binding = bindings.configure(transport_ref="notes-app", project_ref=projects["projects"][0]["project_ref"], executor_endpoint_id="codex")
    assert binding["grant"] == "workspace_write"
    contexts.workspace_grant = "workspace_read"
    assert request(f"/api/chat/sessions/{opened['session_id']}/turns", {"message": "edit", "client_turn_id": "host-downgraded"})[0] == 400
    with pytest.raises(ValueError, match="write grant"):
        bindings.resolve(binding_id=binding["binding_id"], source_ref="a" * 24, sender_ref=proof["operator_ref"], private_human_message=True)
    with pytest.raises(ValueError, match="workspace grant"):
        bindings.configure(transport_ref="notes-app", project_ref=projects["projects"][0]["project_ref"], executor_endpoint_id="codex", project_grant="workspace_write")
    assert bindings.read()["bindings"][0] == binding
    runtime.close()


def test_local_scope_opens_new_session_after_host_grant_changes(ordinary):
    store, runtime, contexts, request, capture, _, _ = ordinary
    ref = contexts.available()[0]["project_ref"]
    body = {"context_kind": "project", "project_ref": ref}
    status, original = request("/api/chat/sessions", body)
    assert status == 201
    sessions = [original["session_id"]]
    try:
        for grant, sandbox in [("workspace_write", "workspace-write"), ("workspace_read", "read-only")]:
            contexts.workspace_grant = grant
            status, opened = request("/api/chat/sessions", body)
            assert status == 201, opened
            sid = opened["session_id"]
            assert sid not in sessions and opened["goal_id"] is None
            assert runtime.adapters[sid].session.sandbox == sandbox
            assert store.load_session(sessions[-1])["project_context"]["grant"] != grant
            status, denied = request(f"/api/chat/sessions/{sessions[-1]}/turns",
                {"message": "old permission", "client_turn_id": "stale-grant"})
            assert status == 400 and "grant changed" in denied["error"]
            assert store.turn_for_client(sessions[-1], "stale-grant") is None
            assert request("/api/chat/sessions", body)[1]["session_id"] == sid
            status, accepted = request(f"/api/chat/sessions/{sid}/turns",
                {"message": "Continue under current permission", "client_turn_id": f"current-{grant}"})
            assert status == 202, accepted
            assert runtime.wait_for_turn(session_id=sid, turn_id=accepted["turn_id"], timeout_sec=10)["status"] == "completed"
            sessions.append(sid)
        requests = [json.loads(line) for line in capture.read_text().splitlines()]
        assert len([row for row in requests if row.get("method") == "thread/start"]) == 3
        assert not any(row.get("method") == "thread/resume" for row in requests)
        assert len(store.list_sessions()) == 3
        assert store.messages(sessions[1]) and store.messages(sessions[2])
    finally:
        runtime.close()
