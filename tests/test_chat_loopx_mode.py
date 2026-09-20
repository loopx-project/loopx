"""Conversation execution admission, durable input, and default-off boundaries."""

import json
from threading import Event
from types import SimpleNamespace

import pytest

from loopx.chat_loopx_mode import TOOL
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.effect_runtime import effect_runtime_result
from test_chat_project_coordination import project  # noqa: F401


@pytest.fixture
def mode(project, monkeypatch):  # noqa: F811
    root, registry, repo = project
    registry_payload = json.loads(registry.read_text())
    next(goal for goal in registry_payload["goals"] if goal["id"] == "research")[
        "spawn_policy"
    ] = {
        "mode": "multi_subagent",
        "allowed": True,
        "max_children": 2,
        "execution_config": ".loopx/config/delegations.json",
    }
    registry.write_text(json.dumps(registry_payload))
    store = ChatSessionStore(root)
    controller = ChatRuntimeController(
        store=store, codex_bin="codex", registry_path=registry
    )
    session = store.create_session(
        goal_id="research",
        agent_id="codex",
        channel_id="goal.research",
        upstream_thread_id="fixture",
        upstream_mode="chat",
        adapter_kind="codex_app_server",
    )
    config = repo / ".loopx/config/delegations.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "schema_version": "loopx_local_delegation_v0",
                "bindings": [
                    {
                        "id": "review",
                        "agent_id": "reviewer",
                        "todo_id": "review-task",
                        "requesters": ["coordinator"],
                        "workspace": str(repo),
                        "host_args": ["--host", "dsh"],
                        "timeout_seconds": 60,
                        "output_refs": ["output.json"],
                    }
                ],
            }
        )
    )
    settings = {
        "agent_id": "coordinator",
        "token_budget": 10000,
    }
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
    return controller.loopx_mode, session["session_id"], repo, settings, calls


def apply(mode, operation, **body):
    service, sid, repo, _, _ = mode
    return service.apply(
        sid,
        {"operation": operation, "operation_id": "run-1", **body},
        work_dir=repo,
        objective="Verify revised disclosure",
    )


def test_start_is_explicit_and_replays_original_request_even_after_settings_change(
    mode,
):
    service, sid, _, settings, calls = mode
    assert not service.snapshot(sid)["enabled"]
    apply(mode, "configure", settings=settings)
    assert not calls and not service.snapshot(sid)["enabled"]
    first = apply(mode, "start")
    second = apply(mode, "start")
    assert first["turn_id"] == second["turn_id"] and len(calls) == 1
    with pytest.raises(ValueError, match="identity conflict"):
        apply(mode, "resume")
    with pytest.raises(ValueError, match="identity conflict"):
        apply(mode, "start", settings={**settings, "token_budget": 20000})
    with pytest.raises(Exception, match="current conversation turn"):
        apply(mode, "start", operation_id="another-run")
    assert len(calls) == 1


def test_unfinished_goal_pins_identity_and_configuration_digest(mode):
    service, sid, repo, settings, _ = mode
    apply(mode, "configure", settings=settings)
    service.store.update_session(
        sid, native_goal={"status": "paused", "tokensUsed": 400}
    )
    with pytest.raises(ValueError, match="cannot change"):
        apply(mode, "configure", settings={**settings, "agent_id": "reviewer"})
    config = repo / ".loopx/config/delegations.json"
    config.write_text(config.read_text() + "\n")
    with pytest.raises(ValueError, match="bindings changed"):
        apply(mode, "resume", settings=settings)
    with pytest.raises(ValueError, match="invalid LoopX mode settings"):
        apply(mode, "resume", settings={**settings, "config_digest": ""})


def test_goal_registry_is_the_execution_config_owner(mode):
    service, sid, _, settings, _ = mode
    snapshot = service.snapshot(sid)
    assert snapshot["settings"]["execution_config"] == (
        ".loopx/config/delegations.json"
    )
    apply(mode, "configure", settings=settings)
    stored = service.store.load_session(sid)["loopx_mode"]["settings"]
    assert "execution_config" not in stored
    assert stored["execution_config_ref"] == ".loopx/config/delegations.json"

    with pytest.raises(ValueError, match="Goal execution bindings changed"):
        apply(
            mode,
            "configure",
            settings={
                **settings,
                "execution_config": ".loopx/config/other.json",
            },
        )


