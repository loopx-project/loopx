"""Source loss is observable without granting historical execution authority."""
import json
import threading
from datetime import datetime
from types import SimpleNamespace

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401

from loopx.extensions.lark.private_ingress import PrivateIngressObserver


@pytest.fixture
def source_monitor(tmp_path, monkeypatch):
    root = tmp_path / "lark-private-deliveries"
    root.mkdir()
    binding = {"binding_id": "binding_public", "transport_ref": "owner-app", "enabled": True,
               "audience": "owner", "grant": "workspace_write"}
    carrier = {"profile": "owner-app", "binding_id": binding["binding_id"], "source": {"source_ref": "source"},
               "event": {"chat_type": "p2p", "chat_id": "oc_owner", "sender_id": "ou_owner",
                         "message_id": "om_known", "create_time": "1000000"}}
    (root / "known.json").write_text(json.dumps(carrier))
    observed = {"bot_app_id": "cli_owner", "bot_open_id": "ou_bot"}
    pages = [{"ok": True, "identity": "bot", "data": {"messages": [], "has_more": False}}]
    calls = []
    history_calls = []
    history_pages = [{"ok": True, "identity": "bot", "data": {"messages": [], "has_more": False}}]

    def run(args, _cwd=None, _timeout=None):
        assert args[:3] == ["lark-cli", "--profile", "owner-app"]
        assert "+chat-messages-list" in args and args[args.index("--chat-id") + 1] == "oc_owner"
        selected_calls, selected_pages = (calls, pages) if "--start" in args else (history_calls, history_pages)
        selected_calls.append(args)
        page = selected_pages[min(len(selected_calls) - 1, len(selected_pages) - 1)]
        if callable(page):
            page = page()
        return {"returncode": 0, "stdout": json.dumps(page), "stderr": ""}

    bindings = SimpleNamespace(read=lambda: {"bindings": [binding]},
        resolve=lambda **kw: {"binding": dict(binding), "observation": observed})
    transport = SimpleNamespace(root=root, bindings=bindings, runner=run, cli_bin="lark-cli",
        profiles=lambda: {"owner-app": {"binding_id": binding["binding_id"]}})
    monitor = PrivateIngressObserver(transport)
    clock = [1100.0]
    monkeypatch.setattr("loopx.extensions.lark.private_ingress.time.time", lambda: clock[0])
    return SimpleNamespace(root=root, carrier=carrier, binding=binding, monitor=monitor,
                           transport=transport, pages=pages, calls=calls, clock=clock,
                           history_calls=history_calls, history_pages=history_pages)


def owner_source(ref="om_missed", **extra):
    return {"message_id": ref, "chat_id": "oc_owner", "sender": {"id": "ou_owner", "sender_type": "user"},
            "content": "Synthetic original request", **extra}


def test_gap_requests_only_one_live_reconnect_and_never_executes_history(source_monitor):
    f = source_monitor
    f.pages[0]["data"]["messages"] = [owner_source()]
    assert f.monitor.observe() == ["owner-app"]
    assert f.monitor.health(f.binding["binding_id"]) == {
        "status": "gap", "last_checked_at": 1100.0, "last_complete_at": 1095.0,
        "source_gap_count": 1, "without_reply_count": 1}
    f.clock[0] += 60
    f.pages[0]["data"]["messages"] = []
    assert f.monitor.observe() == []
    assert f.monitor.health(f.binding["binding_id"])["source_gap_count"] == 1
    # There is no Core, sender or event consumer on this transport fixture.
    assert len(list(f.root.glob("*.json"))) == 1
    state = f.monitor._path(f.binding["binding_id"]).read_text()
    assert "Synthetic original request" not in state and "oc_owner" not in state and "ou_owner" not in state


def test_observed_reply_does_not_claim_native_capture_or_final_delivery(source_monitor):
    f = source_monitor
    f.pages[0]["data"]["messages"] = [owner_source(), {"message_id": "om_reply", "chat_id": "oc_owner",
        "sender": {"id": "cli_owner", "sender_type": "app"}, "reply_to": "om_missed", "content": "Partial acknowledgement"}]
    assert f.monitor.observe() == []
    health = f.monitor.health(f.binding["binding_id"])
    assert health["status"] == "gap" and health["source_gap_count"] == 1 and health["without_reply_count"] == 0
    assert "delivered" not in health


@pytest.mark.parametrize("change", ["foreign_chat", "foreign_owner", "foreign_app_reply", "deleted", "group", "other_provider"])
def test_source_and_audience_boundaries(source_monitor, change):
    f = source_monitor
    message = owner_source()
    if change == "foreign_chat":
        message["chat_id"] = "oc_elsewhere"
    elif change == "foreign_owner":
        message["sender"]["id"] = "ou_elsewhere"
    elif change == "deleted":
        message["deleted"] = True
    elif change == "group":
        f.binding["audience"] = "group"
    elif change == "other_provider":
        f.binding["transport_ref"] = "other-provider"
    f.pages[0]["data"]["messages"] = [message]
    if change == "foreign_app_reply":
        f.pages[0]["data"]["messages"].append({"message_id": "om_foreign_reply", "chat_id": "oc_owner",
            "sender": {"id": "cli_elsewhere", "sender_type": "app"}, "reply_to": "om_missed"})
    result = f.monitor.observe()
    health = f.monitor.health(f.binding["binding_id"])
    if change == "foreign_chat":
        assert result == [] and health["status"] == "unavailable" and health["last_complete_at"] is None
    elif change == "foreign_app_reply":
        assert result == ["owner-app"] and health["without_reply_count"] == 1
    else:
        assert result == [] and health["source_gap_count"] == 0
    if change in {"group", "other_provider"}:
        assert f.calls == []


def test_complete_pages_and_overlap_not_empty_queue_establish_coverage(source_monitor):
    f = source_monitor
    f.pages[:] = [{"ok": True, "identity": "bot", "data": {"messages": [], "has_more": True, "page_token": "next"}},
                 {"ok": True, "identity": "bot", "data": {"messages": [owner_source()], "has_more": False}}]
    assert f.monitor.observe() == ["owner-app"]
    assert f.calls[1][f.calls[1].index("--page-token") + 1] == "next"
    f.clock[0] += 60
    f.monitor.observe()
    assert "--start" in f.calls[-1] and f.monitor.health(f.binding["binding_id"])["last_complete_at"] == 1155.0


def test_first_source_window_covers_a_hole_before_the_latest_capture(source_monitor):
    f = source_monitor
    f.clock[0] = 200000.0
    f.carrier["event"]["create_time"] = str(int((f.clock[0] - 30000) * 1000))
    (f.root / "known.json").write_text(json.dumps(f.carrier))
    missed_at = f.clock[0] - 50000

    def page():
        args = f.calls[-1]
        start = datetime.fromisoformat(args[args.index("--start") + 1]).timestamp()
        assert start <= missed_at  # A later capture does not prove earlier receipt.
        return {"ok": True, "identity": "bot", "data": {"messages": [owner_source()], "has_more": False}}

    f.pages[:] = [page]
    assert f.monitor.observe() == ["owner-app"]
    assert f.monitor.health(f.binding["binding_id"])["source_gap_count"] == 1


