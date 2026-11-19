"""Focused regressions for report validator path portability and cancel recovery."""
from datetime import datetime, timedelta, timezone
import json
import logging

import pytest

from loopx.capabilities.native_chat.steward_monitor import pump_steward_monitors
from loopx.capabilities.native_chat.steward_turn import StewardTurnHost, _same_report_validator_command
from loopx.capabilities.periodic_report import cadence_runtime
from loopx.capabilities.periodic_report.cadence_journal import read_cadence_journal
from loopx.capabilities.periodic_report.incremental import read_periodic_report_publication_cursor
from loopx.capabilities.periodic_report.private_generation import attempt_directory
import loopx.capabilities.native_chat.steward_reports as report_host
from test_native_steward_private import finish, steward as steward_fixture
from test_native_steward_reports import configured, install_model

steward = steward_fixture


def test_same_report_validator_command_tolerates_only_python_executable_drift():
    folder = "/tmp/example-goal/periodic_reports/win-1"
    snippet = (
        "from pathlib import Path; "
        "from loopx.capabilities.periodic_report.private_generation import validate_private_generation; "
        f"validate_private_generation(Path({folder!r}))"
    )
    old_cmd = ["/opt/old-release/python3", "-c", snippet]
    new_cmd = ["/opt/new-release/python3.12", "-c", snippet]
    other_folder_cmd = [
        "/opt/new-release/python3.12",
        "-c",
        snippet.replace(folder, "/tmp/other-goal/periodic_reports/win-1"),
    ]
    arbitrary_cmd = ["/opt/new-release/python3.12", "-c", "print('hi')"]

    assert _same_report_validator_command(old_cmd, new_cmd) is True
    assert _same_report_validator_command(old_cmd, other_folder_cmd) is False
    assert _same_report_validator_command(arbitrary_cmd, arbitrary_cmd) is False
    assert _same_report_validator_command(["/opt/old/python", "-m", snippet], new_cmd) is False


