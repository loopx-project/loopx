"""Original Chat + real calendar/Todo/Turn/report owners; synthetic model/Lark.

These tests certify mechanics and original-audience readback, not the quality
of a live Agent's report or a natural production deadline.
"""
from datetime import datetime, timedelta
import inspect
import json
from pathlib import Path

import pytest

from test_native_steward_managed_mode import original, storage
from test_native_steward_private import steward as steward_fixture, finish
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from loopx.capabilities.native_chat.steward_monitor import pump_steward_monitors
from loopx.capabilities.native_chat.steward_reports import StewardReports
from loopx.capabilities.periodic_report.cadence_journal import read_cadence_journal
from loopx.capabilities.periodic_report.private_generation import attempt_directory, validate_private_generation
from loopx.capabilities.periodic_report.incremental import read_periodic_report_publication_cursor

steward = steward_fixture


def configured_local_report(steward, tmp_path, monkeypatch, provider="sqlite", start_date="2001-01-01"):
    """An ordinary local Goal/Agent Session, with no report-enabled private binding."""
    from loopx.agent_registry import load_goal_from_registry
    from loopx.configure_goal import configure_goal
    from loopx.capabilities.native_chat.steward_reports import ManagedSessionReports
    from test_native_steward_turn_host import cli
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, _, _, binding, capture, _ = steward
    sid, managed = original(steward)
    storage(runtime, provider)
    plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(binding["binding_id"], session_id=sid,
        expected_plan_id=plan["plan_id"], execute=True)["actor"]
    workspace = Path(load_goal_from_registry(runtime.registry_path, actor["goal_id"])["repo"])
    session, _ = runtime.open_session(goal_id=actor["goal_id"], agent_id="codex", work_dir=workspace,
        objective="Report this Goal's original evidence", channel_id=f"goal.{actor['goal_id']}",
        mode="new", managed_agent_id=actor["agent_id"])
    task = cli(runtime, "todo", "add", "--goal-id", actor["goal_id"], "--role", "agent",
        "--claimed-by", actor["agent_id"], "--text", "Keep this original work and its evidence visible.",
        "--action-kind", "inspect", "--execute")
    schedule = {"schema_version": "periodic_report_schedule_v0", "schedule_id": "local-report",
        "timezone": "UTC", "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "start_date": start_date}
    config = {"enabled": True, "profile_preset": "weekly-progress", "route_ref": "managed-session:" + session["session_id"],
        "timezone": "UTC", "schedule": schedule}
    assert configure_goal(registry_path=runtime.registry_path, goal_id=actor["goal_id"],
        periodic_report_configuration=config, execute=True)["ok"]
    return session, ManagedSessionReports(runtime), config, task["todo_id"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_local_agent_calendar_report_uses_original_worker_and_verified_core_return(steward, tmp_path, monkeypatch, provider):
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    from loopx.capabilities.native_chat.steward_managed_mode import ManagedSessionTurn
    session, reports, _, task_id = configured_local_report(steward, tmp_path, monkeypatch, provider)
    store, runtime, bot, _, _, capture, _ = steward
    sid = session["session_id"]
    with monkeypatch.context() as changed_source:
        changed_source.setattr(inspect, "getsource", lambda *_: "not the imported model callable")
        install_model(steward, tmp_path, sid)
    # A host's default codec must not decide how frozen Chinese artifacts read.
    real_read_text = Path.read_text
    def host_codec_read_text(path, *args, **kwargs):
        if path.name in {"editorial-request.json", "publication-candidate.json", "source.json", "report.md"} and not args and "encoding" not in kwargs:
            kwargs["encoding"] = "ascii"
        return real_read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", host_codec_read_text)
    # This local report neither consumes nor borrows the private provider.
    runtime.project_contexts.conversation_bindings = None
    before, calls, writes = store.messages(sid), capture.read_bytes(), list(bot.writes)
    turns = pump_steward_monitors(runtime)
    assert len(turns) == 1
    result = finish(runtime, turns[0])
    assert result["status"] == "completed", result
    native = result["response"]["native_loopx"]
    assert native["status"] == "committed", native
    assert result["response"]["periodic_report"]["delivery_verified"]
    window = read_cadence_journal(runtime_root=reports.root, goal_id=session["goal_id"])["window"]
    folder = attempt_directory(reports.root, window)
    frozen = json.loads((folder / "editorial-request.json").read_text(encoding="utf-8"))
    source = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    assert frozen["destination"] == "original_managed_session_full_body"
    assert source["goal_id"] == session["goal_id"] and source["complete_native_task_read"]
    assert task_id in {row["todo_id"] for row in source["rows"]}
    assert frozen["actual_work_window"]["end_at"] == window["due_at"]
    assert datetime.fromisoformat(window["due_at"]) - datetime.fromisoformat(window["start_at"]) == timedelta(days=1)
    body = (folder / "report.md").read_text(encoding="utf-8")
    messages = store.messages(sid)
    assert messages[:len(before)] == before
    returned = [row for row in messages if row["role"] == "agent" and row["text"] == body]
    assert len(returned) == 1 and returned[0]["turn_id"] == result["turn_id"]
    cursor = read_periodic_report_publication_cursor(runtime_root=reports.root, goal_id=session["goal_id"], agent_id=session["managed_agent_id"])
    assert cursor["generation_id"] == validate_private_generation(folder)["generation_id"]
    assert cursor["covered_until"] == window["due_at"] and reports.health(sid)["publication_verified"]
    rows = StewardMonitor(runtime).todos(result["loopx_request"]["actor"])
    assert next(row for row in rows if row["todo_id"] == result["loopx_request"]["todo_id"])["status"] == "done"
    assert next(row for row in rows if row["todo_id"] == task_id)["status"] == "open"
    assert bot.writes == writes
    after_calls = capture.read_bytes()
    assert len([json.loads(line) for line in after_calls[len(calls):].splitlines()
        if json.loads(line).get("method") == "turn/start"]) == 1
    assert not pump_steward_monitors(runtime)
    assert reports.publish(sid, window)
    assert store.messages(sid) == messages and capture.read_bytes() == after_calls
    inspected = ManagedSessionTurn(runtime).inspect(sid)
    assert inspected["report"]["publication_verified"] and inspected["available"]
    assert inspected["report"]["report_message_id"] == returned[0]["message_id"]
    # A publication cursor alone cannot offer a body that is no longer readable.
    with monkeypatch.context() as unreadable_body:
        unreadable_body.setattr(store, "messages", lambda *_: [row for row in messages
            if row["message_id"] != returned[0]["message_id"]])
        unavailable = reports.health(sid)
        assert not unavailable["publication_verified"] and unavailable["report_message_id"] is None


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_local_agent_report_before_first_deadline_is_effect_free(steward, tmp_path, monkeypatch, provider):
    session, reports, _, _ = configured_local_report(steward, tmp_path, monkeypatch, provider, start_date="2999-01-01")
    store, runtime, bot, *_ = steward
    before = store.messages(session["session_id"]), list(bot.writes)
    assert not pump_steward_monitors(runtime)
    assert read_cadence_journal(runtime_root=reports.root, goal_id=session["goal_id"]) is None
    assert (store.messages(session["session_id"]), list(bot.writes)) == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("lost_after_commit", [False, True])
def test_local_report_restart_reads_back_unknown_publication_without_model_or_duplicate(steward, tmp_path, monkeypatch, provider, lost_after_commit):
    from loopx.capabilities.periodic_report import incremental
    from loopx.chat_store import ChatSessionStore
    from loopx.chat_runtime import ChatRuntimeController
    session, reports, _, _ = configured_local_report(steward, tmp_path, monkeypatch, provider)
    store, runtime, bot, *_ = steward
    sid = session["session_id"]
    install_model(steward, tmp_path, sid)
    runtime.project_contexts.conversation_bindings = None
    commit = incremental.commit_periodic_report_publication_cursor
    def interrupted_commit(**kwargs):
        if lost_after_commit:
            commit(**kwargs)
        raise OSError("Synthetic publication reply lost after the original Core message")
    with monkeypatch.context() as failure:
        failure.setattr(incremental, "commit_periodic_report_publication_cursor", interrupted_commit)
        first = finish(runtime, pump_steward_monitors(runtime)[0])
    assert first["status"] == "failed"
    window = read_cadence_journal(runtime_root=reports.root, goal_id=session["goal_id"])["window"]
    folder = attempt_directory(reports.root, window)
    body = (folder / "report.md").read_text()
    original_turn = store.load_turn(sid, first["turn_id"])
    assert len([row for row in store.messages(sid) if row["text"] == body]) == 1
    calls, writes = steward[5].read_bytes(), list(bot.writes)
    runtime.close()
    restarted = ChatRuntimeController(store=ChatSessionStore(store.root.parent), codex_bin=runtime.codex_bin,
        registry_path=runtime.registry_path, project_contexts=runtime.project_contexts)
    try:
        assert not pump_steward_monitors(restarted)
        assert store.load_turn(sid, first["turn_id"]) == original_turn
        assert len([row for row in store.messages(sid) if row["text"] == body]) == 1
        cursor = read_periodic_report_publication_cursor(runtime_root=reports.root,
            goal_id=session["goal_id"], agent_id=session["managed_agent_id"])
        assert cursor["generation_id"] == validate_private_generation(folder)["generation_id"]
        assert steward[5].read_bytes() == calls and bot.writes == writes
    finally:
        restarted.close()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_local_frozen_report_recovers_only_native_validation_and_returns_full_body(steward, tmp_path, monkeypatch, provider):
    from loopx.capabilities.native_chat.steward_turn import StewardTurnHost
    session, reports, _, _ = configured_local_report(steward, tmp_path, monkeypatch, provider)
    store, runtime, bot, *_ = steward
    sid = session["session_id"]
    install_model(steward, tmp_path, sid)
    runtime.project_contexts.conversation_bindings = None
    with monkeypatch.context() as failure:
        failure.setattr(StewardTurnHost, "_report_validator", lambda *_: lambda *_: {
            "status": "failed", "validator_kind": "callback", "summary": "Synthetic validator unavailable", "recovery_kind": "repair_required"})
        original = finish(runtime, pump_steward_monitors(runtime)[0])
    assert original["response"]["native_loopx"]["result_kind"] == "validation_failed"
    window = read_cadence_journal(runtime_root=reports.root, goal_id=session["goal_id"])["window"]
    folder = attempt_directory(reports.root, window)
    calls, messages, writes = steward[5].read_bytes(), store.messages(sid), list(bot.writes)
    assert not any(row["text"] == (folder / "report.md").read_text() for row in messages)
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)
    assert store.load_turn(sid, original["turn_id"]) == original
    assert store.messages(sid)[:len(messages)] == messages
    assert len([row for row in store.messages(sid) if row["text"] == (folder / "report.md").read_text()]) == 1
    assert reports.health(sid)["publication_verified"]
    assert steward[5].read_bytes() == calls and bot.writes == writes


@pytest.mark.parametrize("fault", ["paused", "unscheduled", "wrong_session", "wrong_profile", "unregistered", "busy"])
def test_local_report_denied_sources_never_launch_an_alternative_worker(steward, tmp_path, monkeypatch, fault):
    from loopx.configure_goal import configure_goal
    session, reports, config, _ = configured_local_report(steward, tmp_path, monkeypatch)
    store, runtime, bot, *_ = steward
    before = store.messages(session["session_id"]), steward[5].read_bytes(), list(bot.writes)
    if fault in {"paused", "unscheduled", "wrong_session", "wrong_profile"}:
        changed = {**config, **({"enabled": False} if fault == "paused" else
            {"schedule": None} if fault == "unscheduled" else
            {"route_ref": "managed-session:" + "b" * 32} if fault == "wrong_session" else
            {"profile_preset": "portfolio-progress", "route_ref": "another-original-route"})}
        assert configure_goal(registry_path=runtime.registry_path, goal_id=session["goal_id"],
            periodic_report_configuration=changed, execute=True)["ok"]
    elif fault == "unregistered":
        assert configure_goal(registry_path=runtime.registry_path, goal_id=session["goal_id"],
            registered_agents=["another-agent"], execute=True)["ok"]
    else:
        store.accept_managed_turn(session["session_id"], client_turn_id="original-busy-user-work",
            message="Keep this user's current work", origin="web")
        before = store.messages(session["session_id"]), steward[5].read_bytes(), list(bot.writes)
    assert not pump_steward_monitors(runtime)
    assert (store.messages(session["session_id"]), steward[5].read_bytes(), list(bot.writes)) == before
    assert read_cadence_journal(runtime_root=reports.root, goal_id=session["goal_id"]) is None
    if fault == "unscheduled":
        assert reports.health(session["session_id"])["enabled"]
        assert reports.health(session["session_id"])["scheduled"] is False


def test_local_report_configuration_retains_frozen_window_and_original_agent_election(steward, tmp_path, monkeypatch):
    from loopx.capabilities.native_chat.steward_reports import session_report_configuration_guard
    from loopx.capabilities.periodic_report.cadence_runtime import periodic_report_cadence_hooks
    from loopx.capabilities.periodic_report.pending_intent import consume_pending_periodic_report_intent
    from loopx.capabilities.native_chat.steward_turn import StewardTurnHost
    from loopx.configure_goal import configure_goal
    session, reports, config, _ = configured_local_report(steward, tmp_path, monkeypatch)
    store, runtime, *_ = steward
    # Another Agent's ordinary CLI guard cannot elect itself for this route.
    assert configure_goal(registry_path=runtime.registry_path, goal_id=session["goal_id"],
        registered_agents=["first-registration", session["managed_agent_id"]], execute=True)["ok"]
    assert not periodic_report_cadence_hooks(registry_path=runtime.registry_path, runtime_root=reports.root,
        goal_id=session["goal_id"], agent_id="first-registration")
    assert read_cadence_journal(runtime_root=reports.root, goal_id=session["goal_id"]) is None
    install_model(steward, tmp_path, session["session_id"])
    # Retain a generated but uncommitted window under its real native journal.
    with monkeypatch.context() as failure:
        failure.setattr(StewardTurnHost, "_report_validator", lambda *_: lambda *_: {
            "status": "failed", "validator_kind": "callback", "summary": "Synthetic validator unavailable", "recovery_kind": "repair_required"})
        original = finish(runtime, pump_steward_monitors(runtime)[0])
    assert original["response"]["native_loopx"]["result_kind"] == "validation_failed"
    for changed in [None, {**config, "route_ref": "managed-session:" + "b" * 32},
            {**config, "schedule": {**config["schedule"], "rrule": "FREQ=DAILY;BYHOUR=10;BYMINUTE=0"}}]:
        with pytest.raises((ValueError, KeyError)):
            with session_report_configuration_guard(runtime, session["goal_id"], changed):
                pytest.fail("a frozen pending report must not be stranded by a settings edit")
    before = runtime.registry_path.read_bytes()
    with session_report_configuration_guard(runtime, session["goal_id"], {**config, "enabled": False}):
        pass
    assert runtime.registry_path.read_bytes() == before
    assert consume_pending_periodic_report_intent(registry_path=runtime.registry_path, runtime_root=reports.root,
        goal_id=session["goal_id"], agent_id=session["managed_agent_id"], execute=True)["status"] == "original_conversation_required"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("interruption", [None, "settings_cleared", "task_blocked"])
def test_local_cancel_retires_only_a_known_failed_window_after_verified_apply(steward, tmp_path, monkeypatch, provider, interruption):
    from datetime import timezone
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    from loopx.capabilities.native_chat.steward_reports import session_report_configuration_guard
    from loopx.capabilities.periodic_report import cadence_runtime
    from loopx.configure_goal import configure_goal
    import loopx.capabilities.native_chat.steward_reports as report_host
    session, reports, config, _ = configured_local_report(steward, tmp_path, monkeypatch, provider)
    store, runtime, bot, _, _, capture, _ = steward
    sid, goal_id = session["session_id"], session["goal_id"]
    point = datetime.now(timezone.utc).replace(hour=9, minute=1, second=0, microsecond=0) - timedelta(days=2)

    class CalendarClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return point.astimezone(tz) if tz else point.replace(tzinfo=None)
    monkeypatch.setattr(report_host, "datetime", CalendarClock)
    monkeypatch.setattr(cadence_runtime, "datetime", CalendarClock)
    install_model(steward, tmp_path, sid)
    model = tmp_path / "report_model.py"
    valid = model.read_text()
    model.write_text(valid.replace('"request_digest": request["request_digest"]', '"request_digest": "known-invalid"'))
    failed = finish(runtime, pump_steward_monitors(runtime)[0])
    assert failed["response"]["native_loopx"]["status"] == "failed"
    journal = read_cadence_journal(runtime_root=reports.root, goal_id=goal_id)
    folder = attempt_directory(reports.root, journal["window"])
    artifacts = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    calls, writes, registry = capture.read_bytes(), list(bot.writes), runtime.registry_path.read_bytes()
    assert not (folder / "private-generation.json").exists()
    # Preview and an apply that fails before verified readback cannot cancel.
    with session_report_configuration_guard(runtime, goal_id, None) as after_apply:
        assert callable(after_apply)
        with pytest.raises(ValueError, match="not cleared"):
            after_apply()
    with pytest.raises(OSError, match="write unavailable"):
        with session_report_configuration_guard(runtime, goal_id, None):
            raise OSError("Synthetic settings write unavailable")
    assert read_cadence_journal(runtime_root=reports.root, goal_id=goal_id) == journal
    assert runtime.registry_path.read_bytes() == registry
    # Existing generation/unknown-delivery evidence must still prevent clear.
    for name in ["private-generation.json", "private-delivery.json"]:
        retained = folder / name
        retained.write_text("{}")
        with pytest.raises(ValueError, match="requires verified delivery"):
            with session_report_configuration_guard(runtime, goal_id, None):
                pytest.fail("an existing effect must not be cancelled as a generation failure")
        retained.unlink()
    if interruption:
        from loopx.capabilities.periodic_report import cadence_journal
        def unavailable_retirement(**_kwargs):
            raise OSError("Synthetic cancellation receipt unavailable")
        with pytest.raises(OSError, match="cancellation receipt unavailable"), monkeypatch.context() as interrupted:
            if interruption == "task_blocked":
                interrupted.setattr(cadence_journal, "_retire_failed_cadence_window_locked", unavailable_retirement)
            with session_report_configuration_guard(runtime, goal_id, None) as after_apply:
                assert configure_goal(registry_path=runtime.registry_path, goal_id=goal_id,
                    clear_periodic_report_configuration=True, execute=True)["ok"]
                if interruption == "settings_cleared":
                    raise OSError("Synthetic cancellation receipt unavailable")
                after_apply()
        assert read_cadence_journal(runtime_root=reports.root, goal_id=goal_id) == journal
        assert capture.read_bytes() == calls and bot.writes == writes
    # A retry uses the original frozen actor even after configuration cleared.
    with session_report_configuration_guard(runtime, goal_id, None) as after_apply:
        assert callable(after_apply)
        assert configure_goal(registry_path=runtime.registry_path, goal_id=goal_id,
            clear_periodic_report_configuration=True, execute=True)["ok"]
        after_apply()
    retired = read_cadence_journal(runtime_root=reports.root, goal_id=goal_id)
    assert retired["retirement"]["source_receipt_id"] == failed["turn_id"]
    assert retired["publication"] is None
    task = next(row for row in StewardMonitor(runtime).todos(failed["loopx_request"]["actor"])
        if row["todo_id"] == failed["loopx_request"]["todo_id"])
    assert task["status"] == "blocked" and not task.get("done")
    assert capture.read_bytes() == calls and bot.writes == writes
    assert store.load_turn(sid, failed["turn_id"]) == failed
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == artifacts
    assert read_periodic_report_publication_cursor(runtime_root=reports.root, goal_id=goal_id,
        agent_id=session["managed_agent_id"]) is None
    with session_report_configuration_guard(runtime, goal_id, None) as after_apply:
        assert after_apply is None
    # The old failed attempt stays retired; a new independent period works.
    with session_report_configuration_guard(runtime, goal_id, config) as after_apply:
        assert after_apply is None
        assert configure_goal(registry_path=runtime.registry_path, goal_id=goal_id,
            periodic_report_configuration=config, execute=True)["ok"]
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)
    assert capture.read_bytes() == calls
    model.write_text(valid)
    point += timedelta(days=1)
    runtime._steward_monitor_next_check.clear()
    completed = finish(runtime, pump_steward_monitors(runtime)[0])
    assert completed["turn_id"] != failed["turn_id"]
    assert completed["response"]["native_loopx"]["status"] == "committed"
    assert reports.health(sid)["publication_verified"]
    assert store.load_turn(sid, failed["turn_id"]) == failed
    assert bot.writes == writes


