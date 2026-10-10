"""Project Chat shares collaboration without becoming another Agent authority."""

import json
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from loopx.chat_coordination import PROJECT_CONTEXT_VERSION
from loopx.chat_agent import CodexChatAgentSession, CodexChatAgentError
from loopx.chat_manager_context import collect_manager_turn_context
from loopx.chat_runtime import ChatRuntimeController, CodexAppServerAdapter
from loopx.chat_store import ChatSessionStore
from loopx.capabilities.manager_context import pending
from loopx.capabilities.manager_context.inspection import ManagerInspection, CONTEXT_TOOL_NAME
from loopx.capabilities.manager_context.roundtrip import drain, reply_status


def test_failed_stop_keeps_active_claim_and_retry_preserves_unrelated_work(tmp_path):
    store = ChatSessionStore(tmp_path)
    session = store.create_session(goal_id="sample", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="thread", upstream_mode="chat")
    sid = session["session_id"]
    controller = ChatRuntimeController(store=store, codex_bin="missing")
    started, release = threading.Event(), threading.Event()

    class Adapter:
        upstream_thread_id = "thread"
        reject = True
        closed = False
        def healthcheck(self): return True
        def close_session(self):
            self.closed = True
            release.set()
        def start_turn(self, message, sink):
            sink("turn.started", {"upstream_turn_id": "current-native"})
            started.set()
            assert release.wait(5)
            return {"message": "discard this late answer"}
        def interrupt_turn(self, turn_id):
            assert turn_id == "current-native"
            if self.reject:
                raise RuntimeError("upstream rejected")
            release.set()

    adapter = Adapter()
    controller.adapters[sid] = adapter
    try:
        turn, _ = controller.submit_turn(session_id=sid, client_turn_id="stop-retry", message="run",
            work_dir=tmp_path, objective="sample")
        tid = turn["turn_id"]
        assert started.wait(2)
        with pytest.raises(CodexChatAgentError, match="停止尚未确认"):
            controller.interrupt_turn(session_id=sid, turn_id=tid)
        assert store.load_turn(sid, tid)["status"] == "interrupting"
        assert store.load_session(sid)["active_turn_id"] == tid
        assert not adapter.closed
        assert not any(e["kind"] == "turn.interrupted" for e in store.events_after(sid, tid, None))
        assert not any("已中断" in m["text"] for m in store.messages(sid))
        adapter.reject = False
        assert controller.interrupt_turn(session_id=sid, turn_id=tid)["status"] == "interrupted"
        assert store.load_session(sid)["active_turn_id"] is None
        assert not adapter.closed
        assert not any("discard this late answer" in m["text"] for m in store.messages(sid))
    finally:
        release.set()
        controller.close()