def test_pump_tolerates_report_validator_python_upgrade_and_still_rejects_target_drift(
    steward, tmp_path, monkeypatch, caplog,
):
    from loopx.chat_agent import CodexChatAgentError
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter

    store, runtime, *_ = steward
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, "sqlite")
    point = datetime.now(timezone.utc).replace(hour=9, minute=1, second=0, microsecond=0) - timedelta(days=2)

    class CalendarClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return point.astimezone(tz) if tz else point.replace(tzinfo=None)

    monkeypatch.setattr(report_host, "datetime", CalendarClock)
    monkeypatch.setattr(cadence_runtime, "datetime", CalendarClock)

    def fail_mid_turn(adapter, _prompt, event_sink):
        event_sink("turn.started", {"upstream_turn_id": "mid-turn-failure-1"})
        raise CodexChatAgentError("Synthetic mid-turn upstream failure.")

    monkeypatch.setattr(CodexAppServerAdapter, "start_turn", fail_mid_turn)
    first = finish(runtime, pump_steward_monitors(runtime)[0])
    assert first["response"]["native_loopx"]["status"] == "failed"

    # Rewrite only the recorded Python executable path as happens across local release upgrades.
    turn = store.load_turn(sid, first["turn_id"])
    old_req = json.loads(json.dumps(turn["loopx_request"]))
    old_req["validation_command"][0] = "/tmp/previous-release/bin/python"
    turn["loopx_request"] = old_req
    store._turn_path(sid, turn["turn_id"]).write_text(json.dumps(turn))

    # Avoid app-server stdio probing in this unit-level identity check; keep focus on _prepare_submission.
    monkeypatch.setattr(StewardTurnHost, "report_failure_observation", lambda self, original: None)
    runtime._steward_monitor_next_check.clear()
    with caplog.at_level(logging.WARNING):
        assert pump_steward_monitors(runtime) == []
    assert "An original steward report is unavailable" not in caplog.text

    # Mutating the validated folder path must still fail closed as an identity conflict.
    _, window = reports.current_window(actor["goal_id"].removeprefix("steward-agent-"))
    host = StewardTurnHost(runtime)
    binding = host._submission_binding(actor["goal_id"].removeprefix("steward-agent-"))
    with pytest.raises(ValueError, match="native steward request identity conflict"):
        host._prepare_submission(
            binding,
            turn_instance_id=old_req["turn_instance_id"],
            todo_id=old_req["todo_id"],
            validation_command=["/opt/new/python", "-c", old_req["validation_command"][2].replace("'", "x'")],
            work_kind="report",
            monitor_deadline=None,
            report_window=window,
        )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_owner_cancel_retires_mid_turn_failed_report_without_editorial_response_and_advances(
    steward, tmp_path, monkeypatch, provider,
):
    from loopx.chat_agent import CodexChatAgentError
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor

    store, runtime, bot, transport, binding, capture, _ = steward
    sid, actor, reports, schedule = configured(steward, tmp_path, monkeypatch, provider)
    point = datetime.now(timezone.utc).replace(hour=9, minute=1, second=0, microsecond=0) - timedelta(days=2)

    class CalendarClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return point.astimezone(tz) if tz else point.replace(tzinfo=None)

    monkeypatch.setattr(report_host, "datetime", CalendarClock)
    monkeypatch.setattr(cadence_runtime, "datetime", CalendarClock)

    def mid_turn_failure(adapter, _prompt, event_sink):
        event_sink("turn.started", {"upstream_turn_id": "mid-turn-limit-hit"})
        raise CodexChatAgentError("You've hit your usage limit mid-turn.")

    with monkeypatch.context() as failure:
        failure.setattr(CodexAppServerAdapter, "start_turn", mid_turn_failure)
        first = finish(runtime, pump_steward_monitors(runtime)[0])
    native = first["response"]["native_loopx"]
    assert native["status"] == "failed"
    assert native["result_kind"] == "host_failure"
    assert native["host_failure"]["retryable"] is False

    old = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])
    folder = attempt_directory(reports.root, old["window"])
    assert not (folder / "editorial-response.json").exists()
    assert not (folder / "private-delivery.json").exists()

    # Keep _known_generation_failure and health().delivery_status unchanged when no editorial-response exists.
    assert reports._known_generation_failure(actor, old["window"]) is None
    assert reports.health(binding["binding_id"])["delivery_status"] == "not_generated"

    # Negative check: an unconfirmed private delivery record must still block failure retirement.
    delivery_path = folder / "private-delivery.json"
    delivery_path.write_text(json.dumps({"delivery_status": "unconfirmed"}))
    blocked_cancel = reports.configure(binding["binding_id"], operation="cancel")["plan"]
    assert blocked_cancel["retires_failed_window"] is False
    delivery_path.unlink()

    # Without delivery or generation artifacts, owner cancel may retire the stuck window cleanly.
    cancel = reports.configure(binding["binding_id"], operation="cancel")["plan"]
    assert cancel["retires_failed_window"] is True
    reports.configure(
        binding["binding_id"],
        operation="cancel",
        execute=True,
        expected_plan_id=cancel["plan_id"],
    )
    retired = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])
    assert retired["retirement"]["source_receipt_id"] == first["turn_id"]
    task = next(row for row in StewardMonitor(runtime).todos(actor) if row["todo_id"] == first["loopx_request"]["todo_id"])
    assert task["status"] == "blocked" and not task.get("done")

    # Re-enabling advances cleanly to the next due window and delivers a real report.
    install_model(steward, tmp_path, sid)
    resume = reports.configure(binding["binding_id"], operation="enable", schedule=schedule)["plan"]
    reports.configure(binding["binding_id"], operation="enable", schedule=schedule, execute=True, expected_plan_id=resume["plan_id"])
    point += timedelta(days=1)
    runtime._steward_monitor_next_check.clear()
    next_turns = pump_steward_monitors(runtime)
    assert len(next_turns) == 1
    completed = finish(runtime, next_turns[0])
    assert completed["response"]["native_loopx"]["status"] == "committed", completed
    assert transport.steward_reports.reconcile() == 1
    cursor = read_periodic_report_publication_cursor(runtime_root=reports.root, goal_id=actor["goal_id"], agent_id=actor["agent_id"])
    assert cursor["covered_until"] == old["window"]["next_due_at"]
