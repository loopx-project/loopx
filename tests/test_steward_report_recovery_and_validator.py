"""Focused regressions for report validator path portability and cancel recovery."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
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
    from loopx.capabilities.native_chat import steward_commands
    from loopx.configure_goal import configure_goal
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement
    from loopx.control_plane.turn_driver.journal_store import turn_journal_path
    from loopx.rollout_event_log import rollout_event_log_path
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
    request = rejected["loopx_request"]
    report_acquires = []
    actual_command = steward_commands.native_command

    def observe_acquire(controller, argv):
        result = actual_command(controller, argv)
        if argv[:2] == ["task-lease", "acquire"]:
            assert argv[argv.index("--todo-id") + 1] == request["todo_id"]
            report_acquires.append(dict(result["lease"]))
        return result

    def report_readback():
        return read_heartbeat_settlement(reports.root,
            goal_id=actor["goal_id"], agent_id=actor["agent_id"], todo_id=request["todo_id"],
            turn_instance_id=request["turn_instance_id"], registry_path=runtime.registry_path)

    def model_call_count():
        return sum(json.loads(line).get("method") == "turn/start"
            for line in steward[5].read_text().splitlines())

    rollout_path = rollout_event_log_path(reports.root, actor["goal_id"])

    def replan_receipt_lines():
        return [line for line in rollout_path.read_bytes().splitlines(keepends=True)
            if (row := json.loads(line)).get("event_kind") == "quota_should_run"
            and row.get("run_id") == request["turn_instance_id"] + ".replan"]

    monkeypatch.setattr(steward_commands, "native_command", observe_acquire)
    model_calls_before_probe = model_call_count()

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
    assert report_acquires == []
    assert model_call_count() == model_calls_before_probe + 1
    governance = next(row["payload"] for row in store.events_after(sid, probe["turn_id"], None)
        if row["kind"] == "native_loopx.replan_result")
    assert governance["status"] == "failed" and governance["validation_stage"] == "host_result_contract"
    assert governance["effects"]["state_written"] is False and governance["effects"]["quota_spent"] is False
    business = report_readback()
    assert business is not None and business.identity.failure is not None
    assert business.identity.failure.kind == "receipt_missing"
    assert business.prepared_lease is None and business.original_prepared_receipt is None
    assert business.writeback_run is None and business.spend_run is None
    probe_journal_path = turn_journal_path(reports.root, goal_id=actor["goal_id"],
        turn_key=governance["resume_turn_key"])
    probe_journal_bytes = probe_journal_path.read_bytes()
    probe_receipt_bytes = replan_receipt_lines()
    assert probe_receipt_bytes

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
    assert len(report_acquires) == 1
    assert model_call_count() == model_calls_before_probe + 2
    business = report_readback()
    assert business is not None and business.prepared_lease == {
        "prepared_lease_key_sha256": sha256(report_acquires[0]["idempotency_key"].encode()).hexdigest(),
        "prepared_lease_version": report_acquires[0]["version"],
        "prepared_lease_epoch": report_acquires[0]["lease_epoch"],
    }
    assert business.original_prepared_receipt is not None
    assert probe_journal_path.read_bytes() == probe_journal_bytes
    assert replan_receipt_lines() == probe_receipt_bytes
    model_bytes = steward[5].read_bytes()
    runtime._steward_monitor_next_check.clear()
    assert not pump_steward_monitors(runtime)
    assert steward[5].read_bytes() == model_bytes
    assert transport.steward_reports.reconcile() == 1
    assert reports.health(owner)["publication_verified"]
    task = next(row for row in StewardMonitor(runtime).todos(actor) if row["todo_id"] == done["loopx_request"]["todo_id"])
    assert task["status"] == "done"


def test_steward_report_normalizes_profile_minimum_scale_shorthand(steward, tmp_path, monkeypatch):
    _, runtime, _, transport, binding, _, _ = steward
    sid, _, reports, _ = configured(steward, tmp_path, monkeypatch, "sqlite")
    install_model(steward, tmp_path, sid)
    helper = tmp_path / "report_model.py"
    helper.write_text(
        helper.read_text(encoding="utf-8").replace(
            '"delivery_batch_scale": "single_surface"',
            '"delivery_batch_scale": "multi_surface_or_implementation"',
        ),
        encoding="utf-8",
    )

    done = finish(runtime, pump_steward_monitors(runtime)[0])
    assert done["response"]["native_loopx"]["status"] == "committed", done
    assert transport.steward_reports.reconcile() == 1
    assert reports.health(binding["binding_id"])["publication_verified"]


@pytest.mark.parametrize("replace_original_lease", [False, True], ids=["same-generation", "replacement-refused"])
def test_frozen_report_recovers_host_result_contract_failure_without_model_call(
    steward, tmp_path, monkeypatch, caplog, replace_original_lease,
):
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    from loopx.capabilities.native_chat import steward_commands
    import loopx.capabilities.native_chat.steward_turn as steward_turn_mod
    from loopx.configure_goal import configure_goal
    from loopx.control_plane.quota import execution_preparation
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement

    _, runtime, _, transport, binding, _, _ = steward
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

    helper = tmp_path / "report_model.py"
    helper.write_text(
        helper.read_text(encoding="utf-8").replace(
            '"delivery_batch_scale": "single_surface"',
            '"delivery_batch_scale": "multi_surface_or_implementation"',
        ),
        encoding="utf-8",
    )
    assert configure_goal(
        registry_path=runtime.registry_path, goal_id=actor["goal_id"],
        execution_replan_after_turns=6, execute=True,
    )["ok"]
    runtime._steward_monitor_next_check.clear()
    with monkeypatch.context() as unnormalized:
        unnormalized.setattr(
            steward_turn_mod,
            "_normalize_report_kernel_candidate",
            lambda candidate: {k: v for k, v in candidate.items() if k != "editorial_response"} | {
                "result_kind": "validated_completion",
            },
        )
        resumed = finish(runtime, pump_steward_monitors(runtime)[0])
    native = resumed["response"]["native_loopx"]
    assert native["status"] == "failed"
    assert native["result_kind"] == "validation_failed"
    assert native["validation_stage"] == "host_result_contract"
    assert native["effects"]["state_written"] is False
    assert native["effects"]["quota_spent"] is False
    window = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])["window"]
    generation_path = attempt_directory(reports.root, window) / "private-generation.json"
    generation_bytes = generation_path.read_bytes()
    request = resumed["loopx_request"]
    route = ["--goal-id", actor["goal_id"], "--todo-id", request["todo_id"]]

    def readback():
        return read_heartbeat_settlement(
            reports.root, goal_id=actor["goal_id"], agent_id=actor["agent_id"],
            todo_id=request["todo_id"], turn_instance_id=request["turn_instance_id"],
            registry_path=runtime.registry_path,
        )

    original_lease = steward_commands.native_command(runtime, ["task-lease", "inspect", *route])["lease"]
    original_readback = readback()
    assert original_readback is not None and original_readback.original_prepared_receipt is not None
    original_prepared = original_readback.prepared_lease
    assert original_prepared == {
        "prepared_lease_key_sha256": sha256(original_lease["idempotency_key"].encode()).hexdigest(),
        "prepared_lease_version": original_lease["version"],
        "prepared_lease_epoch": original_lease["lease_epoch"],
    }
    assert original_lease["status"] == "active"
    assert original_readback.writeback_run is None and original_readback.writeback_event is None
    assert original_readback.spend_run is None and original_readback.spend_event is None

    commands, qualification_requests, qualifications = [], [], []
    actual_command = steward_commands.native_command
    actual_qualification = execution_preparation.read_turn_execution_lease_qualification

    def observe_command(controller, argv):
        result = actual_command(controller, argv)
        if argv[:2] in (["task-lease", "renew"], ["task-lease", "acquire"]):
            commands.append((list(argv), dict(result["lease"])))
        return result

    def observe_qualification(**kwargs):
        qualification_requests.append(kwargs["requested_proof"])
        result = actual_qualification(**kwargs)
        qualifications.append(result.resume_lease_qualification)
        return result

    monkeypatch.setattr(steward_commands, "native_command", observe_command)
    monkeypatch.setattr(execution_preparation, "read_turn_execution_lease_qualification", observe_qualification)
    task = next(row for row in StewardMonitor(runtime).todos(actor) if row["todo_id"] == request["todo_id"])
    model_calls = steward[5].read_bytes()
    replacement = None
    if replace_original_lease:
        actual_command(runtime, ["task-lease", "release", *route, "--owner", actor["agent_id"],
            "--idempotency-key", original_lease["idempotency_key"],
            "--expected-version", str(original_lease["version"])])
        released = actual_command(runtime, ["task-lease", "inspect", *route])["lease"]
        replacement = actual_command(runtime, ["task-lease", "acquire", *route, "--owner", actor["agent_id"],
            "--idempotency-key", request["turn_instance_id"] + ".replacement", "--ttl-seconds", "3600",
            "--expected-version", str(released["version"])])["lease"]
        assert replacement["idempotency_key"] != original_lease["idempotency_key"]
        assert replacement["lease_epoch"] > original_lease["lease_epoch"]
        assert replacement["version"] > original_lease["version"]
        with pytest.raises(execution_preparation.RefreshTaskLeaseUnavailable, match="replacement lease"):
            StewardTurnHost(runtime).recover_report_settlement(actor, window, task)

    runtime._steward_monitor_next_check.clear()
    with caplog.at_level(logging.WARNING):
        assert not pump_steward_monitors(runtime)
    assert steward[5].read_bytes() == model_calls
    assert generation_path.read_bytes() == generation_bytes
    final_readback = readback()
    assert final_readback is not None
    assert final_readback.original_prepared_receipt == original_readback.original_prepared_receipt
    assert final_readback.original_prepared_event_id == original_readback.original_prepared_event_id
    assert final_readback.original_prepared_receipt_sha256 == original_readback.original_prepared_receipt_sha256
    current_lease = actual_command(runtime, ["task-lease", "inspect", *route])["lease"]
    task = next(
        row for row in StewardMonitor(runtime).todos(actor)
        if row["todo_id"] == resumed["loopx_request"]["todo_id"]
    )
    if replace_original_lease:
        assert "An original steward report is unavailable" in caplog.text
        assert commands == [] and qualifications == []
        assert qualification_requests and all(proof == {
            "idempotency_key": replacement["idempotency_key"], "expected_version": replacement["version"],
        } for proof in qualification_requests)
        assert current_lease == replacement
        assert task["status"] == "open"
        assert final_readback.writeback_run is None and final_readback.writeback_event is None
        assert final_readback.spend_run is None and final_readback.spend_event is None
        assert final_readback.completion_event is None
        assert transport.steward_reports.reconcile() == 0
    else:
        assert "An original steward report is unavailable" not in caplog.text
        assert commands and all(argv[:2] == ["task-lease", "renew"] for argv, _ in commands)
        assert qualifications and all(row["state"] == "ready" and row["original_prepared"] == original_prepared
            for row in qualifications)
        for lease in [current_lease, *(lease for _, lease in commands)]:
            assert lease["idempotency_key"] == original_lease["idempotency_key"]
            assert lease["lease_epoch"] == original_lease["lease_epoch"]
            assert lease["version"] >= original_lease["version"]
        assert current_lease["version"] > original_lease["version"]
        assert current_lease["status"] == "released"
        assert actual_command(runtime, ["task-lease", "inspect", *route])["active"] is False
        assert final_readback.writeback_run is not None and final_readback.spend_run is not None
        assert task["status"] == "done"
        assert transport.steward_reports.reconcile() == 1
        assert reports.health(owner)["publication_verified"]


@pytest.mark.parametrize("cleanup_boundary", ["normal", "rollout_occupied"])
def test_legacy_native_task_lease_cleanup_uses_fenced_release(
    steward, tmp_path, monkeypatch, cleanup_boundary,
):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    from loopx.capabilities.native_chat.steward_commands import native_command
    from loopx.control_plane.quota import settlement
    from loopx.file_lock import exclusive_cross_runtime_file_lock
    from loopx.rollout_event_log import rollout_event_log_path

    store, runtime, *_ = steward
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, "sqlite")
    assert actor.get("goal_instance_id") is None
    added = native_command(runtime, ["todo", "add", "--goal-id", actor["goal_id"],
        "--role", "agent", "--text", "[P1] Inspect the authorized task before admission.",
        "--claimed-by", actor["agent_id"], "--operation-id", "lease-cleanup-fixture", "--execute"])
    turn, _ = store.create_queued_turn(sid, client_turn_id="legacy-lease-cleanup",
        message="Inspect the original task before admission.")
    request = {"actor": actor, "todo_id": added["todo_id"], "turn_instance_id": "legacy-lease-cleanup"}
    route = ["--goal-id", actor["goal_id"], "--todo-id", request["todo_id"]]
    actual_readback = settlement.read_heartbeat_settlement
    dispositions = []
    rollout_path = rollout_event_log_path(reports.root, actor["goal_id"])
    original_rollout = rollout_path.read_bytes() if rollout_path.exists() else None
    rollout_held, release_rollout, rollout_released = threading.Event(), threading.Event(), threading.Event()

    def occupy_rollout():
        with exclusive_cross_runtime_file_lock(
            rollout_event_log_path(reports.root, actor["goal_id"]), operation="fixture_competing_rollout_owner",
        ):
            rollout_held.set()
            assert release_rollout.wait(15), "cleanup waited for the occupied rollout lock"
        rollout_released.set()

    with ThreadPoolExecutor(max_workers=1) as workers:
        competing_owner = None

        def observe_cleanup(*args, **kwargs):
            nonlocal competing_owner
            result = actual_readback(*args, **kwargs)
            if kwargs.get("native_task_lease_cleanup") is None:
                return result
            assert kwargs["goal_ref"] is None and kwargs["borrow_source_admission"] is False
            assert kwargs["native_task_lease_cleanup"] == {
                "schema_version": "native_task_lease_cleanup_request_v0", "recovery": False,
                "admission_retry": False, "resume_turn_key_present": False, "original_journal_present": False,
            }
            assert result.identity.failure is not None and result.identity.failure.kind == "receipt_missing"
            assert result.heartbeat_receipt is None
            assert result.native_task_lease_cleanup == {
                "schema_version": "native_task_lease_cleanup_v0", "disposition": "release",
                "reason": "fresh_pre_admission_missing",
            }
            assert result.writeback_run is None and result.writeback_event is None
            assert result.spend_run is None and result.spend_event is None
            assert result.completion_event is None
            dispositions.append(result.native_task_lease_cleanup["disposition"])
            if len(dispositions) == 1 and cleanup_boundary == "rollout_occupied":
                competing_owner = workers.submit(occupy_rollout)
                assert rollout_held.wait(10), "competing owner did not acquire rollout"
            return result

        monkeypatch.setattr(settlement, "read_heartbeat_settlement", observe_cleanup)
        try:
            with StewardTurnHost(runtime)._task_execution_lease(request, turn["turn_id"]) as lease:
                original_lease = dict(lease)
            if cleanup_boundary == "rollout_occupied":
                assert competing_owner is not None and not competing_owner.done()
                assert rollout_held.is_set() and not rollout_released.is_set()
        finally:
            release_rollout.set()
            if competing_owner is not None:
                competing_owner.result(timeout=10)

    inspected = native_command(runtime, ["task-lease", "inspect", *route])
    diagnostics = [row for row in store.events_after(sid, turn["turn_id"], None)
        if row["kind"] == "native_loopx.task_lease_release_unconfirmed"]
    assert (rollout_path.read_bytes() if rollout_path.exists() else None) == original_rollout
    if cleanup_boundary == "normal":
        assert dispositions == ["release", "release"]
        assert inspected["active"] is False and inspected["lease"]["status"] == "released"
        assert inspected["lease"]["idempotency_key"] == original_lease["idempotency_key"]
        assert inspected["lease"]["lease_epoch"] == original_lease["lease_epoch"]
        assert diagnostics == []
    else:
        assert dispositions == ["release"]
        assert inspected["active"] is True and inspected["lease"] == original_lease
        assert len(diagnostics) == 1 and diagnostics[0]["payload"] == {
            "todo_id": request["todo_id"], "lease_epoch": original_lease["lease_epoch"],
        }
