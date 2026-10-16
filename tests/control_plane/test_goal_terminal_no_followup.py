from __future__ import annotations

import pytest

from loopx.bootstrap import (
    bootstrap_project,
    render_state_markdown,
    repair_missing_todo_source_sections,
)
from loopx.control_plane.coordination.local_authority import (
    CanonicalAuthorityRequired,
)
from loopx.control_plane.goals.goal_frontier import (
    build_goal_frontier_projection_from_summaries,
    goal_frontier_is_terminal_no_followup,
)
from loopx.control_plane.todos.active_state_todo_parser import (
    parse_active_state_todos,
)
from loopx.control_plane.todos.quota_summary import summarize_user_todos_for_quota
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.quota import build_quota_should_run


def _terminal_projection(state_text: str) -> dict[str, object]:
    parsed = parse_active_state_todos(state_text)
    user_todos = summarize_user_todos_for_quota(parsed.get("user_todos"))
    agent_todos = summarize_user_todos_for_quota(parsed.get("agent_todos"))
    return build_goal_frontier_projection_from_summaries(
        goal_id="goal-terminal-test",
        agent_id=None,
        user_todo_summary=user_todos,
        agent_todo_summary=agent_todos,
        work_lane_contract=None,
        replan_obligation=None,
    )


def _closed_todo_sources() -> dict[str, object]:
    return parse_active_state_todos(
        """\
## User Todo / Owner Review Reading Queue

## Agent Todo

- [x] [P0] Fix and validate the calculator add bug.
  <!-- loopx:todo todo_id=todo_fix status=done task_class=advancement_task no_followup=true evidence=calculator%20tests%20pass -->
"""
    )


def _missing_vision_checkpoint_run(*, required_resolution: list[str]) -> dict[str, object]:
    return {
        "classification": "validated_progress",
        "generated_at": "2026-07-29T00:00:00+08:00",
        "progress_scope": "goal",
        "vision_checkpoint": {
            "schema_version": "vision_checkpoint_v0",
            "agent_id": None,
            "required": True,
            "satisfied": False,
            "decision": "missing_required",
            "triggers": [{"kind": "material_delivery_outcome"}],
            "required_resolution": required_resolution,
        },
    }


def test_bootstrap_declares_both_todo_sources(tmp_path) -> None:
    state_text = render_state_markdown(
        project=tmp_path,
        goal_id="goal-terminal-test",
        adapter_kind="issue_fix_workflow_v0",
        objective="Fix the issue.",
        updated_at="2026-07-29T00:00:00+08:00",
        goal_doc=None,
        execution_profile=None,
    )

    parsed = parse_active_state_todos(state_text)

    assert parsed["user_todos"]["total_count"] == 0
    assert parsed["user_todos"]["source_proof"]["derived"] is True
    assert parsed["agent_todos"]["total_count"] == 0
    assert parsed["agent_todos"]["source_proof"]["derived"] is True


def test_existing_state_migration_preserves_recognized_legacy_source_heading() -> None:
    state_text = """\
## Owner Reading Queue

## Next Action

- Continue.
"""

    migrated, added_roles = repair_missing_todo_source_sections(state_text)

    assert added_roles == ["agent"]
    assert migrated.count("## Owner Reading Queue") == 1
    assert "## User Todo / Owner Review Reading Queue" not in migrated
    assert "## Agent Todo" in migrated


def test_bootstrap_rejects_existing_markdown_only_state_without_rewriting(tmp_path) -> None:
    state_file = tmp_path / ".codex/goals/goal-terminal-test/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    original = """\
# Active Goal State

## Objective

Preserve this existing state.

## Next Action

- Continue.
"""
    state_file.write_text(original, encoding="utf-8")
    registry_path = tmp_path / ".loopx/registry.json"
    runtime_root = tmp_path / ".loopx-runtime"

    with pytest.raises(CanonicalAuthorityRequired) as caught:
        bootstrap_project(
            project=tmp_path,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id="goal-terminal-test",
            objective="Do not replace the existing objective.",
            domain="issue-fix",
            role="primary",
            parent_goal_id=None,
            state_file=state_file,
            goal_doc=None,
            adapter_kind="issue_fix_workflow_v0",
            adapter_status="connected",
            next_probe=None,
            spawn_allowed=False,
            max_children=0,
            allowed_domains=[],
            write_scope=[],
            force=False,
            dry_run=False,
            sync_global=False,
        )

    assert caught.value.code == "canonical_authority_required"
    assert caught.value.payload["operation"] == "goal_create"
    assert caught.value.payload["legacy_fallback_used"] is False
    assert state_file.read_text(encoding="utf-8") == original
    assert not registry_path.exists()
    assert not runtime_root.exists()


def test_agent_only_no_followup_closes_explicit_empty_user_source() -> None:
    state_text = """\
