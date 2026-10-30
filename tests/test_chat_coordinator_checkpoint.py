import json
import subprocess
from threading import Event
from types import SimpleNamespace
from test_chat_loopx_mode import mode as mode, apply
from test_chat_project_coordination import project as project
import pytest
from loopx.hosts.chat_coordinator_checkpoint import CoordinatorCheckpoints
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from loopx.collaboration_mcp import _pinned_release_environment, _python_module_command
from loopx.todos import add_goal_todo
from loopx.control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from tests.control_plane import test_default_checkpoint_continuation as native
from tests.test_goal_mode_mcp_settlement import _control, GOAL_ID, AGENT_ID


def cli(registry, *args):
    result = subprocess.run(
        [
            *_python_module_command("loopx.cli"),
            "--registry",
            str(registry),
            "--format",
            "json",
            *args,
        ],
        env=_pinned_release_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    return result.returncode, json.loads(result.stdout)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_original_checkpoint_context_stale_rejection_and_single_settlement(
    provider, tmp_path, monkeypatch
):
    monkeypatch.setattr(native, "_run_cli", cli)
    registry, state, todo = native._fixture(provider, tmp_path, monkeypatch)
    control = _control(registry)
    control.run_cli = lambda args, **kw: json.dumps(cli(registry, *args)[1])
    json.loads(control.should_run())
    first = json.loads(
        control.complete_task(todo, AGENT_ID, "Candidate verified.", no_follow_up=True)
    )
    assert first["stage"] == "direction_pending"
    root = tmp_path / "runtime"
    index = root / "goals" / GOAL_ID / "runs/index.jsonl"
    checkpoint = CoordinatorCheckpoints(registry, root, GOAL_ID, AGENT_ID)
    observed = checkpoint.read(todo)
    assert observed["settlement_identity"] == first["settlement_identity"]
    extra = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Verify the newly arrived dependency.",
        claimed_by=AGENT_ID,
        task_class="advancement_task",
        continuation_policy="same_agent_non_delivery",
    )
    before = index.read_bytes()
    stale = checkpoint.resume(
        todo,
        {
            "read_context_id": observed["read_context_id"],
            "vision_unchanged_reason": "The original candidate remains, but this old basis is obsolete.",
        },
    )
    assert not stale["ok"] and index.read_bytes() == before
    with pytest.raises(ValueError):
        checkpoint.read(extra["todo_id"])
    with pytest.raises(ValueError):
        CoordinatorCheckpoints(registry, root, GOAL_ID, "different-agent").read(todo)
    fresh = checkpoint.read(todo)
    assert fresh["read_context_id"] != observed["read_context_id"] and extra[
        "todo_id"
    ] in json.dumps(fresh["basis"])
    decision = {
        "read_context_id": fresh["read_context_id"],
        "vision_unchanged_reason": "The committed candidate remains accepted; the new dependency is the next obligation.",
    }
    with pytest.raises(ValueError):
        checkpoint.resume(todo, {**decision, "agent_id": "different-agent"})
    assert index.read_bytes() == before
    settled = checkpoint.resume(todo, decision)
    assert (
        settled["ok"]
        and settled["settlement_complete"]
        and settled["settlement_identity"] == first["settlement_identity"]
    )
    assert not settled.get("goal_completion_certified")
    repeated = checkpoint.resume(todo, decision)
    assert (
        repeated["ok"]
        and repeated["settlement_identity"] == first["settlement_identity"]
    )
    events = [json.loads(line) for line in index.read_text().splitlines()]
    assert sum(e.get("classification") == "quota_slot_spent" for e in events) == 1
    rows = read_canonical_todos_if_promoted(runtime_root=root, goal_id=GOAL_ID)["todos"]
    original = next(t for t in rows if t["todo_id"] == todo)
    assert (
        original["status"] == "done" and original["evidence"] == "Candidate verified."
    )
    assert next(t for t in rows if t["todo_id"] == extra["todo_id"])["status"] == "open"


