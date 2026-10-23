"""Automatic owner notices through native sources and existing runtime workers."""
import http.client
import json
import subprocess
import threading
import time
from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.extensions.lark.private_conversations import LarkPrivateConversations
from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService


def receipt(o):
    return json.loads(next((o.attention.root / "receipts" / o.binding["binding_id"]).glob("*.json")).read_text())


def publish_tasks(runtime, path, rows, *, goal, provider="file", initial=True):
    """Author disposable native sources; Markdown alone is not authority."""
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    todos = [{"schema_version": "todo_item_v0", "todo_id": row["id"], "role": "user",
              "status": "done" if row.get("done") else "open", "done": bool(row.get("done")),
              "text": row["text"], "task_class": "user_action", "archive_state": "active",
              "source_section": "User Todo", "index": i + 1} for i, row in enumerate(rows)]
    projection = build_todo_runtime_shadow_projection(goal_id=goal, todos=todos, handoff_mode="soft_claim")
    if initial:
        return initialize_canonical_authority(runtime.coordination_runtime_root, goal, projection,
                                              state_path=path, provider=provider)
    module = Path(__file__).parents[1] / "loopx/control_plane/coordination/local_authority_provider.ts"
    script = (f"import {{openLocalAuthorityStore}} from {json.dumps(module.as_uri())};"
        "let input='';for await(const chunk of process.stdin)input+=chunk;const request=JSON.parse(input);"
        "const store=await openLocalAuthorityStore(request.root,request.goal);const old=await store.loadAuthority();"
        "if(old.status!=='loaded')throw new Error(JSON.stringify(old));"
        "const result=await store.commitAuthority({expected_provider_revision:old.provider_revision,"
        "operation_id:request.operation_id,events:[],next_projection:request.projection,receipts:[]});"
        "process.stdout.write(JSON.stringify(result));")
    result = subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
        input=json.dumps({"root": str(runtime.coordination_runtime_root), "goal": goal,
                          "projection": projection, "operation_id": "owner-fixture-" + uuid4().hex}),
        capture_output=True, text=True, timeout=45)
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "applied", result.stderr or result.stdout


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
    published = set()
    def tasks(rows, *, goal="alpha", provider="file", publish=True):
        path = tmp_path / f"{goal}.md"
        path.write_text("# Goal\n\n## User Todo\n\n" + "".join(
            f"- [{'x' if row.get('done') else ' '}] {row['text']}\n"
            f"  <!-- loopx:todo todo_id={row['id']} status={'done' if row.get('done') else 'open'} task_class=user_action -->\n"
            for row in rows) + "\n## Agent Todo\n")
        data = json.loads(registry.read_text())
        data["goals"] = [row for row in data["goals"] if row["id"] != goal] + [
            {"id": goal, "repo": str(tmp_path), "state_file": path.name}]
        registry.write_text(json.dumps(data))
        if publish:
            publish_tasks(runtime, path, rows, goal=goal, provider=provider, initial=goal not in published)
            published.add(goal)
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
    o.tasks([{"id": "todo_stale", "text": "Stale markdown must not notify"}], publish=False)
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


