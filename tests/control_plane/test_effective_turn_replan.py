"""Receipt-backed cadence over an open Todo, through the real CLI and runtime."""

import json
import shlex
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from tests.control_plane.test_quota_settlement_cli import (
    _write_fixture,
    _run_cli,
    AGENT_ID,
    GOAL_ID,
    TODO_ID,
    _configure_selectable_alternative,
    ALTERNATIVE_TODO_ID,
)
from loopx.control_plane.work_items.replan_history_codec import (
    effective_turn_cadence_context,
)
from loopx.control_plane.work_items.replan_history_codec import project_replan_history
from loopx.control_plane.work_items.semantic_replan_writeback import (
    qualify_replan_writeback,
)


def test_open_todo_settled_turn_cadence_and_evidence_linked_review(tmp_path: Path) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    fixture = json.loads(registry.read_text())
    fixture["goals"][0]["quota"]["allowed_slots"] = 4
    registry.write_text(json.dumps(fixture))
    rc, configured = _run_cli(
        registry,
        runtime,
        "configure-goal",
        "--goal-id",
        GOAL_ID,
        "--execution-replan-after-turns",
        "2",
        "--execute",
    )
    assert rc == 0, configured
    goal = json.loads(registry.read_text())["goals"][0]
    assert goal["execution_profile"]["replan_after_effective_turns"] == 2
    context = effective_turn_cadence_context(goal, runtime)

    def periodic():
        path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        runs = (
            [json.loads(line) for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )
        # Deliberate retries cannot inflate the number of accepted Turns.
        return project_replan_history(
            list(reversed(runs)) * 3,
            operation="periodic",
            agent_id=AGENT_ID,
            effective_turn_cadence=context,
        )

    for i in range(2):
        turn = f"effective-turn-{i}"
        rc, guard = _run_cli(
            registry,
            runtime,
            "quota",
            "should-run",
            "--codex-app",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--turn-instance-id",
            turn,
            "--scan-path",
            str(project),
        )
        assert rc == 0, guard
        assert guard["selected_todo"]["todo_id"] == TODO_ID
        rc, refreshed = _run_cli(
            registry,
            runtime,
            "refresh-state",
            "--goal-id",
            GOAL_ID,
            "--classification",
            "validated_progress",
            "--delivery-batch-scale",
            "implementation",
            "--delivery-outcome",
            "outcome_progress",
            "--delivery-boundary",
            "in_flight_continuation",
            "--agent-id",
            AGENT_ID,
            "--todo-id",
            TODO_ID,
            "--turn-instance-id",
            turn,
            "--no-global-sync",
            "--suppress-external-sinks",
        )
        assert rc == 0, json.dumps(refreshed)
        assert periodic() is None, "unsettled writeback must not advance cadence"
        rc, spent = _run_cli(
            registry,
            runtime,
            "quota",
            "spend-slot",
            "--goal-id",
            GOAL_ID,
            "--slots",
            "1",
            "--source",
            "heartbeat",
            "--execute",
            "--agent-id",
            AGENT_ID,
            "--todo-id",
            TODO_ID,
            "--turn-instance-id",
            turn,
        )
        assert rc == 0, spent
        if i == 0:
            assert periodic() is None
    trigger = periodic()
    assert trigger is not None
    assert trigger["kind"] == "periodic_review_due"
    assert trigger["run_count"] == 2
    rc, listed = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert rc == 0, listed
    assert (
        next(row for row in listed["todos"] if row["todo_id"] == TODO_ID)["status"]
        == "open"
    )
    rc, next_guard = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        "effective-turn-next",
        "--scan-path",
        str(project),
    )
    assert rc == 0, next_guard
    obligation = next_guard.get("autonomous_replan_obligation")
    assert obligation is not None, next_guard
    assert "periodic_review_due" in json.dumps(obligation)
    runs = [
        json.loads(line)
        for line in (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl")
        .read_text()
        .splitlines()
    ]
    writeback_obligation, _ = qualify_replan_writeback(
        newest_first_runs=list(reversed(runs)),
        state_text=(project / goal["state_file"]).read_text(),
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
        registry_goal=goal,
        effective_turn_cadence=context,
    )
    assert writeback_obligation is not None
    assert writeback_obligation["obligation_id"] == obligation["obligation_id"]

    # A durable debit without its committed receipt is not an effective Turn.
    # This is an isolated fixture, never a mutation of a live Goal.
    log = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    original = log.read_bytes()
    events = [json.loads(line) for line in original.splitlines()]
    log.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in events
            if not (
                row.get("run_id") == "effective-turn-1"
                and row.get("event_kind") == "quota_spend"
            )
        )
    )
    try:
        assert periodic() is None
    finally:
        log.write_bytes(original)

    # A cadence checkpoint is a review, not an obligation to invent a new route.
    # Execute the projected vision path against the real isolated File runtime.
    selected_rc, selected = _run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "effective-turn-next", "--scan-path", str(project),
    )
    assert selected_rc == 0, selected
    contract = selected["replan_action_packet"]["writeback_contract"]
    assert contract["preferred_input"] == "evidence_linked_vision_path"
    vision_path = tmp_path / "periodic-vision.json"
    vision = {
        "schema_version": "goal_vision_replan_contract_v0",
        "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Original acceptance remains open."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "replan",
                       "prior_assumption": "The existing method remains viable.",
                       "observed_reality": "Current validation supports continuing it.",
                       "retained": ["Existing method and open Todo"], "evidence_refs": []},
    }
    command = shlex.split(selected["interaction_contract"]["cli_channel"]["next_cli_actions"][0])
    command = command[command.index("refresh-state"):]
    command[command.index("--agent-vision-json") + 1] = str(vision_path)
    command += ["--delivery-workspace-path", str(project), "--no-global-sync", "--suppress-external-sinks"]
    vision_path.write_text(json.dumps(vision))
    rc, rejected = _run_cli(registry, runtime, *command)
    assert rc == 1 and rejected["appended"] is False, rejected
    assert "typed semantic delta" in rejected["error"], rejected
    vision["path_delta"]["evidence_refs"] = ["evidence:method-validation"]
    vision_path.write_text(json.dumps(vision))
    rc, accepted = _run_cli(registry, runtime, *command)
    assert rc == 0, accepted
    assert accepted["autonomous_replan_ack"]["semantic_delta"]["satisfying_outcomes"] == ["fresh_vision_path_outcome"]
    rc, spent = _run_cli(
        registry, runtime, "quota", "spend-slot", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "effective-turn-next", "--slots", "1",
        "--source", "heartbeat", "--execute",
    )
    assert rc == 0 and spent["settlement_progress"]["state"] == "settled", json.dumps(spent)
    rc, following = _run_cli(
        registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "after-periodic-review", "--scan-path", str(project),
    )
    assert rc == 0 and following["decision"] == "run", following
    assert following["selected_todo"]["status"] == "open"

    rc, cleared = _run_cli(
        registry,
        runtime,
        "configure-goal",
        "--goal-id",
        GOAL_ID,
        "--clear-execution-replan-after-turns",
        "--execute",
    )
    assert rc == 0, cleared
    assert (
        effective_turn_cadence_context(
            json.loads(registry.read_text())["goals"][0], runtime
        )
        is None
    )


