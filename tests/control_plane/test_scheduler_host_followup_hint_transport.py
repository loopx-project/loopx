from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import zlib
from pathlib import Path

import pytest
import test_quota_settlement_cli as settlement
from canonical_authority_fixture import isolate_sqlite_runtime
from test_quota_authority_settlement_journey import _execute, _guard, _refresh, _source

from loopx.control_plane.scheduler import scheduler_hint
from loopx.control_plane.scheduler.scheduler_hint import (
    build_app_automation_scheduler_ack_hint,
    build_app_automation_scheduler_failure_hint,
)
from loopx.control_plane.runtime.public_safety import SECRET_LIKE_SURFACE_PATTERN

FACTS_FLAG = "--scheduler-host-facts-chunk"


def _host_facts(operation: str) -> dict[str, object]:
    return {
        "schema_version": "loopx_scheduler_heartbeat_host_facts_v0",
        "operation": operation,
        "goal_id": "goal-native-followup",
        "agent_id": "agent-native-followup",
        "surface": "codex_app",
        "state_key": "scheduler_hint.app_automation.stateful_backoff",
        "reset_token": "reset-native-followup",
        "identity_signature": "identity-native-followup",
        "progression_index": 0,
        "progression_minutes": [15, 30, 60],
        "expected_rrule": "FREQ=MINUTELY;INTERVAL=15",
        "applied_rrule": "FREQ=MINUTELY;INTERVAL=15",
        "observed_host_rrule": "FREQ=MINUTELY;INTERVAL=3",
        "cadence_class": "active_work",
        "generated_at": "2026-08-27T06:30:00Z",
        "ack_needed": True,
        "apply_needed": True,
        "source": (
            "quota_scheduler_ack"
            if operation == "ack"
            else "quota_scheduler_host_update_failure"
        ),
        "host_match_observed": operation == "ack",
        "failure_kind": "timeout" if operation == "host_failure" else None,
    }


def _before() -> dict[str, object]:
    return {
        "should_run": True,
        "normal_delivery_allowed": True,
        "recovery_delivery_allowed": False,
        "effective_action": "normal_run",
        "self_repair_allowed": False,
        "capability_repair_allowed": False,
        "workspace_repair_allowed": False,
        "state": "eligible",
        "safe_bypass_allowed": False,
        "safe_bypass_kind": None,
        "blocked_action_scope": None,
        "quota": {
            "compute": 1,
            "window_hours": 4,
            "slot_minutes": 15,
            "spent_slots": 0,
            "allowed_slots": 16,
        },
    }


def _decode(cli_args: list[str]) -> dict[str, object]:
    encoded = "".join(
        cli_args[index + 1]
        for index, value in enumerate(cli_args)
        if value == FACTS_FLAG
    )
    padding = "=" * (-len(encoded) % 4)
    compressed = base64.urlsafe_b64decode(encoded + padding)
    return json.loads(zlib.decompress(compressed))


def test_ack_hint_carries_bounded_native_followup_facts_without_changing_verb() -> None:
    hint = build_app_automation_scheduler_ack_hint(
        goal_id="goal-native-followup",
        agent_id="agent-native-followup",
        applied_rrule="FREQ=MINUTELY;INTERVAL=15",
        reset_token="reset-native-followup",
        identity_signature="identity-native-followup",
        host_match_observed=True,
        scheduler_host_facts=_host_facts("ack"),
        scheduler_before=_before(),
    )

    cli_args = hint["cli_args"]
    assert cli_args[:2] == ["quota", "scheduler-ack-current"]
    assert FACTS_FLAG in cli_args
    assert cli_args[-6:] == [
        "--host-match-observed",
        "--reset-token",
        "reset-native-followup",
        "--identity-signature",
        "identity-native-followup",
        "--execute",
    ]
    assert len(cli_args) <= 64
    assert max(map(len, cli_args)) <= 512
    assert sum(map(len, cli_args)) <= 2_048
    payload = _decode(cli_args)
    assert payload["schema_version"] == "loopx_scheduler_host_followup_hint_v0"
    assert payload["host_facts"] == _host_facts("ack")
    assert payload["before"]["effective_action"] == "normal_run"
    assert payload["use_current_hint"] is True


def test_failure_hint_carries_the_same_versioned_native_boundary() -> None:
    facts = _host_facts("host_failure")
    hint = build_app_automation_scheduler_failure_hint(
        goal_id="goal-native-followup",
        agent_id="agent-native-followup",
        failed_rrule="FREQ=MINUTELY;INTERVAL=15",
        observed_host_rrule="FREQ=MINUTELY;INTERVAL=3",
        scheduler_host_facts=facts,
        scheduler_before=_before(),
    )

    cli_args = hint["cli_args"]
    assert cli_args[:2] == ["quota", "scheduler-fail-current"]
    assert FACTS_FLAG in cli_args
    assert cli_args[-1] == "--execute"
    assert sum(map(len, cli_args)) <= 2_048
    payload = _decode(cli_args)
    assert payload["host_facts"] == facts
    assert payload["use_current_hint"] is False


def test_native_facts_are_not_dropped_when_cli_args_exceed_legacy_budget() -> None:
    capabilities = [
        f"capability-{index}-" + (chr(97 + index) * 140) for index in range(12)
    ]

    hint = build_app_automation_scheduler_ack_hint(
        goal_id="goal-native-followup",
        agent_id="agent-native-followup",
        applied_rrule="FREQ=MINUTELY;INTERVAL=15",
        reset_token="reset-native-followup",
        identity_signature="identity-native-followup",
        available_capabilities=capabilities,
        host_match_observed=True,
        scheduler_host_facts=_host_facts("ack"),
        scheduler_before=_before(),
    )

    cli_args = hint["cli_args"]
    assert sum(map(len, cli_args)) > 2_048
    assert sum(map(len, cli_args)) <= 8_192
    assert FACTS_FLAG in cli_args
    assert _decode(cli_args)["host_facts"] == _host_facts("ack")


