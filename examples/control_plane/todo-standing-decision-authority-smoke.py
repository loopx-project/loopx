#!/usr/bin/env python3
"""Smoke-test durable standing decision receipts in status and quota."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.quota_fixtures import (  # noqa: E402
    quota_status_payload,
    quota_todo_item,
    quota_todo_summary,
)
from loopx.control_plane.todos.active_state_todo_parser import (  # noqa: E402
    parse_active_state_todos,
)
from loopx.control_plane.todos.completed_archive import (  # noqa: E402
    archive_completed_todo_lines,
)
from loopx.control_plane.todos.decision_scope import (  # noqa: E402
    build_required_decision_scope_consistency,
    standing_decision_authority_for_agent,
)
from loopx.control_plane.todos.standing_decision import (  # noqa: E402
    standing_decision_source_items,
)
from loopx.quota import build_quota_should_run  # noqa: E402

GOAL_ID = "standing-decision-authority-fixture"
PRIMARY_AGENT = "codex-main-control"
OTHER_AGENT = "codex-reviewer"
SCOPE = "write_scope:goal:benchmark_pr_self_merge"


def project_standing_authority(items, *, legacy_source_order=True, canonical_records=False):
    """Exercise the production source decoder and composed Todo read transaction."""
    from loopx.control_plane.todos.goal_todo_projection import source_todo_fields

    return source_todo_fields(
        [], lineage=[],
        standing_items=standing_decision_source_items(items, canonical_records=canonical_records),
        legacy_source_order=legacy_source_order and not canonical_records,
    ).get("standing_decision_authority")


def user_receipt(
    todo_id: str,
    *,
    outcome: str = "approve",
    blocks_agent: str = PRIMARY_AGENT,
    unblocks_todo_id: str | None = None,
    status: str = "done",
) -> dict:
    item = quota_todo_item(
        todo_id=todo_id,
        text="[P0] Decide the standing benchmark PR merge policy.",
        role="user",
        status=status,
        task_class="user_gate",
        blocks_agent=blocks_agent,
        decision_scope=SCOPE,
        decision_outcome=outcome,
    )
    if unblocks_todo_id:
        item["unblocks_todo_id"] = unblocks_todo_id
    return item


def agent_todo(*, claimed_by: str = PRIMARY_AGENT) -> dict:
    return quota_todo_item(
        todo_id="todo_benchmark_pr",
        text="[P0] Validate and self-merge one benchmark PR.",
        claimed_by=claimed_by,
        required_decision_scopes=[SCOPE],
    )


def state_text() -> str:
    return (
        "---\n"
        "status: active\n"
        "---\n\n"
        "# Standing Decision Fixture\n\n"
        "## User Todo\n\n"
        "- [x] [P0] Approve validated benchmark PR self-merge for this agent.\n"
        "  <!-- loopx:todo todo_id=todo_standing_approve status=done "
        "task_class=user_gate decision_scope=write_scope:goal:benchmark_pr_self_merge "
        "decision_outcome=approve blocks_agent=codex-main-control -->\n"
        "- [x] [P1] Ordinary completed reminder.\n"
        "  <!-- loopx:todo todo_id=todo_ordinary_done status=done "
        "task_class=user_action bound_agent=codex-main-control -->\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P0] Validate and self-merge one benchmark PR.\n"
        "  <!-- loopx:todo todo_id=todo_benchmark_pr status=open "
        "task_class=advancement_task claimed_by=codex-main-control "
        "required_decision_scopes=write_scope:goal:benchmark_pr_self_merge -->\n"
    )


def assert_parser_projects_receipt_outside_compact_limit() -> dict:
    parsed = parse_active_state_todos(state_text(), item_limit=0)
    authority = parsed["standing_decision_authority"]
    assert authority["active_count"] == 1, authority
    assert authority["entries"][0]["source_todo_id"] == "todo_standing_approve", authority
    assert authority["entries"][0]["active"] is True, authority
    return authority


def assert_receipt_is_agent_scoped_and_revocable(authority: dict) -> None:
    primary = standing_decision_authority_for_agent(
        authority,
        agent_id=PRIMARY_AGENT,
    )
    assert primary is not None and primary["active_count"] == 1, primary
    assert standing_decision_authority_for_agent(
        authority,
        agent_id=OTHER_AGENT,
    ) is None

    consistency = build_required_decision_scope_consistency(
        quota_todo_summary([agent_todo()], role="agent"),
        quota_todo_summary([], role="user"),
        agent_id=PRIMARY_AGENT,
        registered_agent_ids=[PRIMARY_AGENT, OTHER_AGENT],
        agent_source_items=[agent_todo()],
        user_source_items=[],
        standing_decision_authority=authority,
    )
    assert consistency["ok"] is True, consistency
    assert consistency["standing_authority_match_count"] == 1, consistency

    revoked = project_standing_authority(
        [
            user_receipt("todo_standing_approve"),
            user_receipt("todo_standing_reject", outcome="reject"),
        ]
    )
    assert revoked is not None and revoked["active_count"] == 0, revoked
    revoked_consistency = build_required_decision_scope_consistency(
        None,
        None,
        agent_id=PRIMARY_AGENT,
        registered_agent_ids=[PRIMARY_AGENT, OTHER_AGENT],
        agent_source_items=[agent_todo()],
        user_source_items=[],
        standing_decision_authority=revoked,
    )
    assert revoked_consistency["ok"] is False, revoked_consistency
    assert revoked_consistency["errors"][0]["reason_code"] == (
        "dangling_required_decision_scope"
    ), revoked_consistency


def assert_one_shot_and_non_done_gates_are_not_standing() -> None:
    authority = project_standing_authority(
        [
            user_receipt(
                "todo_linked_approve",
                unblocks_todo_id="todo_benchmark_pr",
            ),
            user_receipt("todo_deferred_approve", status="deferred"),
        ]
    )
    assert authority is None, authority


def assert_archive_retains_standing_receipt() -> None:
    archived = archive_completed_todo_lines(
        state_text().splitlines(keepends=True),
        role="user",
        max_active_done=1,
    )
    assert archived["changed"] is True, archived
    assert archived["moved_count"] == 1, archived
    assert archived["retained_standing_decision_count"] == 1, archived
    updated = "".join(archived["lines"])
    assert updated.index("todo_standing_approve") < updated.index("## Agent Todo"), updated
    assert updated.index("## Completed Work Archive") < updated.index(
        "todo_ordinary_done"
    ), updated


def assert_quota_consumes_typed_receipt(authority: dict) -> None:
    todo = agent_todo()
    agent_summary = quota_todo_summary([todo], role="agent")
    user_summary = quota_todo_summary([], role="user")
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action=todo["text"],
        agent_todos=agent_summary,
        user_todos=user_summary,
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [PRIMARY_AGENT, OTHER_AGENT],
        },
        item_extra={"standing_decision_authority": authority},
        project_asset_extra={"standing_decision_authority": authority},
    )
    payload = build_quota_should_run(
        status,
        goal_id=GOAL_ID,
        agent_id=PRIMARY_AGENT,
    )
    assert payload["should_run"] is True, payload
    assert payload["normal_delivery_allowed"] is True, payload
    assert payload["effective_action"] == "normal_run", payload
    assert "todo_decision_scope_consistency" not in payload, payload
    projected = payload["standing_decision_authority"]
    assert projected["agent_id"] == PRIMARY_AGENT, projected
    assert projected["active_count"] == 1, projected


def main() -> int:
    authority = assert_parser_projects_receipt_outside_compact_limit()
    assert_receipt_is_agent_scoped_and_revocable(authority)
    assert_one_shot_and_non_done_gates_are_not_standing()
    assert_archive_retains_standing_receipt()
    assert_quota_consumes_typed_receipt(authority)
    print("todo-standing-decision-authority-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