@pytest.mark.parametrize("provider_kind", ["file", "sqlite"])
@pytest.mark.parametrize("failure", ["unmigrated", "read_error", "incomplete", "detail_race"])
def test_native_source_failure_isolated_and_recovered_without_markdown_fallback(
        owner, tmp_path, monkeypatch, provider_kind, failure):
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.extensions.lark import owner_attention as module
    o = owner
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    o.configure()
    o.tasks([{"id": "todo_stale", "text": "Stale projection is never a notice source"}], publish=False)
    o.tasks([{"id": "todo_beta", "text": "Recovered source decision"}], goal="beta", provider=provider_kind,
            publish=failure != "unmigrated")
    projection = build_todo_runtime_shadow_projection(goal_id="alpha", handoff_mode="soft_claim", todos=[{
        "schema_version": "todo_item_v0", "todo_id": "todo_native", "role": "user", "status": "open",
        "done": False, "text": "Healthy canonical owner decision", "task_class": "user_gate",
        "archive_state": "active", "source_section": "User Todo", "index": 1}])
    initialize_canonical_authority(o.runtime.coordination_runtime_root, "alpha", projection,
                                   state_path=tmp_path / "alpha.md", provider=provider_kind)
    native = module.list_goal_todos
    def read(**args):
        if args["goal_id"] == "beta":
            if failure == "read_error":
                raise OSError("private source details")
            result = native(**args)
            if failure == "incomplete":
                return {**result, "todo_count": result.get("todo_count", 0) + 1}
            if args.get("todo_id"):
                return {**result, "matched": False}
            return result
        return native(**args)
    monkeypatch.setattr(module, "list_goal_todos", read)
    before = len(o.provider.writes)
    assert o.attention.reconcile() == 1
    assert len(o.provider.writes) == before + 1
    assert "Healthy canonical" in o.provider.writes[-1][1]
    assert "Stale projection" not in o.provider.writes[-1][1]
    health = o.attention.health(o.binding["binding_id"])
    assert health["error_code"] == "source_incomplete" and health["last_checked_at"] is not None
    assert health["source_coverage"] == {"goal_count": 2, "read_goal_count": 1, "unavailable_goal_count": 1}
    assert "private source" not in json.dumps(health)
    monkeypatch.setattr(module, "list_goal_todos", native)
    if failure == "unmigrated":
        o.tasks([{"id": "todo_beta", "text": "Recovered source decision"}], goal="beta", provider=provider_kind)
    assert o.attention.reconcile() == 1
    assert len(o.provider.writes) == before + 2 and "Recovered source" in o.provider.writes[-1][1]
    assert o.attention.health(o.binding["binding_id"])["error_code"] is None
    assert o.attention.reconcile() == 0 and len(o.provider.writes) == before + 2
    assert not o.capture.exists() and not o.store.list_sessions()


def test_missing_pending_items_do_not_take_healthy_delivery_slots_or_lose_original_identity(owner, monkeypatch):
    from loopx.extensions.lark import owner_attention as module
    o = owner
    o.configure()
    o.tasks([{"id": f"todo_retained_{i}", "text": f"Retained decision {i}"} for i in range(3)])
    assert len(o.attention._goal_todos("alpha")) == 3
    path = o.attention._path(o.binding["binding_id"])
    pending = o.attention._plan(o.attention._read(path), o.attention._snapshot(o.binding))
    o.attention._write(path, pending)
    original_ids = [row["id"] for row in pending["outbox"]]
    o.tasks([{"id": "todo_healthy", "text": "Healthy task behind the unavailable queue"}], goal="beta")
    native = module.list_goal_todos
    def read(**args):
        if args["goal_id"] == "alpha":
            raise OSError("unavailable")
        return native(**args)
    monkeypatch.setattr(module, "list_goal_todos", read)
    before = len(o.provider.writes)
    assert o.attention.reconcile() == 1
    assert len(o.provider.writes) == before + 1 and "Healthy task" in o.provider.writes[-1][1]
    held = o.attention._read(path)["outbox"]
    assert [row["id"] for row in held] == original_ids
    assert all(row["status"] == "pending" and not row["source_current"] for row in held)
    monkeypatch.setattr(module, "list_goal_todos", native)
    assert o.attention.reconcile() == 2 and o.attention.reconcile() == 1
    assert o.attention.reconcile() == 0 and len(o.provider.writes) == before + 4
    assert all(json.loads((path.parent / "receipts" / path.stem / f"{id}.json").read_text())["status"] == "delivered"
               for id in original_ids)


def test_first_enable_missing_source_baselines_later_recovery_across_restart(owner, monkeypatch):
    from loopx.extensions.lark import owner_attention as module
    o = owner
    o.tasks([{"id": "todo_history", "text": "Historical decision"}])
    native = module.list_goal_todos
    def unavailable(**_):
        raise OSError("unavailable")
    monkeypatch.setattr(module, "list_goal_todos", unavailable)
    o.configure()
    assert o.attention.health(o.binding["binding_id"])["source_coverage"]["unavailable_goal_count"] == 1
    restarted = LarkPrivateConversations(controller=o.runtime, runtime_root=o.store.root.parent,
                                         runner=o.provider, cli_bin="lark-cli").owner_attention
    monkeypatch.setattr(module, "list_goal_todos", native)
    before = len(o.provider.writes)
    assert restarted.reconcile() == 0 and len(o.provider.writes) == before
    o.tasks([{"id": "todo_history", "text": "Historical decision"}, {"id": "todo_new", "text": "New decision"}])
    assert restarted.reconcile() == 1 and len(o.provider.writes) == before + 1
    assert "New decision" in o.provider.writes[-1][1]


