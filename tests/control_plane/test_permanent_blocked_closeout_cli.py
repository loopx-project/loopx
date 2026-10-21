"""An admitted failed attempt can close without resuming its blocked work."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.cli import main as cli_main
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from test_quota_settlement_cli import (
    AGENT_ID, ALTERNATIVE_TODO_ID, GOAL_ID, TODO_ID,
    _classification_count, _configure_completion_validation_todo,
    _configure_selectable_alternative, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_blocked_lifecycle_closes_original_attempt_without_a_retry_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_completion_validation_todo(project)
    _configure_selectable_alternative(project)

    def cli(*args: str) -> tuple[int, dict]:
        with monkeypatch.context() as context:
            context.chdir(project)
            rc = cli_main(["--registry", str(registry), "--runtime-root", str(runtime),
                           "--format", "json", *args])
            return rc, json.loads(capsys.readouterr().out)

    rc, listed = cli("todo", "list", "--goal-id", GOAL_ID)
    assert rc == 0, listed
    initialize_canonical_authority(runtime, GOAL_ID, build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, todos=listed["todos"], handoff_mode="hard_lease", leases=[],
    ), state_path=state, provider=provider)
    binding = ("--agent-id", AGENT_ID, "--todo-id", TODO_ID,
               "--turn-instance-id", f"blocked-lifecycle-{provider}")
    guard_args = ("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app",
                  )
    rc, guard = cli(*guard_args, *binding)
    assert rc == 0 and guard["heartbeat_receipt"]["closeout_required"], guard
    rc, acquired = cli("task-lease", "acquire", "--goal-id", GOAL_ID, "--todo-id", TODO_ID,
                       "--owner", AGENT_ID, "--idempotency-key", "attempt-before-source-loss")
    assert rc == 0, acquired
    rc, blocked = cli("todo", "update", "--goal-id", GOAL_ID, "--todo-id", TODO_ID,
                      "--agent-id", AGENT_ID, "--status", "blocked",
                      "--reason", "Original source is no longer available", "--clear-resume-when",
                      "--task-lease-idempotency-key", "attempt-before-source-loss",
                      "--task-lease-expected-version", str(acquired["lease"]["version"]))
    assert rc == 0, blocked
    assert blocked["blocked_lifecycle_transition"]["lease_retirement"] == "released"
    rc, original = cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
    assert rc == 0, original
    validator = original["todo"]["completion_validation_sha256"]
    rc, recovery = cli(*guard_args, *binding)
    assert rc == 0, recovery
    repair = recovery["unsettled_host_turn_recovery"]
    assert repair["repair"] == "blocked_writeback"
    assert repair["binding_id"] == TODO_ID and repair["turn_instance_id"] == binding[-1]
    actions = recovery["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert any("refresh-state" in action and binding[-1] in action for action in actions)
    assert not any("--status open" in action or "spend-slot" in action for action in actions)
    assert recovery["normal_delivery_allowed"] is False
    rc, wrong_binding = cli(*guard_args, "--agent-id", AGENT_ID,
                            "--todo-id", ALTERNATIVE_TODO_ID, "--turn-instance-id", binding[-1])
    assert rc == 1 and wrong_binding["error_code"] == "heartbeat_receipt_identity_conflict", wrong_binding
    refresh = ("refresh-state", "--goal-id", GOAL_ID, *binding,
               "--classification", "source_unavailable", "--delivery-batch-scale", "single_surface",
               "--delivery-outcome", "outcome_gap", "--progress-result-class", "blocked",
               "--progress-blocker-id", "source:unavailable", "--progress-evidence-id", "evidence:source-readback",
               "--no-global-sync", "--suppress-external-sinks")
    rc, closed = cli(*refresh)
    assert rc == 0, closed
    assert closed["settlement_progress"]["state"] == "settled"
    assert closed["settlement_progress"]["closeout_kind"] == "typed_blocked_writeback_no_spend"
    wait = closed["blocked_retry"]
    assert wait["schema_version"] == "quota_blocked_lifecycle_v0"
    assert wait["blocked_todo"]["status"] == "blocked"
    assert "resume_when" not in wait and "due_at" not in wait
    rc, replay = cli(*refresh)
    assert rc == 0 and replay["idempotent_replay"] is True, replay
    assert replay["blocked_retry"] == wait
    assert _classification_count(runtime, "source_unavailable") == 1
    rc, settled_guard = cli(*guard_args, *binding)
    assert rc == 0 and settled_guard["effective_action"] == "heartbeat_settled_skip", settled_guard
    rc, spent = cli("quota", "spend-slot", "--goal-id", GOAL_ID, *binding,
                    "--slots", "1", "--source", "heartbeat", "--execute", )
    assert rc == 0 and spent["appended"] is False, spent
    assert _spend_run_count(runtime) == 0
    rc, unchanged = cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
    assert rc == 0, unchanged
    assert unchanged["todo"]["status"] == "blocked" and unchanged["todo"]["done"] is False
    assert unchanged["todo"]["completion_validation_sha256"] == validator
    assert not unchanged["todo"].get("resume_when")
    rc, next_turn = cli(*guard_args, "--agent-id", AGENT_ID,
                        "--turn-instance-id", f"independent-after-source-loss-{provider}")
    assert rc == 0 and next_turn["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID, next_turn
    assert _classification_count(runtime, "quota_monitor_poll") == 0


def test_lifecycle_transport_keeps_the_bound_todo_completion_fact() -> None:
    from loopx.control_plane.quota.blocked_retry import require_blocked_retry_wait

    todo = {"todo_id": TODO_ID, "role": "agent", "status": "blocked", "done": False,
            "task_class": "advancement_task", "archive_state": "active", "note": "Not lifecycle proof"}
    source = {"agent_todos": {"items": [todo]}}
    result = require_blocked_retry_wait(source, todo_id=TODO_ID, observed_at="2026-01-01T00:00:00Z")
    assert result["blocked_todo"]["done"] is False
    assert "note" not in result["blocked_todo"]
    todo["done"] = True
    with pytest.raises(ValueError, match="active blocked advancement"):
        require_blocked_retry_wait(source, todo_id=TODO_ID, observed_at="2026-01-01T00:00:00Z")