@pytest.mark.parametrize("invalid", [0, 6, True, 2.5, "2"])
def test_effective_cadence_rejects_invalid_units(invalid):
    from loopx.execution_profile import compact_execution_profile

    with pytest.raises(ValueError, match="integer from 1 to 5"):
        compact_execution_profile({"replan_after_effective_turns": invalid})


def test_verified_negative_work_counts_without_a_score_gain(tmp_path: Path) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    binding = (
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        "negative-turn",
    )
    rc, guard = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert rc == 0, guard
    due = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(
        timespec="seconds"
    )
    rc, waiting = _run_cli(
        registry,
        runtime,
        "todo",
        "update",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--status",
        "open",
        "--resume-when",
        f"resume_at:{due}",
        "--successor-todo-id",
        ALTERNATIVE_TODO_ID,
    )
    assert rc == 0, json.dumps(waiting)
    rc, refreshed = _run_cli(
        registry,
        runtime,
        "refresh-state",
        *binding,
        "--classification",
        "typed_blocker_writeback",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_gap",
        "--progress-result-class",
        "blocked",
        "--progress-blocker-id",
        "fixture:blocker",
        "--progress-evidence-id",
        "fixture:negative-result",
        "--no-global-sync",
        "--suppress-external-sinks",
        cwd=project,
    )
    assert rc == 0, json.dumps(refreshed)
    assert refreshed["settlement_progress"]["state"] == "settled"
    runs = [
        json.loads(line)
        for line in (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl")
        .read_text()
        .splitlines()
    ]
    trigger = project_replan_history(
        list(reversed(runs)),
        operation="periodic",
        agent_id=AGENT_ID,
        effective_turn_cadence={
            "threshold": 1,
            "settlement_source": {"runtime_root": str(runtime), "goal_id": GOAL_ID},
        },
    )
    assert trigger is not None
    assert trigger["run_count"] == 1


def test_profile_rejects_two_counting_units():
    from loopx.execution_profile import compact_execution_profile

    with pytest.raises(ValueError, match="choose one review cadence unit"):
        compact_execution_profile(
            {"replan_after_effective_turns": 2, "replan_after_completed_todos": 3}
        )


def test_source_cadence_uses_live_admission_and_borrows_refresh_locks(tmp_path):
    from tests.control_plane.test_quota_spend_commit_runtime import (
        _write_source_registry,
        INSTANCE_A,
        INSTANCE_B,
        GOAL_ID as source_goal_id,
    )
    from loopx.control_plane.quota.accounting_admission import (
        quota_accounting_admission,
    )
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement

    runtime = tmp_path / "runtime"
    registry = tmp_path / "project" / ".loopx" / "registry.json"
    _write_source_registry(registry, runtime, INSTANCE_A)
    goal = {
        "id": source_goal_id,
        "goal_instance_id": INSTANCE_A,
        "execution_profile": {"replan_after_effective_turns": 2},
    }
    context = effective_turn_cadence_context(goal, runtime, registry_path=registry)
    assert (
        project_replan_history(operation="periodic", effective_turn_cadence=context)
        is None
    )
    with quota_accounting_admission(
        runtime_root=runtime,
        registry_path=registry,
        goal_id=source_goal_id,
        goal_ref=context["goal_ref"],
        operation="fixture-enclosing-refresh",
    ) as admission:
        borrowed = effective_turn_cadence_context(
            goal, runtime, registry_path=registry, source_admission=admission
        )
        assert (
            project_replan_history(
                operation="periodic", effective_turn_cadence=borrowed
            )
            is None
        )
        # An actual second RPC must still adopt the enclosing live lock witnesses.
        read_heartbeat_settlement(
            runtime,
            goal_id=source_goal_id,
            agent_id=AGENT_ID,
            todo_id=TODO_ID,
            turn_instance_id="empty-source-turn",
            goal_ref=context["goal_ref"],
            registry_path=registry,
            source_admission=admission,
            borrow_source_admission=True,
        )
    _write_source_registry(registry, runtime, INSTANCE_B)
    from loopx.control_plane.effect_runtime import EffectRuntimeConflict

    with pytest.raises(EffectRuntimeConflict, match="stale_goal_instance"):
        project_replan_history(operation="periodic", effective_turn_cadence=context)
    without_ref = effective_turn_cadence_context(
        {key: value for key, value in goal.items() if key != "goal_instance_id"},
        runtime,
        registry_path=registry,
    )
    with pytest.raises(ValueError, match="requires an exact GoalRef"):
        project_replan_history(operation="periodic", effective_turn_cadence=without_ref)