@pytest.mark.parametrize("bad_page", [
    {"ok": False, "error": {"message": "private provider detail"}},
    {"ok": True, "identity": "user", "data": {"messages": [], "has_more": False}},
    {"ok": True, "data": {"messages": [], "has_more": True, "page_token": "loop"}},
])
def test_failed_or_incomplete_reads_preserve_previous_clock_and_gaps(source_monitor, bad_page):
    f = source_monitor
    f.pages[0]["data"]["messages"] = [owner_source()]
    f.monitor.observe()
    f.clock[0] += 60
    f.pages[:] = [bad_page]
    assert f.monitor.observe() == []
    health = f.monitor.health(f.binding["binding_id"])
    assert health["status"] == "unavailable" and health["last_complete_at"] == 1095.0
    assert health["source_gap_count"] == 1 and "private provider detail" not in json.dumps(health)


def test_live_capture_during_read_is_not_reported_missing(source_monitor):
    f = source_monitor

    def page():
        native = {**f.carrier, "event": {**f.carrier["event"], "message_id": "om_missed"}}
        (f.root / "concurrent.json").write_text(json.dumps(native))
        return {"ok": True, "data": {"messages": [owner_source()], "has_more": False}}

    f.pages[:] = [page]
    assert f.monitor.observe() == []
    assert f.monitor.health(f.binding["binding_id"])["status"] == "verified"


def test_binding_revoked_during_read_cannot_commit_coverage(source_monitor):
    f = source_monitor

    def page():
        f.binding["enabled"] = False
        return {"ok": True, "data": {"messages": [owner_source()], "has_more": False}}

    # A real reader returns fresh objects; do not share its mutable snapshot.
    f.transport.bindings.read = lambda: {"bindings": [dict(f.binding)]}
    f.pages[:] = [page]
    assert f.monitor.observe() == []
    assert f.monitor.health(f.binding["binding_id"])["last_complete_at"] is None


def test_stale_or_unreadable_health_is_not_verified(source_monitor):
    f = source_monitor
    f.monitor.observe()
    f.clock[0] += 181
    assert f.monitor.health(f.binding["binding_id"])["status"] == "unavailable"
    f.monitor._path(f.binding["binding_id"]).write_text("{broken")
    assert f.monitor.health(f.binding["binding_id"])["status"] == "unavailable"


class _ListenerProcess:
    """Yield fixture events and let the real service end the stream."""

    def __init__(self, events, *, hold_seconds=8):
        self.hold_seconds = hold_seconds
        self.exited = threading.Event()
        self.waited = threading.Event()
        self.stdout = self.lines(events)

    def lines(self, events):
        yield "[event] ready event_key=im.message.receive_v1\n"
        for event in events:
            yield json.dumps(event)
        assert self.exited.wait(self.hold_seconds)
        yield "[event] exited (reason: signal)\n"

    def poll(self):
        return 0 if self.exited.is_set() else None

    def terminate(self):
        self.exited.set()

    def wait(self, timeout=None):
        assert self.exited.wait(timeout or 8)
        self.waited.set()
        return 0


