"""Focused regressions for report validator path portability and cancel recovery."""
import contextlib
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


def test_frozen_report_recovers_host_result_contract_failure_without_model_call(
    steward, tmp_path, monkeypatch, caplog,
):
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    import loopx.capabilities.native_chat.steward_turn as steward_turn_mod
    from loopx.configure_goal import configure_goal

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
    window = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])["window"]
    assert (attempt_directory(reports.root, window) / "private-generation.json").is_file()

    model_calls = steward[5].read_bytes()
    runtime._steward_monitor_next_check.clear()
    with caplog.at_level(logging.WARNING):
        assert not pump_steward_monitors(runtime)
    assert "An original steward report is unavailable" not in caplog.text
    assert steward[5].read_bytes() == model_calls
    assert transport.steward_reports.reconcile() == 1
    assert reports.health(owner)["publication_verified"]
    task = next(
        row for row in StewardMonitor(runtime).todos(actor)
        if row["todo_id"] == resumed["loopx_request"]["todo_id"]
    )
    assert task["status"] == "done"


def test_steward_antigravity_report_timeout_recovers_once_in_isolated_cascade_without_codex_spawn(
    steward, tmp_path, monkeypatch, caplog,
):
    import contextlib
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import logging
    import re
    import threading
    from typing import Any
    from test_chat_manager_model_adoption import configure
    from test_native_steward_private import finish
    from test_native_steward_reports import configured, fake_report_model
    from loopx.capabilities.native_chat.antigravity_adapter import AntigravityAdapter
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor, pump_steward_monitors
    from loopx.chat_agent import CodexChatAgentError
    from loopx.chat_manager import steward_machine_defaults

    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, "sqlite")
    store, runtime, _, transport, binding, capture, _ = steward
    owner = binding["binding_id"]
    configure(runtime.coordination_runtime_root, model="gpt-6.2-sol", effort="xhigh", endpoint="codex", policy="flexible")
    defaults = steward_machine_defaults(runtime)
    session = store.load_session(sid)
    orig_thread = session["upstream_thread_id"]
    turn_switch, _ = store.create_turn(sid, client_turn_id="agy-switch", message="Switch steward to Antigravity")
    store.update_turn(sid, turn_switch["turn_id"], status="queued")
    store.update_session(sid, status="ready", active_turn_id=turn_switch["turn_id"], agent_model_id="gpt-6.2-sol")
    session = store.load_session(sid)
    allocation = {
        "schema_version": "manager_executor_allocation_v0",
        "selection_policy": "flexible",
        "allocation_reason": "flexible_availability_fallback",
        "executor_endpoint": "antigravity",
        "executor_endpoint_source": "flexible_pool",
        "executor_endpoint_default_reason": "",
        "configured_endpoint": "codex",
        "eligible_endpoints": ["codex", "antigravity"],
        "configuration_revision": defaults["configuration_revision"],
        "available": True,
        "model": "gemini-4-carbon-high",
        "model_source": "vendor_default",
        "reasoning_effort": "high",
        "configured_model": "gpt-6.2-sol",
        "configured_reasoning_effort": "xhigh",
    }
    store.update_session(
        sid,
        status="ready",
        active_turn_id=turn_switch["turn_id"],
        expected_upstream_thread_id=orig_thread,
        agent_id="antigravity",
        executor_endpoint_id="antigravity",
        adapter_kind="antigravity_ls",
        upstream_mode="default",
        upstream_thread_id="ag-cascade-1",
        agent_model_id="gemini-4-carbon-high",
        agent_reasoning_effort="high",
        manager_executor_allocation=allocation,
        upstream_context_history=[
            *(session.get("upstream_context_history") or []),
            {
                "thread_id": orig_thread,
                "manager_context_version": session.get("manager_context_version"),
                "agent_id": "codex",
                "agent_model_id": "gpt-6.2-sol",
            },
        ],
    )
    store.update_turn(sid, turn_switch["turn_id"], status="completed")
    store.update_session(sid, active_turn_id=None)
    if sid in runtime.adapters:
        runtime.adapters.pop(sid).close_session()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = self.path.rsplit("/", 1)[-1]
            if method == "GetCascadeModelConfigData":
                resp = {
                    "clientModelConfigs": [{
                        "modelId": "gemini-4-carbon-high",
                        "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M427"},
                        "quotaInfo": {"remainingFraction": 0.9},
                    }]
                }
            elif method == "GetUserStatus":
                resp = {
                    "userStatus": {
                        "email": "synthetic@invalid.example",
                        "cascadeModelConfigData": {
                            "clientModelConfigs": [{
                                "modelId": "gemini-4-carbon-high",
                                "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M427"},
                                "quotaInfo": {
                                    "remainingFraction": 0.9,
                                    "resetTime": "2030-01-01T00:00:00Z",
                                },
                            }]
                        },
                    }
                }
            elif method == "GetCascadeTrajectory":
                resp = {
                    "trajectory": {"cascadeId": body["cascadeId"]},
                    "status": "CASCADE_RUN_STATUS_IDLE",
                    "numTotalSteps": 4,
                }
            else:
                raise AssertionError(method)
            payload = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("ANTIGRAVITY_LS_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", "synthetic-csrf-token")

    def forbid_codex(*_args, **_kwargs):
        raise AssertionError("CodexAppServerAdapter must not be called for agy_turn_* recovery")

    monkeypatch.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(forbid_codex))

    def fake_start(cls, *, work_dir, objective, resume_thread_id=None, history=None,
                   runtime_profile="restricted", team_plan_context=None, executor_model=None,
                   env=None, idle_timeout_sec=None, hard_timeout_sec=None):
        return cls(
            work_dir=work_dir,
            objective=objective,
            cascade_id=resume_thread_id or "ag-cascade-1",
            ls_url=f"http://127.0.0.1:{server.server_port}",
            csrf_token="synthetic-csrf-token",
            model_id=(executor_model or {}).get("model") or "gemini-4-carbon-high",
            model_placeholder="MODEL_PLACEHOLDER_M427",
            reasoning_effort="high",
            runtime_profile=runtime_profile,
            idle_timeout_sec=idle_timeout_sec,
            hard_timeout_sec=hard_timeout_sec,
        )

    turns_seen: list[dict[str, Any]] = []

    def fake_turn(self, message, event_sink=None, **kwargs):
        with (self._launch_guard() if self._launch_guard is not None else contextlib.nullcontext()):
            turns_seen.append({
                "isolated_cascade": kwargs.get("isolated_cascade"),
                "raw_prompt": kwargs.get("raw_prompt"),
                "idle_timeout_sec": kwargs.get("idle_timeout_sec"),
                "message": message,
            })
            if len(turns_seen) == 1:
                if event_sink is not None:
                    event_sink("turn.started", {
                        "threadId": "ag-cascade-1",
                        "upstream_turn_id": "agy_turn_timeout_1",
                    })
                    event_sink("agent.phase", {
                        "method": "native/timeout",
                        "phase": "waiting_model_output",
                        "summary": "Antigravity produced no output for 300s",
                    })
                raise CodexChatAgentError(
                    "Antigravity produced no output for 300s",
                    gate=None,
                    error_code="response_timeout",
                )
            match = re.search(r'"editorial_request_path":\s*"([^"\n]+)"', message)
            assert match is not None
            if event_sink is not None:
                event_sink("turn.started", {
                    "threadId": "ag-isolated-2",
                    "upstream_turn_id": "agy_turn_isolated_2",
                })
                event_sink("turn.native_result", {
                    "thread_id": "ag-isolated-2",
                    "upstream_turn_id": "agy_turn_isolated_2",
                    "text": "isolated report ok",
                })
            body = fake_report_model({"input": [{"text": message}]})
            return {"message": body}

    monkeypatch.setattr(AntigravityAdapter, "start", classmethod(fake_start))
    monkeypatch.setattr(AntigravityAdapter, "start_turn", fake_turn)

    try:
        before_codex = capture.read_bytes()
        original = finish(runtime, pump_steward_monitors(runtime)[0])
        assert original["status"] == "completed"
        assert original["response"]["native_loopx"]["result_kind"] == "host_failure"
        assert original["upstream_turn_id"] == "agy_turn_timeout_1"
        assert not reports.health(owner)["publication_verified"]

        runtime._steward_monitor_next_check.clear()
        with caplog.at_level(logging.WARNING):
            dispatched = pump_steward_monitors(runtime)
        assert "An original steward report is unavailable" not in caplog.text
        assert len(dispatched) == 1
        resumed = finish(runtime, dispatched[0])
        assert resumed["status"] == "completed", resumed
        assert resumed["loopx_request"]["turn_instance_id"].endswith(".timeout-recovery")
        assert resumed["response"]["native_loopx"].get("status") == "committed", resumed
        assert turns_seen[0]["isolated_cascade"] is True
        assert turns_seen[1]["isolated_cascade"] is True
        assert turns_seen[0]["raw_prompt"] is True
        assert turns_seen[1]["raw_prompt"] is True
        assert turns_seen[0]["idle_timeout_sec"] == 300.0
        assert turns_seen[1]["idle_timeout_sec"] == 300.0
        assert capture.read_bytes() == before_codex
        assert transport.steward_reports.reconcile() == 1
        assert reports.health(owner)["publication_verified"]

        runtime._steward_monitor_next_check.clear()
        assert not pump_steward_monitors(runtime)
        task = next(
            row for row in StewardMonitor(runtime).todos(actor)
            if row["todo_id"] == resumed["loopx_request"]["todo_id"]
        )
        assert task["status"] == "done"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextlib.contextmanager
