"""Real native File/SQLite creation behind the original private conversation.

Only the existing synthetic model/provider are fake. These tests do not launch
or qualify a scheduled report, and never migrate an active user's Goal.
"""
import json

import pytest

from test_native_steward_private import steward as steward_fixture, finish
from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
from loopx.capabilities.machine_configuration.store import configure_machine_configuration
from loopx.control_plane.todos.list_readback import list_goal_todos
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

steward = steward_fixture


def original(steward):
    store, runtime, provider, transport, binding, capture, _ = steward
    text = "Retain this original private conversation and its unfinished work."
    transport.admit("steward-app", provider.event("steward-app", "original", text))
    row = next(item for item in transport.core.pending() if item["message"] == text)
    assert finish(runtime, row)["status"] == "completed"
    return row["session_id"], transport.managed_steward


def storage(runtime, provider):
    args = {"runtime_root": runtime.coordination_runtime_root,
        "registry": build_builtin_machine_configuration_registry(),
        "configuration": {"schema_version": "loopx_machine_configuration_v0", "namespaces": {
            "goal_storage": {"schema_version": "loopx_goal_storage_defaults_v1",
                "new_goal_provider": provider, "canonical_creation": True, "new_goal_handoff_mode": "hard_lease"}}}}
    plan = configure_machine_configuration(**args, execute=False)
    configured = configure_machine_configuration(**args, execute=True, expected_plan_revision=plan["plan_revision"])
    assert configured["readback_verified"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_preparation_preserves_original_and_creates_one_real_actor(steward, tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, _, transport, binding, capture, _ = steward
    sid, service = original(steward)
    storage(runtime, provider)
    before_session = store.load_session(sid)
    before_history, before_calls = store.messages(sid), capture.read_bytes()
    before_registry = runtime.registry_path.read_bytes()
    preview = service.prepare(binding["binding_id"], session_id=sid)
    assert not preview["applied"]
    assert runtime.registry_path.read_bytes() == before_registry
    assert capture.read_bytes() == before_calls
    applied = service.prepare(binding["binding_id"], session_id=sid,
        expected_plan_id=preview["plan"]["plan_id"], execute=True)
    actor = applied["actor"]
    assert applied["applied"] and not applied["scheduling_enabled"] and not applied["model_invoked"]
    assert actor["session_id"] == sid and actor["agent_id"] == "steward"
    assert store.load_session(sid) == before_session
    assert store.messages(sid) == before_history and capture.read_bytes() == before_calls
    goals = json.loads(runtime.registry_path.read_text())["goals"]
    assert len(goals) == 1 and goals[0]["id"] == actor["goal_id"]
    assert goals[0]["repo"] != before_session["steward_context"]["workspace_path"]
    todos = list_goal_todos(registry_path=runtime.registry_path, goal_id=actor["goal_id"],
        agent_id="steward", role="agent", runtime_root_arg=str(runtime.coordination_runtime_root))
    assert todos["ok"] and todos["authority_read"]["source_authority"] == f"{provider}_v0"
    assert todos["todo_count"] == 0
    replay = service.prepare(binding["binding_id"], session_id=sid,
        expected_plan_id=preview["plan"]["plan_id"], execute=True)
    assert replay["idempotent"] and replay["actor"] == actor
    assert capture.read_bytes() == before_calls
    assert service.health(binding["binding_id"])["prepared"] is True
    # Changing only the existing portfolio scope must not detach the actor.
    transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
        executor_endpoint_id="codex", context_kind="steward", goal_scope="selected")
    assert transport.bindings.read()["bindings"][-1]["managed_actor"] == actor


@pytest.mark.parametrize("fault", ["queued", "provider", "stale_preview", "foreign_session"])
def test_rejected_preparation_has_no_native_goal_or_model_effect(steward, fault):
    store, runtime, provider, _, binding, capture, _ = steward
    sid, service = original(steward)
    preview = service.prepare(binding["binding_id"], session_id=sid)
    if fault == "queued":
        store.create_queued_turn(sid, client_turn_id="owner-followup", message="Keep my request first", origin="web")
    elif fault == "provider":
        provider.owner_ready = False
    elif fault == "foreign_session":
        sid = "0" * 32
    before_registry, before_calls = runtime.registry_path.read_bytes(), capture.read_bytes()
    with pytest.raises((ValueError, KeyError)):
        service.prepare(binding["binding_id"], session_id=sid, execute=True,
            expected_plan_id="wrong" if fault == "stale_preview" else preview["plan"]["plan_id"])
    assert runtime.registry_path.read_bytes() == before_registry
    assert capture.read_bytes() == before_calls
    assert service.health(binding["binding_id"])["prepared"] is False


def test_preparation_recovers_native_creation_after_registration_loses_response(steward, tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    _, runtime, _, _, binding, capture, _ = steward
    sid, service = original(steward)
    storage(runtime, "sqlite")
    preview = service.prepare(binding["binding_id"], session_id=sid)
    before = capture.read_bytes()
    import loopx.configure_goal as module
    configure = module.configure_goal

    def fail_registration(**kwargs):
        raise OSError("registration response lost")

    monkeypatch.setattr(module, "configure_goal", fail_registration)
    with pytest.raises(OSError, match="response lost"):
        service.prepare(binding["binding_id"], session_id=sid,
            expected_plan_id=preview["plan"]["plan_id"], execute=True)
    first = json.loads(runtime.registry_path.read_text())["goals"]
    assert len(first) == 1
    monkeypatch.setattr(module, "configure_goal", configure)
    recovered = service.prepare(binding["binding_id"], session_id=sid,
        expected_plan_id=preview["plan"]["plan_id"], execute=True)
    assert recovered["actor"]["goal_instance_id"] == first[0].get("goal_instance_id")
    assert len(json.loads(runtime.registry_path.read_text())["goals"]) == 1
    assert capture.read_bytes() == before


def test_http_preparation_requires_current_preview_and_reads_original_binding(steward, tmp_path, monkeypatch):
    import http.client
    import threading
    from types import SimpleNamespace
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, _, transport, binding, capture, _ = steward
    sid, _ = original(steward)
    storage(runtime, "sqlite")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller = store, runtime
    server.registry_path, server.runtime_root_override = runtime.registry_path, store.root.parent
    server.lark_private_conversations = transport
    server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=lambda: {}, close=lambda: None)
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None, origin=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=20)
        try:
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            connection.request("GET" if body is None else "POST", path,
                None if body is None else json.dumps(body), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    base = "/api/chat/lark/private-conversations"
    route = base + "/managed-steward"
    before_history, before_calls = store.messages(sid), capture.read_bytes()
    try:
        assert request(route, {"binding_id": binding["binding_id"]}, "https://foreign.example")[0] == 403
        assert request(route, {"binding_id": binding["binding_id"], "goal_id": "borrowed-project"})[0] == 409
        code, preview = request(route, {"binding_id": binding["binding_id"], "execute": False})
        assert code == 200 and not preview["applied"]
        assert not json.loads(runtime.registry_path.read_text())["goals"]
        # Changing the existing scope is a real source revision, not a forged
        # expected digest. A stale preview must not create a Goal or model.
        transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="codex", context_kind="steward", goal_scope="selected")
        body = {"binding_id": binding["binding_id"], "execute": True,
            "session_id": sid, "plan_id": preview["plan"]["plan_id"]}
        assert request(route, body)[0] == 409
        assert not json.loads(runtime.registry_path.read_text())["goals"]
        code, preview = request(route, {"binding_id": binding["binding_id"], "execute": False})
        assert code == 200
        code, applied = request(route, {**body, "plan_id": preview["plan"]["plan_id"]})
        assert code == 200 and applied["applied"]
        code, readback = request(base)
        assert code == 200
        row = next(row for row in readback["connections"] if row["binding_id"] == binding["binding_id"])
        assert row["managed_steward"] == {"available": True, "prepared": True, "scheduling_enabled": False,
            "goal_id": applied["actor"]["goal_id"], "agent_id": "steward", "session_id": sid}
        assert store.messages(sid) == before_history and capture.read_bytes() == before_calls
        assert request(route, {**body, "plan_id": preview["plan"]["plan_id"]})[1]["idempotent"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