def test_stop_during_native_start_binds_current_turn_without_borrowing_previous_turn(tmp_path, monkeypatch):
    store = ChatSessionStore(tmp_path)
    session = store.create_session(goal_id="sample", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="thread", upstream_mode="chat")
    sid = session["session_id"]
    controller = ChatRuntimeController(store=store, codex_bin="missing")
    starting, bind, stopped = threading.Event(), threading.Event(), threading.Event()
    native = CodexChatAgentSession(process=SimpleNamespace(poll=lambda: None), messages=queue.Queue(),
        thread_id="thread", work_dir=tmp_path, response_timeout_sec=2, current_turn_id="previous-native")
    adapter = CodexAppServerAdapter(native)
    calls = []

    def send(message, *, on_event):
        starting.set()
        assert bind.wait(3)
        on_event("turn.started", {"upstream_turn_id": "current-native"})
        assert stopped.wait(3)
        raise CodexChatAgentError("interrupted", gate=None, error_code="interrupted")

    def interrupt(turn_id):
        calls.append(turn_id)
        stopped.set()

    monkeypatch.setattr(native, "send", send)
    monkeypatch.setattr(native, "interrupt", interrupt)
    monkeypatch.setattr(native, "close", lambda: stopped.set())
    controller.adapters[sid] = adapter
    try:
        turn, _ = controller.submit_turn(session_id=sid, client_turn_id="starting-stop", message="run",
            work_dir=tmp_path, objective="sample")
        tid = turn["turn_id"]
        assert starting.wait(2)
        result, errors = [], []
        def stop():
            try:
                result.append(controller.interrupt_turn(session_id=sid, turn_id=tid))
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=stop)
        worker.start()
        # Wait for the durable fence before releasing the delayed native identity.
        import time
        deadline = time.monotonic() + 2
        while store.load_turn(sid, tid)["status"] != "interrupting" and time.monotonic() < deadline:
            time.sleep(.01)
        assert store.load_turn(sid, tid)["status"] == "interrupting" and not calls
        bind.set()
        worker.join(4)
        assert not worker.is_alive() and not errors
        assert calls == ["current-native"] and result[0]["status"] == "interrupted"
        assert store.load_turn(sid, tid)["upstream_turn_id"] == "current-native"
    finally:
        bind.set()
        stopped.set()
        controller.close()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    goals = []
    for goal_id, agents, title in [("research", ["coordinator", "reviewer"], "Verify corrected source"),
                                   ("other", ["unrelated"], "OTHER_PROJECT_PRIVATE")]:
        repo = tmp_path / goal_id
        state = repo / "ACTIVE_GOAL_STATE.md"
        repo.mkdir()
        state.write_text('---\nstatus: active-read-only\nowner_mode: goal\nobjective: "' + title +
            '"\n---\n\n## Agent Todo\n\n- [ ] ' + title + '\n', encoding="utf-8")
        goals.append({"id": goal_id, "domain": title, "repo": str(repo),
            "state_file": state.name, "status": "active-read-only",
            "coordination": {"registered_agents": agents, "agent_model": "peer_v1"}})
    registry.write_text(json.dumps({"schema_version": "0.1", "common_runtime_root": str(root),
                                    "goals": goals}), encoding="utf-8")
    return root, registry, tmp_path / "research"


def test_project_evidence_scopes_before_reads_and_rejects_remote_and_other_goal(project, monkeypatch):
    root, registry, _ = project
    import loopx.chat_manager_context as context_module
    original = context_module.build_goal_portfolio
    scopes = []

    def collect(**kwargs):
        scopes.append(kwargs["goal_ids"])
        return original(**kwargs)

    monkeypatch.setattr(context_module, "build_goal_portfolio", collect)
    context = collect_manager_turn_context(registry,
        {"channel_id": "goal.research", "goal_id": "research"}, root, remote_evidence=True)
    assert scopes == [["research"]]
    assert context["scope"] == "owner_goal"
    assert "OTHER_PROJECT_PRIVATE" not in json.dumps(context)
    assert "model_defaults" not in context and "remote_evidence" not in context
    records = []
    reader = ManagerInspection(context=context, registry_path=registry, runtime_root=root,
        owner_scope=True, channel_id="goal.research", scope_valid=lambda: True, record=records.append)
    assert [s["source_id"] for s in reader.sources()] == ["local"]
    result = reader.read(CONTEXT_TOOL_NAME, {"view": "todos", "goal_id": "research"})
    assert result["ok"] and "Verify corrected source" in json.dumps(result)
    assert reader.read(CONTEXT_TOOL_NAME, {"view": "todos", "goal_id": "other"})["ok"] is False
    assert reader.read(CONTEXT_TOOL_NAME, {"view": "sources", "source_id": "ssh:other"}) == {
        "ok": False, "error": "source_outside_available_scope"}


