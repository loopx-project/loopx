"""Exact App grants reuse the original registered host and canonical queue."""
import json
from types import SimpleNamespace

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect
from test_attached_session_broker import _registry, GOAL_ID, AGENT_ID, HOST_SURFACE, HOST_SESSION_ID

from loopx.attached_session import bind_attached_agent_session, claim_attached_agent_turn, complete_attached_agent_turn
from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
from loopx.chat_store import _read_json
from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.extensions.lark.private_conversation_api import PrivateConversationRequestMixin
from loopx.presentation.renderers.conversation_status_markdown import (
    render_conversation_status,
)


def target(fixture):
    store, runtime, provider, transport = connect(fixture)
    workspace = fixture[-1]
    registry = _registry()
    registry["goals"][0]["repo"] = str(workspace)
    runtime.registry_path.write_text(json.dumps(registry))
    packet = bind_attached_agent_session(store=store, registry=registry, registry_path=runtime.registry_path,
        goal_id=GOAL_ID, agent_id=AGENT_ID, host_surface=HOST_SURFACE, host_session_id=HOST_SESSION_ID,
        executor_endpoint_id="codex", execute=True)
    sid = packet["session"]["session_id"]
    bindings = transport.bindings
    bid = next(row["binding_id"] for row in bindings.read()["bindings"] if row["transport_ref"] == "notes-app")
    state = bindings.change_agent_target(binding_id=bid, expected_revision=bindings.read()["revision"], session_id=sid)
    grant = next(row for row in state["bindings"] if row["binding_id"] == bid)["agent_targets"][0]
    return store, runtime, provider, transport, bid, sid, grant


def send(provider, transport, name, text, app="notes-app"):
    event = provider.event(app, name, text)
    result = transport.admit(app, event)
    return result, event


def claim(store, runtime, sid, name="exact-claim"):
    return claim_attached_agent_turn(store=store, registry_path=runtime.registry_path, session_id=sid,
        host_surface=HOST_SURFACE, host_session_id=HOST_SESSION_ID, claim_id=name)


def test_private_agent_selection_preserves_original_host_session_and_original_app_result(ordinary):  # noqa: F811
    store, runtime, provider, transport, _, sid, grant = target(ordinary)
    try:
        assert send(provider, transport, "ordinary", "ordinary project text")[0]["status"] == "durably_accepted"
        ordinary_row = next(row for row in transport.core.pending() if row["message"] == "ordinary project text")
        runtime.wait_for_turn(session_id=ordinary_row["session_id"], turn_id=ordinary_row["turn_id"], timeout_sec=10)
        assert send(provider, transport, "list", "/agents")[0]["status"] == "command_recorded"
        assert send(provider, transport, "select", f"/agent {grant['target_ref']}")[0]["status"] == "command_recorded"
        transport.reconcile()
        assert any(grant["target_ref"] in text for _, text in provider.writes)
        assert send(provider, transport, "agent-first", "first direct Agent question")[0]["status"] == "durably_accepted"
        first = next(row for row in transport.core.pending() if row["message"] == "first direct Agent question")
        assert first["session_id"] == sid and len(store.queued_turns(sid)) == 1
        assert sid not in runtime.adapters
        claimed = claim(store, runtime, sid)
        assert claimed["claimed"] and claimed["turn"]["message"] == "first direct Agent question"
        assert claimed["turn"]["external_audience"]["binding_id"] == grant_binding(store)
        assert send(provider, transport, "status", "/status")[0]["status"] == "command_recorded"
        assert send(provider, transport, "stop", "/stop")[0]["status"] == "command_recorded"
        assert send(provider, transport, "new", "/new")[0]["status"] == "command_recorded"
        assert store.load_session(sid)["active_turn_id"] == first["turn_id"]
        assert send(provider, transport, "return", "/project")[0]["status"] == "command_recorded"
        complete_attached_agent_turn(store=store, registry_path=runtime.registry_path, session_id=sid,
            turn_id=first["turn_id"], host_surface=HOST_SURFACE, host_session_id=HOST_SESSION_ID,
            claim_id="exact-claim", completion_id="exact-completion", response={"message": "Original Agent answer"})
        transport.reconcile()
        assert ("notes-app", "Original Agent answer") in provider.writes
        assert not any(app == "steward-app" and "Original Agent" in text for app, text in provider.writes)
        assert any(AGENT_ID in text and "正在执行" in text for _, text in provider.writes)
        assert any("没有被停止或替换" in text for _, text in provider.writes)
        before = len(store.list_sessions())
        assert send(provider, transport, "select-again", f"/agent {grant['target_ref']}")[0]["status"] == "command_recorded"
        assert send(provider, transport, "follow-up", "follow-up with same Agent")[0]["status"] == "durably_accepted"
        second = next(row for row in transport.core.pending() if row["message"] == "follow-up with same Agent")
        assert second["session_id"] == sid
        assert len(store.list_sessions()) == before
        assert store.load_session(sid)["upstream_thread_id"] == HOST_SESSION_ID
        assert claim(store, runtime, sid, "second-claim")["claimed"]
        assert send(provider, transport, "other-app", f"/agent {grant['target_ref']}", "steward-app")[0]["status"] == "command_rejected"
        denied = next(row for row in transport.core.pending() if row["source"]["sender_ref"] != first["source"]["sender_ref"])
        assert denied["status"] == "rejected"
    finally:
        runtime.close()


