from __future__ import annotations

import pytest

from loopx.control_plane.todos.contract import (
    format_todo_metadata_line,
    parse_todo_metadata_line,
)
from loopx.control_plane.todos.active_state_todo_parser import (
    parse_active_state_todos,
)


def test_todo_metadata_round_trip_preserves_canonical_values() -> None:
    line = format_todo_metadata_line(
        todo_id="todo_schema001",
        status="open",
        task_class="advancement_task",
        action_kind="run_eval",
        task_domain="validation",
        capability_binding_ref="issue-fix:feasibility-a1b2c3d4",
        task_repository="git:github.com/owner/repo",
        continuation_policy="same_agent_non_delivery",
        required_write_scopes=["loopx/**", "tests/**"],
        required_capabilities=["network", "filesystem_write"],
        target_capabilities=["quality_receipt"],
        explore_result_node_refs=["result.primary"],
        decision_scope="direction:action:review",
        required_decision_scopes=["write_scope:project:loopx"],
        decision_outcome="approve",
        decision_scope_outcomes=[
            {
                "outcome": "approve",
                "decision_scope": "direction:action:review",
                "source_todo_id": "todo_source001",
            }
        ],
        claimed_by="codex-quality",
        last_actor_agent_id="codex-quality",
        bound_agent="codex-main",
        goal_bound=False,
        blocks_agent="codex-side",
        excluded_agents=["codex-other"],
        global_gate=False,
        unblocks_todo_id="todo_blocked001",
        successor_todo_ids=["todo_next001", "todo_next002"],
        resume_when="todo_done:todo_blocked001",
        no_followup=False,
        target_key="github-pr-1",
        monitor_effect_id="quota-monitor-poll:effect-001",
        cadence="30m",
        next_due_at="2026-07-28T01:00:00+08:00",
        expires_at="2026-07-29T01:00:00+08:00",
        last_checked_at="2026-07-28T00:30:00+08:00",
        result_hash="abc123",
        consecutive_no_change="2",
        material_change="false",
        material_change_generation=0,
        max_no_change_before_replan="3",
        note="focused validation passed",
        evidence="receipt cqr_123",
        reason="bounded refactor",
        completed_at="2026-07-28T00:40:00+08:00",
        updated_at="2026-07-28T00:41:00+08:00",
        superseded_by="todo_replacement001",
    )

    assert line is not None
    assert parse_todo_metadata_line(line) == {
        "todo_id": "todo_schema001",
        "status": "open",
        "task_class": "advancement_task",
        "action_kind": "run_eval",
        "task_domain": "validation",
        "capability_binding_ref": "issue-fix:feasibility-a1b2c3d4",
        "task_repository": "git:github.com/owner/repo",
        "continuation_policy": "same_agent_non_delivery",
        "required_write_scopes": ["loopx/**", "tests/**"],
        "required_capabilities": ["network", "filesystem_write"],
        "target_capabilities": ["quality_receipt"],
        "explore_result_node_refs": ["result.primary"],
        "decision_scope": {
            "schema_version": "decision_scope_v0",
            "kind": "direction",
            "granularity": "action",
            "scope_key": "review",
        },
        "required_decision_scopes": [
            {
                "schema_version": "decision_scope_v0",
                "kind": "write_scope",
                "granularity": "project",
                "scope_key": "loopx",
            }
        ],
        "decision_outcome": "approve",
        "decision_scope_outcomes": [
            {
                "schema_version": "todo_decision_scope_outcome_v0",
                "outcome": "approve",
                "decision_scope": {
                    "schema_version": "decision_scope_v0",
                    "kind": "direction",
                    "granularity": "action",
                    "scope_key": "review",
                },
                "source_todo_id": "todo_source001",
            }
        ],
        "claimed_by": "codex-quality",
        "last_actor_agent_id": "codex-quality",
        "bound_agent": "codex-main",
        "goal_bound": False,
        "blocks_agent": "codex-side",
        "excluded_agents": ["codex-other"],
        "global_gate": False,
        "unblocks_todo_id": "todo_blocked001",
        "successor_todo_ids": ["todo_next001", "todo_next002"],
        "resume_when": "todo_done:todo_blocked001",
        "no_followup": False,
        "target_key": "github-pr-1",
        "monitor_effect_id": "quota-monitor-poll:effect-001",
        "cadence": "30m",
        "next_due_at": "2026-07-28T01:00:00+08:00",
        "expires_at": "2026-07-29T01:00:00+08:00",
        "last_checked_at": "2026-07-28T00:30:00+08:00",
        "result_hash": "abc123",
        "consecutive_no_change": "2",
        "material_change": "false",
        "material_change_generation": "0",
        "max_no_change_before_replan": "3",
        "note": "focused validation passed",
        "evidence": "receipt cqr_123",
        "reason": "bounded refactor",
        "completed_at": "2026-07-28T00:40:00+08:00",
        "updated_at": "2026-07-28T00:41:00+08:00",
        "superseded_by": "todo_replacement001",
    }


def test_todo_metadata_round_trip_preserves_terminal_recovery_state() -> None:
    line = format_todo_metadata_line(
        todo_id="todo_terminal001",
        status="done",
        no_followup=True,
        completion_continuation="no_followup",
        completion_recovery="same_turn_terminal_closeout",
    )

    assert line is not None
    assert parse_todo_metadata_line(line) == {
        "todo_id": "todo_terminal001",
        "status": "done",
        "completion_continuation": "no_followup",
        "completion_recovery": "same_turn_terminal_closeout",
        "no_followup": True,
    }