def test_project_chat_nested_cli_handoff_returns_once_after_restart(project, monkeypatch):
    root, registry, repo = project
    before = registry.read_bytes(), (repo / "ACTIVE_GOAL_STATE.md").read_bytes()
    store = ChatSessionStore(root)
    controller = ChatRuntimeController(store=store, codex_bin="codex", registry_path=registry)
    answer = "**Source comparison**\n\nThe periods differ.\n\n- Keep the correction.\n- Do not infer growth."

    class ModelFixture:
        upstream_thread_id = "fixture-project-model"
        def healthcheck(self): return True
        def close_session(self): pass
        def start_turn(self, message, sink):
            assert "OTHER_PROJECT_PRIVATE" not in message
            assert '"scope": "owner_goal"' in message and '"agent_id": "coordinator"' in message
            sink("answer.delta", {"text": answer})
            return {"schema_version": "loopx_chat_agent_response_v0", "message": answer, "proposals": [], "gate": None,
                    "context_handoff": {"goal_id": "research", "agent_id": "coordinator"}}

    monkeypatch.setattr(controller, "capabilities", lambda: [
        {"agent_id": "codex", "available": True, "adapter_kind": "codex_app_server"}])
    monkeypatch.setattr(controller, "_start_adapter", lambda **_: ModelFixture())

    def cli(agent, action, *args):
        p = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--runtime-root", str(root), "manager-inbox", action, "--goal-id", "research",
            "--agent-id", agent, *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert p.returncode == 0, p.stdout + p.stderr
        return json.loads(p.stdout)

    try:
        session, _ = controller.open_session(goal_id="research", agent_id="codex", work_dir=repo,
            objective="Research delivery", mode="new", channel_id="goal.research")
        turn, _ = controller.submit_turn(session_id=session["session_id"], client_turn_id="correction",
            message="Ask coordinator to check the corrected source with reviewer.", work_dir=repo, objective="Research delivery")
        done = controller.wait_for_turn(session_id=session["session_id"], turn_id=turn["turn_id"], timeout_sec=20)
        assert done["status"] == "completed", done
        assert done["response"]["message"].startswith(answer + "\n\n")
        assert done["response"]["message"].count(answer) == 1
        streamed = [event["payload"]["text"] for event in store.events_after(session["session_id"], turn["turn_id"], None)
                    if event["kind"] == "answer.delta"]
        assert "".join(streamed) == answer
        assert ChatSessionStore(root).load_turn(session["session_id"], turn["turn_id"])["response"] == done["response"]
        receipt = done["response"]["context_handoff_receipt"]
        request_id = receipt["request_id"]
        assert request_id in json.dumps(cli("coordinator", "read"))
        cli("coordinator", "acknowledge", "--request-id", request_id, "--decision", "adopt", "--reason", "Check evidence with peer")
        brief = {"schema_version": "collaboration_brief_v0", "purpose": "Verify correction",
            "context": "A revised source replaces the previous amount.", "constraints": ["Retain original periods"],
            "inputs": [], "acceptance": ["Name the corrected value and comparison limits"],
            "return_requirement": "Return your independent finding to coordinator."}
        brief_file = repo / "brief.json"
        brief_file.write_text(json.dumps(brief), encoding="utf-8")
        child = cli("coordinator", "request", "--peer-agent-id", "reviewer", "--operation-id", "correction-review",
            "--parent-request-id", request_id, "--brief-file", str(brief_file))
        cid = child["request_id"]
        assert cid in json.dumps(cli("reviewer", "read"))
        cli("reviewer", "acknowledge", "--request-id", cid, "--decision", "adopt", "--reason", "Independent source check")
        cli("reviewer", "report", "--request-id", cid, "--reply-text", "Corrected free cash flow is 25. Periods are not comparable.")
        assert "Corrected free cash flow is 25" in json.dumps(cli("coordinator", "read"))
        cli("coordinator", "acknowledge-return", "--request-id", cid)
        cli("coordinator", "report", "--request-id", request_id, "--reply-text",
            "Adopted the peer finding: corrected cash flow is 25; no growth inference across different periods.")
        assert not pending(root, "other", "unrelated")["items"]
        # Real persisted routes and a new store; no second model/user turn or Lark send.
        def forbidden(*_): pytest.fail("local return must not use an external transport")
        for _ in range(2):
            drain(root, registry, ChatSessionStore(root), forbidden)
        returned = [m for m in store.messages(session["session_id"]) if m.get("origin") == "manager_followup"]
        assert len(returned) == 1 and returned[0]["turn_id"] == turn["turn_id"]
        assert "Adopted the peer finding" in returned[0]["text"]
        assert reply_status(root, receipt)[0]["status"] == "delivered"
        assert before == (registry.read_bytes(), (repo / "ACTIVE_GOAL_STATE.md").read_bytes())
    finally:
        controller.close()


