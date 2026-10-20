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


def test_default_scanner_reconnects_only_its_owned_live_worker(tmp_path, monkeypatch):
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService

    clock = [0.0]
    monkeypatch.setattr("loopx.extensions.lark.goal_topic_runtime_service.time.monotonic", lambda: clock[0])
    observed = []
    source = SimpleNamespace(observe=lambda **_kwargs: observed.append(True) or ["owner-app"])

    def pending():
        clock[0] = 61
        return []

    service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=tmp_path,
        runtime_controller=object(), private_conversations=SimpleNamespace(pending_delivery_paths=pending, ingress=source))
    own, other = threading.Event(), threading.Event()
    service._workers = {"owner-app": (own, threading.current_thread(), ("", "", ())),
                        "other-app": (other, threading.current_thread(), ("", "", ()))}
    waited = threading.Event()
    waits = 0

    def wait(_seconds):
        nonlocal waits
        waits += 1
        if own.is_set() or waits >= 100:
            service._closed.set()
        else:
            waited.wait(0.01)

    monkeypatch.setattr(service._delivery_wakeup, "wait", wait)
    service._reconcile_private()
    assert observed == [True] and own.is_set() and not other.is_set()


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


@pytest.mark.parametrize("scenario", ["missing", "long_outage", "concurrent_observers", "historical"])
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
    elif scenario == "historical":
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
