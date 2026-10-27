"""Real native File/SQLite calendar -> Task -> Turn -> original Core result.

The model is a synthetic app-server that computes an independently checked
artifact. These cases do not qualify live-model understanding or production
deadlines, and do not enroll any user's existing conversation.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from test_native_steward_private import steward as steward_fixture, finish
from test_native_steward_turn_host import managed_fixture, cli
from loopx.capabilities.native_chat.steward_monitor import pump_steward_monitors
from loopx.capabilities.native_chat.session_calendar_tasks import SessionCalendarTasks
from loopx.capabilities.periodic_report.cadence_journal import read_cadence_journal
from loopx.capabilities.periodic_report.goal_configuration import normalize_configuration
from loopx.agent_registry import load_goal_from_registry

steward = steward_fixture


@contextmanager
def settings_server(steward):
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    store, runtime, _, transport, *_ = steward
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller = store, runtime
    server.runtime_root = runtime.coordination_runtime_root
    server.registry_path, server.runtime_root_override = runtime.registry_path, store.root.parent
    server.lark_private_conversations = transport
    server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=lambda: {}, close=lambda: None)
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(path, body, origin=None, *, method="POST"):
        connection = http.client.HTTPConnection(*server.server_address, timeout=20)
        try:
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            connection.request(method, path, None if body is None else json.dumps(body), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    try:
        yield request
    finally:
        server.shutdown()
        # This fixture lends the existing runtime to HTTP; the outer steward
        # fixture owns its lifetime and continues exercising the real pump.
        del server.runtime_controller
        server.server_close()
        thread.join(timeout=5)


def apply_configuration(request, goal_id, config):
    body = {"goal_id": goal_id, "capability_id": "periodic_report", "configuration": config}
    status, preview = request("/api/chat/goal-configuration/preview", body)
    assert status == 201, preview
    status, applied = request("/api/chat/goal-configuration/apply",
        {**body, "expected_plan_revision": preview["plan_revision"]})
    assert status == 200, applied
    return applied


def setup_task(steward, tmp_path, monkeypatch, provider, *, decision_scopes=()):
    _, session, actor, unrelated, _, capture = managed_fixture(steward, tmp_path, monkeypatch, provider)
    _, runtime, *_ = steward
    workspace = Path(load_goal_from_registry(runtime.registry_path, actor["goal_id"])["repo"])
    (workspace / "numbers.json").write_text("[19, 23]")
    script = tmp_path / "calendar-model.py"
    script.write_text('''import json
from pathlib import Path
def respond():
    values = json.loads(Path("numbers.json").read_text())
    Path("total.json").write_text(json.dumps({"total": sum(values)}))
    return json.dumps({"result_kind": "validated_completion", "classification": "implementation",
        "summary": "The independently validated total is 42.", "next_action": "Keep future independent periods scheduled.",
        "delivery_batch_scale": "implementation", "delivery_outcome": "outcome_progress",
        "vision_unchanged_reason": "The original task and its scope remain active.",
        "work_evidence": ["total.json computed from numbers.json"],
        "validation_evidence": ["The original independent argv checks the computed artifact"]})
''')
    fake = Path(runtime.codex_bin)
    # managed_fixture already installed its own synthetic result. Replace the
    # complete string value without deriving the expected artifact from it.
    source = fake.read_text()
    import re
    source, count = re.subn(r'"message": \'\{"result_kind".*?\}\'',
        '"message": __import__("runpy").run_path(' + repr(str(script)) + ')["respond"]()', source)
    assert count >= 1
    fake.write_text(source)
    runtime.adapters.pop(session["session_id"], None)
    validator = [sys.executable, "-c", "import json; from pathlib import Path; assert json.loads(Path('total.json').read_text()) == {'total': 42}"]
    for scope in decision_scopes:
        gate = cli(runtime, "todo", "add", "--goal-id", actor["goal_id"], "--role", "user",
            "--text", "Approve this synthetic task's recurring direction.", "--task-class", "user_gate",
            "--blocks-agent", actor["agent_id"], "--decision-scope", scope, "--execute")
        cli(runtime, "todo", "complete", "--goal-id", actor["goal_id"], "--role", "user",
            "--todo-id", gate["todo_id"], "--agent-id", actor["agent_id"], "--decision-outcome", "approve",
            "--evidence", "Synthetic owner standing approval for this exact task scope.")
    source_task = cli(runtime, "todo", "add", "--goal-id", actor["goal_id"], "--role", "agent",
        "--text", "Read numbers.json, compute its sum, and save total.json. Return the result here.",
        "--claimed-by", actor["agent_id"], "--task-class", "advancement_task", "--action-kind", "calculate_total",
        "--validation-command-json", json.dumps(validator),
        *(value for scope in decision_scopes for value in ("--required-decision-scope", scope)), "--execute")
    # The user is scheduling a repeat of an existing completed native task.
    # Calendar configuration cannot create a new host validator or silently
    # resume unrelated deferred/blocked work.
    from loopx.capabilities.native_chat.steward_managed_mode import ManagedSessionTurn
    first, created = ManagedSessionTurn(runtime).submit(session["session_id"], agent_id=actor["agent_id"],
        operation_id="completed-source-task", todo_id=source_task["todo_id"])
    assert created
    completed = finish(runtime, first)
    assert completed["status"] == "completed" and completed["response"]["native_loopx"]["status"] == "committed", completed
    config = {"enabled": True, "profile_preset": "session-task",
        "route_ref": "managed-session:" + session["session_id"], "timezone": "UTC",
        "schedule": {"schema_version": "periodic_report_schedule_v0", "schedule_id": "ordinary-task",
            "timezone": "UTC", "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "start_date": "2020-01-01"},
        "task": {"source_todo_id": source_task["todo_id"]}}
    return session, actor, unrelated, capture, workspace, config


def fixed_clock(monkeypatch):
    import loopx.capabilities.native_chat.session_calendar_tasks as module
    point = [datetime(2020, 1, 1, 9, 0, 1, tzinfo=timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return point[0].astimezone(tz)
    monkeypatch.setattr(module, "datetime", Clock)
    return point


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_calendar_preserves_canonical_decision_scopes_through_original_return(steward, tmp_path, monkeypatch, provider):
    from loopx.control_plane.todos.contract import normalize_todo_required_decision_scopes
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    from loopx.capabilities.periodic_report.private_generation import attempt_directory

    scopes = ("direction:goal:repeat_task", "other:goal:review_total")
    expected = normalize_todo_required_decision_scopes(scopes)
    session, actor, unrelated, capture, _, config = setup_task(
        steward, tmp_path, monkeypatch, provider, decision_scopes=scopes)
    store, runtime, *_ = steward
    sid = session["session_id"]
    service = SessionCalendarTasks(runtime)
    source, _ = service.source_task(actor, config["task"])
    assert source["status"] == "done" and source["required_decision_scopes"] == expected
    history = store.messages(sid)
    fixed_clock(monkeypatch)
    with settings_server(steward) as request:
        apply_configuration(request, actor["goal_id"], config)
        turns = pump_steward_monitors(runtime)
        assert len(turns) == 1, service.health(sid)
        completed = finish(runtime, turns[0])
        assert completed["status"] == "completed", completed
        assert completed["response"]["native_loopx"]["status"] == "committed", completed
        rows = StewardMonitor(runtime).todos(completed["loopx_request"]["actor"])
        child = next(row for row in rows if row["todo_id"] == completed["loopx_request"]["todo_id"])
        assert child["status"] == "done" and child["required_decision_scopes"] == expected
        assert next(row for row in rows if row["todo_id"] == unrelated)["status"] == "open"
        returned = [row for row in store.messages(sid) if row.get("turn_id") == completed["turn_id"] and row["role"] == "agent"]
        assert len(returned) == 1 and returned[0]["text"] == completed["response"]["message"]
        assert "42" in returned[0]["text"] and store.messages(sid)[:len(history)] == history
        after = capture.read_bytes()
        runtime._steward_monitor_next_check.clear()
        assert not pump_steward_monitors(runtime)
        assert capture.read_bytes() == after
        journal = read_cadence_journal(runtime_root=runtime.coordination_runtime_root, goal_id=actor["goal_id"])
        assert journal["task_completion"]["message_id"] == returned[0]["message_id"]
        frozen = json.loads((attempt_directory(runtime.coordination_runtime_root, journal["window"]) / "task.json").read_text())
        assert frozen["source_task"]["required_decision_scopes"] == expected


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_calendar_executes_ordinary_task_and_returns_original_result(steward, tmp_path, monkeypatch, provider):
    import loopx.capabilities.native_chat.session_calendar_tasks as module
    from loopx.capabilities.native_chat.steward_managed_mode import ManagedSessionTurn
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    session, actor, unrelated, capture, workspace, config = setup_task(steward, tmp_path, monkeypatch, provider)
    store, runtime, bot, *_ = steward
    sid = session["session_id"]
    point = datetime(2020, 1, 1, 8, 59, 59, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return point.astimezone(tz)
    monkeypatch.setattr(module, "datetime", Clock)
    original_frontier = module.SessionCalendarTasks.frontier
    def checked_frontier(service, session_id):
        try:
            return original_frontier(service, session_id)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            pytest.fail(f"The real calendar producer rejected its fixture: {exc}")
    monkeypatch.setattr(module.SessionCalendarTasks, "frontier", checked_frontier)
    history, calls, writes = store.messages(sid), capture.read_bytes(), list(bot.writes)
    assert "scheduled_task" not in ManagedSessionTurn(runtime).inspect(sid)
    with settings_server(steward) as request:
        apply_configuration(request, actor["goal_id"], config)
        assert ManagedSessionTurn(runtime).inspect(sid)["scheduled_task"]["configured"]
        assert capture.read_bytes() == calls
        assert not pump_steward_monitors(runtime)
        assert read_cadence_journal(runtime_root=runtime.coordination_runtime_root, goal_id=actor["goal_id"]) is None
        point += timedelta(seconds=2)
        runtime._steward_monitor_next_check.clear()
        turns = pump_steward_monitors(runtime)
        assert len(turns) == 1
        completed = finish(runtime, turns[0])
        assert completed["status"] == "completed", completed
        assert completed["response"]["native_loopx"]["status"] == "committed", completed
        assert completed["response"]["native_loopx"]["effects"]["quota_spent"]
        assert json.loads((workspace / "total.json").read_text()) == {"total": 42}
        assert store.messages(sid)[:len(history)] == history
        returned = [row for row in store.messages(sid) if row.get("turn_id") == completed["turn_id"] and row["role"] == "agent"]
        assert len(returned) == 1 and "42" in returned[0]["text"]
        rows = StewardMonitor(runtime).todos(completed["loopx_request"]["actor"])
        assert next(row for row in rows if row["todo_id"] == completed["loopx_request"]["todo_id"])["status"] == "done"
        assert next(row for row in rows if row["todo_id"] == unrelated)["status"] == "open"
        recursive = {**config, "task": {"source_todo_id": completed["loopx_request"]["todo_id"]}}
        assert request("/api/chat/goal-configuration/preview", {"goal_id": actor["goal_id"],
            "capability_id": "periodic_report", "configuration": recursive})[0] == 400
        after = capture.read_bytes()
        runtime._steward_monitor_next_check.clear()
        assert not pump_steward_monitors(runtime)
        journal = read_cadence_journal(runtime_root=runtime.coordination_runtime_root, goal_id=actor["goal_id"])
        assert journal["task_completion"]["message_id"] == returned[0]["message_id"]
        assert journal["publication"] is None
        assert capture.read_bytes() == after and bot.writes == writes
        point += timedelta(days=1)
        runtime._steward_monitor_next_check.clear()
        second = pump_steward_monitors(runtime)
        assert len(second) == 1 and second[0]["turn_id"] != completed["turn_id"]
        assert finish(runtime, second[0])["response"]["native_loopx"]["status"] == "committed"


@pytest.mark.parametrize("fault", ["wrong-route", "missing-template", "report-profile", "injected-command", "no-calendar"])
def test_task_declarations_never_enroll_other_routes_or_implicit_commands(fault):
    config = {"enabled": True, "profile_preset": "session-task", "route_ref": "managed-session:" + "a" * 32,
        "timezone": "UTC", "task": {"source_todo_id": "todo_original"},
        "schedule": {"schema_version": "periodic_report_schedule_v0", "schedule_id": "ordinary-task",
            "timezone": "UTC", "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "start_date": "2020-01-01"}}
    if fault == "wrong-route":
        config["route_ref"] = "external-channel"
    elif fault == "missing-template":
        config["task"].pop("source_todo_id")
    elif fault == "report-profile":
        config["profile_preset"] = "weekly-progress"
    elif fault == "injected-command":
        config["task"]["validation_command"] = ["untrusted-command"]
    else:
        config.pop("schedule")
    with pytest.raises((ValueError, TypeError)):
        normalize_configuration(config)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_known_failed_repeat_is_retained_without_replay_then_explicitly_cancelled(steward, tmp_path, monkeypatch, provider):
    session, actor, _, capture, workspace, config = setup_task(steward, tmp_path, monkeypatch, provider)
    point = fixed_clock(monkeypatch)
    store, runtime, bot, *_ = steward
    sid = session["session_id"]
    # The synthetic model claims success; its actual artifact is wrong. The
    # independent native validator must hold completion and keep that failure.
    (workspace / "numbers.json").write_text("[1, 2]")
    with settings_server(steward) as request:
        apply_configuration(request, actor["goal_id"], config)
        failed = finish(runtime, pump_steward_monitors(runtime)[0])
        assert failed["status"] == "completed" and failed["response"]["native_loopx"]["status"] == "failed", failed
        assert json.loads((workspace / "total.json").read_text()) == {"total": 3}
        calls, writes, messages = capture.read_bytes(), list(bot.writes), store.messages(sid)
        for _ in range(2):
            runtime._steward_monitor_next_check.clear()
            assert not pump_steward_monitors(runtime)
        assert capture.read_bytes() == calls and bot.writes == writes and store.messages(sid) == messages
        apply_configuration(request, actor["goal_id"], None)
        journal = read_cadence_journal(runtime_root=runtime.coordination_runtime_root, goal_id=actor["goal_id"])
        assert journal["retirement"]["reason"] == "owner_cancelled_failed_task" and journal["publication"] is None
        apply_configuration(request, actor["goal_id"], config)
        runtime._steward_monitor_next_check.clear()
        assert not pump_steward_monitors(runtime)  # Re-enable cannot replay this failed period.
        (workspace / "numbers.json").write_text("[19, 23]")
        point[0] += timedelta(days=1)
        runtime._steward_monitor_next_check.clear()
        completed = finish(runtime, pump_steward_monitors(runtime)[0])
        assert completed["turn_id"] != failed["turn_id"]
        assert completed["response"]["native_loopx"]["status"] == "committed", completed
        assert store.load_turn(sid, failed["turn_id"]) == failed


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_settings_only_select_existing_completed_source_and_current_preview(steward, tmp_path, monkeypatch, provider):
    session, actor, incomplete, capture, _, config = setup_task(steward, tmp_path, monkeypatch, provider)
    _, runtime, bot, *_ = steward
    calls, writes = capture.read_bytes(), list(bot.writes)
    with settings_server(steward) as request:
        body = {"goal_id": actor["goal_id"], "capability_id": "periodic_report", "configuration": config}
        injected = {**config, "task": {**config["task"], "validation_command": ["untrusted-command"]}}
        assert request("/api/chat/goal-configuration/preview", {**body, "configuration": injected})[0] in (400, 409)
        for source in [incomplete, "todo_missing"]:
            denied = {**config, "task": {"source_todo_id": source}}
            assert request("/api/chat/goal-configuration/preview", {**body, "configuration": denied})[0] == 400
        assert request("/api/chat/goal-configuration/preview", body, "https://foreign.example")[0] == 403
        status, preview = request("/api/chat/goal-configuration/preview", body)
        assert status == 201
        assert request("/api/chat/goal-configuration/apply", {**body,
            "configuration": {**config, "enabled": False}, "expected_plan_revision": preview["plan_revision"]})[0] == 409
    assert capture.read_bytes() == calls and bot.writes == writes
    assert not SessionCalendarTasks(runtime).health(session["session_id"])["configured"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["pause", "reschedule", "clear"])
@pytest.mark.parametrize("readback_fault", ["unavailable", "mismatch"])
def test_calendar_settings_report_persisted_changes_when_readback_is_unverified(
        steward, tmp_path, monkeypatch, provider, change, readback_fault):
    from loopx.chat_server import ChatRequestHandler
    from loopx.capabilities.periodic_report.goal_configuration import configuration_summary

    session, actor, _, capture, _, config = setup_task(steward, tmp_path, monkeypatch, provider)
    store, runtime, bot, *_ = steward
    sid, goal_id = session["session_id"], actor["goal_id"]
    target = ({**config, "enabled": False} if change == "pause" else
        {**config, "schedule": {**config["schedule"], "rrule": "FREQ=DAILY;BYHOUR=10;BYMINUTE=0"}}
        if change == "reschedule" else None)
    calls, writes, messages = capture.read_bytes(), list(bot.writes), store.messages(sid)
    with settings_server(steward) as request:
        apply_configuration(request, goal_id, config)
        body = {"goal_id": goal_id, "capability_id": "periodic_report", "configuration": target}
        status, preview = request("/api/chat/goal-configuration/preview", body)
        assert status == 201, preview
        original_read = ChatRequestHandler._read_public_goal_configuration

        def unavailable_after_source_write(handler, requested_goal, *args, **kwargs):
            current = configuration_summary(load_goal_from_registry(runtime.registry_path, goal_id))
            if requested_goal == goal_id and current == (normalize_configuration(target) if target else None):
                if readback_fault == "unavailable":
                    raise OSError("synthetic private readback diagnostic must stay server-side")
                value = original_read(handler, requested_goal, *args, **kwargs)
                entry = next(item for item in value["capability_catalog"]["capabilities"]
                    if item["capability_id"] == "periodic_report")
                entry["current"] = {"enabled": True}
                return value
            return original_read(handler, requested_goal, *args, **kwargs)

        with monkeypatch.context() as fault:
            fault.setattr(ChatRequestHandler, "_read_public_goal_configuration", unavailable_after_source_write)
            status, receipt = request("/api/chat/goal-configuration/apply",
                {**body, "expected_plan_revision": preview["plan_revision"]})
        # Read the actual source independently of the failed response path.
        assert configuration_summary(load_goal_from_registry(runtime.registry_path, goal_id)) == (
            normalize_configuration(target) if target else None)
        assert status == 207, receipt
        assert receipt["status"] == "partial_write" and receipt["source_written"] is True
        assert receipt["readback_verified"] is False and receipt["applied_revision"] is None
        assert receipt["goal_configuration"] == (None if readback_fault == "unavailable" else {"enabled": True})
        assert receipt["capability_id"] == "periodic_report" and receipt["goal_id"] == goal_id
        assert "prior state was preserved" not in receipt["error"]
        assert "synthetic private" not in json.dumps(receipt)
        assert "Reload" in receipt["recommended_action"]
        # A normal read/re-preview settles the uncertainty without replaying a
        # write, a model turn, a scheduled period or an external delivery.
        status, loaded = request("/api/chat/goal-configuration?goal_id=" + goal_id, None, method="GET")
        assert status == 200, loaded
        status, refreshed = request("/api/chat/goal-configuration/preview", body)
        assert status == 201 and refreshed["writes_required"] == 0, refreshed
    assert capture.read_bytes() == calls and bot.writes == writes and store.messages(sid) == messages
    assert read_cadence_journal(runtime_root=runtime.coordination_runtime_root, goal_id=goal_id) is None


@pytest.mark.parametrize("fault", ["queued", "busy", "paused", "unregistered", "stopped"])
def test_original_user_work_and_withdrawn_authority_win_without_model(steward, tmp_path, monkeypatch, fault):
    session, actor, _, capture, _, config = setup_task(steward, tmp_path, monkeypatch, "sqlite")
    fixed_clock(monkeypatch)
    store, runtime, bot, *_ = steward
    with settings_server(steward) as request:
        apply_configuration(request, actor["goal_id"], {**config, "enabled": fault != "paused"})
    if fault == "queued":
        store.create_queued_turn(session["session_id"], client_turn_id="owner-first", message="Retain this user work first", origin="web")
    elif fault == "busy":
        store.accept_managed_turn(session["session_id"], client_turn_id="owner-active", message="Original accepted user work", origin="web")
    elif fault in {"unregistered", "stopped"}:
        registry = json.loads(runtime.registry_path.read_text())
        goal = next(row for row in registry["goals"] if row["id"] == actor["goal_id"])
        if fault == "stopped":
            goal["status"] = "stopped"
        else:
            goal["coordination"]["registered_agents"] = ["peer"]
        runtime.registry_path.write_text(json.dumps(registry))
    calls, writes, messages = capture.read_bytes(), list(bot.writes), store.messages(session["session_id"])
    assert not runtime.closed.is_set()
    assert not pump_steward_monitors(runtime)
    assert capture.read_bytes() == calls and bot.writes == writes and store.messages(session["session_id"]) == messages
    assert read_cadence_journal(runtime_root=runtime.coordination_runtime_root, goal_id=actor["goal_id"]) is None


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_completed_task_and_original_body_reconcile_after_runtime_restart(steward, tmp_path, monkeypatch, provider):
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    import loopx.capabilities.native_chat.session_calendar_tasks as module
    session, actor, _, capture, _, config = setup_task(steward, tmp_path, monkeypatch, provider)
    fixed_clock(monkeypatch)
    store, runtime, bot, *_ = steward
    with settings_server(steward) as request:
        apply_configuration(request, actor["goal_id"], config)
    completed = finish(runtime, pump_steward_monitors(runtime)[0])
    assert completed["response"]["native_loopx"]["status"] == "committed"
    original = module.record_cadence_task_completion
    def lost_response(**kwargs):
        original(**kwargs)
        raise OSError("synthetic lost completion acknowledgement")
    monkeypatch.setattr(module, "record_cadence_task_completion", lost_response)
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)
    monkeypatch.setattr(module, "record_cadence_task_completion", original)
    runtime.close()
    before = capture.read_bytes(), store.messages(session["session_id"]), list(bot.writes)
    recovered = ChatRuntimeController(store=ChatSessionStore(store.root.parent), codex_bin=runtime.codex_bin,
        project_contexts=runtime.project_contexts, registry_path=runtime.registry_path)
    try:
        assert SessionCalendarTasks(recovered).frontier(session["session_id"]) == (None, False)
        assert SessionCalendarTasks(recovered).health(session["session_id"])["completion_verified"]
        assert (capture.read_bytes(), recovered.store.messages(session["session_id"]), list(bot.writes)) == before
    finally:
        recovered.close()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_archived_source_and_completed_period_remain_usable_without_replay(steward, tmp_path, monkeypatch, provider):
    session, actor, _, capture, _, config = setup_task(steward, tmp_path, monkeypatch, provider)
    point = fixed_clock(monkeypatch)
    _, runtime, *_ = steward
    with settings_server(steward) as request:
        apply_configuration(request, actor["goal_id"], config)
    first = finish(runtime, pump_steward_monitors(runtime)[0])
    assert first["response"]["native_loopx"]["status"] == "committed"
    cli(runtime, "todo", "archive-completed", "--goal-id", actor["goal_id"], "--role", "agent",
        "--max-active-done", "0", "--execute")
    calls = capture.read_bytes()
    service = SessionCalendarTasks(runtime)
    assert service.frontier(session["session_id"]) == (None, False)
    assert service.health(session["session_id"])["completion_verified"] and capture.read_bytes() == calls
    point[0] += timedelta(days=1)
    turn, created = service.frontier(session["session_id"])
    assert created and turn["turn_id"] != first["turn_id"]
    assert finish(runtime, turn)["response"]["native_loopx"]["status"] == "committed"


@pytest.mark.parametrize("fault", ["text", "scope", "paused", "ordinary-entry"])
def test_frozen_task_cannot_change_work_or_scope_before_dispatch(steward, tmp_path, monkeypatch, fault):
    from loopx.capabilities.native_chat.steward_managed_mode import ManagedSessionTurn
    session, actor, _, capture, _, config = setup_task(steward, tmp_path, monkeypatch, "sqlite")
    fixed_clock(monkeypatch)
    _, runtime, bot, *_ = steward
    with settings_server(steward) as request:
        apply_configuration(request, actor["goal_id"], config)
    service = SessionCalendarTasks(runtime)
    with monkeypatch.context() as before_launch:
        before_launch.setattr(ManagedSessionTurn, "submit", lambda *_a, **_kw: (None, False))
        assert service.frontier(session["session_id"]) == (None, False)
    window = read_cadence_journal(runtime_root=runtime.coordination_runtime_root, goal_id=actor["goal_id"])["window"]
    todo = service._todo(service.selected(session["session_id"])["actor"], window)
    if fault in {"text", "scope"}:
        cli(runtime, "todo", "update", "--goal-id", actor["goal_id"], "--todo-id", todo["todo_id"],
            "--agent-id", actor["agent_id"], "--update-operation-id", "change-frozen-task",
            *(["--text", "Run a different task"] if fault == "text" else ["--required-capability", "filesystem"]))
    else:
        with settings_server(steward) as request:
            apply_configuration(request, actor["goal_id"], {**config, "enabled": False})
    calls, writes = capture.read_bytes(), list(bot.writes)
    if fault == "ordinary-entry":
        accepted, created = ManagedSessionTurn(runtime).submit(session["session_id"], agent_id=actor["agent_id"],
            operation_id="ordinary-bypass", todo_id=todo["todo_id"])
        assert created
        rejected = finish(runtime, accepted)
        assert rejected["response"]["native_loopx"]["status"] == "failed", rejected
    else:
        with pytest.raises(ValueError, match="subscription changed" if fault == "paused" else "scheduled work requires"):
            ManagedSessionTurn(runtime).submit(session["session_id"], agent_id=actor["agent_id"],
                operation_id="task." + window["window_id"], todo_id=todo["todo_id"], scheduled_window=window)
    assert capture.read_bytes() == calls and bot.writes == writes


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_committed_calendar_result_survives_lost_event_ack_without_reexecution(steward, tmp_path, monkeypatch, provider):
    session, actor, _, capture, workspace, config = setup_task(steward, tmp_path, monkeypatch, provider)
    point = fixed_clock(monkeypatch)
    store, runtime, bot, *_ = steward
    sid = session["session_id"]
    with settings_server(steward) as request:
        apply_configuration(request, actor["goal_id"], config)
        append, lost = store.append_event, []
        before = capture.read_bytes()
        def lose_ack_after_write(*args, **kwargs):
            result = append(*args, **kwargs)
            if kwargs.get("kind") == "native_loopx.result" and not lost:
                lost.append(True)
                raise OSError("synthetic lost acknowledgment after the committed result event persisted")
            return result
        with monkeypatch.context() as lost_ack:
            lost_ack.setattr(store, "append_event", lose_ack_after_write)
            completed = finish(runtime, pump_steward_monitors(runtime)[0])
        assert lost and completed["status"] == "completed", completed
        assert completed["response"]["native_loopx"]["status"] == "committed"
        assert completed["response"]["native_loopx"]["effects"]["quota_spent"] is True
        result_events = [row for row in store.events_after(sid, completed["turn_id"], None)
            if row["kind"] == "native_loopx.result"]
        assert len(result_events) == 1
        assert result_events[0]["payload"] == completed["response"]["native_loopx"]
        messages = [row for row in store.messages(sid)
            if row.get("turn_id") == completed["turn_id"] and row["role"] == "agent"]
        assert len(messages) == 1 and messages[0]["text"] == "The independently validated total is 42."
        status, conversation = request("/api/chat/sessions/" + sid, None, method="GET")
        assert status == 200 and conversation["active_turn"] is None
        assert messages[0] in conversation["messages"]
        calls = [json.loads(line) for line in capture.read_bytes()[len(before):].decode().splitlines()]
        assert len([row for row in calls if row.get("method") == "turn/start"]) == 1
        after = capture.read_bytes(), store.messages(sid), list(bot.writes)
        for _ in range(2):
            runtime._steward_monitor_next_check.clear()
            assert not pump_steward_monitors(runtime)
        journal = read_cadence_journal(runtime_root=runtime.coordination_runtime_root, goal_id=actor["goal_id"])
        assert journal["task_completion"]["message_id"] == messages[0]["message_id"]
        assert (capture.read_bytes(), store.messages(sid), list(bot.writes)) == after
        (workspace / "total.json").write_text('{"total": -1}')
        point[0] += timedelta(days=1)
        runtime._steward_monitor_next_check.clear()
        second = finish(runtime, pump_steward_monitors(runtime)[0])
        assert second["turn_id"] != completed["turn_id"]
        assert second["response"]["native_loopx"]["status"] == "committed"
        assert json.loads((workspace / "total.json").read_text()) == {"total": 42}
        calls = [json.loads(line) for line in capture.read_bytes()[len(before):].decode().splitlines()]
        assert len([row for row in calls if row.get("method") == "turn/start"]) == 2


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_unknown_postcommit_return_never_becomes_cancelled_failure_or_model_replay(steward, tmp_path, monkeypatch, provider):
    session, actor, _, capture, _, config = setup_task(steward, tmp_path, monkeypatch, provider)
    fixed_clock(monkeypatch)
    store, runtime, bot, *_ = steward
    with settings_server(steward) as request:
        apply_configuration(request, actor["goal_id"], config)
        append = store.append_event
        lost = []
        def lose_native_return(*args, **kwargs):
            if kwargs.get("kind") == "native_loopx.result" and not lost:
                lost.append(True)
                raise OSError("synthetic missing Chat result event after the original native commit")
            return append(*args, **kwargs)
        with monkeypatch.context() as lost_return:
            lost_return.setattr(store, "append_event", lose_native_return)
            failed = finish(runtime, pump_steward_monitors(runtime)[0])
        assert failed["status"] == "failed" and lost
        before = capture.read_bytes(), store.messages(session["session_id"]), list(bot.writes)
        for _ in range(2):
            runtime._steward_monitor_next_check.clear()
            assert not pump_steward_monitors(runtime)
        assert not SessionCalendarTasks(runtime).health(session["session_id"])["completion_verified"]
        body = {"goal_id": actor["goal_id"], "capability_id": "periodic_report", "configuration": None}
        assert request("/api/chat/goal-configuration/preview", body)[0] == 400
        assert load_goal_from_registry(runtime.registry_path, actor["goal_id"])["control_plane"]["periodic_report"] == config
        assert (capture.read_bytes(), store.messages(session["session_id"]), list(bot.writes)) == before
        assert store.load_turn(session["session_id"], failed["turn_id"]) == failed


@pytest.mark.parametrize("fault", ["conflicting", "duplicate", "unreadable", "uncommitted"])
def test_lost_result_ack_requires_one_exact_readable_commit(steward, tmp_path, monkeypatch, fault):
    session, actor, _, capture, _, config = setup_task(steward, tmp_path, monkeypatch, "sqlite")
    fixed_clock(monkeypatch)
    store, runtime, bot, *_ = steward
    if fault == "uncommitted":
        script = tmp_path / "calendar-model.py"
        script.write_text(script.read_text().replace('"result_kind": "validated_completion"',
            '"result_kind": "validated_progress"'))
    with settings_server(steward) as request:
        apply_configuration(request, actor["goal_id"], config)
        append, read, lost = store.append_event, store.events_after, []
        def lose_ack(*args, **kwargs):
            if kwargs.get("kind") != "native_loopx.result" or lost:
                return append(*args, **kwargs)
            lost.append(args[1])
            observed = {**kwargs, "payload": {**kwargs["payload"], "ok": False}} if fault == "conflicting" else kwargs
            append(*args, **observed)
            if fault == "duplicate":
                append(*args, **kwargs)
            raise OSError("synthetic result acknowledgment unavailable")
        def unavailable_read(session_id, turn_id, event_id):
            if fault == "unreadable" and turn_id in lost:
                raise OSError("synthetic result readback unavailable")
            return read(session_id, turn_id, event_id)
        with monkeypatch.context() as lost_ack:
            lost_ack.setattr(store, "append_event", lose_ack)
            lost_ack.setattr(store, "events_after", unavailable_read)
            failed = finish(runtime, pump_steward_monitors(runtime)[0])
        assert lost and failed["status"] == "failed", failed
        before = capture.read_bytes(), store.messages(session["session_id"]), list(bot.writes)
        for _ in range(2):
            runtime._steward_monitor_next_check.clear()
            assert not pump_steward_monitors(runtime)
        assert not SessionCalendarTasks(runtime).health(session["session_id"])["completion_verified"]
        assert (capture.read_bytes(), store.messages(session["session_id"]), list(bot.writes)) == before
        assert store.load_turn(session["session_id"], failed["turn_id"]) == failed


def test_partial_progress_keeps_period_open_without_an_automatic_retry(steward, tmp_path, monkeypatch):
    session, actor, _, capture, _, config = setup_task(steward, tmp_path, monkeypatch, "sqlite")
    fixed_clock(monkeypatch)
    _, runtime, *_ = steward
    script = tmp_path / "calendar-model.py"
    script.write_text(script.read_text().replace('"result_kind": "validated_completion"', '"result_kind": "validated_progress"'))
    with settings_server(steward) as request:
        apply_configuration(request, actor["goal_id"], config)
        completed = finish(runtime, pump_steward_monitors(runtime)[0])
        # This native Task requires declared completion before accountable
        # writeback. Partial model work is retained at that gate, not treated
        # as a completed calendar period or permission to invoke it again.
        native = completed["response"]["native_loopx"]
        assert native["ok"] is False and native.get("status") != "committed", completed
        assert native["journal_observation"]["recorded_effects"]["quota_spent"] is False
        service = SessionCalendarTasks(runtime)
        assert not service.health(session["session_id"])["completion_verified"]
        calls = capture.read_bytes()
        runtime._steward_monitor_next_check.clear()
        assert not pump_steward_monitors(runtime) and capture.read_bytes() == calls
        journal = read_cadence_journal(runtime_root=runtime.coordination_runtime_root, goal_id=actor["goal_id"])
        assert not journal.get("task_completion") and journal["publication"] is None
        assert service._todo(service.selected(session["session_id"])["actor"], journal["window"])["status"] == "open"
        assert request("/api/chat/goal-configuration/preview", {"goal_id": actor["goal_id"],
            "capability_id": "periodic_report", "configuration": None})[0] == 400
