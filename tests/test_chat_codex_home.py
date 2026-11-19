"""A managed upstream thread's home is identity, not restart-time routing."""
import http.client
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading

import pytest

from loopx.chat_agent import CodexChatAgentError
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore


class Adapter:
    upstream_thread_id = "fixture-thread"
    closed = False

    def healthcheck(self):
        return True

    def close_session(self):
        self.closed = True


def controller(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPX_CHAT_CODEX_HOME", str(tmp_path / "bound"))
    runtime = ChatRuntimeController(store=ChatSessionStore(tmp_path / "store"), codex_bin="fixture")
    monkeypatch.setattr(runtime, "capabilities", lambda: [{
        "agent_id": "codex", "available": True, "adapter_kind": "codex_app_server",
    }])
    monkeypatch.setattr(runtime, "_start_adapter", lambda **kwargs: Adapter())
    return runtime


def legacy(runtime):
    return runtime.store.create_session(
        goal_id="fixture", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="fixture-thread", upstream_mode="chat",
    )


def test_model_discovery_uses_the_same_home_as_runtime_not_ambient_provider(tmp_path, monkeypatch):
    runtime = controller(tmp_path, monkeypatch)
    bound = tmp_path / "bound"
    ambient = tmp_path / "other-provider"
    for root, model in [(bound, "gpt-fixture"), (ambient, "other-fixture")]:
        root.mkdir()
        (root / "models.json").write_text(json.dumps({"models": [{"slug": model}]}))
    # Python runs this synthetic `debug models` command on every supported OS.
    (tmp_path / "debug").write_text("import os\nfrom pathlib import Path\nprint((Path(os.environ['CODEX_HOME']) / 'models.json').read_text())\n")
    runtime.codex_bin = sys.executable
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(ambient))
    result = runtime.inspect_agent_model_options("codex", work_dir=tmp_path)
    assert result["model"]["options"] == [{"value": "gpt-fixture", "label": "gpt-fixture"}]
    assert os.environ["CODEX_HOME"] == str(ambient)
    runtime.close()


def test_new_session_records_home_and_process_environment_cannot_rebind(tmp_path, monkeypatch):
    runtime = controller(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_CHAT_CODEX_HOME", str(tmp_path / "other"))
    session, _ = runtime.open_session(goal_id="fixture", agent_id="codex",
        work_dir=tmp_path, objective="fixture", mode="new")
    assert session["codex_home"] == str((tmp_path / "bound").resolve())
    with pytest.raises(ValueError, match="cannot rebind"):
        runtime.store.update_session(session["session_id"], codex_home=str(tmp_path / "other"))
    runtime.close()


def test_restart_home_mismatch_refuses_before_resume_or_turn_write(tmp_path, monkeypatch):
    runtime = controller(tmp_path, monkeypatch)
    session = legacy(runtime)
    runtime.store.update_session(session["session_id"], codex_home=str(tmp_path / "other"))
    before = runtime.store.load_session(session["session_id"])
    monkeypatch.setattr(runtime, "_start_adapter", lambda **kwargs: pytest.fail("must not start upstream"))
    for operation in (
        lambda: runtime.resume_session(session_id=session["session_id"], work_dir=tmp_path, objective="fixture"),
        lambda: runtime.submit_turn(session_id=session["session_id"], client_turn_id="fixture-turn",
            message="fixture", work_dir=tmp_path, objective="fixture"),
    ):
        with pytest.raises(CodexChatAgentError) as error:
            operation()
        assert error.value.error_code == "codex_home_mismatch"
        assert runtime.store.load_session(session["session_id"]) == before
    runtime.close()


def test_legacy_home_bound_only_after_successful_resume(tmp_path, monkeypatch):
    runtime = controller(tmp_path, monkeypatch)
    session = legacy(runtime)
    sid = session["session_id"]

    def unavailable(**kwargs):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(runtime, "_start_adapter", unavailable)
    with pytest.raises(CodexChatAgentError):
        runtime.resume_session(session_id=sid, work_dir=tmp_path, objective="fixture")
    assert runtime.store.load_session(sid)["codex_home"] is None
    monkeypatch.setattr(runtime, "_start_adapter", lambda **kwargs: Adapter())
    restored = runtime.resume_session(session_id=sid, work_dir=tmp_path, objective="fixture")
    assert restored["codex_home"] == str((tmp_path / "bound").resolve())
    assert restored["upstream_thread_id"] == "fixture-thread"
    runtime.close()


def test_attached_session_uses_existing_host_not_managed_adapter(tmp_path, monkeypatch):
    runtime = controller(tmp_path, monkeypatch)
    session = runtime.store.create_session(
        goal_id="fixture", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="fixture-attached", session_mode="attached_host",
        host_surface="codex-app",
    )
    monkeypatch.setattr(runtime, "_start_adapter", lambda **kwargs: pytest.fail("attached must not spawn"))
    restored = runtime.resume_session(session_id=session["session_id"], work_dir=tmp_path, objective="fixture")
    assert restored["codex_home"] is None
    turn, created = runtime.submit_turn(session_id=session["session_id"], client_turn_id="attached-turn",
        message="fixture", work_dir=tmp_path, objective="fixture")
    assert created and turn["status"] == "queued"
    assert not runtime.adapters
    runtime.close()


def test_workspace_only_session_pins_private_home_and_resumes_exact_thread(tmp_path, monkeypatch):
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    runtime = controller(tmp_path, monkeypatch)
    runtime.project_contexts = ChatProjectContexts([tmp_path], filesystem_scope="workspace_only")
    ref = runtime.project_contexts.available()[0]["project_ref"]
    session, _ = runtime.open_session(goal_id=None, agent_id="codex", work_dir=tmp_path,
        objective="fixture", mode="new", project_ref=ref)
    assert session["codex_home"] == str(tmp_path / "bound" / "loopx-projects" / f"{ref}.local" / ".codex")
    runtime.adapters.clear()
    calls = []
    monkeypatch.setattr(runtime, "_start_adapter", lambda **kwargs: calls.append(kwargs) or Adapter())
    resumed = runtime.resume_session(session_id=session["session_id"], work_dir=tmp_path, objective="fixture")
    assert resumed["codex_home"] == session["codex_home"]
    assert calls[0]["resume_thread_id"] == "fixture-thread"
    assert resumed["upstream_thread_id"] == "fixture-thread"
    runtime.close()


@pytest.mark.parametrize("old_home", [None, "shared"])
def test_workspace_only_old_shared_context_refuses_rebind_before_any_spawn(tmp_path, monkeypatch, old_home):
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    runtime = controller(tmp_path, monkeypatch)
    context = ChatProjectContexts([tmp_path], filesystem_scope="workspace_only").available()[0]
    session = runtime.store.create_session(goal_id=None, agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="old-thread", upstream_mode="chat", channel_id=f"project.{context['project_ref']}",
        project_context=context, codex_home=str(runtime.codex_home) if old_home else None)
    before = runtime.store.load_session(session["session_id"])
    monkeypatch.setattr(runtime, "_start_adapter", lambda **kwargs: pytest.fail("must not resume or replace thread"))
    with pytest.raises(CodexChatAgentError) as error:
        runtime.resume_session(session_id=session["session_id"], work_dir=tmp_path, objective="fixture")
    assert error.value.error_code == "codex_home_mismatch"
    assert runtime.store.load_session(session["session_id"]) == before
    runtime.close()


def native_home(home, thread="fixture-thread", *, archived=False):
    """A real native index, with a synthetic thread and no account credentials."""
    (home / "sessions").mkdir(parents=True)
    rollout = home / "sessions" / f"{thread}.jsonl"
    rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": thread}}) + "\n")
    with sqlite3.connect(home / "state_1.sqlite") as db:
        db.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, archived INTEGER)")
        db.execute("INSERT INTO threads VALUES (?, ?, ?)", (thread, str(rollout), int(archived)))
    return home


