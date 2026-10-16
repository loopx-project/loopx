"""Automatic owner notices through native sources and existing runtime workers."""
import http.client
import json
import subprocess
import threading
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.extensions.lark.private_conversations import LarkPrivateConversations
from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService


def receipt(o):
    return json.loads(next((o.attention.root / "receipts" / o.binding["binding_id"]).glob("*.json")).read_text())


@pytest.fixture
def owner(ordinary, tmp_path):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    registry = runtime.registry_path
    registry.write_text(json.dumps({"schema_version": 1, "goals": []}))
    transport.bindings.configure(transport_ref="steward-app", project_ref=runtime.project_contexts.available()[0]["project_ref"],
                                 executor_endpoint_id="codex", context_kind="steward")
    event = provider.event("steward-app", "owner_original", "/status")
    transport.admit("steward-app", event)
    transport.reconcile()
    binding = transport._binding("steward-app")
    attention = transport.owner_attention
    def configure(enabled=True, revision=None):
        attention.configure(binding_id=binding["binding_id"], binding_revision=transport.bindings.read()["revision"],
                            revision=attention.health(binding["binding_id"])["revision"] if revision is None else revision,
                            enabled=enabled)
    def tasks(rows, *, goal="alpha"):
        path = tmp_path / f"{goal}.md"
        path.write_text("# Goal\n\n## User Todo\n\n" + "".join(
            f"- [{'x' if row.get('done') else ' '}] {row['text']}\n"
            f"  <!-- loopx:todo todo_id={row['id']} status={'done' if row.get('done') else 'open'} task_class=user_action -->\n"
            for row in rows) + "\n## Agent Todo\n")
        data = json.loads(registry.read_text())
        data["goals"] = [row for row in data["goals"] if row["id"] != goal] + [
            {"id": goal, "repo": str(tmp_path), "state_file": path.name}]
        registry.write_text(json.dumps(data))
    yield SimpleNamespace(store=store, runtime=runtime, provider=provider, transport=transport, binding=binding,
                          attention=attention, configure=configure, tasks=tasks, event=event, capture=ordinary[4])
    runtime.close()


def test_service_discovers_new_native_tasks_without_model_or_agent_poll(owner):
    o = owner
    o.tasks([{"id": "todo_old", "text": "Existing owner decision"}])
    o.configure()
    before = len(o.provider.writes)
    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=o.store.root.parent,
        runtime_controller=o.runtime, private_conversations=o.transport)
    # The production outbound worker, no call to the notice reconciler and no
    # synthetic inbound message or model Turn after creating the native Todo.
    o.tasks([{"id": "todo_old", "text": "Existing owner decision"},
             {"id": "todo_new", "text": "Review preview " + "complete terms " * 100}])
    worker = threading.Thread(target=service._reconcile_private, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if len(o.provider.writes) > before and o.attention.health(o.binding["binding_id"])["unconfirmed_count"] == 0:
                break
            time.sleep(.05)
        assert len(o.provider.writes) == before + 1
        assert ("complete terms " * 100).rstrip() in o.provider.writes[-1][1], "exact source text survives summary bounds"
        assert o.attention.reconcile() == 0
        assert len(o.provider.writes) == before + 1
        assert not o.capture.exists() and not o.store.list_sessions(), "no background model or hidden Goal"
        row = receipt(o)
        assert row["status"] == "delivered" and row["message_id"] in o.provider.messages
        assert row["readback"]["message_id"] == row["message_id"] and row["readback"]["app_id"] == "cli_steward_app"
    finally:
        service._closed.set()
        worker.join(10)


def test_disabled_notifications_preserve_all_four_existing_delivery_slots(owner, monkeypatch):
    o = owner
    entered, release, all_started = [], threading.Event(), threading.Event()
    def existing_reply(path):
        entered.append(path)
        if len(entered) == 4:
            all_started.set()
        release.wait(5)
        return 0
    monkeypatch.setattr(o.transport, "pending_delivery_paths", lambda: [Path(str(i)) for i in range(4)])
    monkeypatch.setattr(o.transport, "reconcile_request", existing_reply)
    monkeypatch.setattr(o.attention, "reconcile", lambda: pytest.fail("disabled notifications must not schedule a job"))
    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=o.store.root.parent,
        runtime_controller=o.runtime, private_conversations=o.transport)
    # Hold the scan boundary: an empty optional job cannot borrow a reply
    # slot and defer the fourth existing request until a later scan.
    monkeypatch.setattr(service._closed, "wait", lambda _: release.wait(5))
    worker = threading.Thread(target=service._reconcile_private, daemon=True)
    worker.start()
    try:
        assert all_started.wait(2)
        assert len(entered) == 4, "disabled feature retains the pre-change outbound concurrency"
    finally:
        service._closed.set()
        release.set()
        worker.join(5)