## User Todo / Owner Review Reading Queue

## Agent Todo

- [x] [P0] Fix and validate the issue.
  <!-- loopx:todo todo_id=todo_fix status=done task_class=advancement_task no_followup=true note=Validated. -->
"""

    projection = _terminal_projection(state_text)

    assert projection["source_completeness"] == {
        "schema_version": "goal_terminal_source_completeness_v0",
        "user_todos": "valid",
        "agent_todos": "valid",
    }
    assert goal_frontier_is_terminal_no_followup(projection=projection) is True


def test_watch_only_monitor_does_not_block_terminal_convergence() -> None:
    state_text = """\
## User Todo / Owner Review Reading Queue

## Agent Todo

- [x] [P0] Complete the bounded delivery.
  <!-- loopx:todo todo_id=todo_delivery status=done task_class=advancement_task no_followup=true evidence=validated -->
- [ ] [P2] Observe public release liveness.
  <!-- loopx:todo todo_id=todo_liveness status=open task_class=continuous_monitor action_kind=monitor target_key=release cadence=1h next_due_at=2026-08-12T00%3A00%3A00Z watch_only=true -->
"""

    projection = _terminal_projection(state_text)

    assert projection["normalized_progress"]["agent_open_count"] == 0
    assert projection["normalized_progress"]["agent_monitor_open_count"] == 0
    assert goal_frontier_is_terminal_no_followup(projection=projection) is True


def test_agent_only_no_followup_fails_closed_when_user_source_is_missing() -> None:
    state_text = """\
## Agent Todo

- [x] [P0] Fix and validate the issue.
  <!-- loopx:todo todo_id=todo_fix status=done task_class=advancement_task no_followup=true note=Validated. -->