def test_restart_recovers_each_original_discovered_profile_without_rebinding(tmp_path, monkeypatch):
    runtime = controller(tmp_path, monkeypatch)
    original = native_home(tmp_path / ".codex-original")
    other = native_home(tmp_path / ".codex-other")  # Same thread id is scoped by home.
    monkeypatch.setenv("LOOPX_CODEX_HOMES", os.pathsep.join(map(str, [original, other])))
    calls = []
    monkeypatch.setattr(runtime, "_start_adapter", lambda **kwargs: calls.append(kwargs) or Adapter())
    sessions = [runtime.store.create_session(goal_id="fixture", agent_id="codex",
        adapter_kind="codex_app_server", upstream_thread_id="fixture-thread", upstream_mode="chat",
        codex_home=str(home)) for home in [original, other]]
    for session in sessions:
        runtime.store.update_session(session["session_id"], coordination_context_version=2)
    try:
        for before in sessions:
            restored = runtime.resume_session(session_id=before["session_id"], work_dir=tmp_path, objective="fixture")
            assert calls[-1]["codex_home_override"] == Path(before["codex_home"])
            assert calls[-1]["resume_thread_id"] == "fixture-thread"
            assert restored["codex_home"] == before["codex_home"]
            assert restored["upstream_thread_id"] == before["upstream_thread_id"]
            assert not runtime.store.messages(before["session_id"])
        assert runtime.codex_home == (tmp_path / "bound").resolve()
    finally:
        runtime.close()


