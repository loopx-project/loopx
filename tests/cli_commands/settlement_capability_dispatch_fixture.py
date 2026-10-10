"""Shared settlement artifacts for todo-complete capability dispatch tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from loopx.capabilities.periodic_report.post_writeback_hook import (
    build_periodic_report_post_writeback_projection,
    periodic_report_post_writeback_hooks_for_goal,
)
from loopx.cli import build_parser
from loopx.cli_commands.todo import handle_todo_command
from loopx.rollout_event_log import (
    append_rollout_event,
    build_rollout_event,
    rollout_event_log_path,
)


GOAL_ID = "todo-complete-capability-goal"
AGENT_ID = "codex-capability-completion"
TURN_ID = "turn-capability-evidence-1"
COMPLETED_TODO = "todo_report_stage"
GATED_TODO = "todo_network_resume"
PLAIN_TODO = "todo_next_family"
EFFECT_ID = f"{GOAL_ID}:{AGENT_ID}:{COMPLETED_TODO}:{TURN_ID}"
TURN_KEY = "sha256:" + "9" * 64
# Ordered prefix of the loopx_turn_transaction_contract_v0 phases.
COMPLETED_PHASES = [
    "host_execute",
    "typed_result",
    "validation",
    "durable_writeback",
    "quota_spend",
    "scheduler_apply",
    "scheduler_ack",
]


def write_periodic_report_registry(project: Path, runtime: Path) -> Path:
    registry_path = project / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "state_file": "goal.md",
                        "control_plane": {
                            "periodic_report": {
                                "enabled": True,
                                "profile_preset": "weekly",
                                "route_ref": "loopx-concierge",
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry_path


def write_successor_run_history(runtime: Path) -> None:
    runs_dir = runtime / "goals" / GOAL_ID / "runs"
    runs_dir.mkdir(parents=True)
    runs = [
        {
            "generated_at": "2026-08-30T11:00:00Z",
            "goal_id": GOAL_ID,
            "agent_vision": {
                "schema_version": "goal_vision_replan_contract_v0",
                "agent_id": AGENT_ID,
                "state": "active",
                "vision_patch": {
                    "acceptance_summary": "Report slice frontier is bounded."
                },
            },
            "autonomous_replan_ack": {
                "recorded": True,
                "frontier_identity": "frontier-capability-2",
                "semantic_delta": {
                    "accepted": True,
                    "outcomes": ["fresh_vision_path_outcome"],
                    "trigger_kinds": ["vision_successor_required"],
                    "obligation_id": "replan-capability-2",
                },
            },
        },
        {
            "generated_at": "2026-08-30T10:00:00Z",
            "goal_id": GOAL_ID,
            "agent_vision": {
                "schema_version": "goal_vision_replan_contract_v0",
                "agent_id": AGENT_ID,
                "state": "vision_closed",
                "vision_patch": {
                    "acceptance_summary": "First report frontier accepted."
                },
            },
            "vision_checkpoint": {
                "schema_version": "vision_checkpoint_v0",
                "satisfied": True,
                "decision": "patched",
                "triggers": [
                    {
                        "kind": "material_delivery_outcome",
                        "delivery_outcome": "outcome_progress",
                    }
                ],
            },
        },
    ]
    (runs_dir / "index.jsonl").write_text(
        "".join(json.dumps(run) + "\n" for run in runs),
        encoding="utf-8",
    )


def write_turn_heartbeat_receipt(runtime: Path) -> None:
    append_rollout_event(
        rollout_event_log_path(runtime, GOAL_ID),
        build_rollout_event(
            goal_id=GOAL_ID,
            event_kind="quota_should_run",
            agent_id=AGENT_ID,
            todo_id=COMPLETED_TODO,
            run_id=TURN_ID,
            status="normal_run",
            summary="capability counterfactual heartbeat receipt",
            details={
                "stall_observation": "not_applicable",
                "todo_id": COMPLETED_TODO,
                "settlement_effect_id": EFFECT_ID,
            },
        ),
    )


def write_turn_journal(runtime: Path, *, observed: list[str]) -> None:
    """Write a settlement-legible journal the TS journal owner can validate."""

    turns_dir = runtime / "goals" / GOAL_ID / "turns"
    turns_dir.mkdir(parents=True, exist_ok=True)
    envelope: dict[str, object] = {
        "goal_id": GOAL_ID,
        "agent_id": AGENT_ID,
        "action": {"selected_todo": {"todo_id": COMPLETED_TODO}},
    }
    if observed:
        envelope["boundary"] = {"available_capabilities": list(observed)}
    journal = {
        "schema_version": "loopx_turn_journal_v0",
        "turn_key": TURN_KEY,
        "goal_id": GOAL_ID,
        "status": "committed",
        "completed_phases": list(COMPLETED_PHASES),
        "plan": {
            "transaction": {
                "turn_key": TURN_KEY,
                "turn_instance_id": TURN_ID,
                "settlement_plan": {
                    "schema_version": "quota_settlement_plan_v1",
                    "identity": {
                        "schema_version": "quota_settlement_identity_v0",
                        "effect_id": EFFECT_ID,
                        "goal_id": GOAL_ID,
                        "agent_id": AGENT_ID,
                        "todo_id": COMPLETED_TODO,
                        "turn_instance_id": TURN_ID,
                    },
                },
            },
            "turn_envelope": envelope,
        },
    }
    (turns_dir / f"{TURN_KEY.removeprefix('sha256:')}.json").write_text(
        json.dumps(journal),
        encoding="utf-8",
    )


def complete_todo_via_cli(
    tmp_path: Path,
    *,
    journal_capabilities: list[str],
    write_state: Callable[[Path], None],
) -> tuple[dict[str, object], Path, Path]:
    project = tmp_path / "repo"
    project.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    write_state(project)
    registry_path = write_periodic_report_registry(project, runtime)
    write_successor_run_history(runtime)
    write_turn_heartbeat_receipt(runtime)
    write_turn_journal(runtime, observed=journal_capabilities)

    captured = run_todo_completion_via_cli(registry_path, runtime)
    return captured, registry_path, runtime


def run_todo_completion_via_cli(
    registry_path: Path,
    runtime: Path,
) -> dict[str, object]:
    """Run todo completion against an existing settlement fixture."""

    args = build_parser().parse_args(
        [
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            COMPLETED_TODO,
            "--agent-id",
            AGENT_ID,
            "--turn-instance-id",
            TURN_ID,
            "--evidence",
            "capability counterfactual completion",
        ]
    )
    captured: dict[str, object] = {}
    exit_code = handle_todo_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=None,
        format_name="json",
        print_payload=lambda payload, *_a, **_k: captured.update(payload),
        append_cli_rollout_event=lambda *_a, **_k: {},
        post_writeback_hooks=periodic_report_post_writeback_hooks_for_goal(
            registry_path=registry_path,
            goal_id=GOAL_ID,
        ),
        post_writeback_projection_builder=(
            build_periodic_report_post_writeback_projection
        ),
    )
    assert exit_code == 0
    assert captured.get("ok") is True, captured
    return captured
