"""Isolated private Lark ingress with real bound Core and synthetic provider wire."""
from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from pathlib import Path

import pytest

from test_native_steward_private import finish, steward  # noqa: F401

from loopx.capabilities.native_chat import steward_triage
from loopx.extensions.lark import private_steward_triage
from loopx.extensions.lark.conversation_identity import identity_ref
from loopx.extensions.lark.goal_topic_runtime import stream_lark_goal_topic_profile
from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService
from loopx.file_lock import try_exclusive_file_lock


def fixture_card_provider(provider, transport, monkeypatch):
    """Model a Bot send UUID and exact provider message readback at the CLI seam."""
    deliveries = {}
    sends = []
    lost = [False]

    def run(args, cwd=None, timeout=None):
        if "+messages-send" not in args or "--msg-type" not in args:
            return provider(args, cwd, timeout)
        key = args[args.index("--idempotency-key") + 1]
        card = json.loads(args[args.index("--content") + 1])
        chat = args[args.index("--chat-id") + 1]
        profile = args[args.index("--profile") + 1]
        assert args[args.index("--msg-type") + 1] == "interactive" and chat == "oc_steward_app"
        if key not in deliveries:
            message_id = f"om_card_{len(deliveries)}"
            deliveries[key] = message_id
            sends.append(message_id)
            provider.messages[message_id] = {
                "message_id": message_id, "chat_id": chat, "msg_type": "interactive",
                "sender": {"id": provider.profile_apps[profile], "sender_type": "app"},
                "body": {"content": json.dumps(card, ensure_ascii=False)},
                "create_time": str(int(time.time() * 1000)),
            }
            if lost[0]:
                lost[0] = False
                # The child ran and the result was lost after committing the write.
                import subprocess
                raise subprocess.TimeoutExpired(args, timeout)
        return {"returncode": 0, "stdout": json.dumps({"ok": True, "data": {"message_id": deliveries[key]}})}

    monkeypatch.setattr(transport, "runner", run)
    return sends, lost


def buttons(card):
    return {button["text"]["content"]: button["value"]
            for row in card["elements"] if row["tag"] == "action" for button in row["actions"]}


def event_for(message_id, action, *, suffix="1", **changes):
    return {"type": "card.action.trigger", "host": "im_message", "action_tag": "button",
            "message_id": message_id, "chat_id": "oc_steward_app", "operator_id": "ou_steward_app",
            "app_id": "cli_steward_app", "event_id": f"triage-callback-{suffix}",
            "timestamp": str(int(time.time() * 1000)), "action_value": action, **changes}