@pytest.mark.parametrize("phase,expected_status", [
    ("preview", 201), ("stale", 409), ("write_failure", 500), ("readback_failure", 500), ("applied", 200),
])
def test_settings_retirement_runs_after_verified_apply_before_the_http_receipt(monkeypatch, phase, expected_status):
    from contextlib import contextmanager
    from test_chat_goal_configuration_api import _PeriodicClearHandler
    import loopx.capabilities.native_chat.steward_reports as report_host
    body = {"goal_id": "goal-example", "capability_id": "periodic_report", "configuration": None}
    preview = _PeriodicClearHandler(body)
    preview._goal_configuration_update(execute=False)
    revision = preview.responses[0]["plan_revision"]
    handler = _PeriodicClearHandler(body if phase == "preview" else
        {**body, "expected_plan_revision": "stale" if phase == "stale" else revision})
    handler.server.runtime_controller = object()
    calls = []

    @contextmanager
    def guarded_clear(_controller, goal_id, proposed):
        assert goal_id == "goal-example" and proposed is None
        def retire():
            assert not handler.override_present and not handler.responses
            calls.append("retired")
        yield retire
    monkeypatch.setattr(report_host, "session_report_configuration_guard", guarded_clear)
    if phase == "write_failure":
        writer = handler._goal_configuration_writer()
        monkeypatch.setattr(handler, "_goal_configuration_writer", lambda: lambda **kwargs:
            {**writer(**kwargs), **({"ok": False} if kwargs["execute"] else {})})
    if phase == "readback_failure":
        monkeypatch.setattr(handler, "_goal_configuration_readback_matches", lambda *_: False)
    handler._goal_configuration_update(execute=phase != "preview")
    assert handler.responses[0]["status_code"] == expected_status
    assert calls == (["retired"] if phase == "applied" else [])


