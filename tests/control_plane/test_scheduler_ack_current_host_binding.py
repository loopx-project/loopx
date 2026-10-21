from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path

import pytest

from examples.control_plane.quota_plan_fixtures import SCOPED_AGENT_ID, write_cli_fixture
from loopx.cli import build_parser
from loopx.cli_commands import quota as quota_command
from loopx.cli_commands import quota_scheduler_followup
from loopx.cli_commands.quota_context import validate_quota_command_context_request
from loopx.control_plane.testing.canary_harness import run_json_cli, run_json_cli_result
from loopx.control_plane.scheduler.state import (
    APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
    build_scheduler_state,
    load_scheduler_state,
    scheduler_state_path,
    write_scheduler_state,
)
from loopx.control_plane.scheduler.execution_context import (
    SchedulerRuntimeProfile,
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.status import AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK


GOAL_ID = "needs-operator"


def _write_heartbeat_rrule(codex_home: Path, rrule: str) -> None:
    automation_path = codex_home / "automations" / "fixture" / "automation.toml"
    automation_path.parent.mkdir(parents=True, exist_ok=True)
    automation_path.write_text(
        "\n".join(
            [
                "version = 1",
                'id = "fixture"',
                'kind = "heartbeat"',
                'name = "Scheduler ACK fixture"',
                (
                    'prompt = "Advance `needs-operator` from active state. '
                    'Agent: `codex-side-bypass`."'
                ),
                'status = "ACTIVE"',
                f'rrule = "{rrule}"',
                'target_thread_id = "fixture-thread"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _quota(
    registry_path: Path,
    runtime_root: Path,
    project: Path,
) -> dict:
    _returncode, payload = run_json_cli_result(
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        SCOPED_AGENT_ID,
        "--codex-app",
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=project,
    )
    return payload


def _trae_quota(
    registry_path: Path,
    runtime_root: Path,
    project: Path,
    *,
    current_rrule: str | None = None,
) -> dict:
    rrule_args = (
        ["--app-automation-current-rrule", current_rrule]
        if current_rrule
        else []
    )
    _returncode, payload = run_json_cli_result(
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        SCOPED_AGENT_ID,
        "--trae_app",
        *rrule_args,
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=project,
    )
    return payload


def test_trae_app_real_cli_ack_and_failure_keep_independent_identity(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, project = write_cli_fixture(
        tmp_path / "fixture",
        scoped_agents=True,
    )

    first = _trae_quota(registry_path, runtime_root, project)
    assert "codex_app" not in first["scheduler_hint"]
    app = first["scheduler_hint"]["app_automation"]
    assert app["host_surface"] == "trae_app"
    assert app["stateful_backoff"]["state_key"] == (
        APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY
    )
    assert "fallback_hint" not in app
    envelope = build_turn_envelope(
        first,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.TRAE_APP
        ),
    )
    assert "codex_app" not in envelope["scheduler"]
    compact_app = envelope["scheduler"]["app_automation"]
    assert compact_app["host_surface"] == "trae_app"
    assert compact_app["ack_cli_args"] == app["ack_hint"]["cli_args"]

    ack = run_json_cli(
        *app["ack_hint"]["cli_args"],
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=project,
    )
    assert ack["scheduler_state_mutated"] is True
    assert ack["surface"] == "trae_app"
    assert ack["state_key"] == APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY
    assert load_scheduler_state(
        runtime_root,
        goal_id=GOAL_ID,
        agent_id=SCOPED_AGENT_ID,
        surface="trae_app",
        state_key=APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
    ) is not None
    assert load_scheduler_state(
        runtime_root,
        goal_id=GOAL_ID,
        agent_id=SCOPED_AGENT_ID,
        surface="codex_app",
        state_key=APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
    ) is None

    failure_registry, failure_runtime, failure_project = write_cli_fixture(
        tmp_path / "failure-fixture",
        scoped_agents=True,
    )
    failure_app = _trae_quota(
        failure_registry,
        failure_runtime,
        failure_project,
        current_rrule="FREQ=MINUTELY;INTERVAL=5",
    )["scheduler_hint"]["app_automation"]
    failure_args = failure_app["failure_hint"]["cli_args"]
    assert "--app-automation-current-rrule" in failure_args
    assert "--codex-app-current-rrule" not in failure_args
    failure = run_json_cli(
        *failure_args,
        registry_path=failure_registry,
        runtime_root=failure_runtime,
        cwd=failure_project,
    )
    assert failure["scheduler_state_mutated"] is True
    assert failure["surface"] == "trae_app"
    assert failure["state_key"] == APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY


def test_trae_app_followup_defaults_to_its_own_surface_and_state_key() -> None:
    args = build_parser().parse_args(
        [
            "quota",
            "scheduler-fail-current",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            SCOPED_AGENT_ID,
            "--trae_app",
            "--failed-rrule",
            "FREQ=MINUTELY;INTERVAL=3",
            "--execute",
        ]
    )

    validate_quota_command_context_request(args)

    assert args.surface == "trae_app"
    assert args.state_key == APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY


def test_trae_app_followup_rejects_codex_rrule_alias() -> None:
    args = build_parser().parse_args(
        [
            "quota",
            "scheduler-fail-current",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            SCOPED_AGENT_ID,
            "--trae_app",
            "--failed-rrule",
            "FREQ=MINUTELY;INTERVAL=3",
            "--codex-app-current-rrule",
            "FREQ=MINUTELY;INTERVAL=5",
            "--execute",
        ]
    )

    with pytest.raises(ValueError, match="Trae App uses --app-automation"):
        validate_quota_command_context_request(args)


def test_scheduler_ack_current_replays_host_binding_after_update(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry_path, runtime_root, project = write_cli_fixture(
        tmp_path / "fixture",
        scoped_agents=True,
    )
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_THREAD_ID", "fixture-thread")
    _write_heartbeat_rrule(codex_home, "FREQ=MINUTELY;INTERVAL=3")

    first = _quota(registry_path, runtime_root, project)
    app = first["scheduler_hint"]["codex_app"]
    target_rrule = app["recommended_rrule"]
    ack_hint = app["ack_hint"]
    assert app["stateful_backoff"]["apply_needed"] is True
    assert ack_hint["after"] == "automation_update_rrule_success"
    assert ack_hint["args"]["host_match_observed"] is True

    # Simulate a successful host update before executing the original ACK hint.
    _write_heartbeat_rrule(codex_home, target_rrule)
    ack = run_json_cli(
        *ack_hint["cli_args"],
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=project,
    )
    assert ack["scheduler_state_mutated"] is True
    assert ack["already_applied"] is False

    settled = _quota(registry_path, runtime_root, project)
    settled_app = settled["scheduler_hint"]["codex_app"]
    assert settled_app["stateful_backoff"]["apply_needed"] is False
    assert settled_app["stateful_backoff"]["ack_needed"] is False
    assert settled_app["host_action"] == "none"


@pytest.mark.parametrize("host_missing", [False, True])
def test_scheduler_ack_current_rejects_claim_before_matching_host_readback(
    tmp_path: Path, monkeypatch, host_missing: bool,
) -> None:
    registry_path, runtime_root, project = write_cli_fixture(
        tmp_path / "fixture", scoped_agents=True,
    )
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_THREAD_ID", "fixture-thread")
    _write_heartbeat_rrule(codex_home, "FREQ=MINUTELY;INTERVAL=3")
    app = _quota(registry_path, runtime_root, project)["scheduler_hint"]["codex_app"]
    if host_missing:
        (codex_home / "automations/fixture/automation.toml").unlink()

    _, ack = run_json_cli_result(
        *app["ack_hint"]["cli_args"], registry_path=registry_path,
        runtime_root=runtime_root, cwd=project,
    )
    assert ack["ok"] is False
    assert ack["scheduler_commit"]["reason_code"] == "rrule_mismatch"
    assert not Path(ack["scheduler_commit"]["path"]).exists()
    assert ack["appended"] is False
    assert ack["registry_mutated"] is False


def test_scheduler_ack_current_resets_stale_identity_from_matching_host(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry_path, runtime_root, project = write_cli_fixture(
        tmp_path / "fixture",
        scoped_agents=True,
    )
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_THREAD_ID", "fixture-thread")
    _write_heartbeat_rrule(codex_home, "FREQ=MINUTELY;INTERVAL=3")

    first = _quota(registry_path, runtime_root, project)
    first_app = first["scheduler_hint"]["codex_app"]
    target_rrule = first_app["recommended_rrule"]
    _write_heartbeat_rrule(codex_home, target_rrule)
    stale_state = build_scheduler_state(
        goal_id=GOAL_ID,
        agent_id=SCOPED_AGENT_ID,
        reset_token="stale-reset-token",
        identity_signature="stale-identity-signature",
        progression_index=0,
        progression_minutes=first_app["example_progression_minutes"],
        last_applied_rrule=target_rrule,
        updated_at="2026-08-26T00:00:00+00:00",
        source="test_scheduler_ack_current_stale_identity",
    )
    write_scheduler_state(
        runtime_root,
        stale_state,
        goal_id=GOAL_ID,
        agent_id=SCOPED_AGENT_ID,
    )

    reset = _quota(registry_path, runtime_root, project)
    reset_app = reset["scheduler_hint"]["codex_app"]
    reset_backoff = reset_app["stateful_backoff"]
    assert reset_backoff["state_status"] == "reset_required"
    assert reset_backoff["apply_needed"] is False
    assert reset_backoff["ack_needed"] is True
    assert reset_backoff["host_observation"]["status"] == "matches_recommended"

    ack = run_json_cli(
        *reset_app["ack_hint"]["cli_args"],
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=project,
    )
    assert ack["scheduler_state_mutated"] is True
    assert ack["scheduler_ack_event"]["scheduler_state"]["reset_token"] == (
        reset_backoff["reset_token"]
    )

    settled = _quota(registry_path, runtime_root, project)
    settled_backoff = settled["scheduler_hint"]["codex_app"][
        "stateful_backoff"
    ]
    assert settled_backoff["state_status"] == "same_identity"
    assert settled_backoff["apply_needed"] is False
    assert settled_backoff["ack_needed"] is False


def test_quota_should_run_ignores_cross_agent_scheduler_state(tmp_path: Path) -> None:
    registry_path, runtime_root, project = write_cli_fixture(
        tmp_path / "fixture",
        scoped_agents=True,
    )
    first = _quota(registry_path, runtime_root, project)
    first_rrule = first["scheduler_hint"]["codex_app"]["recommended_rrule"]
    ack = run_json_cli(
        "quota",
        "scheduler-ack",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        SCOPED_AGENT_ID,
        "--applied-rrule",
        first_rrule,
        "--codex-app",
        "--execute",
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=project,
    )
    state_path = Path(ack["scheduler_state_path"])
    persisted_state = json.loads(state_path.read_text(encoding="utf-8"))
    persisted_state["agent_id"] = "codex-other-agent"
    state_path.write_text(
        json.dumps(persisted_state, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    repaired = _quota(registry_path, runtime_root, project)
    app = repaired["scheduler_hint"]["codex_app"]
    assert app["recommended_rrule"] == first_rrule
    assert app["stateful_backoff"]["state_status"] == "missing"
    assert app["stateful_backoff"]["apply_needed"] is True


def test_scheduler_fail_current_rejects_missing_turn_receipt_without_state_write(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, project = write_cli_fixture(
        tmp_path / "fixture",
        scoped_agents=True,
    )
    state_path = scheduler_state_path(
        runtime_root,
        goal_id=GOAL_ID,
        agent_id=SCOPED_AGENT_ID,
    )

    returncode, payload = run_json_cli_result(
        "quota",
        "scheduler-fail-current",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        SCOPED_AGENT_ID,
        "--turn-instance-id",
        "heartbeat-missing-receipt",
        "--failed-rrule",
        "FREQ=MINUTELY;INTERVAL=3",
        "--codex-app",
        "--execute",
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=project,
    )

    assert returncode == 1
    assert payload["ok"] is False
    assert payload["status"] == "heartbeat_receipt_missing"
    assert payload["state"] == "blocked_receipt"
    assert payload["error_code"] == ("SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_MISSING")
    assert payload["write_performed"] is False
    assert payload["scheduler_state_mutated"] is False
    assert payload["quota_spend_performed"] is False
    assert not state_path.exists()


def test_trae_app_scheduler_failure_does_not_read_codex_automation_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    def reject_codex_store_read(**_kwargs):
        raise AssertionError("Trae App must not read the Codex automation store")

    def fake_build_decision(*_args, **_kwargs):
        return {"effective_action": "execute_todo"}

    def fake_record_failure(_decision, **kwargs):
        observed.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        quota_scheduler_followup,
        "resolve_codex_app_automation_rrule",
        reject_codex_store_read,
    )
    monkeypatch.setattr(
        quota_scheduler_followup,
        "_build_scheduler_followup_decision",
        fake_build_decision,
    )
    monkeypatch.setattr(
        quota_scheduler_followup,
        "record_quota_scheduler_failure_for_decision",
        fake_record_failure,
    )
    args = Namespace(
        quota_command="scheduler-fail-current",
        goal_id=GOAL_ID,
        agent_id=SCOPED_AGENT_ID,
        codex_app_current_rrule=None,
        applied_rrule="FREQ=MINUTELY;INTERVAL=3",
        host_match_observed=False,
        execute=True,
        surface="trae_app",
        state_key=APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
        failed_rrule="FREQ=MINUTELY;INTERVAL=3",
        failure_kind="host_update_failed",
        available_capabilities=[],
    )

    payload = quota_scheduler_followup.build_scheduler_followup_payload(
        {},
        args,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        turn_instance_id=None,
        scheduler_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.TRAE_APP
        ),
        operator_inbox_urgency_projector=lambda **_kwargs: {},
    )

    assert payload == {"ok": True}
    assert observed["surface"] == "trae_app"
    assert observed["observed_host_rrule"] == ""


def test_scheduler_ack_collects_the_periodic_should_run_lookback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seen: dict[str, object] = {}

    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir()
    registry_path.write_text(
        json.dumps({"common_runtime_root": str(runtime_root), "goals": []}),
        encoding="utf-8",
    )

    def fake_collect_status(**kwargs):
        seen["limit"] = kwargs.get("limit")
        return {"ok": True, "runtime_root": "/tmp/runtime"}

    def fake_record_quota_scheduler_ack(status_payload, **kwargs):
        seen["status_payload"] = status_payload
        seen["ack_kwargs"] = kwargs
        return {"ok": True, "mode": "scheduler-ack", "dry_run": True}

    def fake_print_payload(payload, output_format, renderer):
        seen["payload"] = payload
        seen["output_format"] = output_format
        seen["renderer"] = renderer

    def fake_build_lark_projector(*, runtime_root_arg):
        seen["lark_runtime_root"] = runtime_root_arg
        return lambda **_kwargs: {}

    monkeypatch.setattr(quota_command, "collect_status", fake_collect_status)
    monkeypatch.setattr(
        quota_command,
        "build_lark_operator_inbox_urgency_projector",
        fake_build_lark_projector,
    )
    monkeypatch.setattr(
        quota_scheduler_followup,
        "record_quota_scheduler_ack",
        fake_record_quota_scheduler_ack,
    )
    args = Namespace(
        quota_command="scheduler-ack",
        goal_id="scheduler-state-ack-test",
        agent_id=SCOPED_AGENT_ID,
        available_capabilities=["shell", "network", "benchmark_runner"],
        include_scheduler_detail=False,
        runtime_profile=None,
        codex_app=True,
        host_surface=None,
        scheduler_owner=None,
        execution_mode=None,
        codex_app_current_rrule=None,
        slots=1,
        source="heartbeat",
        void_generated_at=None,
        reason_summary=None,
        todo_id=None,
        target_key=None,
        result_hash=None,
        material_change=False,
        cadence=None,
        next_due_at=None,
        next_agent_todo=None,
        next_user_todo=None,
        next_claimed_by=None,
        surface="codex_app",
        state_key=APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
        applied_rrule="FREQ=MINUTELY;INTERVAL=10",
        reset_token="fixture-reset-token",
        identity_signature="fixture-identity-signature",
        host_match_observed=False,
        use_current_hint=False,
        dry_run=False,
        execute=False,
        limit=5,
        format="json",
    )

    result = quota_command.handle_quota_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=None,
        print_payload=fake_print_payload,
        append_cli_rollout_event=lambda **_: {},
    )

    assert result == 0
    assert seen["limit"] == AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK
    assert seen["lark_runtime_root"] == runtime_root
    assert seen["payload"] == {
        "ok": True,
        "mode": "scheduler-ack",
        "dry_run": True,
    }
    ack_kwargs = seen["ack_kwargs"]
    assert ack_kwargs["available_capabilities"] == [
        "shell",
        "network",
        "benchmark_runner",
    ]
    assert ack_kwargs["reset_token"] == "fixture-reset-token"
    assert ack_kwargs["identity_signature"] == "fixture-identity-signature"
