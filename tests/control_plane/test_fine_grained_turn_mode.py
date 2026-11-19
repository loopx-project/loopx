from __future__ import annotations

import json
import os
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from loopx.bootstrap_command_pack import build_start_goal_guided_packet
from loopx.cli_commands.start_goal import _resolve_start_goal_input
from loopx.configure_goal import configure_goal
from loopx.control_plane.goals.goal_frontier import (
    build_goal_frontier_projection_context_from_status,
)
from loopx.control_plane.heartbeat.builder import (
    FINE_GRAINED_TURN_RULE,
    build_heartbeat_prompt,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
    quota_todo_summary,
)
from loopx.execution_profile import (
    build_execution_profile,
    compact_execution_profile,
    execution_profile_is_fine_grained,
)
from loopx.long_task_cadence import build_long_task_cadence_hint

GOAL_ID = "fine-grained-fixture"
AGENT_ID = "codex-fine-agent"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _start_args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "goal_text": None,
        "slash_command_arguments": None,
        "capability_route": None,
        "fine_grained": False,
    }
    values.update(overrides)
    return Namespace(**values)


def test_leading_fine_grained_switch_is_lossless_and_composes_with_route() -> None:
    assert _resolve_start_goal_input(
        _start_args(goal_text="Direct fine goal.", fine_grained=True)
    ) == ("Direct fine goal.", None, True, False)

    goal_text, route, fine_grained, goal_loop = _resolve_start_goal_input(
        _start_args(
            slash_command_arguments=(
                "--fine-grained --capability-route issue-fix "
                "Investigate  two branches without recomposing this text."
            )
        )
    )

    assert goal_loop is False
    assert fine_grained is True
    assert route == "issue-fix"
    assert goal_text == "Investigate  two branches without recomposing this text."

    reverse = _resolve_start_goal_input(
        _start_args(
            slash_command_arguments=(
                "--capability-route=issue-fix --fine-grained Keep exact goal text."
            )
        )
    )
    assert reverse == ("Keep exact goal text.", "issue-fix", True, False)


def test_public_cli_accepts_direct_fine_start_and_bootstrap_flags(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "registry.json"
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    started = subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "loopx"),
            "--format",
            "json",
            "--registry",
            str(registry),
            "start-goal",
            "--guided",
            "--project",
            str(project),
            "--goal-id",
            GOAL_ID,
            "--host-surface",
            "ark-managed-agent",
            "--fine-grained",
            "--goal-text",
            "Deliver one checkpoint.",
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )
    start_payload = json.loads(started.stdout)
    assert start_payload["command_pack"]["goal_start_contract"]["turn_mode"] == (
        "fine_grained"
    )

    bootstrapped = subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "loopx"),
            "--format",
            "json",
            "--registry",
            str(registry),
            "bootstrap",
            "--project",
            str(project),
            "--goal-id",
            GOAL_ID,
            "--objective",
            "Deliver one checkpoint.",
            "--fine-grained",
            "--dry-run",
            "--no-global-sync",
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )
    bootstrap_payload = json.loads(bootstrapped.stdout)
    assert bootstrap_payload["execution_profile"]["turn_granularity"] == "fine"
    assert bootstrap_payload["execution_profile"]["minimum_scale"] == "single_surface"


def test_fine_profile_keeps_small_checkpoints_inside_a_coherent_slice() -> None:
    standard = build_execution_profile()
    fine = build_execution_profile(turn_granularity="fine")

    assert "turn_granularity" not in standard
    assert execution_profile_is_fine_grained(standard) is False
    assert execution_profile_is_fine_grained(compact_execution_profile(fine)) is True
    assert fine["minimum_scale"] == "single_surface"
    assert fine["planning_granularity"] == "fine_checkpoint"
    assert fine["turn_work_budget"] == "coherent_slice"
    assert fine["checkpoint_accounting"] == "advancement_only"
    assert fine["degradation_policy"] == {
        "small_scale_streak_threshold": 5,
        "on_degradation": "review_direction_after_bounded_chain",
    }
    cadence = build_long_task_cadence_hint(
        execution_profile=fine,
        latest_runs=[
            {
                "delivery_batch_scale": "single_surface",
                "delivery_outcome": "validated_progress",
            }
        ],
    )
    assert cadence["recommendation"] == "keep"
    assert cadence["reason_codes"] == ["fine_checkpoint_within_slice"]

    bounded_chain = build_long_task_cadence_hint(
        execution_profile=fine,
        latest_runs=[
            {
                "delivery_batch_scale": "single_surface",
                "delivery_outcome": "validated_progress",
            }
            for _ in range(5)
        ],
    )
    assert bounded_chain["recommendation"] == "replan"
    assert bounded_chain["reason_codes"] == ["fine_direction_review_due"]