def test_restart_verifies_original_locator_and_never_resends(owner):
    o = owner
    o.configure()
    o.tasks([{"id": "todo_new", "text": "Inspect a new preview"}])
    o.provider.verify_replies = False
    before = len(o.provider.writes)
    assert o.attention.reconcile() == 0
    assert o.attention.health(o.binding["binding_id"])["unconfirmed_count"] == 1
    restarted = LarkPrivateConversations(controller=o.runtime, runtime_root=o.store.root.parent,
                                         runner=o.provider, cli_bin="lark-cli").owner_attention
    path = restarted._path(o.binding["binding_id"])
    state = restarted._read(path)
    state["outbox"][0]["next_attempt_at"] = 0
    restarted._write(path, state)
    o.provider.verify_replies = True
    assert restarted.reconcile() == 1
    assert restarted.reconcile() == 0 and len(o.provider.writes) == before + 1
    assert receipt(o)["status"] == "delivered" and restarted._read(path)["outbox"] == []


@pytest.mark.parametrize("failure", ["timeout", "crash_after_effect", "spawn"])
def test_write_failure_semantics_survive_process_restart(owner, failure):
    o = owner
    o.configure()
    o.tasks([{"id": "todo_new", "text": "A real new owner task"}])
    original = o.transport.runner
    def runner(args, cwd=None, timeout=None):
        if "+messages-send" in args:
            if failure == "spawn":
                raise FileNotFoundError("private credential must not leak")
            original(args, cwd, timeout)  # effect happened; no locator returned
            if failure == "crash_after_effect":
                raise KeyboardInterrupt("simulated process crash")
            raise subprocess.TimeoutExpired(args, timeout)
        return original(args, cwd, timeout)
    o.transport.runner = runner
    before = len(o.provider.writes)
    if failure == "crash_after_effect":
        with pytest.raises(KeyboardInterrupt):
            o.attention.reconcile()
    else:
        o.attention.reconcile()
    o.transport.runner = original
    restarted = LarkPrivateConversations(controller=o.runtime, runtime_root=o.store.root.parent,
                                         runner=o.provider, cli_bin="lark-cli").owner_attention
    path = restarted._path(o.binding["binding_id"])
    state = restarted._read(path)
    state["outbox"][0]["next_attempt_at"] = 0
    restarted._write(path, state)
    restarted.reconcile()
    if failure == "spawn":
        assert len(o.provider.writes) == before + 1 and receipt(o)["status"] == "delivered"
    else:
        assert len(o.provider.writes) == before + 1 and restarted._read(path)["outbox"][0]["status"] == "unconfirmed"


@pytest.mark.parametrize("denial", ["wrong_app", "wrong_owner", "source_changed", "revoked", "disabled", "source_unavailable"])
def test_current_audience_scope_and_settings_remain_required(owner, denial):
    o = owner
    o.configure()
    o.tasks([{"id": "todo_new", "text": "A source-owned decision"}])
    if denial == "wrong_app":
        o.provider.profile_apps["steward-app"] = "cli_foreign"
    elif denial == "wrong_owner":
        o.provider.owner_ready = False
    elif denial == "source_changed":
        o.provider.messages[o.event["message_id"]]["chat_id"] = "oc_foreign"
    elif denial == "revoked":
        o.transport.bindings.disconnect(o.binding["binding_id"], expected_revision=o.transport.bindings.read()["revision"])
    elif denial == "disabled":
        o.configure(False)
    else:
        o.runtime.registry_path.write_text("not valid JSON")
    before = len(o.provider.writes)
    o.attention.reconcile()
    assert len(o.provider.writes) == before
    assert "private credential" not in json.dumps(o.attention.health(o.binding["binding_id"]))


