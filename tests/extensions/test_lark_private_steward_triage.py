"""Isolated private Lark ingress with real bound Core and synthetic provider wire."""
from __future__ import annotations

import json
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


def test_direct_private_queries_read_the_real_canonical_provider(steward, monkeypatch):  # noqa: F811
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
    event = provider.event("steward-app", "canonical-gate", "/gates")
    assert transport.admit("steward-app", event)["status"] == "triage_queued"
    assert transport.reconcile() == 1
    result = json.loads(provider.messages[writes[-1]]["body"]["content"])
    assert result["header"]["title"]["content"] == "待我处理"
    assert "Decide whether to accept the prepared report" in json.dumps(result, ensure_ascii=False)
    assert "全部正常" not in json.dumps(result, ensure_ascii=False)
    assert not store.list_sessions() and not capture.exists()
    assert not (transport.core.root / f"{identity_ref(binding['provider_ref'], event['message_id'])}.json").exists()