def test_fine_packet_plans_one_checkpoint_but_budgets_a_coherent_slice(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    fine = build_start_goal_guided_packet(
        project=project,
        goal_id=GOAL_ID,
        agent_id=None,
        cli_bin="loopx",
        host_surface="ark-managed-agent",
        goal_text="Explore a branching integration safely.",
        fine_grained=True,
        include_command_pack_detail=True,
    )
    contract = fine["command_pack"]["goal_start_contract"]
    commands = fine["command_pack"]["commands"]
    steps = fine["guided_transaction"]["ordered_steps"]

    assert contract["turn_mode"] == "fine_grained"
    assert contract["fine_grained"] == {
        "todo_granularity": "small_checkpoint",
        "turn_work_budget": "coherent_slice",
        "turn_boundary": "settle_after_coherent_slice",
        "replan": "direction_change_or_bounded_chain",
        "checkpoint_accounting": "advancement_only",
    }
    assert contract["planner"]["maximum_runnable_todos_written_ahead"] == 1
    assert "--fine-grained" in commands["goal_start_connect_if_needed"]
    assert (
        "--execution-turn-granularity fine"
        in commands["goal_start_configure_turn_granularity"]
    )
    assert any(step["id"] == "configure_fine_grained_turn_mode" for step in steps)
    assert "existing replan obligation/ACK path" in commands["goal_start_plan_prompt"]
    assert "write exactly one current" in commands["goal_start_plan_prompt"]
    assert "one or more causally related" in commands["goal_start_plan_prompt"]
    assert "broad goal 2-5" not in commands["goal_start_plan_prompt"]
    assert fine["guided_transaction"]["checkpoint_policy"] == {
        "task_frontier": "agent_advancement_todos_only",
        "protocol_step_todo_projection": "forbidden",
        "protocol_step_advancement_checkpoint": False,
        "protocol_step_turn_settlement": False,
        "settlement": "once_after_task_facing_slice",
    }

    standard = build_start_goal_guided_packet(
        project=project,
        goal_id=GOAL_ID,
        agent_id=None,
        cli_bin="loopx",
        host_surface="ark-managed-agent",
        goal_text="Explore a branching integration safely.",
        include_command_pack_detail=True,
    )
    standard_contract = standard["command_pack"]["goal_start_contract"]
    assert "turn_mode" not in standard_contract
    assert "fine_grained" not in standard_contract
    assert "--fine-grained" not in standard["command_pack"]["canonical_cli_command"]
    assert not any(
        step["id"] == "configure_fine_grained_turn_mode"
        for step in standard["guided_transaction"]["ordered_steps"]
    )


def test_fine_heartbeat_rule_is_opt_in_only() -> None:
    standard = build_heartbeat_prompt(goal_id=GOAL_ID, thin=True)
    explicit_standard = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        thin=True,
        turn_granularity="standard",
    )
    fine = build_heartbeat_prompt(
        goal_id=GOAL_ID,
        thin=True,
        turn_granularity="fine",
    )

    assert standard == explicit_standard
    assert FINE_GRAINED_TURN_RULE not in standard["task_body"]
    assert fine["turn_mode"] == "fine_grained"
    assert fine["turn_granularity"] == "fine"
    assert FINE_GRAINED_TURN_RULE in fine["task_body"]
    assert "one or more causally related" in fine["task_body"]
    assert "settle the turn once" in fine["task_body"]
    assert "Protocol/setup and capability re-entry" in fine["task_body"]
    assert "independently verifiable checkpoint" in fine["task_body"]
    assert "small verifiable checkpoint" not in fine["task_body"]
    assert "After each completion inspect fresh evidence" in fine["task_body"]


def test_heartbeat_cli_reads_sticky_fine_mode_from_registry(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text("# Active Goal State\n", encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state.relative_to(project)),
                        "execution_profile": build_execution_profile(
                            turn_granularity="fine"
                        ),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "loopx"),
            "--format",
            "json",
            "--registry",
            str(registry),
            "heartbeat-prompt",
            "--thin",
            "--goal-id",
            GOAL_ID,
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["schema_version"] == "heartbeat_agent_input_v1"
    assert "turn_mode" not in payload
    assert "turn_granularity" not in payload
    assert FINE_GRAINED_TURN_RULE in payload["task_body"]