def test_unfinished_legacy_session_can_resume_until_goal_config_is_migrated(mode):
    service, sid, repo, settings, calls = mode
    registry = service.controller.registry_path
    payload = json.loads(registry.read_text())
    goal = next(item for item in payload["goals"] if item["id"] == "research")
    goal["spawn_policy"].pop("execution_config")
    registry.write_text(json.dumps(payload))
    service.store.update_session(
        sid,
        native_goal={"status": "paused", "tokensUsed": 10},
        loopx_mode={
            "enabled": True,
            "paused": True,
            "settings": {
                **settings,
                "execution_config": ".loopx/config/delegations.json",
            },
        },
    )

    assert service.snapshot(sid)["settings"]["execution_config"] == (
        ".loopx/config/delegations.json"
    )
    result = apply(mode, "resume")
    assert result["turn_id"]
    assert len(calls) == 1
    stored = service.store.load_session(sid)["loopx_mode"]["settings"]
    assert "execution_config" not in stored
    assert stored["execution_config_ref"] == ".loopx/config/delegations.json"


def test_completed_legacy_session_requires_goal_owned_execution_config(mode):
    service, sid, _, settings, _ = mode
    registry = service.controller.registry_path
    payload = json.loads(registry.read_text())
    goal = next(item for item in payload["goals"] if item["id"] == "research")
    goal["spawn_policy"].pop("execution_config")
    registry.write_text(json.dumps(payload))
    service.store.update_session(
        sid,
        native_goal={"status": "complete", "tokensUsed": 10},
        loopx_mode={
            "enabled": True,
            "paused": True,
            "settings": {
                **settings,
                "execution_config": ".loopx/config/delegations.json",
            },
        },
    )

    assert service.snapshot(sid)["settings"]["execution_config"] is None
    with pytest.raises(ValueError, match="Goal sub-agent settings"):
        apply(mode, "start")


@pytest.mark.parametrize(
    "field,value",
    [
        ("channel_id", "manager"),
        ("channel_id", "task.research"),
        ("session_mode", "attached_host"),
        ("agent_id", "dsh"),
        ("origin", "external"),
    ],
)
def test_typed_admission_rejects_non_owner_goal_execution(field, value):
    session = {
        "channel_id": "goal.research",
        "goal_id": "research",
        "agent_id": "codex",
        field: value,
    }
    with pytest.raises(Exception):
        effect_runtime_result(
            "collaboration.chat_mode",
            {"session": session, "operation": "exit", "origin": "web", "settings": {}},
        )


def test_queue_inbox_are_durable_distinct_and_steer_failure_is_not_downgraded(
    mode, monkeypatch
):
    service, sid, _, settings, _ = mode
    apply(mode, "start", settings=settings)
    for delivery in ("queue", "inbox"):
        apply(
            mode,
            "message",
            operation_id=delivery,
            delivery_mode=delivery,
            message="Use the corrected filing",
        )
        apply(
            mode,
            "message",
            operation_id=delivery,
            delivery_mode=delivery,
            message="Use the corrected filing",
        )
    assert len(service.store.loopx_ingress(sid)) == 2
    assert (
        len(
            [
                m
                for m in service.store.messages(sid)
                if m.get("origin", "").startswith("loopx_")
            ]
        )
        == 2
    )
    with pytest.raises(ValueError):
        apply(
            mode,
            "message",
            operation_id="queue",
            delivery_mode="queue",
            message="Changed content",
        )
    calls = []
    adapter = SimpleNamespace(
        session=SimpleNamespace(steer=lambda message, **kw: calls.append((message, kw)))
    )
    service.deliver_queue(sid, adapter, "next-native-turn")
    assert len(calls) == 1 and calls[0][1] == {"expected_turn_id": "next-native-turn"}
    assert {r["mode"]: r["status"] for r in service.store.loopx_ingress(sid)} == {
        "loopx_queue": "delivered",
        "loopx_inbox": "pending",
    }
    service.deliver_queue(sid, adapter, "later-native-turn")
    assert len(calls) == 1

    def rejected(**_):
        raise RuntimeError("provider rejected steer")

    monkeypatch.setattr(service.controller, "steer_active_turn", rejected)
    with pytest.raises(RuntimeError, match="provider rejected"):
        apply(
            mode,
            "message",
            operation_id="steer",
            delivery_mode="steer",
            message="Correction",
        )
    assert len(service.store.loopx_ingress(sid)) == 2


