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


def install_model(steward, tmp_path, sid, *, result_kind="validated_completion"):
    _, runtime, _, _, _, _, _ = steward
    helper = tmp_path / "report_model.py"
    helper.write_text(inspect.getsource(fake_report_model).replace(
        '"result_kind": "validated_completion"', '"result_kind": ' + repr(result_kind)))
    fake = Path(runtime.codex_bin)
    source = fake.read_text().replace('"message": "Runtime response."',
        '"message": __import__("runpy").run_path(' + repr(str(helper)) + ')["fake_report_model"](request["params"])')
    fake.write_text(source)
    runtime.adapters.pop(sid).close_session()  # Resume the same native thread with the synthetic response.


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
    point = datetime.now(timezone.utc).replace(hour=9, minute=1, second=0, microsecond=0) - timedelta(days=3)

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