@pytest.mark.parametrize("failure", ["unlisted", "missing", "archived", "wrong_thread", "ambiguous", "explicit_profile", "workspace_only"])
def test_original_profile_recovery_preserves_rejection_before_any_effect(tmp_path, monkeypatch, failure):
    runtime = controller(tmp_path, monkeypatch)
    original = native_home(tmp_path / ".codex-original", archived=failure == "archived")
    selected = native_home(tmp_path / ".codex-selected")
    monkeypatch.setenv("LOOPX_CODEX_HOMES", str(selected) if failure == "unlisted"
        else os.pathsep.join(map(str, [original, selected])))
    if failure == "missing":
        (original / "sessions" / "fixture-thread.jsonl").unlink()
    if failure == "wrong_thread":
        with sqlite3.connect(original / "state_1.sqlite") as db:
            db.execute("UPDATE threads SET id='different-thread'")
    if failure == "ambiguous":
        duplicate = native_home(tmp_path / "other-account" / ".codex-original")
        monkeypatch.setenv("LOOPX_CODEX_HOMES", os.pathsep.join(map(str, [original, duplicate])))
    context = None
    if failure == "workspace_only":
        from loopx.capabilities.native_chat.project_context import ChatProjectContexts
        runtime.project_contexts = ChatProjectContexts([tmp_path], filesystem_scope="workspace_only")
        context = runtime.project_contexts.available()[0]
    session = runtime.store.create_session(goal_id=None if context else "fixture", agent_id="codex",
        adapter_kind="codex_app_server", upstream_thread_id="fixture-thread", upstream_mode="chat",
        codex_home=str(original), project_context=context,
        channel_id=f"project.{context['project_ref']}" if context else None,
        codex_chat_configuration={"schema_version": "codex_chat_machine_defaults_v0",
            "codex_profile": "codex-selected"} if failure == "explicit_profile" else None)
    before = runtime.store.load_session(session["session_id"])
    monkeypatch.setattr(runtime, "_start_adapter", lambda **kwargs: pytest.fail("must not start upstream"))
    try:
        for operation in [
            lambda: runtime.resume_session(session_id=session["session_id"], work_dir=tmp_path, objective="fixture"),
            lambda: runtime.submit_turn(session_id=session["session_id"], client_turn_id="not-admitted",
                message="Must remain unsent", work_dir=tmp_path, objective="fixture"),
        ]:
            with pytest.raises(CodexChatAgentError) as error:
                operation()
            assert error.value.error_code == "codex_home_mismatch"
            assert runtime.store.load_session(session["session_id"]) == before
            assert not runtime.store.messages(session["session_id"])
    finally:
        runtime.close()


def test_legacy_explicit_profile_is_bound_after_resume_to_the_selected_home(tmp_path, monkeypatch):
    runtime = controller(tmp_path, monkeypatch)
    selected = native_home(tmp_path / ".codex-selected")
    monkeypatch.setenv("LOOPX_CODEX_HOMES", str(selected))
    session = runtime.store.create_session(goal_id="fixture", agent_id="codex",
        adapter_kind="codex_app_server", upstream_thread_id="fixture-thread", upstream_mode="chat",
        codex_chat_configuration={"schema_version": "codex_chat_machine_defaults_v0",
            "codex_profile": "codex-selected"})
    try:
        restored = runtime.resume_session(session_id=session["session_id"], work_dir=tmp_path, objective="fixture")
        assert restored["codex_home"] == str(selected)
    finally:
        runtime.close()


@pytest.mark.parametrize("available", [True, False])
def test_http_resume_keeps_the_exact_original_session_and_actionable_failure(tmp_path, monkeypatch, available):
    import loopx.chat_server as chat_server
    runtime = controller(tmp_path, monkeypatch)
    original = native_home(tmp_path / ".codex-original")
    monkeypatch.setenv("LOOPX_CODEX_HOMES", str(original) if available else "")
    session = runtime.store.create_session(goal_id="fixture", agent_id="codex",
        adapter_kind="codex_app_server", upstream_thread_id="fixture-thread", upstream_mode="chat",
        codex_home=str(original))
    runtime.store.update_session(session["session_id"], coordination_context_version=2)
    before = runtime.store.load_session(session["session_id"])
    calls = []
    monkeypatch.setattr(runtime, "_start_adapter", lambda **kwargs: calls.append(kwargs) or Adapter())
    monkeypatch.setattr(chat_server.ChatRequestHandler, "_registry_and_goal", lambda *_: ({}, {}))
    monkeypatch.setattr(chat_server, "_goal_public_context", lambda *_: {
        "project": tmp_path, "objective": "Continue the original conversation.", "title": "Fixture"})
    server = chat_server.ChatHTTPServer(("127.0.0.1", 0), chat_server.ChatRequestHandler)
    server.chat_store, server.runtime_controller, server.verbose = runtime.store, runtime, False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        client.request("POST", f"/api/chat/sessions/{session['session_id']}/resume", body="{}",
            headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{server.server_port}"})
        response = client.getresponse()
        payload = json.loads(response.read())
        assert response.status == (200 if available else 424), payload
        if available:
            assert payload["session"]["session_id"] == session["session_id"]
            assert calls[0]["codex_home_override"] == original
            assert calls[0]["resume_thread_id"] == "fixture-thread"
        else:
            assert payload["error_code"] == "codex_home_mismatch"
            assert "original native profile" in payload["error"]
            assert runtime.store.load_session(session["session_id"]) == before
            assert calls == []
        assert not runtime.store.messages(session["session_id"])
    finally:
        client.close()
        server.shutdown()
        thread.join(5)
        server.server_close()