@pytest.mark.parametrize("transition", ["reconnect", "shutdown", "revoke", "rebind_during_reconnect"])
def test_live_private_ingress_across_listener_transition(ordinary, tmp_path, monkeypatch, transition):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from loopx.extensions.lark import goal_topic_runtime as runtime
    from loopx.extensions.lark import goal_topic_runtime_service as lifecycle
    from loopx.extensions.lark.conversation_identity import identity_ref
    from test_lark_private_conversations import connect

    store, controller, provider, transport = connect(ordinary)
    profile = "notes-app"
    binding = next(row for row in transport.bindings.read()["bindings"] if row["transport_ref"] == profile)
    config = transport.profiles()[profile]
    active = True
    first, second, third = [provider.event(profile, key, f"Live request {key}")
                           for key in ("first", "second", "third")]
    in_admission, release, waiting, received_third = (threading.Event() for _ in range(4))
    clock = [0.0]
    admitted = []
    consumers = []

    def admit(profile_name, event, **kwargs):
        if event["message_id"] == first["message_id"]:
            in_admission.set()
            assert release.wait(8)
        result = transport.admit(profile_name, event, **kwargs)
        admitted.append((event["message_id"], result["status"]))
        if event["message_id"] == third["message_id"]:
            received_third.set()
        return result

    owner = SimpleNamespace(admit=admit, pending_delivery_paths=lambda: [],
                            ingress=SimpleNamespace(observe=lambda **_: [profile, "unowned-app"]))
    service = lifecycle.LarkGoalTopicRuntimeService(
        snapshot_provider=lambda: {"private_profiles": {profile: config}} if active else {},
        runtime_root=store.root.parent, runtime_controller=controller, private_conversations=owner)
    monkeypatch.setattr(lifecycle, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def first_connection():
        yield first
        assert in_admission.wait(8)
        yield second
        waiting.set()  # The reader advanced past submission of the second line.

    def replacement_connection():
        if transition == "reconnect":
            yield {**second, "event_id": "synthetic_provider_redelivery"}
        yield third

    def spawn(args):
        assert args[3:6] == ["event", "consume", "im.message.receive_v1"]
        if consumers:
            assert len(consumers) == 1 and consumers[0].waited.is_set()
        consumer = _ListenerProcess(replacement_connection() if consumers else first_connection())
        consumers.append(consumer)
        return consumer

    stream = runtime.stream_lark_goal_topic_profile

    def isolated_stream(**kwargs):
        kwargs.setdefault("process_factory", spawn)
        return stream(**kwargs)

    monkeypatch.setattr(runtime, "_default_process_factory", spawn)
    monkeypatch.setattr(runtime, "stream_lark_goal_topic_profile", isolated_stream)
    service.start()
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            assert waiting.wait(8)
            if transition in ("reconnect", "rebind_during_reconnect"):
                clock[0] = 61
                service._delivery_wakeup.set()
            elif transition == "shutdown":
                pool.submit(service.close)
            else:
                active = False
                transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
                service.refresh()
            assert consumers[0].exited.wait(8)
            assert len(consumers) == 1
            if transition == "rebind_during_reconnect":
                projects = transport.bindings.projects
                other = tmp_path / "other-workspace"
                other.mkdir()
                projects.roots.append((other, other))
                target = next(x for x in projects.available() if x["workspace_path"] == str(other))
                replacement = transport.bindings.configure(transport_ref=profile, project_ref=target["project_ref"],
                                                           executor_endpoint_id="codex")
                assert replacement["binding_id"] != binding["binding_id"]
                config = transport.profiles()[profile]
            release.set()
            if transition in ("reconnect", "rebind_during_reconnect"):
                assert received_third.wait(8)
            else:
                assert consumers[0].waited.wait(8)
        finally:
            release.set()
            service.close()
            controller.close()

    second_ref = identity_ref(binding["provider_ref"], second["message_id"])
    if transition == "reconnect":
        assert [event_id for event_id, _ in admitted[:2]] == [first["message_id"], second["message_id"]]
        assert consumers[0].waited.is_set() and len(consumers) == 2
        assert [event_id for event_id, _ in admitted].count(second["message_id"]) == 2
        original = transport.core.read_request(second_ref)
        assert original["source"]["source_ref"] == transport.core.read_request(
            identity_ref(binding["provider_ref"], first["message_id"]))["source"]["source_ref"]
        messages = store.messages(original["session_id"])
        assert sum(m.get("text") == second["content"] for m in messages) == 1
    elif transition == "rebind_during_reconnect":
        assert admitted[:2] == [(first["message_id"], "audience_rejected"),
                               (second["message_id"], "audience_rejected")]
        assert not (transport.core.root / f"{second_ref}.json").exists()
        assert not (transport.root / f"{second_ref}.json").exists()
        third_ref = identity_ref(binding["provider_ref"], third["message_id"])
        assert transport.core.read_request(third_ref)["binding_id"] == replacement["binding_id"]
        assert consumers[0].waited.is_set() and len(consumers) == 2
    else:
        assert not any(event_id == second["message_id"] for event_id, _ in admitted)
        assert len(consumers) == 1


@pytest.mark.parametrize(("stored_app", "binding_change"), [
    (True, "replace"), (True, "add"), (True, "remove"),
    (False, "replace"), (False, "add"), (False, "remove"),
    (False, "add_after_retarget"), (False, "remove_after_retarget"),
    (False, "add_cli_alias"), (False, "remove_cli_alias"),
    (False, "add_different_cli"), (False, "remove_different_cli"),
    (False, "profile_read_fails"), (False, "profile_read_fails_then_retargets"),
    (False, "stale_inventory"), (False, "stale_inventory_after_error"),
    (False, "stale_inventory_during_queue_recovery"),
    (False, "removed_inventory"), (False, "closed_during_inventory"),
])
def test_shared_listener_drains_group_inbox_when_private_workspace_changes(
    ordinary,  # noqa: F811
    tmp_path, monkeypatch, stored_app, binding_change,
):
    import http.client
    import os
    import sys
    from pathlib import Path

    from extensions.test_lark_goal_topic_runtime import _reply_runner, _seed_legacy_topic
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.extensions.lark import goal_topic_runtime as runtime
    from loopx.extensions.lark.cli_resolution import LarkCliResolution
    from loopx.extensions.lark.conversation_identity import identity_ref
    from loopx.extensions.lark.event_inbox import inspect_lark_event_inbox, load_lark_event_inbox_config
    from loopx.extensions.lark.goal_channel_contracts import read_goal_channel_binding
    from loopx.extensions.lark.goal_channel_targets import read_goal_channel_targets
    from test_lark_private_conversations import connect

    store, controller, provider, transport = connect(ordinary)
    profile = "notes-app"
    target_path, binding_path = tmp_path / "targets.json", tmp_path / "group-binding.json"
    _seed_legacy_topic(target_path, binding_path)
    targets = read_goal_channel_targets(target_path)
    identity = targets["targets"]["fixture"]["identity"]
    original_app = provider.profile_apps[profile]
    identity.update(sender_profile=profile, bot_open_id="ou_notes_app_bot")
    if stored_app:
        identity["bot_app_id"] = original_app
    else:
        identity.pop("bot_app_id")
    if binding_change.endswith(("_cli_alias", "_different_cli")):
        transport.cli_bin = sys.executable
        if binding_change.endswith("_cli_alias"):
            # Legacy targets may save a PATH command while Chat resolves the
            # same executable to a full path for its private transport.
            executable = Path(sys.executable)
            monkeypatch.setenv("PATH", str(executable.parent) + os.pathsep + os.environ.get("PATH", ""))
            identity["cli_bin"] = executable.name
    bindings = {"goal-alpha": read_goal_channel_binding(binding_path)}
    before = transport.profiles()[profile]["binding_id"]
    adding = binding_change.startswith("add")
    removing = binding_change.startswith("remove")
    retargeted = binding_change.endswith("_retarget")
    different_transport = retargeted or binding_change.endswith("_different_cli")
    stale_inventory = binding_change.startswith("stale_inventory")
    inventory_during_queue_recovery = binding_change == "stale_inventory_during_queue_recovery"
    removed_inventory = binding_change == "removed_inventory"
    closing = binding_change == "closed_during_inventory"
    delayed_inventory = stale_inventory or removed_inventory or closing
    unreadable = binding_change.startswith("profile_read_fails") or delayed_inventory
    retarget_on_retry = binding_change == "profile_read_fails_then_retargets"
    if adding:
        transport.bindings.disconnect(before, expected_revision=transport.bindings.read()["revision"])
    if unreadable:
        for row in transport.bindings.read()["bindings"]:
            transport.bindings.disconnect(row["binding_id"], expected_revision=transport.bindings.read()["revision"])

    group_config = {"target_payload": targets, "binding_payloads": bindings}

    def snapshot():
        private = transport.profiles().get(profile)
        return {**group_config, "private_profiles": {profile: private} if private is not None else {}}

    group_events = [
        dict(provider.topic(name, f"@linkmacbot group {name}", root="om_topic_alpha",
                            chat="oc_public_fixture"), mentioned=True)
        for name in ("first", "second", "third")
    ]
    new_private = provider.event(profile, "after_rebind", "/status")
    in_group_answer, release, queued, next_stream_ready = (threading.Event() for _ in range(4))
    second_replied, inventory_retried = threading.Event(), threading.Event()
    inventory_captured, return_inventory = threading.Event(), threading.Event()
    timer_reads = []
    answered, consumer_processes = [], []
    replies = {}

    def answer(*, route, **_kwargs):
        message_id = route["message_id"]
        if message_id == group_events[0]["message_id"]:
            in_group_answer.set()
            assert release.wait(8)
        answered.append(message_id)
        return "Group answer"

    original_admit = transport.admit

    def admit(profile_name, event, **kwargs):
        result = original_admit(profile_name, event, **kwargs)
        if event["message_id"] == new_private["message_id"]:
            assert result["status"] == "command_recorded"
            next_stream_ready.set()
        return result

    def first_connection():
        yield group_events[0]
        assert in_group_answer.wait(8)
        yield group_events[1]
        queued.set()

    def replacement_connection():
        if not removed_inventory and not closing:
            yield group_events[2] if removing or unreadable else new_private

    def spawn(args):
        assert args[3:6] == ["event", "consume", "im.message.receive_v1"]
        if consumer_processes:
            assert len(consumer_processes) == 1 and consumer_processes[0].waited.is_set()
        consumer = _ListenerProcess(
            replacement_connection() if consumer_processes else first_connection(),
            hold_seconds=22 if delayed_inventory else 8,
        )
        consumer_processes.append(consumer)
        return consumer

    stream = runtime.stream_lark_goal_topic_profile

    def isolated_stream(**kwargs):
        kwargs.setdefault("process_factory", spawn)
        health_sink = kwargs["health_sink"]

        def observe_health(update):
            health_sink(update)
            if update.get("replied_count") == 1:
                if (removing or unreadable) and len(consumer_processes) == 2:
                    next_stream_ready.set()
                if answered == [event["message_id"] for event in group_events[:2]]:
                    second_replied.set()

        return stream(**{**kwargs, "health_sink": observe_health}, reply_runner=_reply_runner(replies))

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(runtime, "_default_process_factory", spawn)
    monkeypatch.setattr(runtime, "stream_lark_goal_topic_profile", isolated_stream)
    monkeypatch.setattr(runtime, "answer_lark_goal_topic", answer)
    monkeypatch.setattr(transport, "admit", admit)
    monkeypatch.setattr("loopx.chat_lark_api.build_lark_goal_topic_runtime_snapshot", lambda **_: group_config)
    monkeypatch.setattr(ChatRequestHandler, "_lark_runner", lambda _: provider)
    service = runtime.LarkGoalTopicRuntimeService(
        snapshot_provider=snapshot, runtime_root=store.root.parent,
        runtime_controller=controller, private_conversations=transport,
    )
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller = store, controller
    server.registry_path, server.runtime_root_override = controller.registry_path, store.root.parent
    server.verbose = False
    server.lark_cli_resolution = LarkCliResolution(
        command=transport.cli_bin, available=True, source="explicit", version="fixture", error_code=None,
    )
    server.lark_private_conversations = transport
    server.lark_goal_topic_runtime = service
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    def change_private_binding(method, body):
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request(method, "/api/chat/lark/private-conversations", json.dumps(body),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            data = json.loads(response.read())
            assert response.status == 200, data
        finally:
            connection.close()

    try:
        service.refresh()
        assert queued.wait(8), service.health_snapshot()
        if retargeted or retarget_on_retry:
            provider.profile_apps[profile] = "cli_retargeted_app"
        if unreadable:
            original_runner = transport.runner
            inventory_failed = threading.Event()
            unreadable_inventory = {"returncode": 1, "stdout": "", "stderr": "temporary profile read failure"}

            def read_profile(args, cwd=None, timeout=None):
                if args[1:] == ["profile", "list"]:
                    if not inventory_failed.is_set():
                        inventory_failed.set()
                        return unreadable_inventory
                    inventory_retried.set()
                    if delayed_inventory and threading.current_thread() is service._refresh_retry:
                        timer_reads.append(threading.current_thread())
                        if not inventory_captured.is_set():
                            if removed_inventory:
                                provider.profile_apps.pop(profile)
                            response = original_runner(args, cwd, timeout)
                            if stale_inventory:
                                rows = json.loads(response["stdout"])
                                response["stdout"] = json.dumps([row for row in rows if row["name"] != profile])
                            inventory_captured.set()
                            assert return_inventory.wait(8)
                            return response
                    if binding_change == "stale_inventory_after_error" and not return_inventory.is_set():
                        return unreadable_inventory
                return original_runner(args, cwd, timeout)

            monkeypatch.setattr(transport, "runner", read_profile)
            original_worker = service._workers[profile]
            service.refresh()
            assert inventory_failed.is_set()
            assert service._workers[profile] == original_worker
            assert not original_worker[0].is_set()
            assert not consumer_processes[0].exited.is_set()
        elif removing:
            change_private_binding("DELETE", {"binding_id": before, "revision": transport.bindings.read()["revision"]})
        else:
            projects = transport.bindings.projects
            other = tmp_path / "other-workspace"
            other.mkdir()
            projects.roots.append((other, other))
            project = next(row for row in projects.available() if row["workspace_path"] == str(other))
            change_private_binding("POST", {"app_ref": profile, "project_ref": project["project_ref"],
                                            "executor_endpoint_id": "codex"})
            replacement = next(row for row in transport.bindings.read()["bindings"] if row["transport_ref"] == profile)
            assert transport.profiles()[profile]["binding_id"] == replacement["binding_id"] != before
            assert replacement["provider_ref"] == identity_ref(provider.profile_apps[profile])
        if delayed_inventory:
            retry = service._refresh_retry
            assert retry is not None and inventory_captured.wait(8)
            if inventory_during_queue_recovery:
                resume_session_queues = service._resume_session_queues

                def resume_after_retry(snapshot):
                    # The foreground has the current inventory but has yet
                    # to update the listener. Finish the older read here.
                    return_inventory.set()
                    retry.join(8)
                    assert not retry.is_alive()
                    assert not original_worker[0].is_set()
                    resume_session_queues(snapshot)

                monkeypatch.setattr(service, "_resume_session_queues", resume_after_retry)
            if stale_inventory:
                service.refresh()
                if not inventory_during_queue_recovery:
                    assert service._refresh_retry is retry
                assert service._workers[profile] == original_worker
            if closing:
                release.set()
                assert second_replied.wait(8), service.health_snapshot()
                service.close()
            return_inventory.set()
            retry.join(8)
            assert not retry.is_alive()
            if removed_inventory:
                assert original_worker[0].is_set()
                assert consumer_processes[0].exited.wait(8)
                release.set()
                original_worker[1].join(8)
                assert not original_worker[1].is_alive()
                expected = group_events[:1]
            else:
                if stale_inventory:
                    assert not original_worker[0].is_set()
                    release.set()
                    assert second_replied.wait(8), service.health_snapshot()
                    with service._lock:
                        recheck = service._refresh_retry
                    assert recheck is not None and recheck is not retry
                    recheck.join(8)
                    assert not recheck.is_alive()
                    assert timer_reads == [retry, recheck]
                    assert service._workers[profile] == original_worker
                else:
                    assert original_worker[0].is_set()
                    assert service.active_profiles() == []
                    assert timer_reads == [retry]
                assert service._refresh_retry is None
                assert len(consumer_processes) == 1
                expected = group_events[:2]
        elif unreadable and not retarget_on_retry:
            release.set()
            assert second_replied.wait(8), service.health_snapshot()
            assert inventory_retried.wait(8)
            assert service._workers[profile] == original_worker
            assert len(consumer_processes) == 1
            expected = group_events[:2]
        else:
            assert consumer_processes[0].exited.wait(8)
            assert len(consumer_processes) == 1
            release.set()
            assert next_stream_ready.wait(8), service.health_snapshot()
            expected = group_events[:1] if different_transport or retarget_on_retry else group_events[:2]
            if removing or unreadable:
                expected += group_events[2:]
            if unreadable:
                assert inventory_retried.is_set()
        assert answered == [event["message_id"] for event in expected]
        reply_targets = [call[call.index("--message-id") + 1] for call in replies["calls"]
                         if "+messages-reply" in call and "--dry-run" not in call]
        assert reply_targets == answered
        config_path, = (store.root.parent / ".loopx/config/lark-goal-topics").glob("*.json")
        inbox = inspect_lark_event_inbox(project=store.root.parent, config_path=config_path)
        config = load_lark_event_inbox_config(project=store.root.parent, config_path=config_path)
        acked = json.loads((config["inbox_path"] / "processed.json").read_text())
        assert set(acked["message_ids"]) == set(reply_targets)
        assert inbox["processed_count"] == len(expected)
        assert inbox["pending_count"] == 0
        if not stored_app and (adding or removing):
            assert [identity["cli_bin"], "profile", "list"] in provider.calls
    finally:
        return_inventory.set()
        release.set()
        service.close()
        server.shutdown()
        server.server_close()
        server_thread.join(3)
        controller.close()


@pytest.mark.parametrize("app_identity", ["legacy_alias", "stored_app", "different_apps"])
def test_shared_app_routes_messages_to_each_legacy_group(
    ordinary, tmp_path, monkeypatch, app_identity,  # noqa: F811
):
    from pathlib import Path

    from extensions.test_lark_goal_topic_runtime import _reply_runner, _seed_legacy_topic
    from loopx.extensions.lark import goal_topic_runtime as runtime
    from loopx.extensions.lark.goal_channel_contracts import read_goal_channel_binding, save_goal_connection
    from loopx.extensions.lark.goal_channel_targets import add_lark_goal_channel_target, read_goal_channel_targets
    from test_lark_private_conversations import connect

    store, controller, provider, transport = connect(ordinary)
    for row in transport.bindings.read()["bindings"]:
        transport.bindings.disconnect(row["binding_id"], expected_revision=transport.bindings.read()["revision"])
    target_path = tmp_path / "targets.json"
    binding_path = tmp_path / "first-binding.json"
    _seed_legacy_topic(target_path, binding_path)
    first_profile = "notes-app"
    second_profile = "steward-app"
    app_id = provider.profile_apps[first_profile]
    if app_identity != "different_apps":
        provider.profile_apps[second_profile] = app_id
    add_lark_goal_channel_target(
        target_path=target_path, target_name="second", chat_id="oc_second_fixture",
        chat_name="Second group", identity_mode="local_user", sender_profile=second_profile,
        bot_app_id=provider.profile_apps[second_profile], bot_display_name="linkmacbot", execute=True,
    )
    other_path = tmp_path / "second-binding.json"
    save_goal_connection(
        binding_path=other_path, payload=read_goal_channel_binding(other_path), goal_id="goal-beta",
        binding={"enabled": True, "provider": "lark", "target_ref": "second",
                 "topic": {"root_message_id": "om_topic_beta"},
                 "routing": {"ingress_mode": "direct_session", "incoming_mode": "mentions", "reply_mode": "topic_reply"}},
    )
    targets = read_goal_channel_targets(target_path)
    first = targets["targets"]["fixture"]["identity"]
    second = targets["targets"]["second"]["identity"]
    first.update(sender_profile=first_profile, bot_app_id=app_id, bot_open_id="ou_notes_app_bot")
    second["bot_open_id"] = "ou_notes_app_bot" if app_identity != "different_apps" else "ou_steward_app_bot"
    if app_identity != "stored_app":
        first.pop("bot_app_id")
        second.pop("bot_app_id")
    bindings = {"goal-alpha": read_goal_channel_binding(binding_path)}
    other = read_goal_channel_binding(other_path)

    def snapshot():
        return {"target_payload": targets, "binding_payloads": dict(bindings), "private_profiles": {}}

    first_message = provider.topic("first_group", "@linkmacbot first", root="om_topic_alpha",
                                   chat="oc_public_fixture")
    second_message = provider.topic("second_group", "@linkmacbot second", root="om_topic_beta",
                                    chat="oc_second_fixture")
    messages = [first_message, second_message]
    processed = {row["message_id"]: threading.Event() for row in messages}
    release = threading.Event()
    processes, answers, results = {}, [], {}
    replies = {}
    reply = _reply_runner(replies)

    def read_group(chat_id):
        def runner(args):
            if args[3:6] == ["im", "chats", "get"]:
                return {"returncode": 0, "stdout": json.dumps({"data": {"chat_id": chat_id}})}
            return reply(args)
        return runner

    def answer(*, route, **_kwargs):
        answers.append((route["app_ref"], route["message_id"], route["topic_root_message_id"]))
        return "Group answer"

    def spawn(args):
        assert args[3:6] == ["event", "consume", "im.message.receive_v1"]
        profile = args[2]
        assert profile not in processes

        def events():
            if profile == first_profile:
                yield first_message
            assert release.wait(12)
            if profile == second_profile or app_identity != "different_apps":
                yield second_message

        processes[profile] = _ListenerProcess(events(), hold_seconds=14)
        return processes[profile]

    stream = runtime.stream_lark_goal_topic_profile
    source_chat = {first_message["message_id"]: first_message["chat_id"],
                   second_message["message_id"]: second_message["chat_id"]}
    def observed_stream(**kwargs):
        profile = kwargs["profile"]
        previous = kwargs["health_sink"]

        def health(update):
            previous(update)
            if update.get("event_count"):
                # One input is released at a time, even when both groups share the pipe.
                message = second_message if release.is_set() else first_message
                message_id = message["message_id"]
                results[message_id] = (profile, update.get("last_event_status"))
                processed[message_id].set()

        def reply_for_topic(args):
            if "+messages-reply" in args:
                message_id = args[args.index("--message-id") + 1]
                return read_group(source_chat[message_id])(args)
            if args[3:6] == ["im", "chats", "get"]:
                return read_group(second_message["chat_id"] if release.is_set() else first_message["chat_id"])(args)
            return reply(args)

        return stream(**{**kwargs, "health_sink": health}, reply_runner=reply_for_topic)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(runtime, "_default_process_factory", spawn)
    monkeypatch.setattr(runtime, "stream_lark_goal_topic_profile", observed_stream)
    monkeypatch.setattr(runtime, "answer_lark_goal_topic", answer)
    service = runtime.LarkGoalTopicRuntimeService(
        snapshot_provider=snapshot, runtime_root=store.root.parent,
        runtime_controller=controller, private_conversations=transport,
    )
    try:
        service.refresh()
        assert processed[first_message["message_id"]].wait(10), service.health_snapshot()
        assert results[first_message["message_id"]] == (first_profile, "replied_and_acknowledged")
        bindings["goal-beta"] = other
        service.refresh()
        if app_identity != "different_apps":
            for _ in range(100):
                if service.health_snapshot().get(second_profile, {}).get("status") == "standby":
                    break
                threading.Event().wait(0.02)
            assert service.health_snapshot()[second_profile]["status"] == "standby"
        release.set()
        assert processed[second_message["message_id"]].wait(10), service.health_snapshot()
        assert results[second_message["message_id"]] == (
            second_profile if app_identity == "different_apps" else first_profile, "replied_and_acknowledged"
        )
        assert set(processes) == ({first_profile, second_profile} if app_identity == "different_apps" else {first_profile})
        assert answers == [(first_profile, "om_first_group", "om_topic_alpha"),
                           (second_profile, "om_second_group", "om_topic_beta")]
        reply_targets = [(call[2], call[call.index("--message-id") + 1]) for call in replies["calls"]
                         if "+messages-reply" in call and "--dry-run" not in call]
        assert reply_targets == [(profile, message) for profile, message, _ in answers]
        inboxes = list(store.root.parent.glob(".loopx/inbox/lark-goal-topics/*/processed.json"))
        assert len(inboxes) == 2
        assert {tuple(json.loads(path.read_text())["message_ids"]) for path in inboxes} == {
            ("om_first_group",), ("om_second_group",),
        }
    finally:
        release.set()
        service.close()
        controller.close()


def test_foreground_inventory_failure_rearms_a_finishing_profile_retry(tmp_path, monkeypatch):
    from extensions.test_lark_goal_topic_runtime import _seed_legacy_topic
    from loopx.extensions.lark import goal_topic_runtime as runtime
    from loopx.extensions.lark.goal_channel_contracts import read_goal_channel_binding
    from loopx.extensions.lark.goal_channel_targets import read_goal_channel_targets

    target_path, binding_path = tmp_path / "targets.json", tmp_path / "binding.json"
    _seed_legacy_topic(target_path, binding_path)
    targets = read_goal_channel_targets(target_path)
    identity = targets["targets"]["fixture"]["identity"]
    profile = identity["sender_profile"]
    app_id = identity.pop("bot_app_id", None) or "cli_public_fixture"
    snapshot = {
        "target_payload": targets,
        "binding_payloads": {"goal-alpha": read_goal_channel_binding(binding_path)},
    }
    available = threading.Event()
    available.set()
    observed, finish = threading.Event(), threading.Event()
    timer_reads = []

    def inventory(*, cli_bin):
        if threading.current_thread() is service._refresh_retry:
            timer_reads.append(threading.current_thread())
        if not available.is_set():
            raise OSError("profile inventory unavailable")
        return {profile: app_id}

    service = runtime.LarkGoalTopicRuntimeService(
        snapshot_provider=lambda: snapshot,
        runtime_root=tmp_path,
        runtime_controller=object(),
        private_conversations=SimpleNamespace(configured_profile_apps=inventory),
        profile_poller=lambda _profile, stop: stop.wait(20),
    )
    original_refresh = service._refresh_profile_workers

    def pause_after_observation(*, resume_queues=True):
        retry_needed = original_refresh(resume_queues=resume_queues)
        if not resume_queues and not observed.is_set():
            assert not retry_needed
            observed.set()
            assert finish.wait(8)
        return retry_needed

    monkeypatch.setattr(service, "_refresh_profile_workers", pause_after_observation)
    try:
        service.refresh()
        worker = service._workers[profile]
        available.clear()
        service.refresh()
        retry = service._refresh_retry
        assert retry is not None
        available.set()
        assert observed.wait(8)
        available.clear()
        service.refresh()
        assert service._refresh_retry is retry
        assert service._workers[profile] == worker
        available.set()
        finish.set()
        retry.join(8)
        assert not retry.is_alive()
        recheck = service._refresh_retry
        assert recheck is not None and recheck is not retry
        recheck.join(8)
        assert not recheck.is_alive()
        assert service._refresh_retry is None
        assert service._workers[profile] == worker
        assert timer_reads == [retry, recheck]
    finally:
        finish.set()
        service.close()


def test_unknown_failed_capture_is_not_replayed(source_monitor):
    f = source_monitor
    native = {**f.carrier, "status": "failed", "event": {**f.carrier["event"], "message_id": "om_missed"}}
    (f.root / "failed.json").write_text(json.dumps(native))
    f.pages[0]["data"]["messages"] = [owner_source()]
    assert f.monitor.observe() == []
    assert f.monitor.health(f.binding["binding_id"])["source_gap_count"] == 0
    assert json.loads((f.root / "failed.json").read_text()) == native


def test_native_connection_readback_keeps_capture_and_provider_health_separate(source_monitor):
    from loopx.extensions.lark.private_conversation_api import PrivateConversationRequestMixin

    f = source_monitor
    f.pages[0]["data"]["messages"] = [owner_source()]
    f.monitor.observe()
    row = {**f.binding, "context_kind": "project", "project_ref": "workspace", "executor_endpoint_id": "codex"}
    foreign = {**row, "binding_id": "foreign", "transport_ref": "other-provider"}
    bindings = SimpleNamespace(read=lambda: {"revision": 1, "bindings": [row, foreign]},
        goal_scope_ids=lambda _row: [], agent_candidates=lambda _ref: [], stop_recoveries=lambda _rows: [])
    server = SimpleNamespace(runtime_controller=SimpleNamespace(project_contexts=SimpleNamespace(
        conversation_bindings=bindings, available=lambda: [{"project_ref": "workspace", "workspace_path": "/synthetic/project"}])),
        lark_goal_topic_runtime=SimpleNamespace(health_snapshot=lambda: {"owner-app": {"status": "listening"},
                                                                       "other-provider": {"status": "listening"}}),
        lark_private_conversations=SimpleNamespace(health=lambda: {}, ingress=f.monitor),
        conversation_transports=SimpleNamespace(transports={"other-provider": object()}))
    packets = []
    handler = SimpleNamespace(server=server, _send_json=packets.append,
        _private_listener_status=lambda _profile, _health: "listening")
    PrivateConversationRequestMixin._private_conversations(handler)
    actual, other = packets[0]["connections"]
    assert actual["listener_status"] == "listening" and actual["pending_count"] == 0
    assert actual["ingress"]["status"] == "gap" and actual["ingress"]["source_gap_count"] == 1
    assert "ingress" not in other
    assert "om_missed" not in json.dumps(packets)


def test_page_budget_or_cancellation_never_advances_complete_coverage(source_monitor):
    f = source_monitor
    f.pages[0]["data"]["messages"] = [owner_source()]
    f.monitor.observe()
    f.clock[0] += 2 * 86400
    f.calls.clear()
    f.pages[:] = [{"ok": True, "data": {"messages": [], "has_more": True, "page_token": f"page-{n}"}}
                 for n in range(20)]
    assert f.monitor.observe() == []
    assert len(f.calls) == 20 and f.monitor.health(f.binding["binding_id"])["last_complete_at"] == 1095.0
    assert f.monitor.health(f.binding["binding_id"])["status"] == "unavailable"
    assert f.monitor.health(f.binding["binding_id"])["source_gap_count"] == 1
    before = f.monitor._path(f.binding["binding_id"]).read_bytes()
    assert f.monitor.observe(cancelled=lambda: True) == []
    assert f.monitor._path(f.binding["binding_id"]).read_bytes() == before


def test_restart_after_a_long_outage_keeps_contiguous_coverage(source_monitor):
    f = source_monitor
    assert f.monitor.observe() == []
    assert f.monitor.health(f.binding["binding_id"])["last_complete_at"] == 1095.0
    # One owner message arrives inside the outage and is never captured.
    missed = owner_source("om_during_outage")
    timeline = [(1400.0, missed)]

    def time_filtered(args, _cwd=None, _timeout=None):
        f.calls.append(args)
        start = datetime.fromisoformat(args[args.index("--start") + 1]).timestamp()
        end = datetime.fromisoformat(args[args.index("--end") + 1]).timestamp()
        rows = [row for at, row in timeline if start <= at <= end]
        return {"returncode": 0, "stdout": json.dumps({"ok": True, "identity": "bot",
            "data": {"messages": rows, "has_more": False}}), "stderr": ""}

    f.transport.runner = time_filtered
    f.clock[0] = 1100 + 2 * 86400
    f.calls.clear()
    f.monitor.observe()
    # The resumed window starts at the stored cursor (- overlap), never at
    # "now minus a day", so the missed message stays observable.
    start = datetime.fromisoformat(f.calls[0][f.calls[0].index("--start") + 1]).timestamp()
    assert start <= 1400
    health = f.monitor.health(f.binding["binding_id"])
    assert health["status"] == "gap" and health["source_gap_count"] == 1
    # A pass that has not reached the live edge advances contiguously and does
    # not certify the whole outage.
    assert 1095.0 < health["last_complete_at"] < f.clock[0] - 86400
    for _ in range(3):
        f.monitor.observe()
    caught_up = f.monitor.health(f.binding["binding_id"])
    assert caught_up["last_complete_at"] == f.clock[0] - 5
    assert caught_up["source_gap_count"] == 1


def test_interleaved_observers_do_not_overwrite_a_committed_gap(source_monitor):
    f = source_monitor
    first = PrivateIngressObserver(f.transport)
    f.pages[0]["data"]["messages"] = [owner_source("om_first")]
    entered, release = threading.Event(), threading.Event()
    original = f.transport.runner
    calls = {"count": 0}
    failures: list[BaseException] = []

    def slow(args, cwd=None, timeout=None):
        calls["count"] += 1
        if calls["count"] == 1:
            entered.set()
            assert release.wait(10)
        return original(args, cwd, timeout)

    def observe_first():
        try:
            first.observe()
        except BaseException as error:  # pragma: no cover - surfaced below
            failures.append(error)

    f.transport.runner = slow
    worker = threading.Thread(target=observe_first)
    worker.start()
    path = f.monitor._path(f.binding["binding_id"])
    try:
        assert entered.wait(10)
        before = path.read_bytes() if path.exists() else None
        # The second observer must not race the receipt read-modify-write while
        # the first one is mid-observation.
        assert f.monitor.observe() == []
        assert (path.read_bytes() if path.exists() else None) == before
    finally:
        release.set()
        worker.join(10)
    assert not failures
    health = f.monitor.health(f.binding["binding_id"])
    assert health["status"] == "gap" and health["source_gap_count"] == 1
    # The next scheduled pass keeps the discovered gap.
    f.clock[0] += 60
    f.monitor.observe()
    assert f.monitor.health(f.binding["binding_id"])["source_gap_count"] == 1


def test_fresh_grant_change_retains_same_source_coverage_without_replaying(source_monitor):
    f = source_monitor
    f.pages[0]["data"]["messages"] = [owner_source()]
    assert f.monitor.observe() == ["owner-app"]
    f.binding["grant"] = "workspace_read"
    f.clock[0] += 60
    assert f.monitor.observe() == []
    health = f.monitor.health(f.binding["binding_id"])
    assert health["status"] == "gap" and health["last_complete_at"] == 1155.0


@pytest.mark.parametrize("scenario", ["missing", "long_outage", "concurrent_observers", "historical", "historical_legacy"])
def test_real_native_binding_and_http_keep_a_missed_source_out_of_core(ordinary, monkeypatch, scenario):  # noqa: F811
    import http.client
    import time
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from test_lark_private_conversations import connect, finish_group_turn

    store, runtime, provider, transport = connect(ordinary)
    event = provider.event("notes-app", "previous", "Explain this project")
    event["create_time"] = str(int((time.time() - 120) * 1000))
    assert transport.admit("notes-app", event)["status"] == "durably_accepted"
    finish_group_turn(runtime, transport, message="Explain this project")
    original = transport.runner
    calls = []
    now = time.time()
    clocks = {"first-observer": now, "second-observer": now + 120}
    clock = [now]
    monkeypatch.setattr("loopx.extensions.lark.private_ingress.time",
                        SimpleNamespace(time=lambda: clocks.get(threading.current_thread().name, clock[0])))
    first_inside, release_first, first_done, second_ready = [threading.Event() for _ in range(4)]
    missed = provider.event("notes-app", "missed", "Synthetic missed original")
    later = provider.event("notes-app", "later", "Synthetic live original")
    sources = []
    if scenario == "historical_legacy":
        # A real capture from an older conversation binding remains a capture
        # of this exact App/owner source; it grants no current work permission.
        path = next(p for p in transport.root.glob("*.json")
                    if json.loads(p.read_text())["event"]["message_id"] == event["message_id"])
        captured = json.loads(path.read_text())
        captured["binding_id"] = "former-context-binding"
        path.write_text(json.dumps(captured))
        carrier = provider.event("notes-app", "current-carrier", "Synthetic current carrier")
        assert transport.admit("notes-app", carrier)["status"] == "durably_accepted"
        finish_group_turn(runtime, transport, message=carrier["content"])
        sources.append((now - 4 * 86400, event))

    def history(args, cwd=None, timeout=None):
        if "+chat-messages-list" not in args:
            return original(args, cwd, timeout)
        calls.append(args)
        if threading.current_thread().name == "first-observer":
            first_inside.set()
            assert release_first.wait(10)
        elif threading.current_thread().name == "second-observer":
            second_ready.set()
            assert first_done.wait(10)
        start = datetime.fromisoformat(args[args.index("--start") + 1]).timestamp() if "--start" in args else float("-inf")
        end = datetime.fromisoformat(args[args.index("--end") + 1]).timestamp()
        rows = [owner_source(source["message_id"], chat_id=source["chat_id"],
                            sender={"id": source["sender_id"], "sender_type": "user"})
                for stamp, source in sources if start <= stamp <= end]
        return {"returncode": 0, "stdout": json.dumps({"ok": True, "identity": "bot",
            "data": {"messages": rows, "has_more": False}}), "stderr": ""}

    transport.runner = history
    core_admit = transport.core.admit

    def deny_history(**_kw):
        pytest.fail("history cannot admit a Core request")

    monkeypatch.setattr(transport.core, "admit", deny_history)
    before = {p.name: p.read_bytes() for p in transport.root.glob("*.json")}
    if scenario == "long_outage":
        assert transport.ingress.observe() == []
        sources.append((now + 3600, missed))
        clock[0] += 2 * 86400
        transport.ingress.observe()
    elif scenario == "concurrent_observers":
        # Both first windows are legitimate, but the later one cannot see A.
        # Pause both reads before either receipt is committed on old code.
        sources.extend([(now - 86400 + 30, missed), (now + 30, later)])
        errors = []

        def observe(observer, done):
            try:
                observer.observe()
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        first = threading.Thread(target=observe, name="first-observer",
                                 args=(transport.ingress, first_done))
        second = threading.Thread(target=observe, name="second-observer",
                                  args=(PrivateIngressObserver(transport), second_ready))
        first.start()
        try:
            assert first_inside.wait(10)
            second.start()
            assert second_ready.wait(10)
        finally:
            release_first.set()
            first.join(timeout=10)
            if second.ident is not None:
                second.join(timeout=10)
        assert not first.is_alive() and not second.is_alive() and errors == []
        clock[0] += 180
        transport.ingress.observe()  # The skipped observer catches up from A's cursor.
    elif scenario in {"historical", "historical_legacy"}:
        sources.append((now - 3 * 86400, missed))
        # This source predates the first live observation. It must be visible,
        # without reconnecting the live worker or acquiring execution authority.
        assert transport.ingress.observe() == []
    else:
        sources.append((now - 30, missed))
        assert transport.ingress.observe() == ["notes-app"]
    assert before == {p.name: p.read_bytes() for p in transport.root.glob("*.json")}
    binding_id = transport.profiles()["notes-app"]["binding_id"]
    gaps_before_capture = transport.ingress.health(binding_id)["source_gap_count"]
    if scenario == "concurrent_observers":
        # Only B arrives through the actual native route. An empty later source
        # window cannot erase A or certify that its original request was captured.
        monkeypatch.setattr(transport.core, "admit", core_admit)
        assert transport.admit("notes-app", later)["status"] == "durably_accepted"
        finish_group_turn(runtime, transport, message=later["content"])
        monkeypatch.setattr(transport.core, "admit", deny_history)
        before = {p.name: p.read_bytes() for p in transport.root.glob("*.json")}
        sources.clear()
        clock[0] += 60
        transport.ingress.observe()
        assert before == {p.name: p.read_bytes() for p in transport.root.glob("*.json")}
    assert calls
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller, server.lark_private_conversations = store, runtime, transport
    server.verbose = False
    server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=lambda: {"notes-app": {"status": "listening"}},
                                                   close=lambda: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=10)
    try:
        connection.request("GET", "/api/chat/lark/private-conversations")
        response = connection.getresponse()
        assert response.status == 200
        packet = json.loads(response.read())
        actual = next(row for row in packet["connections"] if row["app_ref"] == "notes-app")
        assert actual["pending_count"] == 0 and actual["listener_status"] == "listening"
        assert (gaps_before_capture, actual["ingress"]["status"], actual["ingress"]["source_gap_count"]) == (
            2 if scenario == "concurrent_observers" else 1, "gap", 1)
        if scenario == "long_outage":
            # HTTP must not certify the still-unscanned part of the outage.
            assert now - 5 < actual["ingress"]["last_complete_at"] < clock[0] - 86400
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("legacy_cursor", [False, True])
def test_sources_older_than_the_first_live_window_are_reconciled(source_monitor, legacy_cursor):
    f = source_monitor
    f.clock[0] = 200000.0
    if legacy_cursor:
        path = f.monitor._path(f.binding["binding_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": "verified", "gaps": {},
                                   "last_checked_at": 199000.0, "last_complete_at": 198995.0}))
    f.history_pages[0]["data"]["messages"] = [owner_source("om_old_unaccounted")]
    assert f.monitor.observe() == []
    health = f.monitor.health(f.binding["binding_id"])
    assert health["status"] == "gap" and health["source_gap_count"] == 1
    assert len(f.history_calls) == 1 and "--start" not in f.history_calls[0]
    assert len(list(f.root.glob("*.json"))) == 1