def configured(steward, tmp_path, monkeypatch, provider="sqlite", start_date="2001-01-01"):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, _, transport, binding, capture, _ = steward
    sid, managed = original(steward)
    storage(runtime, provider)
    preview = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(binding["binding_id"], session_id=sid, execute=True,
        expected_plan_id=preview["plan_id"])["actor"]
    transport.reconcile()
    reports = StewardReports(runtime)
    schedule = {"schema_version": "periodic_report_schedule_v0", "schedule_id": "daily-report",
        "timezone": "UTC", "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "start_date": start_date}
    before = runtime.registry_path.read_bytes(), store.messages(sid), capture.read_bytes()
    preview = reports.configure(binding["binding_id"], operation="enable", schedule=schedule)
    assert before == (runtime.registry_path.read_bytes(), store.messages(sid), capture.read_bytes())
    assert not preview["applied"] and preview["plan"]["configuration"]["schedule"] == schedule
    result = reports.configure(binding["binding_id"], operation="enable", schedule=schedule,
        execute=True, expected_plan_id=preview["plan"]["plan_id"])
    assert result["applied"] and result["report"]["enabled"] and not result["model_invoked"]
    return sid, actor, reports, schedule


def fake_report_model(params):
    """A transport-only oracle returns one substantive synthetic Chinese report."""
    import json
    import re
    from pathlib import Path
    prompt = "\n".join(row.get("text", "") for row in params.get("input", []) if isinstance(row, dict))
    match = re.search(r'"editorial_request_path":\s*"([^"\n]+)"', prompt)
    request = json.loads(Path(match[1]).read_text())
    ref = request["facts"][0]["source_ref"]
    sections = [("overview", "全景判断"), ("problem_map", "问题版图"),
        ("causal_analysis", "重点因果下钻"), ("coverage_and_actions", "版本覆盖与处置"), ("next_actions", "下一步")]
    narrative = "本期授权工作已完成来源核查，当前记录仍需区分实际成果、来源缺口和后续行动。任务状态与消息送达分别验证，不能根据一次观察宣告业务完成。"
    response = {"schema_version": "periodic_report_editorial_response_v0",
        "request_digest": request["request_digest"], "language": "zh-CN", "title": "本期授权工作详细汇报",
        "highlights": [{"highlight_id": "coverage", "label": "读取范围", "value": "已核查", "detail": "保留来源缺口", "tone": "neutral"},
            {"highlight_id": "delivery", "label": "送达情况", "value": "待核验", "detail": "生成不代表送达", "tone": "attention"}],
        "sections": [{"section_id": key, "title": title, "items": [
            {"title": title + "与证据边界", "summary": narrative * 4, "source_ref": ref,
                "details": [{"label": "来源", "text": narrative * 4},
                    {"label": "边界", "text": narrative * 4}]}
            for _ in range(6)]} for key, title in sections]}
    return json.dumps({"result_kind": "validated_completion", "classification": "periodic_report_generation",
        "summary": "The original report generation was frozen and independently validated; private delivery remains separate.",
        "next_action": "Retain monitoring and future report windows; verify original private delivery.",
        "delivery_batch_scale": "single_surface", "delivery_outcome": "outcome_progress",
        "vision_unchanged_reason": "The original monitoring and reporting route remains active.",
        "work_evidence": ["Frozen report generation and complete report body"],
        "validation_evidence": ["The independent native report validator checks editorial lineage and artifact digests"],
        "editorial_response": response}, ensure_ascii=False)


# Keep the imported oracle stable if a developer edits this file during a run.
# A late inspect.getsource can resolve its old line number into unrelated text.
_REPORT_MODEL_SOURCE = inspect.getsource(fake_report_model)


def install_model(steward, tmp_path, sid, *, result_kind="validated_completion"):
    _, runtime, _, _, _, _, _ = steward
    helper = tmp_path / "report_model.py"
    helper.write_text(_REPORT_MODEL_SOURCE.replace(
        '"result_kind": "validated_completion"', '"result_kind": ' + repr(result_kind)))
    fake = Path(runtime.codex_bin)
    source = fake.read_text().replace('"message": "Runtime response."',
        '"message": __import__("runpy").run_path(' + repr(str(helper)) + ')["fake_report_model"](request["params"])')
    fake.write_text(source)
    runtime.adapters.pop(sid).close_session()  # Resume the same native thread with the synthetic response.


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("local_session", [False, True])
@pytest.mark.parametrize("recovery_succeeds", [False, True])
def test_report_quota_failure_continues_once_after_native_no_effects_and_available_allowance(
        steward, tmp_path, monkeypatch, provider, local_session, recovery_succeeds):
    import time
    from datetime import timezone
    from loopx.chat_agent import CodexChatAgentError
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    from loopx.control_plane.turn_driver.journal_store import turn_journal_path
    from loopx.capabilities.periodic_report import cadence_runtime
    import loopx.capabilities.native_chat.steward_reports as report_host
    point = datetime.now(timezone.utc).replace(hour=9, minute=1, second=0, microsecond=0) - timedelta(days=2)

    class CalendarClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return point.astimezone(tz) if tz else point.replace(tzinfo=None)

    monkeypatch.setattr(report_host, "datetime", CalendarClock)
    monkeypatch.setattr(cadence_runtime, "datetime", CalendarClock)
    store, runtime, _, transport, binding, capture, _ = steward
    if local_session:
        session, reports, _, _ = configured_local_report(steward, tmp_path, monkeypatch, provider)
        sid = owner = session["session_id"]
        actor = reports.selected(sid)["actor"]
    else:
        sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, provider)
        owner = binding["binding_id"]
    install_model(steward, tmp_path, sid)
    calls = []

    def exhausted(adapter, _prompt, event_sink):
        calls.append(adapter.upstream_thread_id)
        event_sink("turn.started", {"upstream_turn_id": "quota-refused-native-turn"})
        raise CodexChatAgentError("The provider allowance was exhausted.")

    real_start = CodexAppServerAdapter.start_turn
    monkeypatch.setattr(CodexAppServerAdapter, "start_turn", exhausted)
    original = finish(runtime, pump_steward_monitors(runtime)[0])
    native = original["response"]["native_loopx"]
    assert native["status"] == "failed" and native["result_kind"] == "host_failure"
    assert native["host_failure"]["kind"] == "unknown"
    original_path = store._turn_path(sid, original["turn_id"])
    journal_path = turn_journal_path(reports.root, goal_id=actor["goal_id"], turn_key=native["resume_turn_key"])
    frozen = original_path.read_bytes(), journal_path.read_bytes()
    _, window = reports.current_window(owner)
    observations = []
    proof = {"thread_id": store.load_session(sid)["upstream_thread_id"],
        "turn_id": original["upstream_turn_id"], "only_original_user_item": True,
        "codex_error_info": "usageLimitExceeded", "quota_available": False,
        "quota_observed_at": time.time()}

    def observe(**kwargs):
        observations.append(kwargs)
        return dict(proof)

    monkeypatch.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(observe))
    runtime._steward_monitor_next_check.clear()
    assert pump_steward_monitors(runtime) == []  # Reset time alone is not usable quota.
    assert len(calls) == 1
    proof.update(quota_available=True, quota_observed_at=time.time())
    if recovery_succeeds:
        monkeypatch.setattr(CodexAppServerAdapter, "start_turn", real_start)
    runtime._steward_monitor_next_check.clear()
    old_adapter = runtime.adapters[sid]
    old_thread = store.load_session(sid)["upstream_thread_id"]
    continued = pump_steward_monitors(runtime)
    assert len(continued) == 1, "the original report must not remain stranded after proven quota recovery"
    result = finish(runtime, continued[0])
    assert runtime.adapters[sid] is not old_adapter
    assert old_adapter.session.process.poll() is not None
    assert store.load_session(sid)["upstream_thread_id"] == old_thread
    assert result["turn_id"] != original["turn_id"]
    request = result["loopx_request"]
    assert request["report_window_id"] == window["window_id"]
    assert request["todo_id"] == original["loopx_request"]["todo_id"]
    assert request["actor"] == original["loopx_request"]["actor"]
    assert request["turn_instance_id"] == original["loopx_request"]["turn_instance_id"] + ".quota-recovery"
    assert result["response"]["native_loopx"]["status"] == ("committed" if recovery_succeeds else "failed"), result
    assert frozen == (original_path.read_bytes(), journal_path.read_bytes())
    assert observations and all(x["turn_id"] == original["upstream_turn_id"] for x in observations)
    assert all(x["thread_ids"] == [proof["thread_id"]] for x in observations)
    if not recovery_succeeds:
        assert not reports.health(owner)["publication_verified"]
        assert len(calls) == 2
    elif local_session:
        assert reports.health(owner)["publication_verified"]
    else:
        assert transport.steward_reports.reconcile() == 1
        assert reports.health(owner)["publication_verified"]
    before = capture.read_bytes(), len(observations), store.messages(sid)
    runtime._steward_monitor_next_check.clear()
    assert pump_steward_monitors(runtime) == []
    assert before == (capture.read_bytes(), len(observations), store.messages(sid))
    if recovery_succeeds:
        point += timedelta(days=1)
        runtime._steward_monitor_next_check.clear()
        following = pump_steward_monitors(runtime)
        assert len(following) == 1
        next_result = finish(runtime, following[0])
        assert next_result["response"]["native_loopx"]["status"] == "committed", next_result
        assert "report_quota_recovery" not in next_result["loopx_request"]
        assert next_result["loopx_request"]["report_window_id"] != window["window_id"]
        assert len(observations) == before[1]  # A new window does not inherit old recovery.
        if not local_session:
            assert transport.steward_reports.reconcile() == 1
        assert reports.health(owner)["publication_verified"]
        assert frozen == (original_path.read_bytes(), journal_path.read_bytes())


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("local_session", [False, True])
@pytest.mark.parametrize("replan_outcome", ["settled", "wait", "invalid_semantic", "already_admitted", "retained_probe"])
def test_report_pump_replans_only_proven_refusals_and_retains_unknown_work(steward, tmp_path, monkeypatch, provider, local_session, replan_outcome):
    from datetime import timezone
    from loopx.capabilities.periodic_report import cadence_runtime
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    import loopx.capabilities.native_chat.steward_reports as report_host
    store, runtime, bot, transport, binding, capture, _ = steward
    point = datetime.now(timezone.utc).replace(hour=9, minute=1, second=0, microsecond=0) - timedelta(days=2)

    class CalendarClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return point.astimezone(tz) if tz else point.replace(tzinfo=None)
    monkeypatch.setattr(report_host, "datetime", CalendarClock)
    monkeypatch.setattr(cadence_runtime, "datetime", CalendarClock)
    if local_session:
        session, reports, _, _ = configured_local_report(steward, tmp_path, monkeypatch, provider)
        sid = owner = session["session_id"]
        actor = reports.selected(sid)["actor"]
    else:
        sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, provider)
        owner = binding["binding_id"]
    from loopx.configure_goal import configure_goal
    assert configure_goal(registry_path=runtime.registry_path, goal_id=actor["goal_id"],
        execution_replan_after_turns=1, execute=True)["ok"]
    install_model(steward, tmp_path, sid)
    assert finish(runtime, pump_steward_monitors(runtime)[0])["response"]["native_loopx"]["status"] == "committed"
    assert transport.steward_reports.reconcile() == (0 if local_session else 1)
    assert reports.health(owner)["publication_verified"]
    _, first_window = reports.current_window(owner)
    first_folder = attempt_directory(reports.root, first_window)
    first_body = (first_folder / "report.md").read_bytes()
    point += timedelta(days=1)
    runtime._steward_monitor_next_check.clear()
    rejected = finish(runtime, pump_steward_monitors(runtime)[0])
    refusal = rejected["response"]["native_loopx"]
    assert refusal["error_code"] == "turn_todo_selection_deferred", refusal
    assert refusal["selection_rejection"]["reason_code"] == "autonomous_replan"
    assert refusal["effects"] == {"host_invoked": False, "state_written": False,
        "quota_spent": False, "scheduler_acknowledged": False}
    original_bytes = store._turn_path(sid, rejected["turn_id"]).read_bytes()
    _, second_window = reports.current_window(owner)
    assert second_window["window_id"] != first_window["window_id"]
    if replan_outcome in {"already_admitted", "retained_probe"}:
        # A past refusal is evidence, not current authority. The native owner
        # can now admit this exact report without a semantic replan.
        assert configure_goal(registry_path=runtime.registry_path, goal_id=actor["goal_id"],
            execution_replan_after_turns=6, execute=True)["ok"]
    # The model is a transport oracle only. Native admission, the persisted
    # missing-baseline trigger, semantic validation and settlement stay real.
    vision = {"schema_version": "goal_vision_replan_contract_v0", "state": "active",
        "vision_patch": {"vision_summary": "Maintain the original monitoring and reporting work.",
            "acceptance_summary": "Independently validate each report and its full original private delivery; retain source gaps."},
        "path_delta": {"outcome": "replan", "prior_assumption": "The first report could keep an absent Agent vision unchanged.",
            "observed_reality": "The first report was independently validated and delivered; native governance now requires a persisted baseline.",
            "retained": ["The same subscriptions, report window, original actor and all native tasks."],
            "changed": ["Record the original Agent's evidence-linked continuation before resuming the retained report."],
            "evidence_refs": ["fixture:validated-original-report-and-provider-delivery"]}}
    replan = {"result_kind": "replan_required", "classification": "original_report_native_replan",
        "summary": "The original reporting route remains active with an evidence-linked baseline.",
        "next_action": "Resume the retained native report task under a fresh exact selection.",
        "delivery_batch_scale": "implementation", "delivery_outcome": "outcome_progress",
        "work_evidence": ["The original report generation and independently read original private delivery."],
        "validation_evidence": ["Native semantic validation checks the persisted baseline and current obligation."],
        "path_delta_mode": "material_replan", "agent_vision_json": json.dumps(vision)}
    if replan_outcome == "wait":
        replan = {"result_kind": "wait", "classification": "fixture_missing_context",
            "next_action": "Retain the original report until its required context is available."}
    elif replan_outcome == "invalid_semantic":
        vision["path_delta"]["evidence_refs"] = []
        replan["agent_vision_json"] = json.dumps(vision)
    helper = tmp_path / "report_model.py"
    helper.write_text(_REPORT_MODEL_SOURCE.replace("def fake_report_model(", "def report_only(") +
        "\ndef fake_report_model(params):\n"
        "    import json\n"
        "    prompt = '\\n'.join(row.get('text', '') for row in params.get('input', []) if isinstance(row, dict))\n"
        "    authority = json.JSONDecoder().raw_decode(prompt.split('Turn authority JSON:', 1)[1].lstrip())[0]\n"
        "    if authority.get('replan_action_packet'):\n"
        "        return " + repr(json.dumps(replan)) + "\n"
        "    return report_only(params)\n")
    calls, writes = capture.read_bytes(), list(bot.writes)
    runtime._steward_monitor_next_check.clear()
    resumed = pump_steward_monitors(runtime)
    assert len(resumed) == 1, "The original idle producer must recover its proven pre-execution refusal"
    if replan_outcome == "retained_probe":
        from loopx.capabilities.native_chat.steward_turn import StewardTurnHost
        selection = StewardTurnHost.report_selection_recovery

        def legacy_selection(host, original, **kwargs):
            # Exercise the real old host-only wait journal, not a fabricated
            # success or an unknown failed model Turn.
            return None if kwargs.get("probe_turn") is not None else selection(host, original)

        with monkeypatch.context() as legacy:
            legacy.setattr(StewardTurnHost, "report_selection_recovery", legacy_selection)
            stopped = finish(runtime, resumed[0])
        assert stopped["delta_count"] == 0 and stopped["upstream_turn_id"] is None
        stopped_bytes = store._turn_path(sid, stopped["turn_id"]).read_bytes()
        assert capture.read_bytes() == calls
        runtime._steward_monitor_next_check.clear()
        continued = pump_steward_monitors(runtime)
        assert len(continued) == 1, "Only the canonical no-model admission probe may continue once"
        done = finish(runtime, continued[0])
        assert store._turn_path(sid, stopped["turn_id"]).read_bytes() == stopped_bytes
    else:
        done = finish(runtime, resumed[0])
    replans = [row["payload"] for row in store.events_after(sid, done["turn_id"], None)
        if row["kind"] == "native_loopx.replan_result"]
    assert len(replans) == (0 if replan_outcome == "retained_probe" else 1)
    if replan_outcome not in {"settled", "already_admitted", "retained_probe"}:
        assert replans[0].get("status") != "committed"
        assert replans[0]["effects"]["state_written"] is False and replans[0]["effects"]["quota_spent"] is False
        assert not (attempt_directory(reports.root, second_window) / "private-generation.json").exists()
        assert bot.writes == writes and transport.steward_reports.reconcile() == 0
        tail = [json.loads(line) for line in capture.read_bytes()[len(calls):].splitlines()]
        assert sum(row.get("method") == "turn/start" for row in tail) == 1
        held = capture.read_bytes(), store.load_turn(sid, rejected["turn_id"])
        for _ in range(2):
            runtime._steward_monitor_next_check.clear()
            assert not pump_steward_monitors(runtime)
        assert (capture.read_bytes(), store.load_turn(sid, rejected["turn_id"])) == held
        assert next(row for row in StewardMonitor(runtime).todos(actor)
            if row["todo_id"] == rejected["loopx_request"]["todo_id"])["status"] == "open"
        assert reports.current_window(owner)[1] == second_window
        return
    assert done.get("response") and done["response"]["native_loopx"].get("status") == "committed", {
        "turn": done, "replan": [row["payload"] for row in store.events_after(sid, done["turn_id"], None)
            if row["kind"] == "native_loopx.replan_result"]}
    assert done["loopx_request"] == rejected["loopx_request"]
    assert done["turn_id"] != rejected["turn_id"]
    assert store._turn_path(sid, rejected["turn_id"]).read_bytes() == original_bytes
    assert reports.current_window(owner)[1] == second_window
    if replan_outcome == "settled":
        assert replans[0]["result_kind"] == "replan_required" and replans[0]["status"] == "committed"
        assert replans[0]["validation"]["summary"] == "independent native semantic replan preview passed"
        assert replans[0]["receipt"]["lineage"]["todo_id"] == ""
        assert replans[0]["receipt"]["lineage"]["replan_obligation_id"]
    elif replan_outcome == "already_admitted":
        assert replans[0]["status"] == "stopped" and replans[0]["effects"]["quota_spent"] is False
    task = next(row for row in StewardMonitor(runtime).todos(actor)
        if row["todo_id"] == done["loopx_request"]["todo_id"])
    assert task["status"] == "done"
    # The existing no-model recovery owner attaches the native commit to the
    # original return reader; the refused outer Chat request stays immutable.
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)
    assert transport.steward_reports.reconcile() == (0 if local_session else 1)
    assert reports.health(owner)["publication_verified"]
    assert (first_folder / "report.md").read_bytes() == first_body
    folder = attempt_directory(reports.root, second_window)
    body = (folder / "report.md").read_text()
    if local_session:
        assert bot.writes == writes
        assert len([row for row in store.messages(sid) if row["role"] == "agent" and row["text"] == body]) == 1
    else:
        proof = json.loads((folder / "private-delivery.json").read_text())["deliveries"]["periodic_report." + second_window["window_id"]]
        assert proof["verified"] and proof["text"] == body.strip()
        assert len(bot.writes) > len(writes)  # The original sender may split a full report.
    tail = [json.loads(line) for line in capture.read_bytes()[len(calls):].splitlines()]
    assert sum(row.get("method") == "turn/start" for row in tail) == (2 if replan_outcome == "settled" else 1)
    assert not any(row.get("method") == "thread/start" for row in tail)
    after = capture.read_bytes(), list(bot.writes)
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)
    assert transport.steward_reports.reconcile() == 0
    assert (capture.read_bytes(), bot.writes) == after


