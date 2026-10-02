"""A native Chat observation consumes only its originating conversation wake."""

import json
from types import SimpleNamespace
from threading import Event
import pytest
from test_local_delegation import service as service, brief, _read
from loopx.collaboration_mcp import Delegations
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore


def chat_team(service, monkeypatch):
    root, runner = service
    monkeypatch.setattr(Delegations, "_spawn", lambda *_: None)
    registry = json.loads(runner.registry.read_text())
    goal = next(g for g in registry["goals"] if g["id"] == runner.goal_id)
    goal["spawn_policy"] = {
        "mode": "multi_subagent",
        "allowed": True,
        "max_children": 2,
        "execution_config": ".loopx/config/delegations.json",
    }
    runner.registry.write_text(json.dumps(registry))
    config = root / "project/.loopx/config/delegations.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(runner.config.read_bytes())
    store = ChatSessionStore(runner.root)
    controller = ChatRuntimeController(
        store=store, codex_bin="fixture-no-provider", registry_path=runner.registry
    )
    calls = []

    def submit(**kwargs):
        calls.append(kwargs)
        turn, created = store.create_turn(
            kwargs["session_id"],
            client_turn_id=kwargs["client_turn_id"],
            message=kwargs["message"],
        )
        store.update_turn(
            kwargs["session_id"],
            turn["turn_id"],
            loopx_execution=True,
            loopx_request=kwargs["loopx_request"],
        )
        return turn, created

    monkeypatch.setattr(controller, "submit_turn", submit)

    def session(label):
        sid = store.create_session(
            goal_id=runner.goal_id,
            agent_id="codex",
            channel_id="goal." + runner.goal_id,
            upstream_thread_id=label,
            upstream_mode="chat",
            adapter_kind="codex_app_server",
        )["session_id"]
        controller.loopx_mode.apply(
            sid,
            {
                "operation": "start",
                "operation_id": label,
                "settings": {"agent_id": "lead", "token_budget": 10000},
            },
            work_dir=root / "project",
            objective="Synthetic independently accepted work",
        )
        return sid, store.load_session(sid)["active_turn_id"]

    def tool(sid, tid):
        adapter = SimpleNamespace(
            session=SimpleNamespace(),
            goal_driver=SimpleNamespace(turn_start_handler=None, stopped=Event()),
        )
        lock = controller.loopx_mode.prepare(
            sid, tid, adapter, lambda *_: {}, lambda *_: None
        )
        return lock, adapter.session.read_tool_handler

    return root, runner, store, controller, calls, session, tool


def test_native_conversation_b_read_cannot_retire_a_origin_intent(service, monkeypatch):
    root, runner, store, controller, calls, session, tool = chat_team(
        service, monkeypatch
    )
    a, ta = session("origin-a")
    held, dispatch = tool(a, ta)
    try:
        dispatch(
            "loopx_collaboration",
            {
                "action": "start",
                "binding_id": "analysis",
                "operation_id": "a-result",
                "brief": brief(),
            },
        )
    finally:
        held.__exit__(None, None, None)
    runner.execute("a-result")
    assert runner.wait("a-result")["status"] == "accepted"
    path = runner.path("a-result")
    before = _read(path)["wake"]
    assert before["state"] == "pending" and before["conversation"]["session_id"] == a
    store.update_turn(a, ta, status="completed")
    store.update_session(
        a, active_turn_id=None, native_goal={"status": "paused", "tokensUsed": 0}
    )
    b, tb = session("reader-b")
    held, dispatch = tool(b, tb)
    try:
        result = dispatch(
            "loopx_collaboration", {"action": "read", "operation_id": "a-result"}
        )
    finally:
        held.__exit__(None, None, None)
    assert result["status"] == "accepted"
    after = _read(path)["wake"]
    calls.clear()
    from loopx.chat_loopx_mode import pump_delegation_wakes

    receipts = pump_delegation_wakes(
        controller,
        goal_context=lambda _: {
            "project": root / "project",
            "objective": "Continue accepted work",
        },
    )
    assert after["state"] == "pending", (
        "a same-Goal conversation retired another origin conversation wake"
    )
    assert len(receipts) == 1
    assert len(calls) == 1 and calls[0]["session_id"] == a


def test_native_conversationless_read_keeps_plain_result_shape(service, monkeypatch):
    root, runner, store, controller, calls, session, tool = chat_team(
        service, monkeypatch
    )
    runner.start("analysis", "ordinary-result", brief())
    runner.execute("ordinary-result")
    plain = runner.wait("ordinary-result")
    assert (
        plain["status"] == "accepted"
        and "wake" not in plain
        and "wake" not in _read(runner.path("ordinary-result"))
    )
    b, tb = session("reader-b")
    held, dispatch = tool(b, tb)
    try:
        result = dispatch(
            "loopx_collaboration", {"action": "read", "operation_id": "ordinary-result"}
        )
    finally:
        held.__exit__(None, None, None)
    assert "wake" not in result, (
        "conversationless read gained wake:null in shared readback"
    )