def test_menu_and_callbacks_use_private_ingress_without_a_model_turn(steward, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, binding, capture, _ = steward
    registry = json.loads(runtime.registry_path.read_text())
    registry["goals"] = [{"id": "demo-a"}, {"id": "demo-b"}]
    runtime.registry_path.write_text(json.dumps(registry))
    profile = "steward-app"
    transport.steward_callback_readiness[(profile, provider.profile_apps[profile])] = lambda: True
    event = provider.event(profile, "before-menu", "Ordinary text still goes to the original agent.")
    assert transport.admit(profile, event)["status"] == "durably_accepted"
    original = transport.core.read_request(identity_ref(binding["provider_ref"], event["message_id"]))
    assert finish(runtime, original)["status"] == "completed"
    assert transport.reconcile() == 1
    before = capture.read_bytes(), store.messages(original["session_id"])
    native_refs = set(transport.core.root.glob("*.json"))
    writes, lost = fixture_card_provider(provider, transport, monkeypatch)
    reads = []
    unavailable = {"demo-a", "demo-b"}

    def todos(*, goal_id, **_):
        reads.append(goal_id)
        if goal_id in unavailable:
            raise ValueError("synthetic private error /Users/private/not-for-cards")
        return {"ok": True, "todos": []}

    monkeypatch.setattr(steward_triage, "list_goal_todos", todos)
    event = provider.event(profile, "menu", "/triage")
    assert transport.admit(profile, event)["status"] == "triage_queued"
    assert not reads and not writes
    menu_path = transport.pending_body_paths()[0]
    lost[0] = True
    assert transport.reconcile_request(menu_path) == 0
    assert transport.reconcile_request(menu_path) == 1
    assert transport.reconcile_request(menu_path) == 0
    assert len(writes) == 1 and not reads
    menu_id = writes[-1]
    menu = json.loads(provider.messages[menu_id]["body"]["content"])
    assert set(buttons(menu)) == {"待我处理", "任务速览", "总览"}
    assert menu["config"]["enable_forward"] is False

    service = LarkGoalTopicRuntimeService(
        snapshot_provider=lambda: {"private_profiles": transport.profiles()}, runtime_root=transport.runtime_root,
        runtime_controller=runtime, private_conversations=transport,
    )

    def click(message_id, label, suffix, *, run_worker=True):
        card = json.loads(provider.messages[message_id]["body"]["content"])
        action = buttons(card)[label]
        event = event_for(message_id, action, suffix=suffix)
        result = service._handle_private_steward_callback(profile, event, action)
        assert result["ok"]
        if run_worker:
            assert transport.reconcile() == 1
        return event

    emitted = click(menu_id, "待我处理", "query")
    assert reads == ["demo-a", "demo-b"]
    first = writes[-1]
    failure = json.loads(provider.messages[first]["body"]["content"])
    assert failure["header"]["template"] == "orange"
    assert "暂时读不到" in json.dumps(failure, ensure_ascii=False)
    assert "/Users/" not in json.dumps(failure)
    replay = {**emitted, "timestamp": str(int(emitted["timestamp"]) + 1)}
    assert service._handle_private_steward_callback(profile, replay, emitted["action_value"])["status"] == (
        "triage_callback_already_captured"
    )
    assert transport.reconcile() == 0 and writes[-1] == first

    unavailable.remove("demo-a")
    click(first, "刷新", "partial")
    partial = json.loads(provider.messages[writes[-1]]["body"]["content"])
    assert partial["header"]["template"] == "orange"
    assert "1 个项目暂时读不到" in json.dumps(partial, ensure_ascii=False)
    assert "全部正常" not in json.dumps(partial, ensure_ascii=False)
    reads_before_picker = list(reads)
    click(writes[-1], "选择项目", "picker")
    assert reads == reads_before_picker
    picker_id = writes[-1]
    picker = json.loads(provider.messages[picker_id]["body"]["content"])
    assert {"demo-a", "demo-b"} <= set(buttons(picker))
    click(picker_id, "demo-a", "scoped")
    assert reads == [*reads_before_picker, "demo-a"]
    recovered = json.loads(provider.messages[writes[-1]]["body"]["content"])
    assert recovered["header"]["template"] == "green"
    assert "全部正常" in json.dumps(recovered, ensure_ascii=False)
    click(writes[-1], "返回菜单", "back")
    assert reads == [*reads_before_picker, "demo-a"]
    assert set(buttons(json.loads(provider.messages[writes[-1]]["body"]["content"]))) == set(buttons(menu))
    direct = provider.event(profile, "direct", "/todos --goal demo-a")
    assert transport.admit(profile, direct)["status"] == "triage_queued"
    assert transport.reconcile() == 1
    assert reads[-1] == "demo-a"
    direct_card = json.loads(provider.messages[writes[-1]]["body"]["content"])
    assert direct_card["header"]["title"]["content"] == "任务速览"
    assert (capture.read_bytes(), store.messages(original["session_id"])) == before
    assert set(transport.core.root.glob("*.json")) == native_refs and not store.queued_turns(original["session_id"])

    action = buttons(picker)["demo-a"]
    current = event_for(picker_id, action, suffix="forged")
    sends_before_denials = list(writes)
    for bad in [
        {**current, "chat_id": "oc_forwarded"},
        {**current, "operator_id": "ou_other"},
        {**current, "app_id": "cli_another"},
        {**current, "message_id": menu_id},
        {**current, "timestamp": str(int((time.time() - 172800) * 1000))},
        {**current, "action_value": {**action, "goal": "unapproved"}},
    ]:
        with pytest.raises(ValueError):
            service._handle_private_steward_callback(profile, bad, bad["action_value"])
    assert transport.reconcile() == 0
    assert writes == sends_before_denials

    # The grant changes after a callback was captured, before the body worker
    # sees it. Neither this queued copy nor a replay can disclose old scope.
    click(picker_id, "demo-a", "revoked-in-flight", run_worker=False)
    snapshot = transport.bindings.read()
    transport.bindings.disconnect(binding["binding_id"], expected_revision=snapshot["revision"])
    with pytest.raises(ValueError):
        service._handle_private_steward_callback(profile, current, action)
    assert transport.reconcile() == 0 and writes == sends_before_denials
    assert (capture.read_bytes(), store.messages(original["session_id"])) == before


def test_private_callback_consumer_dispatches_the_steward_action_on_its_original_stream(tmp_path: Path):
    seen = threading.Event()
    observed = []
    action = {"schema_version": private_steward_triage.TRIAGE_CARD_ACTION, "operation": "menu"}
    event = event_for("om_fixture", json.dumps(action))
    snapshot = {"private_profiles": {"steward-app": {
        "cli_bin": "fixture-cli", "audience": "owner", "context_kind": "steward",
    }}}

    class Consumer:
        def __init__(self, stdout):
            self.stdout = stdout

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def handler(payload):
        observed.append(payload)
        seen.set()
        return {"ok": True}

    def messages():
        yield "[event] ready event_key=im.message.receive_v1\n"
        assert seen.wait(2)
        yield "[event] exited — received 0 event(s) in 1s (reason: timeout)\n"

    def process_factory(argv):
        if "card.action.trigger" in argv:
            assert private_steward_triage.TRIAGE_CARD_ACTION in argv[argv.index("--jq") + 1]
            return Consumer(iter([json.dumps(event)]))
        return Consumer(messages())

    result = stream_lark_goal_topic_profile(
        profile="steward-app", snapshot_provider=lambda: snapshot, stop=threading.Event(),
        runtime_root=tmp_path, answer=lambda *_: pytest.fail("ordinary turn started"),
        process_factory=process_factory, review_callback_handler=handler,
    )
    assert result["ok"] is True
    assert observed == [{**event, "action_value": action}]


@pytest.mark.parametrize("authority_provider", ["file", "sqlite"])
def test_triage_reads_declared_source_before_shared_runtime(steward, monkeypatch, authority_provider):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "control_plane"))
    from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.testing.canary_harness import write_fixture_registry

    store, runtime, provider, transport, binding, capture, workspace = steward
    isolate_sqlite_runtime(workspace.parent, monkeypatch)
    shared_root = runtime.coordination_runtime_root
    source_root, source_registry = workspace / "source-runtime", workspace / "registry.json"
    state = workspace / "STATE.md"
    state.write_text("# Source Goal\n\n## User Todo\n\n## Agent Todo\n")
    write_fixture_registry(
        project=workspace, runtime_root=source_root, registry_path=source_registry,
        goal_id="demo-a", domain="fixture", adapter_kind="generic_project_goal_v0", state_file=str(state),
    )
    global_registry = json.loads(source_registry.read_text())
    global_registry.update(registry_role="global-local", common_runtime_root=str(shared_root))
    source_goal = global_registry["goals"][0]
    global_registry["goals"] = [
        {**source_goal, "source_registry": str(source_registry)},
        {**source_goal, "id": "demo-empty"},
        {**source_goal, "id": "demo-unready"},
    ]
    runtime.registry_path.write_text(json.dumps(global_registry))

    gate = {
        "schema_version": "todo_item_v0", "todo_id": "todo_source_report", "role": "user", "status": "open",
        "text": "Review the source report", "done": False, "task_class": "user_gate",
        "index": 1, "source_section": "User Todo", "archive_state": "active",
    }
    for root, goal_id, todos in (
        (source_root, "demo-a", [gate]), (shared_root, "demo-a", []), (shared_root, "demo-empty", []),
    ):
        projection = build_todo_runtime_shadow_projection(
            goal_id=goal_id, handoff_mode="soft_claim", todos=todos,
        )
        initialize_canonical_authority(root, goal_id, projection, state_path=state, provider=authority_provider)
    for root, summary in ((source_root, "Source export verified"), (shared_root, "Stale shared progress")):
        index = root / "goals" / "demo-a" / "runs" / "index.jsonl"
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps({"summary": summary}) + "\n", encoding="utf-8")

    writes, _ = fixture_card_provider(provider, transport, monkeypatch)
    profile = "steward-app"
    transport.steward_callback_readiness[(profile, provider.profile_apps[profile])] = lambda: True
    service = LarkGoalTopicRuntimeService(
        snapshot_provider=lambda: {"private_profiles": transport.profiles()},
        runtime_root=transport.runtime_root, runtime_controller=runtime, private_conversations=transport,
    )

    def read_card():
        message_id = writes[-1]
        observed = provider.messages[message_id]
        assert observed["chat_id"] == "oc_steward_app"
        return message_id, json.loads(observed["body"]["content"])

    def send(name, text):
        event = provider.event(profile, name, text)
        assert transport.admit(profile, event)["status"] == "triage_queued"
        assert transport.reconcile() == 1
        return read_card()

    def require_source_result(card):
        contents = json.dumps(card, ensure_ascii=False)
        assert card["header"]["template"] == "orange"
        assert "Review the source report" in contents
        assert "Source export verified" in contents
        assert "Stale shared progress" not in contents
        assert "1 个项目暂时读不到" in contents and "全部正常" not in contents

    try:
        _, text_card = send("source-text", "/triage all")
        require_source_result(text_card)
        menu_id, menu = send("source-menu", "/triage")
        action = buttons(menu)["总览"]
        callback = event_for(menu_id, action, suffix="source")
        assert service._handle_private_steward_callback(profile, callback, action)["status"] == "triage_callback_queued"
        assert transport.reconcile() == 1
        require_source_result(read_card()[1])
        assert service._handle_private_steward_callback(profile, callback, action)["status"] == "triage_callback_already_captured"
        assert transport.reconcile() == 0

        _, unavailable = send("unpromoted-goal", "/triage all --goal demo-unready")
        assert unavailable["header"]["template"] == "orange"
        assert "暂时读不到项目状态" in json.dumps(unavailable, ensure_ascii=False)
        _, empty = send("verified-empty", "/triage all --goal demo-empty")
        assert empty["header"]["template"] == "green"
        assert "全部正常" in json.dumps(empty, ensure_ascii=False)

        source_registry.rename(source_registry.with_suffix(".offline"))
        _, disconnected = send("missing-source", "/triage all --goal demo-a")
        contents = json.dumps(disconnected, ensure_ascii=False)
        assert disconnected["header"]["template"] == "orange"
        assert "暂时读不到项目状态" in contents
        assert "Stale shared progress" not in contents and "全部正常" not in contents
    finally:
        service.close()
    assert not store.list_sessions() and not capture.exists()
    assert not list(transport.core.root.glob("*.json"))


