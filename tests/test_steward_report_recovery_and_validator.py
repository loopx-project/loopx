"""Focused regressions for report validator path portability and cancel recovery."""
from datetime import datetime, timedelta, timezone
import json
import logging

import pytest

from loopx.capabilities.native_chat.steward_monitor import pump_steward_monitors
from loopx.capabilities.native_chat.steward_turn import (
    StewardTurnHost,
    _same_report_validator_command,
    native_steward_client_id,
)
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


def test_selection_recovery_admission_resume_tolerates_validator_python_upgrade(
    steward, tmp_path, monkeypatch, caplog,
):
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    from loopx.configure_goal import configure_goal
    from test_native_steward_reports import _REPORT_MODEL_SOURCE

    store, runtime, _, transport, binding, _, _ = steward
    point = datetime.now(timezone.utc).replace(hour=9, minute=1, second=0, microsecond=0) - timedelta(days=2)

    class CalendarClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return point.astimezone(tz) if tz else point.replace(tzinfo=None)

    monkeypatch.setattr(report_host, "datetime", CalendarClock)
    monkeypatch.setattr(cadence_runtime, "datetime", CalendarClock)
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, "sqlite")
    owner = binding["binding_id"]
    assert configure_goal(
        registry_path=runtime.registry_path, goal_id=actor["goal_id"],
        execution_replan_after_turns=1, execute=True,
    )["ok"]
    install_model(steward, tmp_path, sid)
    assert finish(runtime, pump_steward_monitors(runtime)[0])["response"]["native_loopx"]["status"] == "committed"
    assert transport.steward_reports.reconcile() == 1

    point += timedelta(days=1)
    runtime._steward_monitor_next_check.clear()
    rejected = finish(runtime, pump_steward_monitors(runtime)[0])
    assert rejected["response"]["native_loopx"]["error_code"] == "turn_todo_selection_deferred"

    bad_vision = {
        "schema_version": "goal_vision_replan_contract_v0",
        "state": "active",
        "vision_patch": {"vision_summary": "Keep reporting.", "acceptance_summary": "Validate full report."},
        "path_delta": {
            "outcome": "replan",
            "prior_assumption": "Old assumption",
            "observed_reality": "Reality",
            "retained": ["Same report route"],
            "changed": ["Updated baseline"],
            "evidence_refs": ["fixture:evidence"],
        },
    }
    bad_outcome = {
        "result_kind": "replan_required",
        "classification": "outcome_gap_without_blocked_fields",
        "delivery_batch_scale": "implementation",
        "delivery_outcome": "outcome_gap",
        "work_evidence": ["Observed gap"],
        "validation_evidence": ["Validated check"],
        "path_delta_mode": "material_replan",
        "agent_vision_json": json.dumps(bad_vision),
        "summary": "Missing blocked fields trigger host_result_contract validation failure.",
        "next_action": "Clear replan before resuming report.",
    }
    helper = tmp_path / "report_model.py"
    helper.write_text(
        _REPORT_MODEL_SOURCE.replace("def fake_report_model(", "def report_only(")
        + "\ndef fake_report_model(params):\n"
        + "    import json\n"
        + "    prompt = '\\n'.join(row.get('text', '') for row in params.get('input', []) if isinstance(row, dict))\n"
        + "    request = json.JSONDecoder().raw_decode(prompt.split('Turn request:', 1)[1].lstrip())[0]\n"
        + "    if request.get('detail_ref', {}).get('replan_action_packet'):\n"
        + "        return " + repr(json.dumps(bad_outcome)) + "\n"
        + "    return report_only(params)\n"
    )
    runtime._steward_monitor_next_check.clear()
    probe = finish(runtime, pump_steward_monitors(runtime)[0])

    # Simulate host Python interpreter path upgrade on both the original refused turn and probe turn.
    for row_id in (rejected["turn_id"], probe["turn_id"]):
        row = store.load_turn(sid, row_id)
        assert row is not None
        req = json.loads(json.dumps(row["loopx_request"]))
        req["validation_command"][0] = "/tmp/previous-release/bin/python"
        row["loopx_request"] = req
        store._turn_path(sid, row_id).write_text(json.dumps(row))

    assert configure_goal(
        registry_path=runtime.registry_path, goal_id=actor["goal_id"],
        execution_replan_after_turns=6, execute=True,
    )["ok"]
    runtime._steward_monitor_next_check.clear()
    with caplog.at_level(logging.WARNING):
        resumed = pump_steward_monitors(runtime)
    assert "An original steward report is unavailable" not in caplog.text
    assert len(resumed) == 1
    expected_client_id = native_steward_client_id(
        actor,
        rejected["loopx_request"]["turn_instance_id"] + ".admission-resume",
    )
    assert resumed[0]["client_turn_id"] == expected_client_id
    done = finish(runtime, resumed[0])
    assert done["response"]["native_loopx"]["status"] == "committed", done
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)
    assert transport.steward_reports.reconcile() == 1
    assert reports.health(owner)["publication_verified"]
    task = next(row for row in StewardMonitor(runtime).todos(actor) if row["todo_id"] == done["loopx_request"]["todo_id"])
    assert task["status"] == "done"