def test_oversized_native_facts_fail_instead_of_falling_back_to_python() -> None:
    facts = _host_facts("ack")
    facts["source"] = "".join(
        hashlib.sha256(str(index).encode()).hexdigest() for index in range(100)
    )

    with pytest.raises(ValueError, match="exceed the native CLI transport bound"):
        build_app_automation_scheduler_ack_hint(
            goal_id="goal-native-followup",
            agent_id="agent-native-followup",
            applied_rrule="FREQ=MINUTELY;INTERVAL=15",
            reset_token="reset-native-followup",
            identity_signature="identity-native-followup",
            host_match_observed=True,
            scheduler_host_facts=facts,
            scheduler_before=_before(),
        )


def test_native_facts_chunks_do_not_look_like_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scheduler_hint.zlib,
        "compress",
        lambda _value, *, level: base64.urlsafe_b64decode("-ak-"),
    )

    args = scheduler_hint._scheduler_host_followup_transport_args(
        _host_facts("ack"),
        before=_before(),
        use_current_hint=True,
    )

    assert args == [FACTS_FLAG, "+ak+"]
    assert SECRET_LIKE_SURFACE_PATTERN.search(args[-1]) is None


def test_legacy_hint_builder_without_host_facts_keeps_the_compatibility_route() -> None:
    hint = build_app_automation_scheduler_ack_hint(
        goal_id="goal-native-followup",
        agent_id="agent-native-followup",
        applied_rrule="FREQ=MINUTELY;INTERVAL=15",
        reset_token="reset-native-followup",
        identity_signature="identity-native-followup",
    )

    assert FACTS_FLAG not in hint["cli_args"]
    assert hint["cli_args"][-1] == "--execute"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("caller", ["python", "native"])
def test_failed_quota_invocation_keeps_receipt_ack_and_exact_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, caller: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    project, runtime, registry, _, _ = _source(
        tmp_path, provider=provider, extra=f"claimed_by={settlement.AGENT_ID}",
    )
    code, guard = _guard(project, runtime, registry)
    assert code == 0 and guard["normal_delivery_allowed"] is True, guard
    receipt = guard["heartbeat_receipt"]
    hint_args = guard["scheduler_hint"]["codex_app"]["ack_hint"]["cli_args"]

    # Exercise the real parameter-error audit, rather than fabricating a receipt.
    code, rejected = settlement._run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", settlement.GOAL_ID, "--agent-id", settlement.AGENT_ID,
        "--todo-id", settlement.TODO_ID, "--turn-instance-id", settlement.TURN_ID,
        "--begin-turn", "--scan-path", str(project), cwd=project,
    )
    assert code == 1 and rejected["error_code"] == "QUOTA_VALIDATION_FAILED", rejected
    log_path = runtime / "goals" / settlement.GOAL_ID / "rollout-event-log.jsonl"
    audit = json.loads(log_path.read_text().splitlines()[-1])
    assert audit["event_kind"] == "quota_should_run" and "run_id" not in audit
    code, replay = _guard(project, runtime, registry)
    assert code == 0 and replay["normal_delivery_allowed"] is True, replay
    assert replay["heartbeat_receipt"]["settlement_identity"] == receipt["settlement_identity"]
    assert replay["heartbeat_receipt"]["event_id"] == receipt["event_id"]

    def acknowledge():
        if caller == "python":
            return settlement._run_cli(registry, runtime, *hint_args, cwd=project)
        result = subprocess.run(
            [str(settlement.REPO_ROOT / "scripts" / "loopx"), "--format", "json", *hint_args],
            cwd=project, text=True, capture_output=True, check=False,
            env={**os.environ, "LOOPX_PYTHON": str(tmp_path / "unavailable-python")},
        )
        assert result.stdout, result.stderr
        return result.returncode, json.loads(result.stdout)

    code, ack = acknowledge()
    assert code == 0 and ack["scheduler_commit"]["written"] is True, ack
    state_path = Path(ack["scheduler_state_path"])
    state_bytes = state_path.read_bytes()
    code, retried = acknowledge()
    assert code == 0 and retried["scheduler_commit"]["replayed"] is True, retried
    assert state_path.read_bytes() == state_bytes
    assert settlement._spend_run_count(runtime) == 0
    assert audit in [json.loads(line) for line in log_path.read_text().splitlines()]

    code, refreshed = _refresh(
        project, runtime, registry, "--delivery-boundary", "in_flight_continuation",
    )
    assert code == 0, refreshed
    code, spent = _execute(refreshed["settlement_owed"]["command"], project, runtime, registry)
    assert code == 0 and spent["settlement_progress"]["state"] == "settled", spent
    code, settled = _guard(project, runtime, registry)
    assert code == 0 and settled["effective_action"] == "heartbeat_settled_skip", settled
    assert settled["heartbeat_receipt"]["settlement_identity"] == receipt["settlement_identity"]
    assert settlement._spend_run_count(runtime) == 1

    code, newer = _guard(project, runtime, registry, turn_id="turn-followup-newer")
    assert code == 0 and newer["heartbeat_receipt"]["closeout_required"] is True, newer
    code, stale = acknowledge()
    assert code == 1 and stale["error_code"] == "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_STALE", stale
    assert stale["scheduler_state_mutated"] is False
    assert state_path.read_bytes() == state_bytes
    assert settlement._spend_run_count(runtime) == 1