def test_todo_metadata_round_trip_preserves_lifecycle_reentry_recovery() -> None:
    line = format_todo_metadata_line(
        todo_id="todo_terminal002",
        status="done",
        no_followup=True,
        completion_continuation="no_followup",
        completion_recovery="lifecycle_reentry_terminal_closeout",
    )

    assert line is not None
    assert parse_todo_metadata_line(line)["completion_recovery"] == (
        "lifecycle_reentry_terminal_closeout"
    )


def test_todo_metadata_parser_ignores_noncanonical_field_names() -> None:
    parsed = parse_todo_metadata_line(
        "  <!-- loopx:todo "
        "todo_id=todo_canonical001 "
        "required_write_scope=loopx%2F** "
        "required-capabilities=network "
        "target_capability=quality_receipt "
        "explore_result_node_ref=result.primary "
        "required_decision_scope=direction:action:review "
        "excluded_agent=codex-other "
        "successor_todo_id=todo_next001 "
        "legacy-status=done "
        "continuation_policy=same_agent_non_delivery "
        "-->"
    )

    assert parsed == {
        "todo_id": "todo_canonical001",
        "continuation_policy": "same_agent_non_delivery",
    }


def test_todo_metadata_parser_skips_invalid_canonical_values() -> None:
    assert parse_todo_metadata_line(
        "  <!-- loopx:todo todo_id=todo_valid001 status=unknown "
        "claimed_by=%2Fprivate required_capabilities=network -->"
    ) == {
        "todo_id": "todo_valid001",
        "required_capabilities": ["network"],
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"todo_id": "bad"}, "todo_id must use the public token shape"),
        ({"status": "unknown"}, "todo status must be one of"),
        (
            {"capability_binding_ref": "not-namespaced"},
            "capability_binding_ref must be a public-safe namespaced token",
        ),
        ({"required_write_scopes": ["/private"]}, "public-safe relative scope"),
        ({"decision_scope": "invalid"}, "decision_scope must use"),
        ({"excluded_agents": ["bad/value"]}, "public-safe agent tokens"),
        ({"successor_todo_ids": ["bad"]}, "successor_todo_ids must contain"),
        (
            {"completion_continuation": "implicit"},
            "completion_continuation must be one of",
        ),
        (
            {"completion_recovery": "best_effort"},
            "completion_recovery must be same_turn_terminal_closeout",
        ),
    ],
)
def test_todo_metadata_formatter_rejects_invalid_canonical_values(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        format_todo_metadata_line(**kwargs)


def test_todo_metadata_formatter_enforces_cross_field_constraints() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        format_todo_metadata_line(
            continuation_policy="independent_handoff",
            removed_continuation_policy="review_handoff",
        )

    with pytest.raises(ValueError, match="cannot also appear in excluded_agents"):
        format_todo_metadata_line(
            claimed_by="codex-quality",
            excluded_agents=["codex-quality"],
        )


def test_multi_subagent_todo_parser_projects_untruncated_admission_authority() -> None:
    lines = ["## Agent Todo", ""]
    for index in range(1, 19):
        todo_id = "todo_blocked_child" if index == 18 else f"todo_agent_{index}"
        validation = (
            " validation_command=python3%20-m%20pytest"
            if index == 2
            else ""
        )
        lines.extend(
            [
                f"- [ ] [P0] Inspect agent fixture {index}.",
                (
                    f"  <!-- loopx:todo todo_id={todo_id} status=open "
                    "task_class=advancement_task action_kind=inspect "
                    f"task_domain=code{validation} -->"
                ),
            ]
        )
    lines.extend(["", "## User Todo", ""])
    for index in range(1, 19):
        task_class = "user_gate" if index == 18 else "user_action"
        metadata = f"todo_id=todo_user_{index} status=open task_class={task_class}"
        if index == 18:
            metadata += " unblocks_todo_id=todo_blocked_child"
        lines.extend(
            [
                f"- [ ] [P1] Review user fixture {index}.",
                f"  <!-- loopx:todo {metadata} -->",
            ]
        )
    state_text = "\n".join(lines) + "\n"

    regular = parse_active_state_todos(state_text)
    adaptive = parse_active_state_todos(
        state_text,
        goal={
            "spawn_policy": {
                "mode": "multi_subagent",
                "allowed": True,
                "max_children": 2,
            }
        },
    )

    assert "task_orchestration_authority" not in regular["agent_todos"]
    assert len(adaptive["agent_todos"]["items"]) == 12
    assert len(adaptive["user_todos"]["items"]) == 12
    agent_authority = adaptive["agent_todos"]["task_orchestration_authority"]
    user_authority = adaptive["user_todos"]["task_orchestration_authority"]
    assert len(agent_authority["candidate_items"]) == 18
    assert agent_authority["candidate_items"][-1]["todo_id"] == "todo_blocked_child"
    declared_validation = agent_authority["candidate_items"][1]
    assert declared_validation["completion_validation_required"] is True
    assert "validation_command" not in declared_validation
    assert "validation_command_argv" not in declared_validation
    assert user_authority["user_blocker_items"] == [
        {
            "todo_id": "todo_user_18",
            "status": "open",
            "done": False,
            "task_class": "user_gate",
            "unblocks_todo_id": "todo_blocked_child",
        }
    ]