def test_withdrawal_disablement_and_new_goal_after_enable(owner):
    o = owner
    o.configure()
    o.tasks([{"id": "todo_new", "text": "New registered Goal needs an owner"}], goal="beta")
    o.configure(False)
    assert o.attention.reconcile() == 0
    o.configure(True)
    path = o.attention._path(o.binding["binding_id"])
    assert len(o.attention._read(path)["outbox"]) == 1
    o.tasks([{"id": "todo_new", "text": "New registered Goal needs an owner", "done": True}], goal="beta")
    before = len(o.provider.writes)
    assert o.attention.reconcile() == 0 and len(o.provider.writes) == before
    assert receipt(o)["status"] == "superseded"


def test_real_settings_endpoint_cas_and_disablement_without_provider(owner):
    o = owner
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller, server.verbose = o.store, o.runtime, False
    server.lark_private_conversations = o.transport
    server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=lambda: {}, close=lambda: None)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    def post(body):
        c = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            c.request("POST", "/api/chat/lark/private-conversations/owner-notifications", json.dumps(body), {"Content-Type": "application/json"})
            response = c.getresponse()
            return response.status, json.loads(response.read())
        finally:
            c.close()
    body = {"binding_id": o.binding["binding_id"], "binding_revision": o.transport.bindings.read()["revision"], "revision": 0, "enabled": True}
    try:
        code, payload = post(body)
        assert code == 200
        row = next(row for row in payload["connections"] if row["binding_id"] == o.binding["binding_id"])
        assert row["owner_attention"]["enabled"] and row["owner_attention"]["revision"] == 1
        assert post(body)[0] == 400, "stale settings cannot overwrite a newer choice"
        assert post({**body, "revision": 1, "chat_id": "oc_foreign"})[0] == 400
        assert post({**body, "binding_id": o.transport._binding("notes-app")["binding_id"]})[0] == 400
        o.provider.owner_ready = False
        assert post({**body, "revision": 1, "enabled": False})[0] == 200, "local disable does not depend on network auth"
        assert not o.attention.health(o.binding["binding_id"])["enabled"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.parametrize("provider_kind", ["file", "sqlite"])
def test_discovery_uses_promoted_native_authority_not_stale_markdown(owner, tmp_path, monkeypatch, provider_kind):
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    o = owner
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    o.configure()
    o.tasks([{"id": "todo_stale", "text": "Stale markdown must not notify"}])
    todos = [{"schema_version": "todo_item_v0", "todo_id": "todo_native", "role": "user", "status": "open",
              "done": False, "text": "Current canonical owner decision", "task_class": "user_gate",
              "archive_state": "active", "source_section": "User Todo", "index": 1}]
    projection = build_todo_runtime_shadow_projection(goal_id="alpha", todos=todos, handoff_mode="soft_claim")
    initialize_canonical_authority(o.runtime.coordination_runtime_root, "alpha", projection,
                                   state_path=tmp_path / "alpha.md", provider=provider_kind)
    before = len(o.provider.writes)
    assert o.attention.reconcile() == 1
    assert len(o.provider.writes) == before + 1
    assert "Current canonical owner decision" in o.provider.writes[-1][1]
    assert "Stale markdown" not in o.provider.writes[-1][1]


@pytest.mark.parametrize("mismatch", ["app", "chat", "text"])
def test_independent_readback_cannot_borrow_neighbor_identity_or_body(owner, mismatch):
    o = owner
    o.configure()
    o.tasks([{"id": "todo_new", "text": "Verify the exact delivered message"}])
    original = o.transport.runner
    def runner(args, cwd=None, timeout=None):
        result = original(args, cwd, timeout)
        if "+messages-mget" in args and args[args.index("--message-ids") + 1].startswith("om_out_"):
            payload = json.loads(result["stdout"])
            message = payload["data"]["items"][0]
            if mismatch == "app":
                message["sender"] = {"sender_type": "app", "id": "cli_neighbor"}
            elif mismatch == "chat":
                message["chat_id"] = "oc_neighbor"
            else:
                message["body"] = {"content": json.dumps({"text": "Only part of the answer"})}
            result = {**result, "stdout": json.dumps(payload)}
        return result
    o.transport.runner = runner
    before = len(o.provider.writes)
    assert o.attention.reconcile() == 0
    assert o.attention.health(o.binding["binding_id"])["unconfirmed_count"] == 1
    assert len(o.provider.writes) == before + 1