def test_uncertain_queue_is_not_replayed(mode):
    service, sid, _, settings, _ = mode
    apply(mode, "start", settings=settings)
    apply(
        mode, "message", operation_id="q", delivery_mode="queue", message="Correction"
    )

    def lost(*_, **__):
        raise TimeoutError("lost acknowledgement")

    adapter = SimpleNamespace(session=SimpleNamespace(steer=lost))
    with pytest.raises(TimeoutError):
        service.deliver_queue(sid, adapter, "next-turn")
    assert service.store.loopx_ingress(sid)[0]["status"] == "uncertain"
    service.deliver_queue(sid, adapter, "later-turn")


def test_pause_fences_dispatch_and_does_not_cancel_members(mode, monkeypatch):
    service, sid, _, settings, _ = mode
    started = apply(mode, "start", settings=settings)
    stopped = Event()
    adapter = SimpleNamespace(
        goal_driver=SimpleNamespace(stopped=stopped), session=SimpleNamespace()
    )
    lock = service.prepare(
        sid, started["turn_id"], adapter, lambda *a: {"read": True}, lambda *a: None
    )
    try:
        assert (
            adapter.session.read_tool_handler(TOOL["name"], {"action": "bindings"})[
                "bindings"
            ][0]["id"]
            == "review"
        )
        inventory = adapter.session.read_tool_handler(TOOL["name"], {"action": "operations"})
        assert inventory["ok"] and inventory["items"] == []
        assert inventory["page_readback_complete"] and not inventory["has_more"]
        invalid = adapter.session.read_tool_handler(TOOL["name"], {"action": "operations", "operation_id": "one"})
        assert invalid["error"] == "collaboration_request_rejected"

        from loopx.collaboration_mcp import Delegations
        decisions = []
        def adopt(bound, operation, consumer):
            decisions.append((bound.agent_id, operation, consumer))
            return {"adoptions": []}
        monkeypatch.setattr(Delegations, "adopt_result", adopt)
        handler = adapter.session.read_tool_handler
        assert handler(TOOL["name"], {"action": "adopt", "operation_id": "source",
                                      "consumer_operation_id": "consumer"})["ok"]
        assert decisions == [(settings["agent_id"], "source", "consumer")]
        assert handler(TOOL["name"], {"action": "adopt", "operation_id": "source",
                                     "consumer_operation_id": "consumer", "binding_id": "other"})["error"] == "collaboration_request_rejected"

        # Pause persists before attempting potentially slow provider interruption.
        def interrupt(**_):
            assert service.store.load_session(sid)["loopx_mode"]["paused"]
            assert (
                adapter.session.read_tool_handler(TOOL["name"], {"action": "start"})[
                    "error"
                ]
                == "conversation_execution_inactive"
            )

        monkeypatch.setattr(service.controller, "interrupt_turn", interrupt)
        apply(mode, "pause")
        assert adapter.session.read_tool_handler(TOOL["name"], {"action": "operations"})["error"] == "conversation_execution_inactive"
        assert handler(TOOL["name"], {"action": "adopt", "operation_id": "source",
                                     "consumer_operation_id": "consumer"})["error"] == "conversation_execution_inactive"
        assert len(decisions) == 1
        with pytest.raises(Exception, match="active conversation execution"):
            apply(mode, "message", delivery_mode="queue", message="After pause")
        assert adapter.session.read_tool_handler("loopx_context_read", {}) == {
            "read": True
        }
    finally:
        lock.__exit__(None, None, None)


def test_sender_cannot_drive_two_conversations_concurrently(mode):
    service, sid, _, settings, _ = mode
    started = apply(mode, "start", settings=settings)
    adapter = SimpleNamespace(
        goal_driver=SimpleNamespace(stopped=Event()), session=SimpleNamespace()
    )
    lock = service.prepare(
        sid, started["turn_id"], adapter, lambda *a: {}, lambda *a: None
    )
    try:
        with pytest.raises(Exception):
            service.prepare(
                sid, started["turn_id"], adapter, lambda *a: {}, lambda *a: None
            )
    finally:
        lock.__exit__(None, None, None)