def _fine_frontier_context_after_completions(
    completion_count: int, *, execution_profile: dict | None = None
) -> dict:
    successor_id = "todo_checkpoint_successor"
    completed = [
        quota_todo_item(
            todo_id=f"todo_checkpoint_{index}",
            index=index,
            text=f"[P0] Complete checkpoint {index}.",
            status="done",
            claimed_by=AGENT_ID,
            completed_at=f"2026-08-11T09:0{index}:00+08:00",
            successor_todo_ids=[successor_id],
        )
        for index in range(1, completion_count + 1)
    ]
    successor = quota_todo_item(
        todo_id=successor_id,
        index=completion_count + 1,
        text="[P0] Continue the current evidence direction.",
        claimed_by=AGENT_ID,
    )
    summary = quota_todo_summary([*completed, successor], role="agent")
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Continue the current evidence direction.",
        agent_todos=summary,
        project_asset_extra={
            "execution_profile": (
                execution_profile if execution_profile is not None
                else build_execution_profile(turn_granularity="fine")
            )
        },
        latest_runs=[
            {
                "generated_at": "2026-08-11T08:00:00+08:00",
                "agent_id": AGENT_ID,
                "agent_vision": {
                    "schema_version": "agent_vision_v0",
                    "agent_id": AGENT_ID,
                    "state": "active",
                    "vision_patch": {},
                },
            }
        ],
    )
    item = status["attention_queue"]["items"][0]
    return build_goal_frontier_projection_context_from_status(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        status_payload=status,
        item=item,
        project_asset=item["project_asset"],
        user_todo_summary=item["user_todos"],
        agent_todo_summary=summary,
        work_lane_contract={"lane": "advancement_task", "must_attempt_work": True},
        neutral_replan_ack_classifications=set(),
        registered_agent_ids=[AGENT_ID],
        goal_status="active",
    )


def test_fine_completion_replans_after_a_bounded_chain_not_each_todo() -> None:
    first = _fine_frontier_context_after_completions(1)
    assert first["acceptance_gaps"] == []
    assert first["replan_obligation"] is None

    bounded_chain = _fine_frontier_context_after_completions(5)
    gaps = bounded_chain["acceptance_gaps"]
    assert len(gaps) == 1
    assert gaps[0]["kind"] == "vision_outcome_checkpoint_required"
    assert gaps[0]["completed_todo_threshold"] == 5
    assert "replan_cadence" not in gaps[0]
    assert bounded_chain["replan_obligation"]["required"] is True


def test_configure_goal_persists_and_can_revert_fine_mode(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "execution_profile": build_execution_profile(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    applied = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        execution_turn_granularity="fine",
        execute=True,
    )
    assert applied["changed_fields"] == ["execution_profile"]
    persisted = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    assert persisted["execution_profile"]["turn_granularity"] == "fine"

    reverted = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        execution_turn_granularity="standard",
        execute=True,
    )
    assert reverted["changed_fields"] == ["execution_profile"]
    persisted = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    assert "turn_granularity" not in persisted["execution_profile"]
    assert "planning_granularity" not in persisted["execution_profile"]
    assert "turn_work_budget" not in persisted["execution_profile"]
    assert "checkpoint_accounting" not in persisted["execution_profile"]


@pytest.mark.parametrize("mode", ["standard", "fine"])
@pytest.mark.parametrize("threshold", [1, 2, 3, 4, 5])
def test_configured_completion_cadence_reaches_the_active_frontier(mode, threshold):
    profile = build_execution_profile(turn_granularity=mode)
    profile["replan_after_completed_todos"] = threshold
    before = _fine_frontier_context_after_completions(
        threshold - 1, execution_profile=compact_execution_profile(profile)
    )
    assert before["acceptance_gaps"] == []
    assert before["replan_obligation"] is None
    due = _fine_frontier_context_after_completions(
        threshold, execution_profile=compact_execution_profile(profile)
    )
    gap = due["acceptance_gaps"][0]
    assert gap["source"] == "recent_completed_advancement_todo"
    assert gap["completed_todo_threshold"] == threshold
    assert gap["required_path_outcomes"] == ["continue", "no_change", "replan"]
    assert due["replan_obligation"]["required"] is True
