"""A managed upstream thread's home is identity, not restart-time routing."""
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
