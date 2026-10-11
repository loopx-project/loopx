"""Unavailable observation attempts settle their Turn, never a Monitor poll."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.cli import main as cli_main
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _classification_count, _spend_run_count, _write_fixture,
)

MONITOR_ID = "todo_unavailable_monitor"
OBSERVATION_FIELDS = (
    "last_checked_at", "next_due_at", "result_hash", "material_change",
    "material_change_generation", "cadence", "expires_at",
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("scenario", ["unavailable", "capability_restore", "observed"])
def test_unavailable_monitor_closes_original_turn_without_observation_or_debit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    provider: str, scenario: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(
        tmp_path, required_capability="network" if scenario == "capability_restore" else None,
    )
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace(
        f"todo_id={TODO_ID} status=open task_class=advancement_task action_kind=validate",
        f"todo_id={MONITOR_ID} status=open task_class=continuous_monitor action_kind=observe "
        f"claimed_by={AGENT_ID} target_key=synthetic-source watch_only=true cadence=30m "
        "last_checked_at=1999-01-01T00%3A00%3A00Z next_due_at=2000-01-01T00%3A00%3A00Z "
        f"result_hash={'a' * 64} material_change=false material_change_generation=7",
    ))

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
    original_turn = f"unavailable-original-{provider}"
    binding = ("--agent-id", AGENT_ID, "--todo-id", MONITOR_ID,
               "--turn-instance-id", original_turn)
    capabilities = ("--available-capability", "network") if scenario == "capability_restore" else ()
    if scenario == "capability_restore":
        rc, unavailable = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app", *binding)
        assert rc != 0 and unavailable["error_code"] == "quota_action_selection_rejected", unavailable
        assert _classification_count(runtime, "monitor_observation_unavailable") == 0
        assert _spend_run_count(runtime) == 0
    rc, guard = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app", *binding, *capabilities)
    assert rc == 0 and guard["should_run"] is True, guard
    assert guard["work_lane_contract"]["obligation"] == "attempt_due_monitor"
    assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == MONITOR_ID
    plan = guard["interaction_contract"]["cli_channel"]["settlement_plan"]
    writeback_step = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
    assert "observation is unavailable" in writeback_step["precondition"]
    rc, lease = cli("task-lease", "acquire", "--goal-id", GOAL_ID, "--todo-id", MONITOR_ID,
                    "--owner", AGENT_ID, "--idempotency-key", f"unavailable-{provider}")
    assert rc == 0 and lease["acquired"] is True, lease
    rc, before = cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", MONITOR_ID)
    assert rc == 0, before
    fields = {k: before["todo"].get(k) for k in OBSERVATION_FIELDS}

    # The next real Turn must recover the original identity; it cannot use
    # metadata as a result hash or silently move the unfinished receipt to P1.
    recovery_turn = f"unavailable-recovery-{provider}"
    if scenario != "observed":
        rc, recovery = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app",
                           "--agent-id", AGENT_ID, "--turn-instance-id", recovery_turn, *capabilities)
        assert rc == 0 and recovery["effective_action"] == "unsettled_host_turn_recovery", recovery
        actions = recovery["interaction_contract"]["cli_channel"]["next_cli_actions"]
        command = next(action for action in actions if " refresh-state " in action)
        # Consume the actual recovery packet rather than inventing an action.
        projected = shlex.split(command)
        projected = projected[projected.index("refresh-state"):]
        assert projected[projected.index("--todo-id") + 1] == MONITOR_ID
        assert projected[projected.index("--turn-instance-id") + 1] == original_turn

    refresh = ("refresh-state", "--goal-id", GOAL_ID, *binding,
               "--classification", "monitor_observation_unavailable",
               "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_gap",
               "--progress-result-class", "blocked", "--progress-blocker-id", "blocker:source-control",
               "--progress-evidence-id", "evidence:control-ownership-readback",
               "--vision-unchanged-reason", "Source unavailable; monitor acceptance and clocks retained.",
               "--no-global-sync", "--suppress-external-sinks")
    if scenario == "observed":
        rc, poll = cli("quota", "monitor-poll", "--goal-id", GOAL_ID, "--codex-app", *binding,
                       "--target-key", "synthetic-source", "--result-hash", "synthetic-observed",
                       "--task-lease-idempotency-key", f"unavailable-{provider}",
                       "--task-lease-expected-version", str(lease["lease"]["version"]),
                       "--execute")
        assert rc == 0 and poll["appended"] is True, poll
        rc, denied = cli(*refresh)
        assert rc != 0 and denied["appended"] is False, denied
        assert _classification_count(runtime, "monitor_observation_unavailable") == 0
        assert _classification_count(runtime, "quota_monitor_poll") == 1
        assert _spend_run_count(runtime) == 0
        rc, cold = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app", *binding)
        assert rc == 0 and cold["interaction_contract"]["mode"] == "heartbeat_settled_skip", cold
        return
    replacements = {
        "<verified-blocker-id>": "blocker:source-control",
        "<verified-evidence-ref>": "evidence:control-ownership-readback",
        "<verified-unchanged-vision-reason>": "Source unavailable; monitor acceptance and clocks retained.",
    }
    refresh = (*[replacements.get(arg, arg) for arg in projected],
               "--no-global-sync", "--suppress-external-sinks")
    without_evidence = list(refresh)
    evidence_index = without_evidence.index("--progress-evidence-id")
    del without_evidence[evidence_index:evidence_index + 2]
    rc, denied = cli(*without_evidence)
    assert rc != 0 and denied["appended"] is False, denied
    assert _classification_count(runtime, "monitor_observation_unavailable") == 0
    rc, closed = cli(*refresh)
    assert rc == 0, closed
    wait = closed["blocked_retry"]
    assert wait["schema_version"] == "quota_monitor_unavailable_v0"
    assert wait["observation_available"] is False and "due_at" not in wait
    assert {k: wait["monitor_todo"].get(k) for k in OBSERVATION_FIELDS} == fields
    assert closed["settlement_progress"]["state"] == "settled"
    assert closed["settlement_progress"]["closeout_kind"] == "typed_blocked_writeback_no_spend"
    assert {r["effect_id"] for r in closed["settlement_result"]["receipts"]} == {
        guard["heartbeat_receipt"]["settlement_identity"]["effect_id"]
    }
    rc, replay = cli(*refresh)
    assert rc == 0 and replay["idempotent_replay"] is True, replay
    assert replay["blocked_retry"] == wait
    assert _classification_count(runtime, "monitor_observation_unavailable") == 1
    assert _classification_count(runtime, "quota_monitor_poll") == 0
    assert _spend_run_count(runtime) == 0
    rc, cold = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app", *binding)
    assert rc == 0 and cold["should_run"] is False, cold
    assert cold["interaction_contract"]["mode"] == "heartbeat_settled_skip"
    rc, after = cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", MONITOR_ID)
    assert rc == 0 and after["todo"]["status"] == "open", after
    assert {k: after["todo"].get(k) for k in OBSERVATION_FIELDS} == fields
    assert not after["todo"].get("resume_when")

    # A newly executable independent advancement belongs to the same recovery
    # host Turn after the original effect is closed, without a third identity.
    rc, restored = cli("task-lease", "release", "--goal-id", GOAL_ID, "--todo-id", MONITOR_ID,
                       "--owner", AGENT_ID, "--idempotency-key", f"unavailable-{provider}",
                       "--expected-version", str(lease["lease"]["version"]))
    assert rc == 0, restored
    rc, successor = cli("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
                        "--text", "Inspect an independent public source.", "--task-class", "advancement_task",
                        "--claimed-by", AGENT_ID, "--operation-id", f"independent-{provider}")
    assert rc == 0, successor
    rc, next_turn = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app",
                        "--agent-id", AGENT_ID, "--turn-instance-id", recovery_turn, *capabilities)
    assert rc == 0 and next_turn["effective_action"] != "unsettled_host_turn_recovery", next_turn
    assert next_turn["work_lane_contract"]["lane"] == "advancement_task"
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("invalid", ["evidence", "binding"])
def test_unavailable_closeout_does_not_accept_missing_evidence_or_wrong_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], invalid: str,
) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    # This guard binds an advancement. A caller cannot close another Monitor
    # or omit the typed evidence required by the existing settlement owner.
    def cli(*args: str) -> tuple[int, dict]:
        with monkeypatch.context() as context:
            context.chdir(project)
            rc = cli_main(["--registry", str(registry), "--runtime-root", str(runtime),
                           "--format", "json", *args])
            return rc, json.loads(capsys.readouterr().out)

    binding = ("--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", "invalid-monitor")
    rc, guard = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app", *binding)
    assert rc == 0 and guard["should_run"] is True, guard
    args = ["refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--todo-id", MONITOR_ID if invalid == "binding" else TODO_ID,
            "--turn-instance-id", "invalid-monitor", "--classification", "invalid_unavailable",
            "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_gap",
            "--progress-result-class", "blocked", "--progress-blocker-id", "blocker:source-control",
            "--no-global-sync", "--suppress-external-sinks"]
    if invalid == "binding":
        args += ["--progress-evidence-id", "evidence:control-readback"]
    rc, denied = cli(*args)
    assert rc != 0 and denied["appended"] is False, denied
    assert _classification_count(runtime, "invalid_unavailable") == 0
    assert _spend_run_count(runtime) == 0