def _antigravity_steward_harness(steward, tmp_path, monkeypatch, *, provider="sqlite", fake_turn):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from typing import Any
    from test_chat_manager_model_adoption import configure
    from test_native_steward_reports import configured
    from loopx.capabilities.native_chat import antigravity_adapter as agy_mod
    from loopx.capabilities.native_chat.antigravity_adapter import AntigravityAdapter
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    from loopx.capabilities.native_chat.steward_monitor import pump_steward_monitors
    from loopx.chat_manager import steward_machine_defaults

    point = datetime.now(timezone.utc).replace(hour=9, minute=1, second=0, microsecond=0) - timedelta(days=3)
    clock = {"now": point}

    class CalendarClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].astimezone(tz) if tz else clock["now"].replace(tzinfo=None)

    monkeypatch.setattr(report_host, "datetime", CalendarClock)
    monkeypatch.setattr(cadence_runtime, "datetime", CalendarClock)
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, provider)
    store, runtime, _, transport, binding, capture, _ = steward
    configure(runtime.coordination_runtime_root, model="gpt-6.2-sol", effort="xhigh", endpoint="codex", policy="flexible")
    defaults = steward_machine_defaults(runtime)
    session = store.load_session(sid)
    orig_thread = session["upstream_thread_id"]
    turn_switch, _ = store.create_turn(sid, client_turn_id="agy-switch", message="Switch steward to Antigravity")
    store.update_turn(sid, turn_switch["turn_id"], status="queued")
    store.update_session(sid, status="ready", active_turn_id=turn_switch["turn_id"], agent_model_id="gpt-6.2-sol")
    session = store.load_session(sid)
    allocation = {
        "schema_version": "manager_executor_allocation_v0",
        "selection_policy": "flexible",
        "allocation_reason": "flexible_availability_fallback",
        "executor_endpoint": "antigravity",
        "executor_endpoint_source": "flexible_pool",
        "executor_endpoint_default_reason": "",
        "configured_endpoint": "codex",
        "eligible_endpoints": ["codex", "antigravity"],
        "configuration_revision": defaults["configuration_revision"],
        "available": True,
        "model": "gemini-4-carbon-high",
        "model_source": "vendor_default",
        "reasoning_effort": "high",
        "configured_model": "gpt-6.2-sol",
        "configured_reasoning_effort": "xhigh",
    }
    store.update_session(
        sid,
        status="ready",
        active_turn_id=turn_switch["turn_id"],
        expected_upstream_thread_id=orig_thread,
        agent_id="antigravity",
        executor_endpoint_id="antigravity",
        adapter_kind="antigravity_ls",
        upstream_mode="default",
        upstream_thread_id="ag-cascade-main",
        agent_model_id="gemini-4-carbon-high",
        agent_reasoning_effort="high",
        manager_executor_allocation=allocation,
        upstream_context_history=[
            *(session.get("upstream_context_history") or []),
            {
                "thread_id": orig_thread,
                "manager_context_version": session.get("manager_context_version"),
                "agent_id": "codex",
                "agent_model_id": "gpt-6.2-sol",
            },
        ],
    )
    store.update_turn(sid, turn_switch["turn_id"], status="completed")
    store.update_session(sid, active_turn_id=None)
    if sid in runtime.adapters:
        runtime.adapters.pop(sid).close_session()

    ls_state: dict[str, Any] = {
        "online": True,
        "trajectories": {},
        "step_rpc_calls": [],
        "steps_error": False,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = self.path.rsplit("/", 1)[-1]
            if method == "GetCascadeModelConfigData":
                resp: dict[str, Any] = {
                    "clientModelConfigs": [{
                        "modelId": "gemini-4-carbon-high",
                        "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M427"},
                        "quotaInfo": {"remainingFraction": 0.9},
                    }]
                }
            elif method == "GetUserStatus":
                resp = {
                    "userStatus": {
                        "email": "synthetic@invalid.example",
                        "cascadeModelConfigData": {
                            "clientModelConfigs": [{
                                "modelId": "gemini-4-carbon-high",
                                "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M427"},
                                "quotaInfo": {"remainingFraction": 0.9, "resetTime": "2030-01-01T00:00:00Z"},
                            }]
                        },
                    }
                }
            elif method == "GetCascadeTrajectory":
                cid = body["cascadeId"]
                entry = ls_state["trajectories"].get(cid, {"status": "CASCADE_RUN_STATUS_IDLE", "steps": [], "numTotalSteps": 0})
                traj_obj: dict[str, Any] = {"cascadeId": entry.get("cascadeId", cid)}
                if not entry.get("omit_inline_steps"):
                    traj_obj["steps"] = entry.get("steps", [])
                resp = {
                    "trajectory": traj_obj,
                    "status": entry.get("status", "CASCADE_RUN_STATUS_IDLE"),
                    "numTotalSteps": entry.get("numTotalSteps", len(entry.get("steps", []))),
                }
            elif method == "GetCascadeTrajectorySteps":
                cid = body["cascadeId"]
                offset = int(body.get("stepOffset", body.get("startIndex", 0)))
                end = int(body.get("endIndex", offset + 1))
                ls_state["step_rpc_calls"].append({"cascadeId": cid, "offset": offset, "end": end})
                if ls_state["steps_error"]:
                    self.send_response(500)
                    self.end_headers()
                    return
                entry = ls_state["trajectories"].get(cid, {"steps": []})
                all_steps = entry.get("steps", [])
                # Simulate paginated chunks of size 1 with stepIndex metadata.
                slice_end = min(len(all_steps), max(offset + 1, min(end, offset + 1)))
                resp = {
                    "steps": [
                        {
                            **step,
                            "metadata": {
                                **(step.get("metadata") or {}),
                                "sourceTrajectoryStepInfo": {"stepIndex": idx},
                            },
                        }
                        for idx, step in enumerate(all_steps[offset:slice_end], start=offset)
                    ]
                }
            else:
                raise AssertionError(method)
            payload = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("ANTIGRAVITY_LS_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", "synthetic-csrf-token")
    real_discover = agy_mod.discover_antigravity_language_server

    def checked_discover(env=None, *, deadline=None, include_model_config=False):
        if not ls_state["online"]:
            return None
        return real_discover(env=env, deadline=deadline, include_model_config=include_model_config)

    monkeypatch.setattr(agy_mod, "discover_antigravity_language_server", checked_discover)
    monkeypatch.setattr(
        CodexAppServerAdapter,
        "failed_request_observation",
        staticmethod(lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("Codex probe must not run for agy_turn_*"))),
    )

    def fake_start(cls, *, work_dir, objective, resume_thread_id=None, history=None,
                   runtime_profile="restricted", team_plan_context=None, executor_model=None,
                   env=None, idle_timeout_sec=None, hard_timeout_sec=None):
        return cls(
            work_dir=work_dir,
            objective=objective,
            cascade_id=resume_thread_id or "ag-cascade-main",
            ls_url=f"http://127.0.0.1:{server.server_port}",
            csrf_token="synthetic-csrf-token",
            model_id=(executor_model or {}).get("model") or "gemini-4-carbon-high",
            model_placeholder="MODEL_PLACEHOLDER_M427",
            reasoning_effort="high",
            runtime_profile=runtime_profile,
            idle_timeout_sec=idle_timeout_sec,
            hard_timeout_sec=hard_timeout_sec,
        )

    monkeypatch.setattr(AntigravityAdapter, "start", classmethod(fake_start))
    monkeypatch.setattr(AntigravityAdapter, "start_turn", fake_turn)

    def wake():
        cache = getattr(runtime, "_steward_monitor_next_check", None)
        if cache is not None:
            cache.clear()
        return pump_steward_monitors(runtime)

    try:
        yield {
            "sid": sid,
            "actor": actor,
            "reports": reports,
            "owner": binding["binding_id"],
            "store": store,
            "runtime": runtime,
            "transport": transport,
            "capture": capture,
            "clock": clock,
            "ls_state": ls_state,
            "wake": wake,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_steward_antigravity_exhausted_timeout_recovery_advances_at_next_boundary_and_retains_proof(
    steward, tmp_path, monkeypatch, caplog, provider,
):
    import re
    from test_native_steward_private import finish
    from test_native_steward_reports import fake_report_model
    from loopx.capabilities.native_chat.report_failure_proof import REPORT_FAILURE_PROOF
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    from loopx.chat_agent import CodexChatAgentError

    calls: list[dict[str, object]] = []
    ls_holder: dict[str, dict] = {}

    def fake_turn(self, message, event_sink=None, **_kwargs):
        with (self._launch_guard() if self._launch_guard is not None else contextlib.nullcontext()):
            idx = len(calls) + 1
            calls.append({"idx": idx, "message": message})
            if idx <= 2:
                cid = f"ag-isolated-{idx}"
                tid = f"agy_turn_timeout_{idx}"
                ls_holder["state"]["trajectories"][cid] = {
                    "status": "CASCADE_RUN_STATUS_IDLE",
                    "numTotalSteps": 3,
                    "steps": [
                        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"},
                        {
                            "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                            "status": "CORTEX_STEP_STATUS_DONE",
                            "plannerResponse": {"toolCalls": [{"id": "tc-1"}], "response": ""},
                        },
                        {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_DONE"},
                    ],
                }
                if event_sink is not None:
                    event_sink("turn.started", {"threadId": cid, "upstream_turn_id": tid})
                    event_sink("agent.phase", {"method": "native/timeout", "phase": "waiting_model_output"})
                raise CodexChatAgentError("Antigravity produced no output for 300s", error_code="response_timeout")
            assert re.search(r'"editorial_request_path":\s*"([^"\n]+)"', message) is not None
            if event_sink is not None:
                event_sink("turn.started", {"threadId": "ag-isolated-3", "upstream_turn_id": "agy_turn_isolated_3"})
                event_sink("turn.native_result", {"thread_id": "ag-isolated-3", "upstream_turn_id": "agy_turn_isolated_3", "text": "ok"})
            return {"message": fake_report_model({"input": [{"text": message}]})}

    with _antigravity_steward_harness(steward, tmp_path, monkeypatch, provider=provider, fake_turn=fake_turn) as ctx:
        ls_holder["state"] = ctx["ls_state"]
        runtime, reports = ctx["runtime"], ctx["reports"]
        actor, owner = ctx["actor"], ctx["owner"]

        initial = finish(runtime, ctx["wake"]()[0])
        assert initial["response"]["native_loopx"]["result_kind"] == "host_failure"
        _, old = reports.current_window(owner)
        old_folder = attempt_directory(reports.root, old)
        previous_journal = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])
        old_todo_id = initial["loopx_request"]["todo_id"]

        retried = finish(runtime, ctx["wake"]()[0])
        assert retried["loopx_request"]["turn_instance_id"].endswith(".timeout-recovery")
        assert retried["response"]["native_loopx"]["result_kind"] == "host_failure"
        assert ctx["wake"]() == []
        old_artifacts = (old_folder / "source.json", old_folder / "editorial-request.json")
        old_bytes = [p.read_bytes() for p in old_artifacts]

        # Bounded recovery is already exhausted, so the very next cadence boundary advances without wasting another full period.
        ctx["clock"]["now"] += timedelta(days=1)
        with caplog.at_level(logging.WARNING):
            dispatched = ctx["wake"]()
        assert "An original steward report is unavailable" not in caplog.text
        assert len(dispatched) == 1
        done = finish(runtime, dispatched[0])
        assert done["response"]["native_loopx"]["status"] == "committed", done

        _, latest = reports.current_window(owner)
        assert latest["due_at"] == old["next_due_at"]
        assert done["loopx_request"]["report_window_id"] == latest["window_id"]
        assert done["loopx_request"]["todo_id"] != old_todo_id
        superseded = json.loads((reports.root / "goals" / actor["goal_id"] / "periodic_reports" / "superseded-cadence" / f"{old['window_id']}.json").read_text())
        assert superseded == previous_journal
        proof = json.loads((old_folder / REPORT_FAILURE_PROOF).read_text())
        assert proof["adapter_kind"] == "antigravity_ls"
        assert proof["provider_thread_id"] == "ag-isolated-2"
        assert proof["cascade_idle"] is True and proof["no_final_response"] is True
        assert "only_original_user_item" not in proof
        assert [p.read_bytes() for p in old_artifacts] == old_bytes
        assert not (old_folder / "editorial-response.json").exists()
        assert any(row["todo_id"] == old_todo_id and row["status"] == "open" for row in StewardMonitor(runtime).todos(actor))

        latest_folder = attempt_directory(reports.root, latest)
        body = (latest_folder / "report.md").read_text()
        expected_due = old["due_at"][:16].replace("T", " ") + "+00:00"
        assert "缺报区间" in body and expected_due in body and "从" not in body.split("缺报区间", 1)[1].splitlines()[0]
        bundle = json.loads((latest_folder / "generation-bundle.json").read_text())
        html = next(item["content"] for item in bundle["artifacts"] if item["renderer_kind"] == "html")
        assert "缺报区间" in html and expected_due in html
        assert ctx["transport"].steward_reports.reconcile() == 1
        assert reports.health(owner)["publication_verified"]
        assert ctx["wake"]() == [] and len(calls) == 3