def test_historical_pages_resume_after_restart_without_starving_live_reads(source_monitor):
    f = source_monitor
    f.history_pages[:] = [{"ok": True, "data": {"messages": [], "has_more": True, "page_token": f"old-{n}"}}
                          for n in range(7)]
    f.history_pages.append({"ok": True, "data": {"messages": [owner_source("om_old_unaccounted")], "has_more": False}})
    assert f.monitor.observe() == []
    assert len(f.history_calls) == 5 and len(f.calls) == 1
    assert f.monitor.health(f.binding["binding_id"])["status"] == "unavailable"
    f.clock[0] += 60
    restarted = PrivateIngressObserver(f.transport)
    assert restarted.observe() == []
    assert len(f.calls) == 2 and len(f.history_calls) == 8
    assert f.history_calls[5][f.history_calls[5].index("--page-token") + 1] == "old-4"
    assert f.history_calls[0][f.history_calls[0].index("--end") + 1] == f.history_calls[-1][f.history_calls[-1].index("--end") + 1]
    assert restarted.health(f.binding["binding_id"])["source_gap_count"] == 1
    f.clock[0] += 60
    restarted.observe()
    assert len(f.calls) == 3 and len(f.history_calls) == 8


@pytest.mark.parametrize("failure", ["unavailable", "cursor_loop", "revoked", "cancelled"])
def test_incomplete_historical_reads_preserve_the_committed_cursor_and_gaps(source_monitor, failure):
    f = source_monitor
    f.history_pages[:] = [{"ok": True, "data": {"messages": [owner_source("om_old_unaccounted")],
                                               "has_more": True, "page_token": f"old-{n}"}} for n in range(5)]
    f.monitor.observe()
    path = f.monitor._path(f.binding["binding_id"])
    prior = json.loads(path.read_text())
    cancelled = [False]
    f.transport.bindings.read = lambda: {"bindings": [dict(f.binding)]}

    def failed_page():
        if failure == "revoked":
            f.binding["enabled"] = False
        if failure == "cancelled":
            cancelled[0] = True
            return {"ok": True, "data": {"messages": [], "has_more": False}}
        if failure == "unavailable":
            return {"ok": False, "error": {"message": "private history error"}}
        return {"ok": True, "data": {"messages": [], "has_more": failure == "cursor_loop", "page_token": "old-2"}}

    f.history_pages[:] = [failed_page]
    f.clock[0] += 60
    assert f.monitor.observe(cancelled=lambda: cancelled[0]) == []
    current = json.loads(path.read_text())
    assert current["history"] == prior["history"] and current["gaps"] == prior["gaps"]
    assert current["last_complete_at"] == prior["last_complete_at"]
    assert current["status"] == "unavailable" and "private history error" not in json.dumps(current)


@pytest.mark.parametrize("variant", ["same_source", "foreign_source", "foreign_chat", "foreign_owner", "foreign_profile", "group"])
def test_older_binding_capture_requires_the_same_provider_owner_source(source_monitor, variant):
    f = source_monitor
    old = {**f.carrier, "binding_id": "older-binding", "status": "delivered",
           "event": {**f.carrier["event"], "message_id": "om_missed"}}
    if variant == "foreign_source":
        old["source"] = {"source_ref": "different-app-source"}
    elif variant == "foreign_chat":
        old["event"]["chat_id"] = "oc_elsewhere"
    elif variant == "foreign_owner":
        old["event"]["sender_id"] = "ou_elsewhere"
    elif variant == "foreign_profile":
        old["profile"] = "different-app"
    elif variant == "group":
        old["event"]["chat_type"] = "group"
    path = f.root / "older.json"
    path.write_text(json.dumps(old))
    before = path.read_bytes()
    f.pages[0]["data"]["messages"] = [owner_source()]
    assert f.monitor.observe() == ([] if variant == "same_source" else ["owner-app"])
    assert f.monitor.health(f.binding["binding_id"])["source_gap_count"] == (0 if variant == "same_source" else 1)
    assert path.read_bytes() == before