def test_origin_conversation_read_retires_only_its_own_pending_intent(
    service, monkeypatch
):
    root, runner, store, controller, calls, session, tool = chat_team(
        service, monkeypatch
    )
    a, ta = session("origin-a")
    held, dispatch = tool(a, ta)
    try:
        dispatch(
            "loopx_collaboration",
            {
                "action": "start",
                "binding_id": "analysis",
                "operation_id": "a-result",
                "brief": brief(),
            },
        )
    finally:
        held.__exit__(None, None, None)
    runner.execute("a-result")
    assert runner.wait("a-result")["status"] == "accepted"
    assert _read(runner.path("a-result"))["wake"]["state"] == "pending"
    held, dispatch = tool(a, ta)
    try:
        result = dispatch(
            "loopx_collaboration", {"action": "read", "operation_id": "a-result"}
        )
    finally:
        held.__exit__(None, None, None)
    assert result["wake"]["state"] == "observed_in_turn"
    before = runner.path("a-result").read_bytes()
    store.update_turn(a, ta, status="completed")
    store.update_session(
        a, active_turn_id=None, native_goal={"status": "paused", "tokensUsed": 0}
    )
    calls.clear()
    from loopx.chat_loopx_mode import pump_delegation_wakes

    assert (
        pump_delegation_wakes(
            controller,
            goal_context=lambda _: {
                "project": root / "project",
                "objective": "Continue",
            },
        )
        == []
    )
    assert calls == [] and runner.path("a-result").read_bytes() == before


@pytest.mark.parametrize("observer", ["origin", "other"])
def test_native_adoption_preserves_conversation_scope_for_source_and_consumer(
    service, monkeypatch, observer
):
    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"].append(
        {
            **config["bindings"][0],
            "id": "synthesis",
            "agent_id": "reviewer",
            "todo_id": "todo_reviewer-corrected",
            "workspace": str(root / "reviewer/corrected"),
        }
    )
    runner.config.write_text(json.dumps(config))
    root, runner, store, controller, calls, session, tool = chat_team(
        service, monkeypatch
    )
    a, ta = session("origin-a")
    held, dispatch = tool(a, ta)
    try:
        dispatch(
            "loopx_collaboration",
            {
                "action": "start",
                "binding_id": "analysis",
                "operation_id": "a-source",
                "brief": brief(),
            },
        )
    finally:
        held.__exit__(None, None, None)
    runner.execute("a-source")
    source = runner.wait("a-source")
    assert source["status"] == "accepted"
    artifact = source["artifacts"][0]
    (root / "reviewer/corrected/accepted-input.json").write_text(artifact["text"])
    dependency = {
        "ref": "accepted-input.json",
        "description": "Accepted analysis for synthesis",
        "sha256": artifact["sha256"],
        "delegation": {
            "operation_id": "a-source",
            "ref": artifact["ref"],
            "relation": "uses",
        },
    }
    held, dispatch = tool(a, ta)
    try:
        dispatch(
            "loopx_collaboration",
            {
                "action": "start",
                "binding_id": "synthesis",
                "operation_id": "a-consumer",
                "brief": {**brief(), "inputs": [dependency]},
            },
        )
    finally:
        held.__exit__(None, None, None)
    runner.execute("a-consumer")
    assert runner.wait("a-consumer")["status"] == "accepted"
    assert all(
        _read(runner.path(op))["wake"]["state"] == "pending"
        for op in ("a-source", "a-consumer")
    )
    sid, tid = a, ta
    if observer == "other":
        store.update_turn(a, ta, status="completed")
        store.update_session(
            a, active_turn_id=None, native_goal={"status": "paused", "tokensUsed": 0}
        )
        sid, tid = session("observer-b")
    held, dispatch = tool(sid, tid)
    try:
        result = dispatch(
            "loopx_collaboration",
            {
                "action": "adopt",
                "operation_id": "a-source",
                "consumer_operation_id": "a-consumer",
            },
        )
    finally:
        held.__exit__(None, None, None)
    assert result["ok"] and result["adoptions"][0]["state"] == "current"
    states = {
        op: _read(runner.path(op))["wake"]["state"] for op in ("a-source", "a-consumer")
    }
    expected = "observed_in_turn" if observer == "origin" else "pending"
    assert states == {"a-source": expected, "a-consumer": expected}