def test_steward_antigravity_initial_failure_keeps_next_boundary_and_advances_after_missed_window_with_retained_proof(
    steward, tmp_path, monkeypatch, caplog,
):
    from test_native_steward_private import finish
    from test_native_steward_reports import fake_report_model
    from loopx.capabilities.native_chat.report_failure_proof import REPORT_FAILURE_PROOF
    from loopx.chat_agent import CodexChatAgentError

    calls = 0
    ls_holder: dict[str, dict] = {}

    def fake_turn(self, message, event_sink=None, **_kwargs):
        nonlocal calls
        with (self._launch_guard() if self._launch_guard is not None else contextlib.nullcontext()):
            calls += 1
            if calls == 1:
                ls_holder["state"]["trajectories"]["ag-init-1"] = {
                    "status": "CASCADE_RUN_STATUS_IDLE",
                    "omit_inline_steps": True,
                    "numTotalSteps": 2,
                    "steps": [
                        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"},
                        {
                            "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                            "status": "CORTEX_STEP_STATUS_DONE",
                            "plannerResponse": {"toolCalls": [{"id": "tc-1"}], "response": ""},
                        },
                    ],
                }
                if event_sink is not None:
                    event_sink("turn.started", {"threadId": "ag-init-1", "upstream_turn_id": "agy_turn_fail_1"})
                # Non-timeout host failure: no .timeout-recovery turn is created.
                raise CodexChatAgentError("Synthetic upstream stream disconnect")
            if event_sink is not None:
                event_sink("turn.started", {"threadId": "ag-init-2", "upstream_turn_id": "agy_turn_ok_2"})
                event_sink("turn.native_result", {"thread_id": "ag-init-2", "upstream_turn_id": "agy_turn_ok_2", "text": "ok"})
            return {"message": fake_report_model({"input": [{"text": message}]})}

    with _antigravity_steward_harness(steward, tmp_path, monkeypatch, provider="sqlite", fake_turn=fake_turn) as ctx:
        ls_holder["state"] = ctx["ls_state"]
        runtime, reports = ctx["runtime"], ctx["reports"]
        owner = ctx["owner"]

        initial = finish(runtime, ctx["wake"]()[0])
        assert initial["response"]["native_loopx"]["result_kind"] == "host_failure"
        _, old = reports.current_window(owner)
        old_folder = attempt_directory(reports.root, old)

        # Initial failure (recovery not exhausted) still preserves +1 day boundary.
        ctx["clock"]["now"] += timedelta(days=1)
        assert ctx["wake"]() == []
        assert reports.current_window(owner)[1]["window_id"] == old["window_id"]

        # Verify paginated GetCascadeTrajectorySteps works and persists retained proof.
        host = StewardTurnHost(runtime)
        assert host.report_window_failure_verified(initial, old) is True
        assert any(call["cascadeId"] == "ag-init-1" for call in ctx["ls_state"]["step_rpc_calls"])
        assert (old_folder / REPORT_FAILURE_PROOF).is_file()

        # Simulate LS temporarily unavailable before +2d boundary: un-contradicted retained proof still lets cadence advance cleanly.
        ctx["ls_state"]["online"] = False
        ctx["clock"]["now"] += timedelta(days=1)
        with caplog.at_level(logging.WARNING):
            dispatched = ctx["wake"]()
        assert "An original steward report is unavailable" not in caplog.text
        assert len(dispatched) == 1
        done = finish(runtime, dispatched[0])
        assert done["response"]["native_loopx"]["status"] == "committed", done
        _, latest = reports.current_window(owner)
        assert latest["due_at"] == (datetime.fromisoformat(old["due_at"].replace("Z", "+00:00")) + timedelta(days=2)).isoformat().replace("+00:00", "Z")
        body = (attempt_directory(reports.root, latest) / "report.md").read_text()
        assert "缺报区间" in body and "从" in body and "到" in body