def embedded_report_python(tmp_path, monkeypatch):
    """A real child interpreter without the embedding host's LoopX bootstrap."""
    import subprocess
    import sys
    import os
    from types import SimpleNamespace
    from loopx.capabilities.native_chat import steward_reports
    folder = tmp_path / "embedded-python"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(folder)], check=True)
    python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    missing = subprocess.run([str(python), "-I", "-c", "import loopx"],
        cwd=tmp_path, capture_output=True, text=True)
    assert missing.returncode != 0 and "ModuleNotFoundError" in missing.stderr
    monkeypatch.setattr(steward_reports, "sys", SimpleNamespace(executable=str(python)))


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_embedded_host_validates_report_without_a_second_python_installation(steward, tmp_path, monkeypatch, provider):
    _, runtime, _, transport, binding, _, _ = steward
    sid, _, reports, _ = configured(steward, tmp_path, monkeypatch, provider)
    install_model(steward, tmp_path, sid)
    embedded_report_python(tmp_path, monkeypatch)
    result = finish(runtime, pump_steward_monitors(runtime)[0])
    assert result["response"]["native_loopx"].get("status") == "committed", result
    assert transport.steward_reports.reconcile() == 1
    assert reports.health(binding["binding_id"])["publication_verified"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("stop", [None, "pause", "tamper"])
def test_original_frozen_report_recovers_validation_without_repeating_model_or_business(steward, tmp_path, monkeypatch, provider, stop):
    from loopx.cli_commands import turn as turn_command
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    from loopx.control_plane.turn_driver.journal_store import find_loopx_turn_key_by_settlement_identity, turn_journal_path
    store, runtime, bot, transport, binding, capture, _ = steward
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, provider)
    install_model(steward, tmp_path, sid)
    embedded_report_python(tmp_path, monkeypatch)
    command = turn_command.handle_turn_command

    def legacy_validator(*args, **kwargs):
        # Seed the exact historical command failure, not a fabricated journal.
        kwargs.pop("native_task_validator", None)
        return command(*args, **kwargs)

    with monkeypatch.context() as historical:
        historical.setattr(turn_command, "handle_turn_command", legacy_validator)
        original = pump_steward_monitors(runtime)[0]
        failed = finish(runtime, original)
    native = failed["response"]["native_loopx"]
    assert native["result_kind"] == "validation_failed" and native["receipt"]["next_phase"] == "validation"
    todo = next(row for row in StewardMonitor(runtime).todos(actor) if row["todo_id"] == original["loopx_request"]["todo_id"])
    assert todo["status"] == "open"
    key = find_loopx_turn_key_by_settlement_identity(reports.root, goal_id=actor["goal_id"],
        agent_id=actor["agent_id"], todo_id=todo["todo_id"], turn_instance_id=original["loopx_request"]["turn_instance_id"])
    path = turn_journal_path(reports.root, goal_id=actor["goal_id"], turn_key=key)
    journal = json.loads(path.read_text())
    assert journal["completed_phases"] == ["host_execute", "typed_result"]
    window = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])["window"]
    folder = attempt_directory(reports.root, window)
    generation = validate_private_generation(folder)
    body, history, calls, writes = (folder / "report.md").read_bytes(), store.load_turn(sid, original["turn_id"]), capture.read_bytes(), list(bot.writes)
    assert transport.steward_reports.reconcile() == 0
    if stop == "pause":
        preview = reports.configure(binding["binding_id"], operation="pause")
        reports.configure(binding["binding_id"], operation="pause", execute=True, expected_plan_id=preview["plan"]["plan_id"])
    elif stop == "tamper":
        (folder / "report.md").write_bytes(body + b"altered")
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)
    assert capture.read_bytes() == calls and bot.writes == writes
    if stop:
        assert transport.steward_reports.reconcile() == 0
        assert json.loads(path.read_text()) == journal
        assert not (folder / "private-delivery.json").exists()
        return
    recovered = json.loads(path.read_text())
    assert recovered["status"] == "committed", recovered
    assert recovered["turn_key"] == key and recovered["host_attempt_count"] == journal["host_attempt_count"]
    assert store.load_turn(sid, original["turn_id"]) == history
    task = next(row for row in StewardMonitor(runtime).todos(actor) if row["todo_id"] == todo["todo_id"])
    assert task["status"] == "done" and task["completion_turn_key"] == original["loopx_request"]["turn_instance_id"]
    events = [row["payload"] for row in store.events_after(sid, original["turn_id"], None)
        if row["kind"] == "native_loopx.result" and row["payload"].get("status") == "committed"]
    assert len(events) == 1 and events[0]["recovery"]["actual"]["host_invoked"] is False
    assert transport.steward_reports.reconcile() == 1
    assert (folder / "report.md").read_bytes() == body
    delivery = json.loads((folder / "private-delivery.json").read_text())
    proof = delivery["deliveries"]["periodic_report." + window["window_id"]]
    assert proof["verified"] and proof["text"] == body.decode().strip()
    cursor = read_periodic_report_publication_cursor(runtime_root=reports.root, goal_id=actor["goal_id"], agent_id=actor["agent_id"])
    assert cursor["generation_id"] == generation["generation_id"] and cursor["covered_until"] == window["due_at"]
    sent = list(bot.writes)
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime) and transport.steward_reports.reconcile() == 0
    assert capture.read_bytes() == calls and bot.writes == sent


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("change", ["none", "growth", "remove_unread", "replace_unread"])
def test_partial_read_coverage_is_not_the_original_report_authorization(steward, tmp_path, monkeypatch, provider, legacy, change):
    from loopx.bootstrap import bootstrap_project
    from loopx.capabilities.manager_context import report_source
    from loopx.capabilities.native_chat import steward_reports as report_module
    from loopx.cli_commands import turn as turn_command
    from loopx.control_plane.turn_driver.journal_store import find_loopx_turn_key_by_settlement_identity, turn_journal_path
    store, runtime, bot, transport, binding, capture, _ = steward
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, provider)

    def add_goal(gid):
        project = tmp_path / gid
        project.mkdir()
        assert bootstrap_project(project=project, registry_path=runtime.registry_path,
            runtime_root=runtime.coordination_runtime_root, goal_id=gid,
            objective="Retain this independently authorized synthetic work.", domain="test", role="primary",
            parent_goal_id=None, state_file=None, goal_doc=None, adapter_kind="generic_project_goal_v0",
            adapter_status="connected", next_probe=None, spawn_allowed=False, max_children=0,
            allowed_domains=[], write_scope=[], preserve_todos=True, force=False, dry_run=False,
            sync_global=False)["ok"]

    unread = "unread-authorized-goal"
    add_goal(unread)
    collect = report_source.collect_manager_report_source

    def partial(read, query):
        def source_io(arguments):
            page = read(arguments)
            if arguments["view"] == "portfolio":
                # The actual scoped reader remains authoritative; simulate an
                # oversized/unreadable row, not an authorization or success.
                return {**page, "rows": [r for r in page["rows"] if r.get("goal_id") != unread], "unknown": True}
            return page
        return collect(source_io, query)

    monkeypatch.setattr(report_source, "collect_manager_report_source", partial)
    install_model(steward, tmp_path, sid)
    prepare, command = report_module.prepare_private_report, turn_command.handle_turn_command

    def old_prepare(**kwargs):
        from loopx.capabilities.periodic_report.pending_intent import _canonical_digest
        from loopx.registry import atomic_write_json
        request = dict(prepare(**kwargs))
        # Seed only the historical input format, before any model or generation.
        # The real worker, validator, journal and sender still own the outcome.
        folder = attempt_directory(kwargs["root"], kwargs["window"])
        assert not (folder / "private-generation.json").exists()
        request.pop("authorization_goal_ids", None)
        request["request_digest"] = _canonical_digest({k: v for k, v in request.items() if k != "request_digest"})
        atomic_write_json(folder / "editorial-request.json", request)
        return request

    def old_validator(*args, **kwargs):
        kwargs.pop("native_task_validator", None)
        return command(*args, **kwargs)

    with monkeypatch.context() as old:
        if legacy:
            embedded_report_python(tmp_path, old)
            old.setattr(report_module, "prepare_private_report", old_prepare)
            old.setattr(turn_command, "handle_turn_command", old_validator)
        original = pump_steward_monitors(runtime)[0]
        done = finish(runtime, original)
    assert done["status"] == "completed"
    if legacy:
        assert done["response"]["native_loopx"]["result_kind"] == "validation_failed"
    window = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])["window"]
    folder = attempt_directory(reports.root, window)
    source = json.loads((folder / "source.json").read_text())
    request = json.loads((folder / "editorial-request.json").read_text())
    assert unread not in source["goal_ids"] and not source["coverage"]["complete"]
    if legacy:
        assert "authorization_goal_ids" not in request
    frozen = [(folder / name).read_bytes() for name in ["report.md", "source.json", "editorial-request.json"]]
    key = find_loopx_turn_key_by_settlement_identity(reports.root, goal_id=actor["goal_id"],
        agent_id=actor["agent_id"], todo_id=original["loopx_request"]["todo_id"],
        turn_instance_id=original["loopx_request"]["turn_instance_id"])
    path = turn_journal_path(reports.root, goal_id=actor["goal_id"], turn_key=key)
    journal, history, calls, writes = path.read_bytes(), store.load_turn(sid, original["turn_id"]), capture.read_bytes(), list(bot.writes)
    if change == "growth":
        add_goal("later-authorized-goal")
    elif change in {"remove_unread", "replace_unread"}:
        registry = json.loads(runtime.registry_path.read_text())
        if change == "remove_unread":
            registry["goals"] = [row for row in registry["goals"] if row["id"] != unread]
        else:
            next(row for row in registry["goals"] if row["id"] == unread)["creation_operation_id"] += ".replacement"
        runtime.registry_path.write_text(json.dumps(registry))
    allowed = change == "none" or change == "growth" and not legacy
    assert reports.delivery_current(binding["binding_id"], window) is allowed
    if not legacy and allowed:
        assert request["authorization_goal_ids"] == sorted([actor["goal_id"], unread])
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)
    assert capture.read_bytes() == calls and store.load_turn(sid, original["turn_id"]) == history
    assert [(folder / name).read_bytes() for name in ["report.md", "source.json", "editorial-request.json"]] == frozen
    if not allowed:
        assert path.read_bytes() == journal
        assert transport.steward_reports.reconcile() == 0 and bot.writes == writes
        assert not (folder / "private-delivery.json").exists()
        return
    recovered = json.loads(path.read_text())
    assert recovered["status"] == "committed" and recovered["turn_key"] == key
    if legacy:
        assert recovered["host_attempt_count"] == json.loads(journal)["host_attempt_count"]
        assert recovered["recovery_audit"]["actual"]["host_invoked"] is False
    assert transport.steward_reports.reconcile() == 1
    assert reports.health(binding["binding_id"])["publication_verified"]
    sent = list(bot.writes)
    assert transport.steward_reports.reconcile() == 0 and bot.writes == sent and capture.read_bytes() == calls


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("result_kind", ["validated_progress", "validated_completion"])
def test_original_due_report_freezes_once_and_returns_full_body_after_native_settlement(steward, tmp_path, monkeypatch, provider, result_kind):
    store, runtime, bot, transport, binding, capture, _ = steward
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, provider)
    install_model(steward, tmp_path, sid, result_kind=result_kind)
    calls, writes, history = capture.read_bytes(), list(bot.writes), store.messages(sid)
    thread = store.load_session(sid)["upstream_thread_id"]
    turns = pump_steward_monitors(runtime)
    assert len(turns) == 1 and turns[0]["session_id"] == sid
    done = finish(runtime, turns[0])
    assert done["status"] == "completed", done
    assert done["response"]["native_loopx"]["status"] == "committed", done
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    task = next(row for row in StewardMonitor(runtime).todos(actor) if row["todo_id"] == turns[0]["loopx_request"]["todo_id"])
    assert task["status"] == "done"  # Completed generation is not left as tomorrow's backlog.
    journal = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])
    window = journal["window"]
    assert datetime.fromisoformat(window["due_at"].replace("Z", "+00:00")) - datetime.fromisoformat(window["start_at"].replace("Z", "+00:00")) == timedelta(hours=24)
    folder = attempt_directory(reports.root, window)
    snapshot = json.loads((folder / "source.json").read_text())
    assert snapshot["coverage"]["goals_in_inventory"] == 1 and snapshot["goal_ids"] == [actor["goal_id"]]
    generation = validate_private_generation(folder)
    assert journal["publication"] is None and read_periodic_report_publication_cursor(
        runtime_root=reports.root, goal_id=actor["goal_id"], agent_id=actor["agent_id"]) is None
    assert bot.writes == writes  # A frozen generation is not a send.
    assert store.messages(sid)[:len(history)] == history
    assert store.load_session(sid)["upstream_thread_id"] == thread
    native_calls = [json.loads(line) for line in capture.read_bytes()[len(calls):].splitlines()]
    assert sum(call.get("method") == "turn/start" for call in native_calls) == 1
    assert not any(call.get("method") == "thread/start" for call in native_calls)
    assert transport.steward_reports.reconcile() == 1
    cursor = read_periodic_report_publication_cursor(runtime_root=reports.root, goal_id=actor["goal_id"], agent_id=actor["agent_id"])
    assert cursor["generation_id"] == generation["generation_id"] and cursor["covered_until"] == window["due_at"]
    body = (folder / "report.md").read_text()
    assert done["response"]["message"] == body and "全景判断" in body and "重点因果下钻" in body
    delivery = json.loads((folder / "private-delivery.json").read_text())
    assert delivery["delivery_status"] == "delivered"
    proof = delivery["deliveries"]["periodic_report." + window["window_id"]]
    assert proof["verified"] and proof["text"] == body.strip()
    assert len(body.encode()) > 40000  # Exercise the original multipart full-body sender.
    after = capture.read_bytes(), list(bot.writes)
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)
    assert transport.steward_reports.reconcile() == 0
    assert after == (capture.read_bytes(), bot.writes)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_report_collects_evidence_before_capturing_native_launch_authority(steward, tmp_path, monkeypatch, provider):
    store, runtime, _, transport, binding, capture, _ = steward
    sid, _, reports, _ = configured(steward, tmp_path, monkeypatch, provider)
    install_model(steward, tmp_path, sid)
    prepare = StewardReports.prepare

    def collect(self, *args, **kwargs):
        result = prepare(self, *args, **kwargs)
        # A legitimate registry rewrite during a long portfolio read must be
        # captured by the later native plan, not make it stale before dispatch.
        runtime.registry_path.write_bytes(runtime.registry_path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(StewardReports, "prepare", collect)
    before = capture.read_bytes()
    turn = pump_steward_monitors(runtime)[0]
    done = finish(runtime, turn)
    assert done["status"] == "completed", done
    assert done["response"]["native_loopx"].get("status") == "committed", done
    assert transport.steward_reports.reconcile() == 1
    assert reports.health(binding["binding_id"])["publication_verified"]
    calls = [json.loads(line) for line in capture.read_bytes()[len(before):].splitlines()]
    assert sum(row.get("method") == "turn/start" for row in calls) == 1
    assert store.load_session(sid)["upstream_thread_id"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("reject_recovery", [False, True])
def test_original_pump_resumes_exact_prelaunch_rejection_once_without_replaying_unknown_work(steward, tmp_path, monkeypatch, provider, reject_recovery):
    from loopx.chat_agent import CodexChatAgentSession
    from loopx.control_plane.turn_driver.journal_store import turn_journal_path
    store, runtime, bot, transport, binding, capture, _ = steward
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, provider)
    install_model(steward, tmp_path, sid)
    history, calls = store.messages(sid), capture.read_bytes()
    write = CodexChatAgentSession._write

    def concurrent_registry_update(self, payload, **kwargs):
        if payload.get("method") == "turn/start":
            # The genuine legacy witness check rejects immediately before
            # transport publication, after source collection and native plan.
            runtime.registry_path.write_bytes(runtime.registry_path.read_bytes() + b"\n")
        return write(self, payload, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(CodexChatAgentSession, "_write", concurrent_registry_update)
        first = pump_steward_monitors(runtime)[0]
        rejected = finish(runtime, first)
    failure = rejected["response"]["native_loopx"]
    assert failure["error_code"] == "stale_goal_authority", rejected
    assert rejected["delta_count"] == 0 and rejected["upstream_turn_id"] is None
    native = turn_journal_path(reports.root, goal_id=actor["goal_id"], turn_key=failure["resume_turn_key"])
    assert json.loads(native.read_text())["host_attempt_count"] == 1
    assert json.loads(native.read_text())["completed_phases"] == []
    original_turn = store._turn_path(sid, first["turn_id"]).read_bytes()
    assert not any(json.loads(line).get("method") == "turn/start" for line in capture.read_bytes()[len(calls):].splitlines())
    assert transport.steward_reports.reconcile() == 0
    # A new host adapter after idle/restart uses the original producer and
    # canonical journal, rather than replaying the failed outer Chat request.
    runtime._steward_monitor_next_check.clear()
    with monkeypatch.context() as retry_fault:
        if reject_recovery:
            retry_fault.setattr(CodexChatAgentSession, "_write", concurrent_registry_update)
        retry = pump_steward_monitors(runtime)[0]
        assert retry["turn_id"] != first["turn_id"]
        assert retry["loopx_request"]["turn_instance_id"] == first["loopx_request"]["turn_instance_id"]
        assert retry["loopx_request"]["resume_turn_key"] == failure["resume_turn_key"]
        completed = finish(runtime, retry)
    assert completed["status"] == "completed", {key: completed.get(key) for key in ("status", "error", "error_code", "response")}
    if reject_recovery:
        assert completed["response"]["native_loopx"]["error_code"] == "stale_goal_authority"
        before = capture.read_bytes(), list(bot.writes), native.read_bytes()
        for _ in range(2):
            runtime._steward_monitor_next_check.clear()
            assert not pump_steward_monitors(runtime)
            assert transport.steward_reports.reconcile() == 0
        assert (capture.read_bytes(), bot.writes, native.read_bytes()) == before
        assert store._turn_path(sid, first["turn_id"]).read_bytes() == original_turn
        return
    assert completed["response"]["native_loopx"].get("status") == "committed", completed
    assert completed["response"]["native_loopx"]["resume_turn_key"] == failure["resume_turn_key"]
    assert store._turn_path(sid, first["turn_id"]).read_bytes() == original_turn
    assert json.loads(native.read_text())["host_attempt_count"] == 2
    # The existing no-model settlement recovery supplies the original return
    # reader with the committed native event; publication remains separate.
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)
    assert transport.steward_reports.reconcile() == 1
    assert reports.health(binding["binding_id"])["publication_verified"]
    assert store.messages(sid)[:len(history)] == history
    tail = [json.loads(line) for line in capture.read_bytes()[len(calls):].splitlines()]
    assert sum(row.get("method") == "turn/start" for row in tail) == 1
    sent, calls = list(bot.writes), capture.read_bytes()
    for _ in range(2):
        runtime._steward_monitor_next_check.clear()
        assert not pump_steward_monitors(runtime)
        assert transport.steward_reports.reconcile() == 0
    assert bot.writes == sent and capture.read_bytes() == calls


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_report_source_revoked_during_collection_never_enters_native_execution(steward, tmp_path, monkeypatch, provider):
    _, runtime, bot, _, binding, capture, _ = steward
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, provider)
    install_model(steward, tmp_path, sid)
    prepare = StewardReports.prepare

    def revoked(self, *args, **kwargs):
        result = prepare(self, *args, **kwargs)
        bot.owner_ready = False
        return result

    monkeypatch.setattr(StewardReports, "prepare", revoked)
    before = capture.read_bytes()
    done = finish(runtime, pump_steward_monitors(runtime)[0])
    assert done["status"] == "failed", done
    calls = [json.loads(line) for line in capture.read_bytes()[len(before):].splitlines()]
    assert not any(row.get("method") == "turn/start" for row in calls)
    assert not list((reports.root / "goals" / actor["goal_id"] / "turns").glob("*.json"))
    assert not reports.health(binding["binding_id"]).get("publication_verified")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_future_first_date_pause_and_cancel_never_start_models_or_shift_window(steward, tmp_path, monkeypatch, provider):
    store, runtime, bot, _, binding, capture, _ = steward
    sid, actor, reports, schedule = configured(steward, tmp_path, monkeypatch, provider, "2099-05-01")
    before = capture.read_bytes(), list(bot.writes), store.messages(sid)
    for _ in range(3):
        getattr(runtime, "_steward_monitor_next_check", {}).clear()
        assert not pump_steward_monitors(runtime)
    assert read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"]) is None
    assert reports.health(binding["binding_id"])["next_due_at"] == "2099-05-01T09:00:00Z"
    for operation in ["pause", "cancel"]:
        preview = reports.configure(binding["binding_id"], operation=operation)
        result = reports.configure(binding["binding_id"], operation=operation, execute=True,
            expected_plan_id=preview["plan"]["plan_id"])
        assert not result["report"]["enabled"] and result["report"]["schedule"] == schedule
        assert not pump_steward_monitors(runtime)
    assert before == (capture.read_bytes(), bot.writes, store.messages(sid))