def test_project_codex_tool_and_legacy_session_refresh_do_not_inherit_steward_permissions(project, monkeypatch):
    root, registry, repo = project
    store = ChatSessionStore(root)
    controller = ChatRuntimeController(store=store, codex_bin="codex", registry_path=registry)
    starts = []
    class ModelFixture:
        upstream_thread_id = "new-project-thread"
        def healthcheck(self): return True
        def close_session(self): pass
    def start(**kwargs):
        starts.append(kwargs)
        return ModelFixture()
    monkeypatch.setattr(CodexAppServerAdapter, "start", start)
    old = store.create_session(goal_id="research", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="legacy-project-thread", upstream_mode="chat", channel_id="goal.research")
    store.append_message(old["session_id"], role="user", text="Keep the earlier correction.")
    try:
        controller._ensure_adapter(old, work_dir=repo, objective="Research delivery")
        call = starts[0]
        assert call["resume_thread_id"] is None
        assert call["runtime_profile"] == "restricted" and call["sandbox"] is None
        assert [t["name"] for t in call["dynamic_tools"]] == [CONTEXT_TOOL_NAME]
        assert "Keep the earlier correction" in call["objective"]
        current = store.load_session(old["session_id"])
        assert current["coordination_context_version"] == PROJECT_CONTEXT_VERSION
        controller._ensure_adapter(current, work_dir=repo, objective="Research delivery")
        assert len(starts) == 1
    finally:
        controller.close()


def test_external_input_cannot_reuse_the_project_turns_private_read_handler(project, monkeypatch):
    root, registry, repo = project
    controller = ChatRuntimeController(store=ChatSessionStore(root), codex_bin="codex", registry_path=registry)
    sent = []
    upstream = CodexChatAgentSession(thread_id="fixture-thread", messages=queue.Queue(),
        work_dir=repo, process=SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(upstream, "close", lambda: None)
    writes = []
    monkeypatch.setattr(upstream, "_write", writes.append)
    def send(message, **_):
        sent.append(message)
        upstream.current_turn_id = f"turn-{len(sent)}"
        assert upstream._check_server_gate({
            "id": len(sent), "method": "item/tool/call", "params": {
                "threadId": upstream.thread_id, "turnId": upstream.current_turn_id,
                "tool": CONTEXT_TOOL_NAME,
                "arguments": {"view": "todos", "goal_id": "research"},
            },
        })
        response = writes[-1]["result"]
        page = json.loads(response["contentItems"][0]["text"])
        if len(sent) != 2:
            assert response["success"]
            assert page["ok"] and "Verify corrected source" in json.dumps(page)
        else:
            assert not response["success"]
            assert page == {"ok": False, "error": "conversation_scope_unavailable"}
            assert "Fresh Core evidence" not in message
            with pytest.raises(CodexChatAgentError):
                upstream._check_server_gate({"id": 100, "method": "item/commandExecution/requestApproval"})
        return {"schema_version": "loopx_chat_agent_response_v0", "message": "Scoped answer", "proposals": [], "gate": None}
    upstream.send = send
    monkeypatch.setattr(controller, "capabilities", lambda: [
        {"agent_id": "codex", "available": True, "adapter_kind": "codex_app_server"}])
    monkeypatch.setattr(controller, "_start_adapter", lambda **_: CodexAppServerAdapter(upstream))
    try:
        session, _ = controller.open_session(goal_id="research", agent_id="codex", work_dir=repo,
            objective="Research", mode="new", channel_id="goal.research")
        for index, origin in enumerate(("web", "lark", "web")):
            turn, _ = controller.enqueue_turn(session_id=session["session_id"], client_turn_id=f"{index}-{origin}",
                message="Inspect current work", work_dir=repo, objective="Research", origin=origin)
            result = controller.wait_for_turn(session_id=session["session_id"], turn_id=turn["turn_id"], timeout_sec=20)
            assert result["status"] == "completed", result
        assert len(sent) == 3
    finally:
        controller.close()