def test_chat_checkpoint_dispatch_keeps_pause_binding_and_member_namespace(
    mode, monkeypatch
):
    service, sid, repo, settings, _ = mode
    started = apply(mode, "start", settings=settings)
    service.controller.coordination_runtime_root = repo / "canonical-runtime"
    calls = []

    class Checkpoints:
        def __init__(self, registry, root, goal_id, agent_id):
            assert registry == service.controller.registry_path
            assert root == service.controller.coordination_runtime_root
            assert goal_id == "research" and agent_id == "coordinator"

        def read(self, todo):
            calls.append(("read", todo))
            return {"ok": True, "read_context_id": "fresh"}

        def resume(self, todo, decision):
            calls.append(("resume", todo, decision))
            return {"ok": True}

    monkeypatch.setattr(
        "loopx.hosts.chat_coordinator_checkpoint.CoordinatorCheckpoints", Checkpoints
    )
    adapter = SimpleNamespace(
        goal_driver=SimpleNamespace(stopped=Event()), session=SimpleNamespace()
    )
    lock = service.prepare(
        sid, started["turn_id"], adapter, lambda *a: {}, lambda *a: None
    )
    try:
        handler = adapter.session.read_tool_handler
        read = {
            "action": "read",
            "operation_id": "todo_original",
            "brief": {"purpose": "first_delivery"},
        }
        assert handler(TOOL_NAME, read)["ok"]
        resume = {
            "action": "resume",
            "operation_id": "todo_original",
            "brief": {
                "purpose": "first_delivery",
                "read_context_id": "fresh",
                "vision_unchanged_reason": "Current facts preserve the route.",
            },
        }
        assert handler(TOOL_NAME, resume)["ok"]
        assert calls[-1][2] == {
            "read_context_id": "fresh",
            "vision_unchanged_reason": "Current facts preserve the route.",
        }
        assert not handler(TOOL_NAME, {**read, "agent_id": "foreign"})["ok"]
        binding = service.store.load_session(sid)["loopx_mode"]["settings"]
        member, _, _, _ = service._execution(service.store.load_session(sid), binding)
        path = member.path("todo_collision")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
        assert not handler(TOOL_NAME, {**read, "operation_id": "todo_collision"})["ok"]
        assert len(calls) == 2
        monkeypatch.setattr(service.controller, "interrupt_turn", lambda **_: None)
        apply(mode, "pause")
        assert handler(TOOL_NAME, resume)["error"] == "conversation_execution_inactive"
        assert len(calls) == 2
    finally:
        lock.__exit__(None, None, None)


TOOL_NAME = "loopx_collaboration"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("runtime_route", ["registered", "chat_local"])
def test_checkpoint_snapshot_and_resume_follow_selected_chat_runtime(
    provider, runtime_route, tmp_path, monkeypatch
):
    registry, _, todo = native._fixture(provider, tmp_path, monkeypatch)
    control = _control(registry)
    assert json.loads(control.should_run()).get("delivery_result_context")
    result = json.loads(control.complete_task(todo, AGENT_ID, "Verified retained result.", no_follow_up=True))
    assert result["stage"] == "direction_pending", result
    root = tmp_path / "runtime"
    default_root = tmp_path / "unrelated-default-runtime"
    monkeypatch.setattr("loopx.paths.select_default_runtime_root", lambda: default_root)
    if runtime_route == "chat_local":
        registration = json.loads(registry.read_text())
        del registration["common_runtime_root"]
        registry.write_text(json.dumps(registration))
    store = ChatSessionStore(tmp_path / "chat-only" if runtime_route == "registered" else root)
    controller = ChatRuntimeController(store=store, codex_bin="unused-fixture-host", registry_path=registry)
    try:
        session = store.create_session(goal_id=GOAL_ID, agent_id="codex", channel_id=f"goal.{GOAL_ID}",
            upstream_thread_id="fixture-session", upstream_mode="chat", adapter_kind="codex_app_server")
        store.update_session(session["session_id"], native_goal={"status": "paused"},
            loopx_mode={"enabled": True, "paused": True, "settings": {"agent_id": AGENT_ID, "token_budget": 10000}})
        assert controller.coordination_runtime_root == root
        assert (store.root.parent != root) == (runtime_route == "registered")
        observed = controller.loopx_mode.snapshot(session["session_id"])["coordinator_checkpoint"]
        assert observed["stage"] == "direction_pending" and observed["result_committed"] is True
        assert observed["quota_spent"] is False and observed["settlement_identity"] == result["settlement_identity"]
        checkpoint = CoordinatorCheckpoints(registry, controller.coordination_runtime_root, GOAL_ID, AGENT_ID)
        context = checkpoint.read(todo)
        assert context["settlement_identity"] == result["settlement_identity"]
        decision = {
            "read_context_id": context["read_context_id"],
            "vision_unchanged_reason": "The retained result and current obligations preserve the route.",
        }
        settled = checkpoint.resume(todo, decision)
        assert settled["ok"] and settled["settlement_complete"], settled
        assert settled["settlement_identity"] == result["settlement_identity"]
        index = root / "goals" / GOAL_ID / "runs/index.jsonl"
        ledger = index.read_bytes()
        repeated = checkpoint.resume(todo, decision)
        assert repeated["ok"] and repeated["settlement_identity"] == result["settlement_identity"]
        assert index.read_bytes() == ledger
        events = [json.loads(line) for line in index.read_text().splitlines()]
        assert sum(event.get("classification") == "quota_slot_spent" for event in events) == 1
        original = next(
            row for row in read_canonical_todos_if_promoted(runtime_root=root, goal_id=GOAL_ID)["todos"]
            if row["todo_id"] == todo
        )
        assert original["status"] == "done" and original["evidence"] == "Verified retained result."
        assert not default_root.exists()
    finally:
        controller.close()