@pytest.mark.parametrize("fault", ["stale", "provider", "revoked", "foreign_route"])
def test_report_configuration_fails_closed_on_original_source_changes(steward, tmp_path, monkeypatch, fault):
    _, runtime, provider, transport, binding, capture, _ = steward
    _, actor, reports, schedule = configured(steward, tmp_path, monkeypatch, start_date="2099-05-01")
    preview = reports.configure(binding["binding_id"], operation="pause")
    if fault == "provider":
        provider.owner_ready = False
    elif fault == "revoked":
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    elif fault == "foreign_route":
        from loopx.configure_goal import configure_goal
        configure_goal(registry_path=runtime.registry_path, goal_id=actor["goal_id"], execute=True,
            periodic_report_configuration={"enabled": True, "profile_preset": "weekly", "route_ref": "other-channel",
                "timezone": "UTC", "schedule": schedule})
    before, calls = runtime.registry_path.read_bytes(), capture.read_bytes()
    with pytest.raises((ValueError, StopIteration)):
        reports.configure(binding["binding_id"], operation="pause", execute=True,
            expected_plan_id="stale" if fault == "stale" else preview["plan"]["plan_id"])
    assert runtime.registry_path.read_bytes() == before and capture.read_bytes() == calls


def test_finite_completion_is_opt_in_and_other_host_prompts_and_results_are_unchanged():
    from loopx.control_plane.turn_driver.host_candidate import render_prompt, build_result
    authority = {"primary_action": "Inspect an authorized bounded artifact.", "write_scope": []}
    assert render_prompt(authority) == render_prompt(authority, allow_completion=False)
    assert "validated_completion |" not in render_prompt(authority)
    assert "validated_completion |" in render_prompt(authority, allow_completion=True)
    request = {"turn_key": "opaque-original-turn"}
    candidate = {"result_kind": "validated_progress", "classification": "bounded_observation",
        "summary": "A bounded source was independently read.", "next_action": "Retain the original task."}
    assert build_result(request, candidate) == build_result(request, candidate, allow_completion=True)
    completion = {**candidate, "result_kind": "validated_completion"}
    assert build_result(request, completion)["result_kind"] == "wait"
    assert build_result(request, completion, allow_completion=True)["result_kind"] == "validated_completion"