def test_steward_antigravity_report_failure_verification_fails_closed_on_running_contradicted_or_incomplete_cascade(
    steward, tmp_path, monkeypatch,
):
    from test_native_steward_private import finish
    from loopx.capabilities.native_chat.report_failure_proof import (
        CONTRADICTED_REPORT_FAILURES,
        REPORT_FAILURE_PROOF,
    )
    from loopx.chat_agent import CodexChatAgentError

    ls_holder: dict[str, dict] = {}

    def fake_turn(self, message, event_sink=None, **_kwargs):
        with (self._launch_guard() if self._launch_guard is not None else contextlib.nullcontext()):
            ls_holder["state"]["trajectories"]["ag-guard-1"] = {
                "status": "CASCADE_RUN_STATUS_IDLE",
                "omit_inline_steps": True,
                "numTotalSteps": 4,
                "steps": [],
            }
            if event_sink is not None:
                event_sink("turn.started", {"threadId": "ag-guard-1", "upstream_turn_id": "agy_turn_guard_1"})
            raise CodexChatAgentError("Synthetic upstream disconnect")

    with _antigravity_steward_harness(steward, tmp_path, monkeypatch, provider="sqlite", fake_turn=fake_turn) as ctx:
        ls_holder["state"] = ctx["ls_state"]
        runtime, reports = ctx["runtime"], ctx["reports"]
        actor, owner = ctx["actor"], ctx["owner"]
        host = StewardTurnHost(runtime)

        initial = finish(runtime, ctx["wake"]()[0])
        _, old = reports.current_window(owner)
        old_folder = attempt_directory(reports.root, old)
        contradiction_ledger = reports.root / "goals" / actor["goal_id"] / "periodic_reports" / CONTRADICTED_REPORT_FAILURES

        # 1. Incomplete step coverage fails closed without persisting contradiction.
        ctx["ls_state"]["steps_error"] = True
        assert host.report_window_failure_verified(initial, old) is False
        assert not (old_folder / REPORT_FAILURE_PROOF).exists()
        assert not contradiction_ledger.exists()
        ctx["ls_state"]["steps_error"] = False

        # 2. Running cascade status or running step holds safely without writing durable contradiction.
        ctx["ls_state"]["trajectories"]["ag-guard-1"] = {
            "status": "CASCADE_RUN_STATUS_RUNNING",
            "numTotalSteps": 1,
            "steps": [{"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"}],
        }
        assert host.report_window_failure_verified(initial, old) is False
        ctx["ls_state"]["trajectories"]["ag-guard-1"] = {
            "status": "CASCADE_RUN_STATUS_IDLE",
            "numTotalSteps": 2,
            "steps": [
                {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"},
                {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_RUNNING"},
            ],
        }
        assert host.report_window_failure_verified(initial, old) is False
        assert not contradiction_ledger.exists()

        # 3. Idle with no final text retains proof; later explicit final planner response durably contradicts and holds forever.
        ctx["ls_state"]["trajectories"]["ag-guard-1"] = {
            "status": "CASCADE_RUN_STATUS_IDLE",
            "numTotalSteps": 2,
            "steps": [
                {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"},
                {
                    "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "plannerResponse": {"toolCalls": [{"id": "tc-1"}], "response": ""},
                },
            ],
        }
        assert host.report_window_failure_verified(initial, old) is True
        assert (old_folder / REPORT_FAILURE_PROOF).is_file()

        ctx["clock"]["now"] += timedelta(days=3)
        ctx["ls_state"]["trajectories"]["ag-guard-1"] = {
            "status": "CASCADE_RUN_STATUS_IDLE",
            "numTotalSteps": 2,
            "steps": [
                {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"},
                {
                    "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "plannerResponse": {"response": "Finished full report draft."},
                },
            ],
        }
        assert ctx["wake"]() == []
        assert contradiction_ledger.is_file()
        assert json.loads((old_folder / REPORT_FAILURE_PROOF).read_text())["contradicted"] is True
        assert reports.current_window(owner)[1]["window_id"] == old["window_id"]

        # Even if trajectory later lacks text or LS goes offline, the durable contradiction keeps holding.
        ctx["ls_state"]["trajectories"]["ag-guard-1"]["steps"][1]["plannerResponse"]["response"] = ""
        assert host.report_window_failure_verified(initial, old) is False
        ctx["ls_state"]["online"] = False
        assert ctx["wake"]() == []
        assert reports.current_window(owner)[1]["window_id"] == old["window_id"]


def test_cadence_journal_missed_bounds_preserves_exhausted_single_and_chained_windows(tmp_path):
    from loopx.capabilities.periodic_report.cadence_journal import admit_cadence_window, missed_cadence_bounds

    schedule = {
        "schema_version": "periodic_report_schedule_v0",
        "schedule_id": "daily-report",
        "timezone": "Asia/Shanghai",
        "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        "start_date": "2001-01-01",
    }
    subscription = {
        "enabled": True,
        "profile_preset": "portfolio-progress",
        "schedule": schedule,
        "effective_revision": "sha256:" + "2" * 64,
    }

    def admit(day: int, *, allow_exhausted: bool = False):
        return admit_cadence_window(
            runtime_root=tmp_path,
            goal_id="example",
            agent_id="reporter",
            subscription=subscription,
            now=datetime(2026, 1, day, 3, tzinfo=timezone.utc),
            can_replace_failed_window=lambda _: True,
            **({"can_replace_exhausted_failed_window": lambda _: True} if allow_exhausted else {}),
        )

    day1 = admit(1)["window"]
    attempt_directory(tmp_path, day1).mkdir(parents=True)

    # Without exhausted recovery checker, +1 day stays pending on day1.
    held = admit(2, allow_exhausted=False)
    assert held["status"] == "pending" and held["mutated"] is False and held["window"] == day1

    # With exhausted recovery checker, +1 day advances to day2 and records day1 in missed bounds.
    day2 = admit(2, allow_exhausted=True)["window"]
    assert day2["due_at"] == "2026-01-02T01:00:00Z"
    assert missed_cadence_bounds(runtime_root=tmp_path, window=day2) == {
        "first_due_at": "2026-01-01T01:00:00Z",
        "last_due_at": "2026-01-01T01:00:00Z",
    }

    # Chaining across a multi-day gap still preserves the first missed deadline (day1).
    attempt_directory(tmp_path, day2).mkdir(parents=True)
    day4 = admit(4, allow_exhausted=False)["window"]
    assert day4["due_at"] == "2026-01-04T01:00:00Z"
    assert missed_cadence_bounds(runtime_root=tmp_path, window=day4) == {
        "first_due_at": "2026-01-01T01:00:00Z",
        "last_due_at": "2026-01-03T01:00:00Z",
    }


def test_steward_antigravity_report_prompt_uses_raw_prompt_and_omits_unfrozen_manager_context(
    steward, tmp_path, monkeypatch,
):
    from copy import deepcopy
    from test_native_steward_private import finish
    from test_native_steward_reports import fake_report_model
    from loopx.capabilities.periodic_report.private_generation import report_turn_prompt_authority

    # Verify helper projects without mutating the caller's authority dictionary.
    sample_authority = {
        "primary_action": "author report",
        "vision_continuation_audit": {"required": True},
        "_model_input": {
            "route": "agent_turn",
            "detail_ref": {"captured_decision": "/tmp/cap.json"},
            "task_context": {
                "goal_id": "g1",
                "selected_todo": {"todo_id": "t1"},
                "execution_profile": {"kind": "standard"},
                "agent_todo_summary": {"huge": list(range(10))},
                "interaction_contract": {"cli_channel": {}},
                "heartbeat_recommendation": {"next": "claim"},
                "blocked_priority_fallback": {"items": []},
                "vision_continuation_audit": {"required": True},
            },
        },
    }
    snapshot_before = deepcopy(sample_authority)
    projected = report_turn_prompt_authority(sample_authority)
    assert sample_authority == snapshot_before
    assert "vision_continuation_audit" not in projected
    assert "detail_ref" not in projected["_model_input"]
    assert set(projected["_model_input"]["task_context"]) == {
        "goal_id", "selected_todo", "execution_profile",
    }

    seen: list[dict[str, object]] = []

    def fake_turn(self, message, event_sink=None, **kwargs):
        with (self._launch_guard() if self._launch_guard is not None else contextlib.nullcontext()):
            seen.append({
                "isolated_cascade": kwargs.get("isolated_cascade"),
                "raw_prompt": kwargs.get("raw_prompt"),
                "message": message,
            })
            if event_sink is not None:
                event_sink("turn.started", {"threadId": "ag-isolated-1", "upstream_turn_id": "agy_turn_clean_1"})
                event_sink("turn.native_result", {
                    "thread_id": "ag-isolated-1",
                    "upstream_turn_id": "agy_turn_clean_1",
                    "text": "clean prompt ok",
                })
            return {"message": fake_report_model({"input": [{"text": message}]})}

    with _antigravity_steward_harness(steward, tmp_path, monkeypatch, provider="sqlite", fake_turn=fake_turn) as ctx:
        done = finish(ctx["runtime"], ctx["wake"]()[0])
        assert done["status"] == "completed", done
        assert done["response"]["native_loopx"].get("status") == "committed", done
        assert ctx["transport"].steward_reports.reconcile() == 1
        assert ctx["reports"].health(ctx["owner"])["publication_verified"] is True

    assert len(seen) == 1
    assert seen[0]["isolated_cascade"] is True
    assert seen[0]["raw_prompt"] is True
    prompt = str(seen[0]["message"])
    assert "Original conversation evidence (data):" not in prompt
    assert "Turn request:" in prompt
    assert '"editorial_request_path"' in prompt and '"source_path"' in prompt and '"REPORTING"' in prompt
    assert "read them directly instead of invoking periodic-report CLI commands" in prompt
    turn_req = json.JSONDecoder().raw_decode(prompt.split("Turn request:", 1)[1].lstrip())[0]
    assert "detail_ref" not in turn_req
    task_ctx = turn_req.get("task_context") or {}
    assert "selected_todo" in task_ctx
    for noisy_key in (
        "agent_todo_summary",
        "interaction_contract",
        "heartbeat_recommendation",
        "blocked_priority_fallback",
        "vision_continuation_audit",
    ):
        assert noisy_key not in task_ctx


def test_governance_turn_prompt_authority_compacts_without_mutating_and_keeps_replan_packet(
    steward, tmp_path, monkeypatch,
):
    from copy import deepcopy
    from loopx.capabilities.native_chat.steward_monitor import governance_turn_prompt_authority
    from loopx.configure_goal import configure_goal
    from test_native_steward_private import finish
    from test_native_steward_reports import fake_report_model

    sample_authority = {
        "primary_action": "settle autonomous replan",
        "vision_continuation_audit": {"required": True},
        "_model_input": {
            "route": "agent_turn",
            "detail_ref": {
                "captured_decision": "/tmp/cap.json",
                "replan_action_packet": {"obligation_id": "replan-1", "required": True},
            },
            "task_context": {
                "goal_id": "g1",
                "agent_identity": {"agent_id": "a1"},
                "execution_profile": {"kind": "standard"},
                "task_scope": "execution",
                "mode": "autonomous",
                "state": "active",
                "selected_todo": {"todo_id": "t1"},
                "agent_todo_summary": {"huge": list(range(10))},
                "interaction_contract": {"cli_channel": {}},
                "collaboration_inbox": {"messages": []},
                "vision_continuation_audit": {"required": True},
            },
        },
    }
    snapshot_before = deepcopy(sample_authority)
    projected = governance_turn_prompt_authority(sample_authority)
    assert sample_authority == snapshot_before
    assert "vision_continuation_audit" not in projected
    assert projected["_model_input"]["detail_ref"]["replan_action_packet"] == {"obligation_id": "replan-1", "required": True}
    assert set(projected["_model_input"]["task_context"]) == {
        "goal_id", "agent_identity", "execution_profile", "task_scope", "mode", "state",
    }

    seen_prompts: list[str] = []

    def fake_turn(self, message, event_sink=None, **kwargs):
        with (self._launch_guard() if self._launch_guard is not None else contextlib.nullcontext()):
            prompt = str(message)
            seen_prompts.append(prompt)
            if event_sink is not None:
                idx = len(seen_prompts)
                event_sink("turn.started", {"threadId": f"ag-gov-{idx}", "upstream_turn_id": f"agy_turn_gov_{idx}"})
                event_sink("turn.native_result", {
                    "thread_id": f"ag-gov-{idx}",
                    "upstream_turn_id": f"agy_turn_gov_{idx}",
                    "text": "ok",
                })
            turn_req = json.JSONDecoder().raw_decode(prompt.split("Turn request:", 1)[1].lstrip())[0]
            if turn_req.get("detail_ref", {}).get("replan_action_packet"):
                gov_ctx = json.JSONDecoder().raw_decode(
                    prompt.split("Steward governance replan context (data):", 1)[1].lstrip()
                )[0]
                stride = gov_ctx["writeback"]["stride_control"]
                vision = {
                    "schema_version": "goal_vision_replan_contract_v0",
                    "state": "active",
                    "vision_patch": {
                        "vision_summary": "Maintain the original monitoring and reporting work.",
                        "acceptance_summary": "Validate each report and private delivery cleanly.",
                    },
                    "path_delta": {
                        "outcome": "replan",
                        "prior_assumption": "Initial baseline absent.",
                        "observed_reality": "First report completed; record baseline.",
                        "retained": ["Same subscriptions and report window."],
                        "changed": ["Persisted evidence-linked vision baseline."],
                        "evidence_refs": ["fixture:validated-first-report"],
                    },
                    **(
                        {"stride_decision": stride["minimal_example"]}
                        if stride.get("required_on_periodic_review")
                        else {}
                    ),
                }
                return {"message": json.dumps({
                    "result_kind": "replan_required",
                    "classification": "original_report_native_replan",
                    "summary": "Recorded evidence-linked baseline for reporting.",
                    "next_action": "Resume retained daily report window.",
                    "delivery_batch_scale": "implementation",
                    "delivery_outcome": "outcome_progress",
                    "work_evidence": ["Recorded validated baseline."],
                    "validation_evidence": ["Checked semantic replan writeback preview."],
                    "path_delta_mode": "material_replan",
                    "agent_vision_json": json.dumps(vision),
                })}
            return {"message": fake_report_model({"input": [{"text": prompt}]})}

    with _antigravity_steward_harness(steward, tmp_path, monkeypatch, provider="sqlite", fake_turn=fake_turn) as ctx:
        assert configure_goal(
            registry_path=ctx["runtime"].registry_path,
            goal_id=ctx["actor"]["goal_id"],
            execution_replan_after_turns=1,
            execute=True,
        )["ok"]
        first = finish(ctx["runtime"], ctx["wake"]()[0])
        assert first["response"]["native_loopx"]["status"] == "committed", first
        assert ctx["transport"].steward_reports.reconcile() == 1
        assert ctx["reports"].health(ctx["owner"])["publication_verified"] is True
        ctx["clock"]["now"] += timedelta(days=1)
        rejected = finish(ctx["runtime"], ctx["wake"]()[0])
        assert rejected["response"]["native_loopx"]["error_code"] == "turn_todo_selection_deferred", rejected
        resumed = finish(ctx["runtime"], ctx["wake"]()[0])
        assert resumed["response"]["native_loopx"]["status"] == "committed", resumed

    assert len(seen_prompts) == 3
    gov_prompt = seen_prompts[1]
    assert "Original conversation evidence (data):" not in gov_prompt
    assert "Steward governance replan context (data):" in gov_prompt
    assert "All review, writeback, stride_control, current_agent_vision, and open_agent_todos facts are already provided" in gov_prompt
    gov_turn_req = json.JSONDecoder().raw_decode(gov_prompt.split("Turn request:", 1)[1].lstrip())[0]
    assert gov_turn_req.get("detail_ref", {}).get("replan_action_packet")
    gov_task_ctx = gov_turn_req.get("task_context") or {}
    for noisy_key in ("agent_todo_summary", "interaction_contract", "selected_todo", "vision_continuation_audit"):
        assert noisy_key not in gov_task_ctx


def test_validate_replan_surfaces_replan_writeback_rejected_reason_code(
    steward, tmp_path, monkeypatch,
):
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    from loopx.configure_goal import configure_goal
    from test_native_steward_monitor import edit_local_watch, local_watch_row
    from test_native_steward_private import finish
    from test_native_steward_reports import fake_report_model

    holder: dict[str, object] = {}

    def fake_turn(self, message, event_sink=None, **kwargs):
        with (self._launch_guard() if self._launch_guard is not None else contextlib.nullcontext()):
            prompt = str(message)
            if event_sink is not None:
                event_sink("turn.started", {"threadId": "ag-gov-bad", "upstream_turn_id": "agy_turn_gov_bad"})
                event_sink("turn.native_result", {
                    "thread_id": "ag-gov-bad",
                    "upstream_turn_id": "agy_turn_gov_bad",
                    "text": "ok",
                })
            turn_req = json.JSONDecoder().raw_decode(prompt.split("Turn request:", 1)[1].lstrip())[0]
            if turn_req.get("detail_ref", {}).get("replan_action_packet"):
                gov_ctx = json.JSONDecoder().raw_decode(
                    prompt.split("Steward governance replan context (data):", 1)[1].lstrip()
                )[0]
                stride = dict(gov_ctx["writeback"]["stride_control"]["minimal_example"])
                # Submit the valid delivered stride_decision, while mutating runs/ mid-flight
                # via a concurrent material monitor poll so dry-run validation hits
                # ReplanWritebackRejected(reason_code="stride_review_basis_mismatch").
                ctx_obj = holder["ctx"]
                mon_todo_id = holder["mon_todo_id"]
                mon = StewardMonitor(ctx_obj["runtime"])
                edit_local_watch(
                    ctx_obj["runtime"],
                    ctx_obj["actor"],
                    mon_todo_id,
                    "--next-due-at",
                    (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
                )
                cur = local_watch_row(ctx_obj["runtime"], ctx_obj["actor"], mon_todo_id)
                lease = mon._acquire(ctx_obj["actor"], cur, "concurrent-poll-1")["lease"]
                try:
                    mon._native_poll([
                        "quota",
                        "monitor-poll",
                        "--goal-id",
                        ctx_obj["actor"]["goal_id"],
                        "--agent-id",
                        ctx_obj["actor"]["agent_id"],
                        "--todo-id",
                        cur["todo_id"],
                        "--turn-instance-id",
                        "concurrent-poll-1",
                        "--available-capability",
                        "filesystem",
                        "--available-capability",
                        "external_evidence_poll",
                        "--runtime-profile",
                        "codex_app_server",
                        "--target-key",
                        cur["target_key"],
                        "--result-hash",
                        "sha256:" + "9" * 64,
                        "--task-lease-idempotency-key",
                        "concurrent-poll-1",
                        "--task-lease-expected-version",
                        str(lease["version"]),
                        "--execute",
                        "--material-change",
                    ])
                finally:
                    mon._release(ctx_obj["actor"], cur, lease)
                vision = {
                    "schema_version": "goal_vision_replan_contract_v0",
                    "state": "active",
                    "vision_patch": {
                        "vision_summary": "Maintain reporting.",
                        "acceptance_summary": "Validate each report.",
                    },
                    "path_delta": {
                        "outcome": "replan",
                        "prior_assumption": "Initial baseline absent.",
                        "observed_reality": "First report completed.",
                        "retained": ["Same report window."],
                        "changed": ["Persisted baseline."],
                        "evidence_refs": ["fixture:validated-first-report"],
                    },
                    "stride_decision": stride,
                }
                return {"message": json.dumps({
                    "result_kind": "replan_required",
                    "classification": "original_report_native_replan",
                    "summary": "Attempted replan after concurrent monitor poll shifted runs basis.",
                    "next_action": "Retry with fresh context.",
                    "delivery_batch_scale": "implementation",
                    "delivery_outcome": "outcome_progress",
                    "work_evidence": ["Recorded baseline candidate."],
                    "validation_evidence": ["Dry-run checks basis match."],
                    "path_delta_mode": "material_replan",
                    "agent_vision_json": json.dumps(vision),
                })}
            return {"message": fake_report_model({"input": [{"text": prompt}]})}

    with _antigravity_steward_harness(steward, tmp_path, monkeypatch, provider="sqlite", fake_turn=fake_turn) as ctx:
        holder["ctx"] = ctx
        monitor = StewardMonitor(ctx["runtime"])
        enable_plan = monitor.configure(ctx["owner"], operation="enable")["plan"]
        mon_todo = monitor.configure(
            ctx["owner"],
            operation="enable",
            expected_plan_id=enable_plan["plan_id"],
            execute=True,
        )["monitor"]
        holder["mon_todo_id"] = mon_todo["todo_id"]
        edit_local_watch(
            ctx["runtime"],
            ctx["actor"],
            mon_todo["todo_id"],
            "--next-due-at",
            (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        )
        assert configure_goal(
            registry_path=ctx["runtime"].registry_path,
            goal_id=ctx["actor"]["goal_id"],
            execution_replan_after_turns=1,
            execute=True,
        )["ok"]
        first = finish(ctx["runtime"], ctx["wake"]()[0])
        assert first["response"]["native_loopx"]["status"] == "committed", first
        assert ctx["transport"].steward_reports.reconcile() == 1
        assert ctx["reports"].health(ctx["owner"])["publication_verified"] is True
        ctx["clock"]["now"] += timedelta(days=1)
        rejected = finish(ctx["runtime"], ctx["wake"]()[0])
        assert rejected["response"]["native_loopx"]["error_code"] == "turn_todo_selection_deferred", rejected
        failed_replan = finish(ctx["runtime"], ctx["wake"]()[0])
        replans = [
            row["payload"]
            for row in ctx["store"].events_after(ctx["sid"], failed_replan["turn_id"], None)
            if row["kind"] == "native_loopx.replan_result"
        ]
        assert len(replans) == 1
        validation = replans[0]["validation"]
        assert validation is not None, replans[0]
        assert validation["status"] == "failed"
        assert validation["recovery_kind"] == "replan_required"
        assert "stride_review_basis_mismatch" in validation["summary"]


def test_selection_recovery_admission_resume_recovers_after_task_postcondition_replan_failure(
    steward, tmp_path, monkeypatch, caplog,
):
    from loopx.capabilities.native_chat.steward_monitor import StewardMonitor
    from loopx.configure_goal import configure_goal
    from test_native_steward_reports import _REPORT_MODEL_SOURCE

    _, runtime, _, transport, binding, _, _ = steward
    point = datetime.now(timezone.utc).replace(hour=9, minute=1, second=0, microsecond=0) - timedelta(days=3)

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

    rejected_vision = {
        "schema_version": "goal_vision_replan_contract_v0",
        "state": "active",
        "vision_patch": {
            "vision_summary": "Keep reporting.",
            "acceptance_summary": "Validate full report.",
        },
        "path_delta": {
            "outcome": "replan",
            "prior_assumption": "Old assumption",
            "observed_reality": "Reality",
            "retained": ["Same report route"],
            "changed": ["Updated baseline"],
            "evidence_refs": ["nonexistent_evidence_ref_triggers_postcondition_reject"],
        },
    }
    postcondition_fail = {
        "result_kind": "replan_required",
        "classification": "replan_postcondition_rejected",
        "delivery_batch_scale": "implementation",
        "delivery_outcome": "outcome_progress",
        "work_evidence": ["Observed baseline candidate"],
        "validation_evidence": ["Preview checks evidence refs"],
        "path_delta_mode": "material_replan",
        "agent_vision_json": json.dumps(rejected_vision),
        "summary": "Syntactically valid replan candidate rejected by semantic preview.",
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
        + "        return " + repr(json.dumps(postcondition_fail)) + "\n"
        + "    return report_only(params)\n"
    )
    runtime._steward_monitor_next_check.clear()
    probe = finish(runtime, pump_steward_monitors(runtime)[0])
    replan_events = [
        row["payload"]
        for row in runtime.store.events_after(sid, probe["turn_id"], None)
        if row["kind"] == "native_loopx.replan_result"
    ]
    assert len(replan_events) == 1
    assert replan_events[0]["status"] == "failed"
    assert replan_events[0]["result_kind"] == "validation_failed"
    assert replan_events[0]["validation_stage"] == "task_postcondition"

    # While replan obligation remains active, subsequent pump ticks must stay quiet without warning spam.
    runtime._steward_monitor_next_check.clear()
    with caplog.at_level(logging.WARNING):
        assert pump_steward_monitors(runtime) == []
    assert "An original steward report is unavailable" not in caplog.text

    # Once replan clears, .admission-resume recovers the retained report window and advances cleanly.
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
    assert reports.health(owner)["publication_verified"] is True
    task = next(
        row for row in StewardMonitor(runtime).todos(actor)
        if row["todo_id"] == done["loopx_request"]["todo_id"]
    )
    assert task["status"] == "done"

    # Next day's morning report window is no longer blocked behind the recovered window.
    point += timedelta(days=1)
    runtime._steward_monitor_next_check.clear()
    next_day = pump_steward_monitors(runtime)
    assert len(next_day) == 1
    next_done = finish(runtime, next_day[0])
    assert next_done["response"]["native_loopx"]["status"] == "committed", next_done
    assert transport.steward_reports.reconcile() == 1


def test_advancement_followup_recovers_after_failed_inline_replan_and_pre_dispatch_resume_failed():
    from loopx.capabilities.native_chat.steward_monitor_turns import (
        ADVANCEMENT_PRECOMMIT_RETRY_COOLDOWN_SECONDS,
        ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX,
        advancement_turn_id,
        is_exhausted_zero_write_followup_todo,
        resolve_advancement_turn_id,
    )

    actor = {
        "schema_version": "loopx_steward_managed_actor_v0",
        "goal_id": "steward-agent-e0e2226022a24adf9209cbe7",
        "agent_id": "steward",
        "session_id": "19fc2a50410c4ee387d14d40053cae0d",
    }
    todo = {
        "todo_id": "todo_followup_1",
        "status": "open",
        "task_class": "advancement_task",
        "target_key": "steward-monitor-followup:todo_mon_1",
        "updated_at": "2026-11-26T02:00:00Z",
        "note": "",
    }
    base = advancement_turn_id(todo)
    base_client = native_steward_client_id(actor, base)
    retry_client = native_steward_client_id(actor, base + ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX)
    completed_iso = "2026-11-26T02:03:34.505828Z"
    completed_epoch = datetime.fromisoformat(completed_iso.replace("Z", "+00:00")).timestamp()

    class FakeStore:
        def __init__(self, by_client: dict[str, dict[str, object]]) -> None:
            self.by_client = by_client

        def turn_for_client(self, session_id: str, client_turn_id: str):
            assert session_id == actor["session_id"]
            return self.by_client.get(client_turn_id)

    replan_deferred_with_host_turn = {
        "status": "completed",
        "completed_at": completed_iso,
        "response": {
            "schema_version": "loopx_chat_agent_response_v0",
            "message": "本轮任务尚未完成；执行状态已保留。",
            "proposals": [],
            "gate": None,
            "native_loopx": {
                "ok": False,
                "error_code": "turn_todo_selection_deferred",
                "selection_rejection": {
                    "state": "deferred",
                    "reason_code": "autonomous_replan",
                    "recovery_action": "reenter_guard_without_selection",
                },
                "effects": {
                    "host_invoked": True,
                    "state_written": False,
                    "quota_spent": False,
                    "scheduler_acknowledged": False,
                },
            },
        },
    }
    store = FakeStore({base_client: replan_deferred_with_host_turn})
    assert resolve_advancement_turn_id(store, actor, todo, now=completed_epoch + 10) is None
    assert is_exhausted_zero_write_followup_todo(store, actor, todo, now=completed_epoch + 10) is False

    after_cooldown = completed_epoch + ADVANCEMENT_PRECOMMIT_RETRY_COOLDOWN_SECONDS + 1
    assert resolve_advancement_turn_id(store, actor, todo, now=after_cooldown) == (
        base + ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX
    )
    assert is_exhausted_zero_write_followup_todo(store, actor, todo, now=after_cooldown) is False

    # If the precommit retry also fails with zero writes, the follow-up is marked exhausted so monitor polls can rearm it.
    pre_dispatch_failed_turn = {
        "status": "failed",
        "origin": "native_loopx",
        "loopx_execution": True,
        "error_code": "resume_failed",
        "response": None,
        "upstream_turn_id": None,
        "started_at": None,
        "first_event_at": None,
        "delta_count": 0,
        "completed_at": completed_iso,
    }
    exhausted_store = FakeStore({
        base_client: replan_deferred_with_host_turn,
        retry_client: pre_dispatch_failed_turn,
    })
    assert resolve_advancement_turn_id(exhausted_store, actor, todo, now=after_cooldown) is None
    assert is_exhausted_zero_write_followup_todo(exhausted_store, actor, todo, now=after_cooldown) is True

    # Direct pre-dispatch outer Chat failure on base turn also schedules .precommit-retry after cooldown,
    # while any started/streaming outer failure fails closed.
    outer_only_store = FakeStore({base_client: pre_dispatch_failed_turn})
    assert resolve_advancement_turn_id(outer_only_store, actor, todo, now=after_cooldown) == (
        base + ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX
    )
    started_outer_failure = {**pre_dispatch_failed_turn, "upstream_turn_id": "started-turn-1"}
    worker_exited_started = {
        **pre_dispatch_failed_turn,
        "error_code": "execution_worker_exited",
        "started_at": completed_iso,
    }
    unmapped_worker_exit = {**pre_dispatch_failed_turn, "error_code": "execution_worker_exited_before_dispatch"}
    for non_retryable in (started_outer_failure, worker_exited_started, unmapped_worker_exit):
        unsafe_store = FakeStore({base_client: non_retryable})
        assert resolve_advancement_turn_id(unsafe_store, actor, todo, now=after_cooldown) is None
        assert is_exhausted_zero_write_followup_todo(unsafe_store, actor, todo, now=after_cooldown) is False