def test_real_runtime_upgrades_idle_tools_preserves_history_and_completes(
    mode, monkeypatch
):
    from loopx.chat_runtime import CodexAppServerAdapter
    from test_chat_codex_goal import Host, begin, complete, end

    service, sid, repo, settings, _ = mode
    controller = service.controller
    monkeypatch.setattr(
        controller, "submit_turn", ChatRuntimeController.submit_turn.__get__(controller)
    )
    old = Host(repo, monkeypatch)
    old.session.model = "fixture-model"
    old.session.reasoning_effort = "high"
    controller.adapters[sid] = CodexAppServerAdapter(old.session)
    observed = []

    def read_bindings(host):
        observed.append(
            host.session.read_tool_handler(TOOL["name"], {"action": "bindings"})
        )
        return complete(host)

    new = Host(
        repo,
        monkeypatch,
        events=[begin("native-one"), read_bindings, end("native-one")],
    )
    calls = []

    def start(**kwargs):
        calls.append(kwargs)
        return CodexAppServerAdapter(new.session)

    monkeypatch.setattr(controller, "_start_adapter", start)
    service.store.append_message(
        sid, role="user", text="Retain the original research constraints"
    )
    try:
        response = apply(mode, "start", settings=settings)
        done = controller.wait_for_turn(
            session_id=sid, turn_id=response["turn_id"], timeout_sec=30
        )
        assert done["status"] == "completed", done
        assert (
            observed[0]["ok"] and observed[0]["bindings"][0]["agent_id"] == "reviewer"
        )
        assert calls[0]["loopx_tools"] is True
        assert calls[0]["executor_model"] == {
            "model": "fixture-model",
            "reasoning_effort": "high",
        }
        assert calls[0]["history"] == [
            {"role": "user", "content": "Retain the original research constraints"}
        ]
        assert old.closed and service.snapshot(sid)["native"]["status"] == "complete"
        assert service.store.load_session(sid)["loopx_tools"] is True
        apply(mode, "exit")
        assert not service.snapshot(sid)["enabled"]
    finally:
        controller.close()


def test_tool_upgrade_never_replaces_unfinished_native_goal(mode, monkeypatch):
    from loopx.chat_runtime import CodexAppServerAdapter
    from test_chat_codex_goal import Host, native_goal

    service, sid, repo, settings, _ = mode
    controller = service.controller
    monkeypatch.setattr(
        controller, "submit_turn", ChatRuntimeController.submit_turn.__get__(controller)
    )
    host = Host(repo, monkeypatch, goal=native_goal("paused"))
    controller.adapters[sid] = CodexAppServerAdapter(host.session)
    monkeypatch.setattr(
        controller,
        "_start_adapter",
        lambda **kw: pytest.fail("must preserve unfinished thread"),
    )
    with pytest.raises(ValueError, match="unfinished"):
        apply(mode, "start", settings=settings)
    assert not host.closed and not service.snapshot(sid)["enabled"]


def test_pause_cannot_enable_mode_and_ordinary_turn_is_not_execution(mode):
    service, sid, _, settings, _ = mode
    with pytest.raises(Exception, match="pause requires enabled"):
        apply(mode, "pause")
    assert not service.snapshot(sid)["enabled"]
    service.store.update_session(
        sid, loopx_mode={"enabled": True, "paused": True, "settings": settings}
    )
    ordinary, _ = service.store.create_turn(
        sid, client_turn_id="ordinary", message="Explain this finding"
    )
    snapshot = service.snapshot(sid)
    assert snapshot["conversation_busy"] and snapshot["active_turn_id"] is None
    with pytest.raises(Exception):
        apply(mode, "resume")
    assert service.store.load_turn(sid, ordinary["turn_id"])["status"] == "queued"


def test_owner_team_readback_is_configured_scoped_and_does_not_start_a_turn(mode):
    service, sid, _, settings, calls = mode
    with pytest.raises(ValueError):
        service.read_team(sid, {"operation": "operations"})
    apply(mode, "configure", settings=settings)
    before = service.store.load_session(sid)
    result = service.read_team(sid, {"operation": "operations"})
    assert result["items"] == [] and result["page_readback_complete"]
    assert service.store.load_session(sid) == before
    assert calls == []
    with pytest.raises(ValueError, match="invalid team readback"):
        service.read_team(sid, {"operation": "operations", "agent_id": "other"})