"""

    projection = _terminal_projection(state_text)

    assert "terminal_state" not in projection
    assert goal_frontier_is_terminal_no_followup(projection=projection) is False


def test_truncated_completed_source_uses_full_terminal_closure_proof() -> None:
    state_text = "\n".join(
        [
            "## User Todo / Owner Review Reading Queue",
            "",
            "## Agent Todo",
            "",
            *[
                (
                    f"- [x] [P0] Completed item {index}.\n"
                    f"  <!-- loopx:todo todo_id=todo_{index} status=done "
                    f"task_class=advancement_task no_followup={'true' if index == 13 else 'false'} "
                    "evidence=validated -->"
                )
                for index in range(1, 14)
            ],
        ]
    )

    parsed = parse_active_state_todos(state_text)
    agent_source = parsed["agent_todos"]
    agent_summary = summarize_user_todos_for_quota(agent_source)
    projection = build_goal_frontier_projection_from_summaries(
        goal_id="goal-terminal-test",
        agent_id=None,
        user_todo_summary=summarize_user_todos_for_quota(parsed["user_todos"]),
        agent_todo_summary=agent_summary,
        work_lane_contract=None,
        replan_obligation=None,
    )

    assert len(agent_source["items"]) == 12
    assert agent_source["total_count"] == 13
    assert goal_frontier_is_terminal_no_followup(projection=projection) is True


def test_no_followup_resolves_later_missing_vision_checkpoint() -> None:
    parsed = _closed_todo_sources()
    status = quota_status_payload(
        goal_id="goal-terminal-test",
        status="active",
        recommended_action="Goal complete; no further work is needed.",
        active_state_next_action=(
            "Goal complete: calculator add bug fixed, no further work needed."
        ),
        user_todos=parsed["user_todos"],
        agent_todos=parsed["agent_todos"],
        latest_runs=[
            _missing_vision_checkpoint_run(
                required_resolution=[
                    "write_vision_patch",
                    "record_unchanged_reason",
                    "record_no_followup",
                    "link_successor_or_supersede",
                ]
            )
        ],
        item_extra={
            "state_projection_gap": {
                "schema_version": "state_projection_gap_v0",
                "kind": "state_projection_gap",
                "requires_todo_expansion": True,
                "agent_open_count": 0,
                "user_open_count": 0,
                "target_roles": ["agent"],
            }
        },
    )

    decision = build_quota_should_run(status, goal_id="goal-terminal-test")

    assert decision["decision"] == "skip"
    assert decision["should_run"] is False
    assert decision["effective_action"] == "terminal_no_followup"
    assert decision["goal_frontier_projection"]["acceptance_gaps"] == []


def test_no_followup_does_not_hide_unresolved_vision_checkpoint() -> None:
    parsed = _closed_todo_sources()
    status = quota_status_payload(
        goal_id="goal-terminal-test",
        status="active",
        recommended_action="Resolve the vision checkpoint.",
        user_todos=parsed["user_todos"],
        agent_todos=parsed["agent_todos"],
        latest_runs=[
            _missing_vision_checkpoint_run(
                required_resolution=["write_vision_patch"]
            )
        ],
    )

    decision = build_quota_should_run(status, goal_id="goal-terminal-test")

    assert decision["decision"] == "autonomous_replan_required"
    assert decision["should_run"] is True
    assert decision["effective_action"] == "autonomous_replan_required"
    assert decision["goal_frontier_projection"]["acceptance_gaps"][0]["kind"] == (
        "vision_checkpoint_missing"
    )


def test_no_followup_does_not_hide_open_vision_acceptance() -> None:
    parsed = _closed_todo_sources()
    latest_run = _missing_vision_checkpoint_run(
        required_resolution=["record_no_followup"]
    )
    latest_run["agent_vision"] = {
        "schema_version": "goal_vision_replan_contract_v0",
        "agent_id": None,
        "state": "vision_drift_detected",
        "vision_patch": {
            "acceptance_summary": "Prove the broader acceptance contract.",
            "replan_trigger_summary": "The broader acceptance remains open.",
            "advancement_policy": "repeat_until_closed",
        },
    }
    status = quota_status_payload(
        goal_id="goal-terminal-test",
        status="active",
        recommended_action="Resolve the broader acceptance gap.",
        user_todos=parsed["user_todos"],
        agent_todos=parsed["agent_todos"],
        latest_runs=[latest_run],
    )

    decision = build_quota_should_run(status, goal_id="goal-terminal-test")

    assert decision["decision"] == "autonomous_replan_required"
    assert decision["should_run"] is True
    assert decision["goal_frontier_projection"]["acceptance_gaps"][0]["kind"] == (
        "vision_acceptance_gap"
    )


def test_structured_terminal_settlement_ignores_stale_next_action_repair() -> None:
    parsed = _closed_todo_sources()
    status = quota_status_payload(
        goal_id="goal-terminal-test",
        status="active",
        recommended_action="Stop; the bounded goal is complete.",
        active_state_next_action="Inspect an obsolete frontier.",
        user_todos=parsed["user_todos"],
        agent_todos=parsed["agent_todos"],
        latest_runs=[
            {
                "classification": "bounded_terminal_closeout",
                "generated_at": "2026-08-13T12:00:00+08:00",
                "agent_vision": {
                    "schema_version": "goal_vision_replan_contract_v0",
                    "state": "no_followup",
                    "vision_patch": {
                        "acceptance_summary": "The bounded goal is complete."
                    },
                    "path_delta": {
                        "outcome": "stop",
                        "evidence_refs": ["evidence:terminal-coverage"],
                    },
                },
            }
        ],
        item_extra={
            "state_projection_gap": {
                "schema_version": "state_projection_gap_v0",
                "kind": "state_projection_gap",
                "requires_todo_expansion": True,
                "agent_open_count": 0,
                "user_open_count": 0,
                "target_roles": ["agent"],
            }
        },
    )

    decision = build_quota_should_run(status, goal_id="goal-terminal-test")

    assert decision["decision"] == "skip"
    assert decision["effective_action"] == "terminal_no_followup"
    assert decision["goal_frontier_projection"]["acceptance_gaps"] == []
    assert "state_projection_gap_repair" not in decision