def test_partial_source_worker_recovers_unknown_receipt_without_duplicate_send(owner, monkeypatch):
    from loopx.extensions.lark import owner_attention as module
    o = owner
    o.configure()
    o.tasks([{"id": "todo_unknown", "text": "Original uncertain notification"}])
    before = len(o.provider.writes)
    o.provider.verify_replies = False
    assert o.attention.reconcile() == 0
    path = o.attention._path(o.binding["binding_id"])
    unknown = o.attention._read(path)["outbox"][0]
    native = module.list_goal_todos
    def read(**args):
        if args["goal_id"] == "alpha":
            raise OSError("source temporarily unavailable")
        return native(**args)
    monkeypatch.setattr(module, "list_goal_todos", read)
    o.provider.verify_replies = True
    state = o.attention._read(path)
    state["outbox"][0]["next_attempt_at"] = 0
    o.attention._write(path, state)
    o.tasks([{"id": "todo_healthy", "text": "Healthy independent notification"}], goal="beta")
    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=o.store.root.parent,
        runtime_controller=o.runtime, private_conversations=o.transport)
    worker = threading.Thread(target=service._reconcile_private, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if len(o.provider.writes) == before + 2 and o.attention.health(o.binding["binding_id"])["unconfirmed_count"] == 0:
                break
            time.sleep(.05)
        assert len(o.provider.writes) == before + 2, "the original worker makes healthy progress despite the gap"
    finally:
        service._closed.set()
        worker.join(10)
    saved = json.loads((path.parent / "receipts" / path.stem / f"{unknown['id']}.json").read_text())
    assert saved["status"] == "delivered" and saved["id"] == unknown["id"]
    assert saved["intent"] == unknown["intent"] and saved["message_id"] == unknown["message_id"]
    assert o.attention.health(o.binding["binding_id"])["source_coverage"]["unavailable_goal_count"] == 1
    monkeypatch.setattr(module, "list_goal_todos", native)
    restarted = LarkPrivateConversations(controller=o.runtime, runtime_root=o.store.root.parent,
                                         runner=o.provider, cli_bin="lark-cli").owner_attention
    assert restarted.reconcile() == 0 and len(o.provider.writes) == before + 2


@pytest.mark.parametrize("change", ["withdrawn", "terms_changed"])
def test_readback_of_frozen_send_does_not_require_current_todo_terms(owner, change):
    o = owner
    o.configure()
    o.tasks([{"id": "todo_notice", "text": "Original owner decision"}])
    o.provider.verify_replies = False
    before = len(o.provider.writes)
    assert o.attention.reconcile() == 0
    path = o.attention._path(o.binding["binding_id"])
    state = o.attention._read(path)
    unknown = state["outbox"][0]
    unknown["next_attempt_at"] = 0
    o.attention._write(path, state)
    o.tasks([{"id": "todo_notice", "text": "Changed owner decision" if change == "terms_changed"
              else "Original owner decision", "done": change == "withdrawn"}])
    o.provider.verify_replies = True
    restarted = LarkPrivateConversations(controller=o.runtime, runtime_root=o.store.root.parent,
                                         runner=o.provider, cli_bin="lark-cli").owner_attention
    assert restarted.reconcile() == (2 if change == "terms_changed" else 1)
    saved = json.loads((path.parent / "receipts" / path.stem / f"{unknown['id']}.json").read_text())
    assert saved["status"] == "delivered" and saved["message_id"] == unknown["message_id"]
    assert saved["intent"] == unknown["intent"]
    assert restarted.reconcile() == 0
    assert len(o.provider.writes) == before + (2 if change == "terms_changed" else 1)