def test_report_http_preview_apply_is_original_source_bound_and_failure_is_reviewable(steward, tmp_path, monkeypatch):
    import http.client
    import threading
    from types import SimpleNamespace
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    store, runtime, provider, transport, binding, capture, _ = steward
    sid, _, reports, schedule = configured(steward, tmp_path, monkeypatch, start_date="2099-05-01")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller = store, runtime
    server.registry_path, server.runtime_root_override = runtime.registry_path, str(store.root.parent)
    server.lark_private_conversations = transport
    server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=lambda: {}, close=lambda: None)
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    route = "/api/chat/lark/private-conversations/managed-steward/report"

    def post(body, origin=None):
        client = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
            client.request("POST", route, json.dumps(body), headers)
            reply = client.getresponse()
            return reply.status, json.loads(reply.read())
        finally:
            client.close()

    body = {"binding_id": binding["binding_id"], "operation": "enable", "schedule": schedule}
    before = runtime.registry_path.read_bytes(), store.messages(sid), capture.read_bytes(), list(provider.writes)
    try:
        assert post(body, "https://foreign.example")[0] == 403
        assert post({**body, "goal_id": "borrowed-project"})[0] == 409
        status, preview = post({**body, "execute": False})
        assert status == 200 and not preview["applied"]
        assert before == (runtime.registry_path.read_bytes(), store.messages(sid), capture.read_bytes(), provider.writes)
        provider.owner_ready = False
        assert post({**body, "execute": True, "plan_id": preview["plan"]["plan_id"]})[0] == 409
        assert before == (runtime.registry_path.read_bytes(), store.messages(sid), capture.read_bytes(), provider.writes)
        provider.owner_ready = True
        assert post({**body, "execute": True, "plan_id": "stale"})[0] == 409
        status, applied = post({**body, "execute": True, "plan_id": preview["plan"]["plan_id"]})
        assert status == 200 and applied["applied"] and reports.health(binding["binding_id"])["enabled"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_distinct_due_daily_windows_keep_normal_native_replan_and_quota_rules(steward, tmp_path, monkeypatch):
    from datetime import timezone
    from loopx.capabilities.native_chat import steward_reports as module
    from loopx.capabilities.periodic_report import cadence_runtime
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor

    store, runtime, _, transport, _, capture, _ = steward
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch)
    install_model(steward, tmp_path, sid)
    # All three source windows must be complete even when tests run before
    # 09:00 UTC; the report-source owner correctly rejects a future interval.
    point = datetime.now(timezone.utc).replace(hour=9, minute=1, second=0, microsecond=0) - timedelta(days=4)

    class CalendarClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return point.astimezone(tz) if tz else point.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", CalendarClock)
    monkeypatch.setattr(cadence_runtime, "datetime", CalendarClock)
    windows = []
    for day in range(3):
        point += timedelta(days=1)
        runtime._steward_monitor_next_check = {}
        turns = pump_steward_monitors(runtime)
        assert len(turns) == 1, (day, turns, StewardMonitor(runtime).todos(actor))
        done = finish(runtime, turns[0])
        assert done["status"] == "completed" and done["response"]["native_loopx"]["status"] == "committed", done
        assert transport.steward_reports.reconcile() == 1
        cursor = read_periodic_report_publication_cursor(runtime_root=reports.root,
            goal_id=actor["goal_id"], agent_id=actor["agent_id"])
        windows.append(cursor["covered_until"])
    assert len(set(windows)) == 3
    assert all(row["status"] == "done" for row in StewardMonitor(runtime).todos(actor)
        if row.get("action_kind") == "generate_periodic_report")
    assert store.load_session(sid)["upstream_thread_id"] == "durable-thread"