def test_selected_agent_status_freezes_owner_cadence_and_replays_without_reread(  # noqa: F811
    ordinary, monkeypatch  # noqa: F811
):
    store, runtime, provider, transport, _, _, grant = target(ordinary)
    from loopx.control_plane import effect_runtime

    actual = effect_runtime.effect_runtime_result
    cadence_reads = []

    def observe(method, params, **kwargs):
        if method == "quota.automation_cadence.manage":
            cadence_reads.append(dict(params))
            return {
                "schema_version": "automation_cadence_result_v1",
                "ok": True,
                "goal_id": GOAL_ID,
                "agent_id": AGENT_ID,
                "automation_id": None,
                "configuration_revision": 4,
                "min_interval_minutes": 60,
                "eligibility": {
                    "state": "waiting",
                    "reason": "minimum_interval_wait",
                    "eligible_now": False,
                    "next_eligible_at_ms": 1791637200000,
                },
                "sources": [{"owner_reference": "must-not-leak"}],
            }
        return actual(method, params, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", observe)
    try:
        send(provider, transport, "select-cadence", f"/agent {grant['target_ref']}")
        transport.reconcile()
        result, event = send(provider, transport, "cadence-status", "/status")
        assert result["status"] == "command_recorded"
        assert cadence_reads == [{
            "runtime_root": str(runtime.coordination_runtime_root),
            "operation": "read",
            "goal_id": GOAL_ID,
            "agent_id": AGENT_ID,
            "automation_id": None,
        }]
        native = next(
            row for row in transport.core.pending() if row["command"] == "status"
        )
        cadence = native["status_snapshot"]["automation_cadence"]
        assert cadence["eligibility"]["state"] == "waiting"
        assert cadence["eligibility"]["next_eligible_at_ms"] == 1791637200000
        assert "owner_reference" not in json.dumps(native["status_snapshot"])

        before = len(provider.writes)
        assert transport.reconcile() == 1
        assert len(provider.writes) == before + 1
        assert "等待至 2026-10-10T13:00:00.000Z" in provider.writes[-1][1]
        frozen = native["status_snapshot"]

        assert transport.admit("notes-app", {**event, "event_id": "redelivery"})[
            "status"
        ] == "command_recorded"
        restarted = type(transport)(
            controller=runtime,
            runtime_root=transport.runtime_root,
            runner=provider,
            cli_bin="lark-cli",
        )
        assert restarted.reconcile() == 1
        assert len(cadence_reads) == 1
        assert len(provider.writes) == before + 1
        assert restarted.core.read_request(native["request_ref"])[
            "status_snapshot"
        ] == frozen
        assert len(store.list_sessions()) == 1
    finally:
        runtime.close()


def test_real_owner_interval_eligibility_does_not_override_stronger_status(  # noqa: F811
    ordinary,  # noqa: F811
):
    _, runtime, provider, transport, _, _, grant = target(ordinary)
    try:
        configured = effect_runtime_result(
            "quota.automation_cadence.manage",
            {
                "runtime_root": str(runtime.coordination_runtime_root),
                "operation": "configure",
                "goal_id": GOAL_ID,
                "agent_id": None,
                "automation_id": None,
                "expected_revision": 0,
                "min_interval_minutes": 60,
                "owner_reference": "failed-session-regression",
                "execute": True,
            },
            retry_safe=False,
        )
        assert configured["configuration_revision"] == 1

        send(provider, transport, "select-failed-session", f"/agent {grant['target_ref']}")
        transport.reconcile()
        send(provider, transport, "failed-session-status", "/status")
        native = next(
            row for row in transport.core.pending() if row["command"] == "status"
        )
        assert (
            native["status_snapshot"]["automation_cadence"]["eligibility"]["state"]
            == "eligible"
        )

        for changes, expected in [
            ({"session_status": "resume_failed"}, "会话恢复失败"),
            (
                {
                    "active_turn_status": "running",
                    "active_turn_observation_available": False,
                },
                "执行状态暂不可读",
            ),
        ]:
            rendered = render_conversation_status(
                {**native["status_snapshot"], **changes}
            )
            assert expected in rendered
            assert "最小间隔条件已满足" in rendered
            assert "当前可启动" not in rendered
    finally:
        runtime.close()


@pytest.mark.parametrize("failure", ["missing_root", "read_failed", "malformed", "mismatch"])
def test_selected_agent_status_owner_failure_is_unknown_not_ready(  # noqa: F811
    ordinary, monkeypatch, failure  # noqa: F811
):
    _, runtime, provider, transport, _, _, grant = target(ordinary)
    from loopx.control_plane import effect_runtime

    send(provider, transport, f"select-{failure}", f"/agent {grant['target_ref']}")
    transport.reconcile()
    actual = effect_runtime.effect_runtime_result
    calls = []

    def observe(method, params, **kwargs):
        if method != "quota.automation_cadence.manage":
            return actual(method, params, **kwargs)
        calls.append(dict(params))
        if failure == "read_failed":
            raise OSError("private owner path")
        result = {
            "schema_version": "automation_cadence_result_v1",
            "ok": True,
            "goal_id": "another-goal" if failure == "mismatch" else GOAL_ID,
            "agent_id": AGENT_ID,
            "automation_id": None,
            "configuration_revision": 1,
            "min_interval_minutes": 60,
            "eligibility": {
                "state": "waiting",
                "reason": "minimum_interval_wait",
                "eligible_now": False,
                "next_eligible_at_ms": 1791637200000,
            },
        }
        if failure == "malformed":
            result["eligibility"]["next_eligible_at_ms"] = None
        return result

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", observe)
    if failure == "missing_root":
        monkeypatch.setattr(runtime, "coordination_runtime_root", None)
    try:
        send(provider, transport, f"status-{failure}", "/status")
        native = next(
            row for row in transport.core.pending() if row["command"] == "status"
        )
        cadence = native["status_snapshot"]["automation_cadence"]
        assert cadence["eligibility"] == {
            "state": "unavailable",
            "reason": (
                "runtime_root_unavailable"
                if failure == "missing_root"
                else "owner_read_failed"
            ),
            "eligible_now": None,
            "next_eligible_at_ms": None,
        }
        assert cadence["configuration_revision"] is None
        assert cadence["min_interval_minutes"] is None
        assert len(calls) == (0 if failure == "missing_root" else 1)
        transport.reconcile()
        assert "不能据此判断可启动" in provider.writes[-1][1]
        assert "当前可启动" not in provider.writes[-1][1]
        assert "private owner path" not in provider.writes[-1][1]
    finally:
        runtime.close()


def grant_binding(store):
    return next(row["binding_id"] for row in _read_json(store.root / "conversation-bindings.json")["bindings"] if row["transport_ref"] == "notes-app")


def test_revoked_agent_queue_cannot_be_claimed_and_session_audience_cannot_be_reassigned(ordinary):  # noqa: F811
    store, runtime, provider, transport, bid, sid, grant = target(ordinary)
    try:
        send(provider, transport, "select", f"/agent {grant['target_ref']}")
        send(provider, transport, "pending", "pending private message")
        row = next(row for row in transport.core.pending() if row["message"] == "pending private message")
        bindings = transport.bindings
        other = next(item["binding_id"] for item in bindings.read()["bindings"] if item["transport_ref"] == "steward-app")
        with pytest.raises(ValueError, match="another App"):
            bindings.change_agent_target(binding_id=other, expected_revision=bindings.read()["revision"], session_id=sid)
        bindings.change_agent_target(binding_id=bid, expected_revision=bindings.read()["revision"], target_ref=grant["target_ref"])
        assert not claim(store, runtime, sid)["claimed"]
        assert store.load_turn(sid, row["turn_id"])["error_code"] == "external_agent_grant_unavailable"
        with pytest.raises(ValueError, match="another App"):
            bindings.change_agent_target(binding_id=other, expected_revision=bindings.read()["revision"], session_id=sid)
        assert send(provider, transport, "return", "/project")[0]["status"] == "command_recorded"
        assert send(provider, transport, "normal", "ordinary chat remains usable")[0]["status"] == "durably_accepted"
        assert not any("pending private message" in text for _, text in provider.writes)
        assert bindings.agent_candidates(other) == []
    finally:
        runtime.close()


def test_committed_host_claim_replays_after_audience_revocation(ordinary):  # noqa: F811
    """A lost claim response must stay recoverable by the exact owning host."""

    store, runtime, provider, transport, bid, sid, grant = target(ordinary)
    try:
        send(provider, transport, "select", f"/agent {grant['target_ref']}")
        send(provider, transport, "ask", "committed private question")
        row = next(row for row in transport.core.pending() if row["message"] == "committed private question")
        send(provider, transport, "queued", "not yet claimed")
        queued = next(row for row in transport.core.pending() if row["message"] == "not yet claimed")
        with pytest.raises(ConnectionError, match="response lost"):
            committed = claim(store, runtime, sid, "stable-claim")
            assert committed["claimed"] and committed["turn"]["turn_id"] == row["turn_id"]
            assert committed["turn"]["claim_id"] == "stable-claim"
            raise ConnectionError("synthetic response lost after the durable claim")
        bindings = transport.bindings
        bindings.change_agent_target(binding_id=bid, expected_revision=bindings.read()["revision"], target_ref=grant["target_ref"])
        # A different claim id still cannot take over the committed Turn.
        assert not claim(store, runtime, sid, "other-claim")["claimed"]
        # The host that lost its response re-reads the same committed receipt.
        replayed = claim(store, runtime, sid, "stable-claim")
        assert replayed["claimed"] and replayed["turn"]["turn_id"] == row["turn_id"]
        assert replayed["turn"]["claim_id"] == "stable-claim"
        active = store.load_turn(sid, row["turn_id"])
        assert active["status"] == "running"
        assert active["host_claim_id"] == "stable-claim"
        assert len(store.list_sessions()) == 1
        complete_attached_agent_turn(store=store, registry_path=runtime.registry_path, session_id=sid,
            turn_id=row["turn_id"], host_surface=HOST_SURFACE, host_session_id=HOST_SESSION_ID,
            claim_id="stable-claim", completion_id="stable-completion", response={"message": "Recovered answer"})
        assert store.load_turn(sid, row["turn_id"])["status"] == "completed"
        transport.reconcile()
        # Revocation still withholds the private result from the original App.
        assert not any("Recovered answer" in text for _, text in provider.writes)
        # The recovery exception grants no authority to start queued or new work.
        assert not claim(store, runtime, sid, "fresh-claim")["claimed"]
        assert store.load_turn(sid, queued["turn_id"])["status"] == "failed"
        assert send(provider, transport, "new-after-revoke", "new private work")[0]["status"] == "command_rejected"
    finally:
        runtime.close()


def test_prepared_agent_request_recovers_same_turn_after_recipient_switch(ordinary, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, _, sid, grant = target(ordinary)
    try:
        send(provider, transport, "select", f"/agent {grant['target_ref']}")
        import loopx.capabilities.native_chat.external_conversations as external
        actual_write = external._atomic_write_json
        def fail_once(path, row):
            if row.get("status") == "accepted":
                raise OSError("synthetic crash after canonical admission")
            actual_write(path, row)
        with monkeypatch.context() as patch:
            patch.setattr(external, "_atomic_write_json", fail_once)
            with pytest.raises(OSError):
                send(provider, transport, "crash", "already admitted before crash")
        assert len(store.queued_turns(sid)) == 1
        send(provider, transport, "return", "/project")
        restarted = ChatExternalConversations(runtime)
        restarted.recover()
        row = next(item for item in restarted.pending() if item["message"] == "already admitted before crash")
        assert row["status"] == "accepted" and row["session_id"] == sid
        assert len(store.queued_turns(sid)) == 1 and sid not in runtime.adapters
        assert claim(store, runtime, sid)["turn"]["message"] == row["message"]
    finally:
        runtime.close()


def test_agent_settings_api_requires_exact_revision_and_registered_workspace(ordinary):  # noqa: F811
    store, runtime, provider, transport, bid, sid, grant = target(ordinary)
    class Handler(PrivateConversationRequestMixin):
        server = SimpleNamespace(runtime_controller=runtime)
        def _read_json(self): return self.body
        def _private_conversations(self): self.result = {"ok": True}
        def _send_error(self, message, **kwargs): self.result = {"error": message, **kwargs}
    handler = Handler()
    try:
        handler.body = {"binding_id": bid, "revision": -1, "target_ref": grant["target_ref"]}
        handler._private_conversation_agent_target()
        assert handler.result["status"] == 400 and "revision" in handler.result["error"]
        handler.body = {"binding_id": bid, "revision": transport.bindings.read()["revision"], "target_ref": grant["target_ref"]}
        handler._private_conversation_agent_target()
        assert handler.result == {"ok": True}
        registry = json.loads(runtime.registry_path.read_text())
        registry["goals"][0]["repo"] = str(ordinary[-1].parent)
        runtime.registry_path.write_text(json.dumps(registry))
        with pytest.raises(ValueError, match="workspace"):
            transport.bindings.change_agent_target(binding_id=bid, expected_revision=transport.bindings.read()["revision"], session_id=sid)
    finally:
        runtime.close()


def test_same_host_in_another_session_cannot_inherit_a_different_app_audience(ordinary):  # noqa: F811
    store, runtime, _, transport, bid, sid, grant = target(ordinary)
    try:
        registry = json.loads(runtime.registry_path.read_text())
        duplicate = bind_attached_agent_session(store=store, registry=registry, registry_path=runtime.registry_path,
            goal_id=GOAL_ID, agent_id=AGENT_ID, host_surface=HOST_SURFACE, host_session_id=HOST_SESSION_ID,
            executor_endpoint_id="codex", channel_id="another-channel", execute=True)["session"]["session_id"]
        bindings = transport.bindings
        other = next(item["binding_id"] for item in bindings.read()["bindings"] if item["transport_ref"] == "steward-app")
        bindings.change_agent_target(binding_id=bid, expected_revision=bindings.read()["revision"], target_ref=grant["target_ref"])
        with pytest.raises(ValueError, match="available registered host"):
            bindings.change_agent_target(binding_id=other, expected_revision=bindings.read()["revision"], session_id=duplicate)
        assert bindings.agent_candidates(other) == []
        # Removing the host's registration after admission prevents claim.
        bindings.change_agent_target(binding_id=bid, expected_revision=bindings.read()["revision"], session_id=sid)
        new_grant = next(row for row in bindings.read()["bindings"] if row["binding_id"] == bid)["agent_targets"][0]
        provider = transport.runner
        send(provider, transport, "select", f"/agent {new_grant['target_ref']}")
        send(provider, transport, "queued", "registration-sensitive message")
        registry["goals"][0]["coordination"]["thread_agent_bindings"] = []
        runtime.registry_path.write_text(json.dumps(registry))
        assert not claim(store, runtime, sid)["claimed"]
    finally:
        runtime.close()


def test_http_agent_configuration_and_revoked_workspace_projection(ordinary):  # noqa: F811
    import http.client
    import threading
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    _, runtime, _, transport, bid, sid, grant = target(ordinary)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.runtime_controller = runtime
    server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=lambda: {}, close=lambda: None)
    server.lark_private_conversations = transport
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = "/api/chat/lark/private-conversations"
    def request(method, url, body=None, origin=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            connection.request(method, url, json.dumps(body) if body else None, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    try:
        code, snapshot = request("GET", path)
        assert code == 200
        row = next(row for row in snapshot["connections"] if row["binding_id"] == bid)
        assert row["agent_candidates"][0]["session_id"] == sid
        assert row["agent_targets"][0]["target_ref"] == grant["target_ref"]
        assert "upstream_thread_id" not in json.dumps(snapshot) and "host_session_id" not in json.dumps(snapshot)
        body = {"binding_id": bid, "revision": snapshot["revision"], "target_ref": grant["target_ref"]}
        assert request("POST", path + "/agent-targets", body, "https://untrusted.example")[0] == 403
        code, updated = request("POST", path + "/agent-targets", body)
        assert code == 200
        assert next(row for row in updated["connections"] if row["binding_id"] == bid)["agent_targets"] == []
        runtime.project_contexts.roots = []
        code, unavailable = request("GET", path)
        assert code == 200
        assert all(row["agent_candidates"] == [] and not row["context_available"] for row in unavailable["connections"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        runtime.close()