@pytest.mark.parametrize("shared_app", [False, True])
def test_private_callback_recovers_without_restarting_messages(steward, monkeypatch, shared_app):  # noqa: F811
    from loopx.extensions.lark import goal_topic_runtime
    from extensions.test_lark_goal_topic_runtime import _seed_legacy_topic
    from loopx.extensions.lark.goal_channel_contracts import read_goal_channel_binding
    from loopx.extensions.lark.goal_channel_targets import read_goal_channel_targets

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "control_plane"))
    from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.testing.canary_harness import write_fixture_registry

    store, runtime, provider, transport, binding, capture, workspace = steward
    isolate_sqlite_runtime(workspace.parent, monkeypatch)
    authority = runtime.coordination_runtime_root
    state = workspace / "STATE.md"
    state.write_text("# Synthetic Goal\n\n## User Todo\n\n## Agent Todo\n")
    write_fixture_registry(
        project=workspace, runtime_root=authority, registry_path=runtime.registry_path,
        goal_id="demo-a", domain="fixture", adapter_kind="generic_project_goal_v0", state_file=str(state),
    )
    seed = build_todo_runtime_shadow_projection(goal_id="demo-a", handoff_mode="soft_claim", todos=[{
        "schema_version": "todo_item_v0", "todo_id": "todo_demo_report", "role": "user", "status": "open",
        "text": "Decide whether to accept the prepared report", "done": False, "task_class": "user_gate",
        "index": 1, "source_section": "User Todo", "archive_state": "active",
    }])
    initialize_canonical_authority(authority, "demo-a", seed, state_path=state, provider="sqlite")
    writes, _ = fixture_card_provider(provider, transport, monkeypatch)
    profile = "steward-app"
    app_id = provider.profile_apps[profile]
    ready_key = profile, app_id
    app_lease = hashlib.sha256(app_id.encode()).hexdigest()[:32]
    lock_path = workspace.parent / "lark-consumers" / app_lease
    profiles = transport.profiles()
    snapshot = {"private_profiles": profiles}
    if shared_app:
        targets_path = workspace / "targets.json"
        binding_path = workspace / "group-binding.json"
        _seed_legacy_topic(targets_path, binding_path)
        targets = read_goal_channel_targets(targets_path)
        targets["targets"]["fixture"]["identity"].update(sender_profile=profile, bot_app_id=app_id)
        snapshot.update(target_payload=targets, binding_payloads={"goal-alpha": read_goal_channel_binding(binding_path)})
    service = LarkGoalTopicRuntimeService(
        snapshot_provider=lambda: snapshot, runtime_root=transport.runtime_root,
        runtime_controller=runtime, private_conversations=transport, action_service=object() if shared_app else None,
    )

    class Consumer:
        def __init__(self, event_key):
            self.event_key = event_key
            self.lines = queue.Queue()
            self.ended = threading.Event()
            self.stdout = iter(self.lines.get, None)

        def emit(self, payload):
            self.lines.put(json.dumps(payload) + "\n")

        def ready(self):
            self.lines.put(f"[event] ready event_key={self.event_key}\n")

        def poll(self):
            return 0 if self.ended.is_set() else None

        def wait(self, timeout=None):
            assert self.ended.wait(timeout)
            return 0

        def terminate(self):
            self.ended.set()
            self.lines.put(None)

    messages = Consumer("im.message.receive_v1")
    children = []
    failed_spawn = threading.Event()
    callbacks = queue.Queue()

    def start(args):
        if messages.event_key in args:
            children.append(messages)
            return messages
        assert "card.action.trigger" in args
        if not failed_spawn.is_set():
            failed_spawn.set()
            raise OSError("callback consumer could not start")
        child = Consumer("card.action.trigger")
        children.append(child)
        callbacks.put(child)
        return child

    def eventually(predicate):
        deadline = time.monotonic() + 8
        while not predicate():
            assert time.monotonic() < deadline
            threading.Event().wait(0.01)

    def available():
        probe = transport.steward_callback_readiness.get(ready_key)
        return probe is not None and probe()

    def request(name, command):
        previous = len(writes)
        event = provider.event(profile, name, command)
        messages.emit(event)
        path = transport.root / f"{identity_ref(binding['provider_ref'], event['message_id'])}.json"
        eventually(lambda: path.exists() and json.loads(path.read_text()).get("status") == "delivered")
        assert len(writes) == previous + 1
        message_id = writes[-1]
        assert provider.messages[message_id]["chat_id"] == event["chat_id"]
        return json.loads(provider.messages[message_id]["body"]["content"]), message_id

    def assert_gate(card):
        assert card["header"]["title"]["content"] == "待我处理"
        assert "Decide whether to accept the prepared report" in json.dumps(card, ensure_ascii=False)
        assert "全部正常" not in json.dumps(card, ensure_ascii=False)

    def isolate_consumer_lock(path, **kwargs):
        assert path.name == app_lease
        return try_exclusive_file_lock(lock_path, **kwargs)

    monkeypatch.setattr(goal_topic_runtime, "_default_process_factory", start)
    monkeypatch.setattr("loopx.extensions.lark.goal_topic_runtime_service.try_exclusive_file_lock", isolate_consumer_lock)
    service.start()
    try:
        messages.ready()
        assert failed_spawn.wait(5)
        first = callbacks.get(timeout=5)
        menu, _ = request("before-callback-ready", "/triage")
        assert not buttons(menu)
        assert all(cmd in json.dumps(menu) for cmd in ("/gates", "/todos", "/triage all"))
        first.ready()
        eventually(available)
        eventually(lambda: service.health_snapshot()[profile].get("callback_error_code") is None)
        menu, _ = request("live-menu", "/triage")
        assert set(buttons(menu)) == {"待我处理", "任务速览", "总览"}
        first.terminate()
        assert not available()
        eventually(lambda: service.health_snapshot()[profile].get("callback_error_code")
                   == "lark_review_callback_source_disconnected")
        assert service.health_snapshot()[profile]["status"] == "listening"
        fallback, _ = request("callback-outage", "/triage")
        assert not buttons(fallback)
        result, _ = request("canonical-gate", "/gates")
        assert_gate(result)
        assert not buttons(result)
        index_path = authority / "goals" / "demo-a" / "runs" / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_bytes(
            b'{"generated_at":"2026-01-01T00:00:00Z","summary":"Validated report export"}\n'
            b'{"summary":"Partial final row \xe7\x9b\n'
        )
        partial, _ = request("torn-history", "/triage all")
        assert "Decide whether to accept the prepared report" in json.dumps(partial, ensure_ascii=False)
        assert "近期进展暂未读全" in json.dumps(partial, ensure_ascii=False)
        assert "Validated report export" in json.dumps(partial, ensure_ascii=False)
        assert "Partial final row" not in json.dumps(partial, ensure_ascii=False)
        assert partial["header"]["template"] == "orange"
        index_path.unlink()
        with try_exclusive_file_lock(lock_path, operation="competing_listener") as lease:
            assert lease is None

        second = callbacks.get(timeout=5)
        second.ready()
        eventually(available)
        menu, menu_id = request("recovered-menu", "/triage")
        action = buttons(menu)["总览"]
        callback = event_for(menu_id, action, suffix="recovered")
        index_path.touch()
        original_open = Path.open
        def deny_history(path, *args, **kwargs):
            if path == index_path:
                raise PermissionError("Synthetic run index access denied")
            return original_open(path, *args, **kwargs)

        with monkeypatch.context() as filesystem:
            filesystem.setattr(Path, "open", deny_history)
            second.emit(callback)
            ref = identity_ref(action["request"], callback["event_id"])
            path = transport.root / f"{ref}.json"
            eventually(lambda: path.exists() and json.loads(path.read_text()).get("status") == "delivered")
        result_id = writes[-1]
        assert provider.messages[result_id]["chat_id"] == callback["chat_id"]
        partial = json.loads(provider.messages[result_id]["body"]["content"])
        assert "Decide whether to accept the prepared report" in json.dumps(partial, ensure_ascii=False)
        assert "近期进展暂未读全" in json.dumps(partial, ensure_ascii=False)
        assert partial["header"]["template"] == "orange"
        assert children == [messages, first, second]
        assert messages.poll() is None
        health = service.health_snapshot()[profile]
        assert health["status"] == "listening" and health["restart_count"] == 0
        eventually(lambda: service.health_snapshot()[profile].get("callback_error_code") is None)
    finally:
        service.close()
    assert not transport.steward_callback_readiness
    assert all(child.poll() is not None for child in children)
    with try_exclusive_file_lock(lock_path, operation="next_listener") as lease:
        assert lease is not None
    assert not store.list_sessions() and not capture.exists()
    assert not list(transport.core.root.glob("*.json"))