def test_due_report_rebinds_model_context_after_a_new_authorized_goal_without_a_user_message(steward, tmp_path, monkeypatch):
    from loopx.capabilities.native_chat.steward_managed_mode import StewardManagedMode
    store, runtime, provider, transport, binding, capture, _ = steward
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch)
    original_history = store.messages(sid)
    original_scope = store.load_session(sid)["manager_authorization_scope_id"]
    other = transport.bindings.configure(transport_ref="notes-app", project_ref=binding["project_ref"],
        executor_endpoint_id="codex", context_kind="steward")
    transport.admit("notes-app", provider.event("notes-app", "independent-steward", "Keep this other steward independent."))
    accepted = next(row for row in transport.core.pending() if row["message"] == "Keep this other steward independent.")
    assert finish(runtime, accepted)["status"] == "completed"
    managed = StewardManagedMode(runtime, transport.bindings)
    preview = managed.prepare(other["binding_id"], session_id=accepted["session_id"])["plan"]
    managed.prepare(other["binding_id"], session_id=accepted["session_id"], execute=True,
        expected_plan_id=preview["plan_id"])
    install_model(steward, tmp_path, sid)
    before = capture.read_bytes()
    turns = pump_steward_monitors(runtime)
    assert len(turns) == 1 and turns[0]["session_id"] == sid
    done = finish(runtime, turns[0])
    assert done["status"] == "completed" and done["response"]["native_loopx"]["status"] == "committed", done
    assert transport.steward_reports.reconcile() == 1
    assert store.messages(sid)[:len(original_history)] == original_history
    assert next(row for row in transport.bindings.read()["bindings"]
        if row["binding_id"] == binding["binding_id"])["managed_actor"] == actor
    calls = [json.loads(line) for line in capture.read_bytes()[len(before):].splitlines()]
    assert sum(row.get("method") == "thread/start" for row in calls) == 1
    assert sum(row.get("method") == "turn/start" for row in calls) == 1
    journal = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])
    source = json.loads((attempt_directory(reports.root, journal["window"]) / "source.json").read_text())
    assert source["coverage"]["goals_in_inventory"] == 2
    events = store.events_after(sid, turns[0]["turn_id"], None)
    context = next(row["payload"] for row in events if row["kind"] == "manager.context")
    assert context["authorization_scope_id"] == store.load_session(sid)["manager_authorization_scope_id"]
    assert context["authorization_scope_id"] != original_scope


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_owner_cancel_retires_known_invalid_generation_and_next_window_progresses(steward, tmp_path, monkeypatch, provider):
    from datetime import timezone
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    import loopx.capabilities.native_chat.steward_reports as report_host
    from loopx.capabilities.periodic_report import cadence_runtime
    store, runtime, bot, transport, binding, capture, _ = steward
    sid, actor, reports, schedule = configured(steward, tmp_path, monkeypatch, provider)
    point = datetime.now(timezone.utc).replace(hour=9, minute=1, second=0, microsecond=0) - timedelta(days=2)
    class CalendarClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return point.astimezone(tz) if tz else point.replace(tzinfo=None)
    monkeypatch.setattr(report_host, "datetime", CalendarClock)
    monkeypatch.setattr(cadence_runtime, "datetime", CalendarClock)
    install_model(steward, tmp_path, sid)
    helper = tmp_path / "report_model.py"
    valid_model = helper.read_text()
    helper.write_text(valid_model.replace('"request_digest": request["request_digest"]', '"request_digest": "known-invalid"'))
    turn = pump_steward_monitors(runtime)[0]
    failed = finish(runtime, turn)
    assert failed["response"]["native_loopx"]["status"] == "failed"
    old = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])
    folder = attempt_directory(reports.root, old["window"])
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    calls, writes = capture.read_bytes(), list(bot.writes)
    assert reports.health(binding["binding_id"])["delivery_status"] == "generation_failed"
    cancel = reports.configure(binding["binding_id"], operation="cancel")["plan"]
    assert cancel["retires_failed_window"]
    reports.configure(binding["binding_id"], operation="cancel", execute=True, expected_plan_id=cancel["plan_id"])
    retired = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])
    assert retired["retirement"]["source_receipt_id"] == turn["turn_id"] and retired["publication"] is None
    task = next(row for row in StewardMonitor(runtime).todos(actor) if row["todo_id"] == turn["loopx_request"]["todo_id"])
    assert task["status"] == "blocked" and not task.get("done")
    future = {**schedule, "start_date": "2099-05-01"}
    replacement = reports.configure(binding["binding_id"], operation="enable", schedule=future)["plan"]
    reports.configure(binding["binding_id"], operation="enable", schedule=future, execute=True,
        expected_plan_id=replacement["plan_id"])
    assert reports.health(binding["binding_id"])["schedule"] == future
    # Same-plan restoration does not replay this failed attempt. The next
    # calendar window remains useful, under the ordinary native admission.
    resume = reports.configure(binding["binding_id"], operation="enable", schedule=schedule)["plan"]
    reports.configure(binding["binding_id"], operation="enable", schedule=schedule, execute=True, expected_plan_id=resume["plan_id"])
    assert not pump_steward_monitors(runtime)
    assert calls == capture.read_bytes() and writes == bot.writes
    helper.write_text(valid_model)
    point += timedelta(days=1)
    runtime._steward_monitor_next_check.clear()
    next_turn = pump_steward_monitors(runtime)[0]
    assert next_turn["turn_id"] != turn["turn_id"]
    completed = finish(runtime, next_turn)
    assert completed["response"]["native_loopx"]["status"] == "committed", completed
    assert transport.steward_reports.reconcile() == 1
    assert store.load_turn(sid, turn["turn_id"])["response"]["native_loopx"]["status"] == "failed"
    assert before == {name: (folder / name).read_bytes() for name in before}
    assert all(json.loads(line).get("method") != "thread/start" for line in capture.read_bytes()[len(calls):].splitlines())
    assert read_periodic_report_publication_cursor(runtime_root=reports.root,
        goal_id=actor["goal_id"], agent_id=actor["agent_id"])["covered_until"] == old["window"]["next_due_at"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fault", ["spend_rejected", "writeback_rejected", "spend_response_lost", "writeback_response_lost", "result_event_lost"])
def test_original_report_resumes_remaining_native_spend_after_generation_done_without_model_replay(steward, tmp_path, monkeypatch, provider, fault):
    from loopx.cli_commands import turn_run_once
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    store, runtime, bot, transport, binding, capture, _ = steward
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, provider)
    install_model(steward, tmp_path, sid)
    if fault == "writeback_rejected":
        from datetime import timezone
        from loopx.capabilities.native_chat import steward_reports as report_host
        from loopx.capabilities.periodic_report import cadence_runtime
        # Keep both synthetic due windows in the past; the independent native
        # validator uses the real clock and must still reject future work.
        point = datetime.now(timezone.utc) - timedelta(days=2)
        class NextWindowClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return point.astimezone(tz) if tz else point.replace(tzinfo=None)
        monkeypatch.setattr(report_host, "datetime", NextWindowClock)
        monkeypatch.setattr(cadence_runtime, "datetime", NextWindowClock)
    method = "refresh_state_run" if fault.startswith("writeback") else "spend_quota_slot"
    owner = getattr(turn_run_once, method)
    attempts = []
    healthy = False
    if fault == "result_event_lost":
        append = store.append_event
        def lose_result(*args, **kwargs):
            if not healthy and kwargs.get("kind") == "native_loopx.result" and kwargs["payload"].get("status") == "committed":
                raise OSError("synthetic lost outer completion event after native commit")
            return append(*args, **kwargs)
        monkeypatch.setattr(store, "append_event", lose_result)
    else:
        def interrupted_owner(*args, **kwargs):
            attempts.append(kwargs.get("effect_ref"))
            if not healthy and fault.endswith("rejected"):
                return {"ok": False, "appended": False, "reason": "synthetic known absent native effect"}
            result = owner(*args, **kwargs)
            if not healthy:
                raise OSError("synthetic lost response after native effect committed")
            return result
        monkeypatch.setattr(turn_run_once, method, interrupted_owner)
    original = pump_steward_monitors(runtime)[0]
    failed = finish(runtime, original)
    if fault != "result_event_lost":
        # An interrupted callback can return the CLI's error envelope rather
        # than a final journal status. Neither shape authorizes delivery.
        assert failed["response"]["native_loopx"].get("status") != "committed"
    historical = store.load_turn(sid, original["turn_id"])
    todo = next(row for row in StewardMonitor(runtime).todos(actor)
        if row["todo_id"] == original["loopx_request"]["todo_id"])
    assert todo["status"] == "done" and todo["completion_turn_key"] == original["loopx_request"]["turn_instance_id"]
    from loopx.control_plane.turn_driver.journal_store import find_loopx_turn_key_by_settlement_identity
    original_key = find_loopx_turn_key_by_settlement_identity(reports.root, goal_id=actor["goal_id"],
        agent_id=actor["agent_id"], todo_id=todo["todo_id"], turn_instance_id=original["loopx_request"]["turn_instance_id"])
    assert original_key
    window = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])["window"]
    folder = attempt_directory(reports.root, window)
    body = (folder / "report.md").read_bytes()
    assert validate_private_generation(folder)
    assert transport.steward_reports.reconcile() == 0
    calls, writes = capture.read_bytes(), list(bot.writes)
    healthy = True
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)  # Resume native effects, never a new Chat/model Turn.
    assert capture.read_bytes() == calls and bot.writes == writes
    assert transport.steward_reports.reconcile() == 1, json.dumps([{k: v for k, v in row["payload"].items()
        if k in {"status", "reason", "error", "error_code", "recovery", "receipt", "result_kind", "completed_phases"}}
        for row in store.events_after(sid, original["turn_id"], None) if row["kind"] == "native_loopx.result"])
    cursor = read_periodic_report_publication_cursor(runtime_root=reports.root,
        goal_id=actor["goal_id"], agent_id=actor["agent_id"])
    assert cursor and cursor["covered_until"] == window["due_at"]
    assert body == (folder / "report.md").read_bytes()
    assert store.load_turn(sid, original["turn_id"])["response"] == historical["response"]
    recovered = [row["payload"] for row in store.events_after(sid, original["turn_id"], None)
        if row["kind"] == "native_loopx.result" and row["payload"].get("status") == "committed"]
    assert len(recovered) == 1 and recovered[0]["recovery"]["actual"]["host_invoked"] is False
    if fault != "result_event_lost":
        assert recovered[0]["resume_turn_key"] == original_key
        assert len(attempts) == (1 if fault.endswith("response_lost") else 2)
    sent = list(bot.writes)
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime) and transport.steward_reports.reconcile() == 0
    assert bot.writes == sent and capture.read_bytes() == calls
    if fault == "writeback_rejected":
        point = datetime.fromisoformat(window["next_due_at"].replace("Z", "+00:00")) + timedelta(minutes=1)
        runtime._steward_monitor_next_check.clear()
        next_turn = pump_steward_monitors(runtime)[0]
        assert next_turn["turn_id"] != original["turn_id"]
        next_result = finish(runtime, next_turn)["response"]["native_loopx"]
        assert next_result["status"] == "committed", {key: value for key, value in next_result.items()
            if key in {"reason", "error", "error_code", "status", "validation", "recovery"}}
        assert transport.steward_reports.reconcile() == 1
        next_calls = [json.loads(line) for line in capture.read_bytes()[len(calls):].splitlines()]
        assert sum(row.get("method") == "turn/start" for row in next_calls) == 1
        assert all(row.get("method") != "thread/start" for row in next_calls)
        assert body == (folder / "report.md").read_bytes()
        assert read_periodic_report_publication_cursor(runtime_root=reports.root,
            goal_id=actor["goal_id"], agent_id=actor["agent_id"])["covered_until"] == window["next_due_at"]
