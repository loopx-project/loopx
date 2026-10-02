#!/usr/bin/env python3
"""Smoke-test multi-agent user gate scope enforcement."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.contract import check_contract  # noqa: E402
from loopx.control_plane.todos.todo_summary import active_state_todo_attention_item  # noqa: E402
from loopx.quota import build_quota_should_run  # noqa: E402
from loopx.status import collect_status, parse_active_state_todos  # noqa: E402
from loopx.todos import add_goal_todo, complete_goal_todo, update_goal_todo  # noqa: E402


GOAL_ID = "user-gate-scope-smoke"
PRIMARY_AGENT = "codex-main-control"
SIDE_AGENT = "codex-product-capability"


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    repo = root / "repo"
    repo.mkdir()
    state = repo / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "updated_at: 2026-06-26T00:00:00+00:00",
                "---",
                "",
                "## Agent Todo",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    registry = root / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(root / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(repo),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "adapter": {"kind": "harness_self_improvement"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [PRIMARY_AGENT, SIDE_AGENT],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return repo, state, registry


def assert_raises_message(action: Callable[[], object], expected: str) -> None:
    try:
        action()
    except ValueError as exc:
        assert expected in str(exc), str(exc)
        return
    raise AssertionError(f"expected ValueError containing {expected!r}")


def insert_before_agent_section(state: Path, block: str) -> None:
    text = state.read_text(encoding="utf-8")
    marker = "\n## Agent Todo\n"
    assert marker in text, text
    state.write_text(text.replace(marker, f"\n{block.rstrip()}\n{marker}", 1), encoding="utf-8")


def assert_user_action_does_not_drive_active_state_attention() -> None:
    item = active_state_todo_attention_item(
        {"id": GOAL_ID},
        {
            "user_todos": {
                "open_count": 1,
                "first_open_items": [
                    {
                        "text": "Track a non-blocking owner note.",
                        "task_class": "user_action",
                    }
                ],
            },
            "agent_todos": {
                "open_count": 1,
                "first_open_items": [
                    {
                        "text": "Deliver a product-capability slice.",
                        "task_class": "advancement_task",
                    }
                ],
            },
        },
        None,
        public_safe_compact_text=lambda value, limit=320: str(value or "").strip()[:limit] or None,
        first_open_todo_text=lambda summary: (
            str((summary.get("first_open_items") or [{}])[0].get("text") or "")
            if isinstance(summary, dict) and summary.get("first_open_items")
            else None
        ),
        todo_summary_open_count=lambda summary: int(summary.get("open_count") or 0) if isinstance(summary, dict) else 0,
        goal_lifecycle_fields=lambda _goal, _run: {},
        attention_item=lambda **kwargs: kwargs,
    )
    assert item is not None, item
    assert item["status"] == "active_state_agent_todo", item
    assert item["waiting_on"] == "codex", item

    gate_item = active_state_todo_attention_item(
        {"id": GOAL_ID},
        {
            "user_todos": {
                "open_count": 1,
                "first_open_items": [
                    {
                        "text": "Approve the side-agent delivery.",
                        "task_class": "user_gate",
                        "blocks_agent": SIDE_AGENT,
                    }
                ],
            },
            "agent_todos": {
                "open_count": 1,
                "first_open_items": [
                    {
                        "text": "Deliver a product-capability slice.",
                        "task_class": "advancement_task",
                    }
                ],
            },
        },
        None,
        public_safe_compact_text=lambda value, limit=320: str(value or "").strip()[:limit] or None,
        first_open_todo_text=lambda summary: (
            str((summary.get("first_open_items") or [{}])[0].get("text") or "")
            if isinstance(summary, dict) and summary.get("first_open_items")
            else None
        ),
        todo_summary_open_count=lambda summary: int(summary.get("open_count") or 0) if isinstance(summary, dict) else 0,
        goal_lifecycle_fields=lambda _goal, _run: {},
        attention_item=lambda **kwargs: kwargs,
    )
    assert gate_item is not None, gate_item
    assert gate_item["status"] == "active_state_user_gate", gate_item
    assert gate_item["waiting_on"] == "controller", gate_item


def main() -> int:
    assert_user_action_does_not_drive_active_state_attention()

    with tempfile.TemporaryDirectory(prefix="loopx-user-gate-scope-") as tmp:
        root = Path(tmp)
        repo, state, registry = write_fixture(root)

        assert_raises_message(
            lambda: add_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                role="user",
                text="Track a non-blocking owner note.",
                dry_run=True,
            ),
            "user todo requires explicit --task-class",
        )

        assert_raises_message(
            lambda: add_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                role="user",
                text="Track a non-blocking owner note.",
                task_class="user_action",
                dry_run=True,
            ),
            "multi-agent user todo requires an explicit binding",
        )
        assert_raises_message(
            lambda: add_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                role="user",
                text="Do not overload execution ownership as user routing.",
                task_class="user_action",
                claimed_by=SIDE_AGENT,
                dry_run=True,
            ),
            "claimed_by is execution ownership for agent todos",
        )

        user_action = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="user",
            text="Track a non-blocking owner note.",
            task_class="user_action",
            agent_id=SIDE_AGENT,
        )
        assert user_action["task_class"] == "user_action", user_action
        assert user_action["bound_agent"] == SIDE_AGENT, user_action
        assert user_action["claimed_by"] is None, user_action

        goal_bound_action = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="user",
            text="Track a goal-wide owner note.",
            task_class="user_action",
            goal_bound=True,
            dry_run=True,
        )
        assert goal_bound_action["goal_bound"] is True, goal_bound_action

        other_lane_only_status = collect_status(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
            goal_id=GOAL_ID,
        )
        other_lane_only_quota = build_quota_should_run(
            other_lane_only_status,
            goal_id=GOAL_ID,
            agent_id=PRIMARY_AGENT,
        )
        other_lane_only_summary = other_lane_only_quota["user_todo_summary"]
        assert other_lane_only_summary["open_count"] == 0, other_lane_only_quota
        assert other_lane_only_summary["all_open_count"] == 1, other_lane_only_quota
        assert other_lane_only_summary[
            "other_agent_bound_user_action_open_count"
        ] == 1, other_lane_only_quota
        user_action_override = other_lane_only_quota[
            "agent_scoped_user_action_override"
        ]
        assert user_action_override["to_state"] == "waiting", user_action_override
        assert "quota_patch" not in user_action_override, user_action_override
        assert "item_patch" not in user_action_override, user_action_override
        assert other_lane_only_quota["interaction_contract"]["user_channel"][
            "notify"
        ] == "DONT_NOTIFY", other_lane_only_quota
        assert other_lane_only_quota["interaction_contract"]["user_channel"].get(
            "actions", []
        ) == [], other_lane_only_quota

        assert_raises_message(
            lambda: add_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                role="user",
                text="Try to make a non-blocking action scoped.",
                task_class="user_action",
                blocks_agent=SIDE_AGENT,
                dry_run=True,
            ),
            "user_action is non-blocking",
        )

        side_agent_todo = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="Deliver a product-capability slice.",
            task_class="advancement_task",
            claimed_by=SIDE_AGENT,
        )
        assert side_agent_todo["claimed_by"] == SIDE_AGENT, side_agent_todo

        blocked_agent_todo = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="Clean up the exact approved duplicate records.",
            status="blocked",
            task_class="advancement_task",
            claimed_by=SIDE_AGENT,
        )
        approval = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="user",
            text="Authorize the exact duplicate cleanup.",
            task_class="user_action",
            bound_agent=SIDE_AGENT,
            unblocks_todo_id=blocked_agent_todo["todo_id"],
        )
        assert_raises_message(
            lambda: complete_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                todo_id=approval["todo_id"],
                role="user",
                agent_id=PRIMARY_AGENT,
                evidence="wrong-lane response attempt",
                dry_run=True,
            ),
            "response continuation is bound",
        )
        approval_completed = complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=approval["todo_id"],
            role="user",
            agent_id=SIDE_AGENT,
            evidence="user authorized the exact bounded cleanup",
        )
        assert approval_completed["unblock_resume"]["state"] == "resumed", approval_completed
        assert approval_completed["unblock_resume"]["target_todo_id"] == blocked_agent_todo["todo_id"], approval_completed
        resumed = next(
            item
            for item in parse_active_state_todos(state.read_text(encoding="utf-8"))["agent_todos"]["items"]
            if item["todo_id"] == blocked_agent_todo["todo_id"]
        )
        assert resumed["status"] == "open", resumed
        assert resumed["claimed_by"] == SIDE_AGENT, resumed

        multiply_blocked = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="Run a cleanup that needs two exact user actions.",
            status="blocked",
            task_class="advancement_task",
            claimed_by=SIDE_AGENT,
        )
        approvals = [
            add_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                role="user",
                text=f"Authorize bounded cleanup part {part}.",
                task_class="user_action",
                bound_agent=SIDE_AGENT,
                unblocks_todo_id=multiply_blocked["todo_id"],
            )
            for part in ("one", "two")
        ]
        first_approval = complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=approvals[0]["todo_id"],
            role="user",
            agent_id=SIDE_AGENT,
            evidence="first exact authorization",
        )
        assert first_approval["unblock_resume"]["state"] == "other_user_blockers_active", first_approval
        assert first_approval["unblock_resume"]["remaining_user_blocker_todo_ids"] == [
            approvals[1]["todo_id"]
        ], first_approval
        second_approval = complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=approvals[1]["todo_id"],
            role="user",
            agent_id=SIDE_AGENT,
            evidence="second exact authorization",
        )
        assert second_approval["unblock_resume"]["state"] == "resumed", second_approval

        status_payload = collect_status(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
            goal_id=GOAL_ID,
        )
        indexed_target = next(
            item
            for item in status_payload["todo_index"]["items"]
            if item.get("todo_id") == blocked_agent_todo["todo_id"]
        )
        assert indexed_target["status"] == "open", indexed_target
        quota_payload = build_quota_should_run(
            status_payload,
            goal_id=GOAL_ID,
            agent_id=SIDE_AGENT,
        )
        user_summary = quota_payload.get("user_todo_summary")
        assert isinstance(user_summary, dict), quota_payload
        assert user_summary["user_action_open_count"] == 1, user_summary
        assert user_summary["gate_open_items"] == [], user_summary
        executable_ids = {
            item.get("todo_id")
            for item in quota_payload["agent_todo_summary"]["first_executable_items"]
        }
        assert blocked_agent_todo["todo_id"] in executable_ids, quota_payload
        assert multiply_blocked["todo_id"] in executable_ids, quota_payload

        primary_quota = build_quota_should_run(
            status_payload,
            goal_id=GOAL_ID,
            agent_id=PRIMARY_AGENT,
        )
        primary_user_summary = primary_quota["user_todo_summary"]
        assert primary_user_summary.get("user_action_open_count", 0) == 0, primary_user_summary
        assert primary_user_summary["other_agent_bound_user_action_open_count"] == 1, primary_user_summary
        assert primary_user_summary["other_agent_bound_user_action_items"][0][
            "todo_id"
        ] == user_action["todo_id"], primary_user_summary
        assert all(
            "Track a non-blocking owner note." not in action
            for action in primary_quota["interaction_contract"]["user_channel"].get(
                "actions", []
            )
        ), primary_quota

        assert_raises_message(
            lambda: add_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                role="user",
                text="Approve the product-capability PR merge.",
                task_class="user_gate",
                dry_run=True,
            ),
            "multi-agent user_gate requires an explicit scope",
        )

        scoped = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="user",
            text="Approve the product-capability PR merge.",
            task_class="user_gate",
            agent_id=SIDE_AGENT,
        )
        assert scoped["blocks_agent"] == SIDE_AGENT, scoped
        assert scoped["bound_agent"] == SIDE_AGENT, scoped
        assert f"blocks_agent={SIDE_AGENT}" in state.read_text(encoding="utf-8")
        assert f"bound_agent={SIDE_AGENT}" in state.read_text(encoding="utf-8")

        global_gate = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="user",
            text="Approve pausing every registered agent.",
            task_class="user_gate",
            global_gate=True,
        )
        assert global_gate["global_gate"] is True, global_gate
        assert global_gate["goal_bound"] is True, global_gate
        assert "global_gate=true" in state.read_text(encoding="utf-8")
        assert "goal_bound=true" in state.read_text(encoding="utf-8")

        agent_todo = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="Prepare release notes for review.",
            task_class="advancement_task",
            claimed_by=PRIMARY_AGENT,
        )
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=str(agent_todo["todo_id"]),
            role="agent",
            claimed_by=PRIMARY_AGENT,
            agent_id=PRIMARY_AGENT,
            evidence="fixture validation",
            next_user_todo="Approve publishing the release notes.",
            next_user_task_class="user_gate",
        )
        state_text = state.read_text(encoding="utf-8")
        assert "Approve publishing the release notes." in state_text, state_text
        assert f"blocks_agent={PRIMARY_AGENT}" in state_text, state_text
        assert f"bound_agent={PRIMARY_AGENT}" in state_text, state_text

        assert_raises_message(
            lambda: update_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                todo_id=str(agent_todo["todo_id"]),
                role="agent",
                agent_id=PRIMARY_AGENT,
                global_gate=True,
                dry_run=True,
            ),
            "global_gate is only valid for user_gate todos",
        )

        clean_check = check_contract(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
        )
        assert clean_check["ok"] is True, clean_check

        insert_before_agent_section(
            state,
            "- [ ] Approve an intentionally unscoped gate.\n"
            "  <!-- loopx:todo todo_id=todo_unscoped_gate status=open task_class=user_gate -->",
        )
        bad_check = check_contract(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
        )
        assert bad_check["ok"] is False, bad_check
        assert any("todo_unscoped_gate" in item for item in bad_check["errors"]), bad_check

        closed = update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id="todo_unscoped_gate",
            role="user",
            agent_id=PRIMARY_AGENT,
            status="done",
        )
        assert closed["changed"] is True, closed
        repaired_check = check_contract(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
        )
        assert repaired_check["ok"] is True, repaired_check

        insert_before_agent_section(
            state,
            "- [ ] Track an unbound non-blocking user action.\n"
            "  <!-- loopx:todo todo_id=todo_unbound_action status=open task_class=user_action -->",
        )
        unbound_action_check = check_contract(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
        )
        assert unbound_action_check["ok"] is False, unbound_action_check
        assert any(
            "todo_unbound_action" in item and "requires bound_agent" in item
            for item in unbound_action_check["errors"]
        ), unbound_action_check
        rebound_action = update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id="todo_unbound_action",
            role="user",
            agent_id=PRIMARY_AGENT,
            bound_agent=SIDE_AGENT,
        )
        assert rebound_action["bound_agent"] == SIDE_AGENT, rebound_action
        rebound_check = check_contract(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
        )
        assert rebound_check["ok"] is True, rebound_check

        insert_before_agent_section(
            state,
            "- [ ] Resolve a mismatched gate binding.\n"
            f"  <!-- loopx:todo todo_id=todo_mismatched_gate_binding status=open "
            f"task_class=user_gate blocks_agent={SIDE_AGENT} "
            f"bound_agent={PRIMARY_AGENT} -->",
        )
        mismatched_binding_check = check_contract(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
        )
        assert mismatched_binding_check["ok"] is False, mismatched_binding_check
        assert any(
            "todo_mismatched_gate_binding" in item
            and "must bind to blocks_agent" in item
            for item in mismatched_binding_check["errors"]
        ), mismatched_binding_check
        repaired_gate_binding = update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id="todo_mismatched_gate_binding",
            role="user",
            agent_id=PRIMARY_AGENT,
            bound_agent=SIDE_AGENT,
        )
        assert repaired_gate_binding["bound_agent"] == SIDE_AGENT, repaired_gate_binding
        repaired_binding_check = check_contract(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
        )
        assert repaired_binding_check["ok"] is True, repaired_binding_check

        insert_before_agent_section(
            state,
            "- [ ] Track an intentionally untyped user todo.\n"
            "  <!-- loopx:todo todo_id=todo_untyped_user status=open -->",
        )
        untyped_check = check_contract(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
        )
        assert untyped_check["ok"] is False, untyped_check
        assert any("todo_untyped_user" in item and "requires task_class" in item for item in untyped_check["errors"]), untyped_check

        closed_untyped = update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id="todo_untyped_user",
            role="user",
            agent_id=PRIMARY_AGENT,
            status="done",
        )
        assert closed_untyped["changed"] is True, closed_untyped

        insert_before_agent_section(
            state,
            "- [ ] Approve a gate with an unregistered blocked agent.\n"
            "  <!-- loopx:todo todo_id=todo_bad_agent status=open task_class=user_gate blocks_agent=codex-unknown -->\n"
            "- [ ] Approve a gate with conflicting scope.\n"
            f"  <!-- loopx:todo todo_id=todo_both_scopes status=open task_class=user_gate blocks_agent={SIDE_AGENT} global_gate=true -->",
        )
        invalid_scope_check = check_contract(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
        )
        assert invalid_scope_check["ok"] is False, invalid_scope_check
        assert any("todo_bad_agent" in item and "not registered" in item for item in invalid_scope_check["errors"]), invalid_scope_check
        assert any("todo_both_scopes" in item and "cannot set both" in item for item in invalid_scope_check["errors"]), invalid_scope_check

        state.write_text(
            state.read_text(encoding="utf-8")
            + "\n- [ ] Repair a removed continuation value.\n"
            + "  <!-- loopx:todo todo_id=todo_removed_continuation status=open task_class=advancement_task action_kind=review continuation_policy=review_handoff -->\n"
            + "- [ ] Repair removed agent gate routing.\n"
            + f"  <!-- loopx:todo todo_id=todo_removed_agent_gate status=open task_class=advancement_task blocks_agent={SIDE_AGENT} -->\n"
            + "- [ ] Repair an unknown executor exclusion.\n"
            + "  <!-- loopx:todo todo_id=todo_unknown_exclusion status=open task_class=advancement_task excluded_agents=codex-unknown -->\n"
            + "- [ ] Repair malformed executor exclusion metadata.\n"
            + "  <!-- loopx:todo todo_id=todo_malformed_exclusion status=open task_class=advancement_task excluded_agents=%%% -->\n",
            encoding="utf-8",
        )
        hard_cut_check = check_contract(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
        )
        assert hard_cut_check["ok"] is False, hard_cut_check
        assert any(
            "todo_removed_continuation" in item and "removed continuation_policy" in item
            for item in hard_cut_check["errors"]
        ), hard_cut_check
        assert any(
            "todo_removed_agent_gate" in item and "removed blocks_agent routing" in item
            for item in hard_cut_check["errors"]
        ), hard_cut_check
        assert any(
            "todo_unknown_exclusion" in item and "unregistered agents" in item
            for item in hard_cut_check["errors"]
        ), hard_cut_check
        assert any(
            "todo_malformed_exclusion" in item and "malformed excluded_agents" in item
            for item in hard_cut_check["errors"]
        ), hard_cut_check

        repaired_agent_gate = update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id="todo_removed_agent_gate",
            role="agent",
            agent_id=PRIMARY_AGENT,
            clear_blocks_agent=True,
        )
        assert repaired_agent_gate["blocks_agent"] is None, repaired_agent_gate
        repaired_hard_cut_check = check_contract(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
        )
        assert not any(
            "todo_removed_agent_gate" in item
            for item in repaired_hard_cut_check["errors"]
        ), repaired_hard_cut_check

    print("todo-user-gate-scope-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
