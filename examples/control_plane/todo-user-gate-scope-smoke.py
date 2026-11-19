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
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402
from loopx.control_plane.todos.todo_summary import active_state_todo_attention_item  # noqa: E402
from loopx.quota import build_quota_should_run  # noqa: E402
from loopx.status import collect_status, parse_active_state_todos  # noqa: E402
from loopx.todos import (  # noqa: E402
    add_goal_todo, complete_goal_todo, list_goal_todos, update_goal_todo,
)


GOAL_ID = "user-gate-scope-smoke"
PRIMARY_AGENT = "codex-main-control"
SIDE_AGENT = "codex-product-capability"


def write_fixture(root: Path, *, import_authority: bool = True) -> tuple[Path, Path, Path]:
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
                "## User Todo",
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
    if import_authority:
        import_fixture_authority(registry, GOAL_ID)
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



def read_todo(registry: Path, todo_id: str) -> dict:
    view = list_goal_todos(registry_path=registry, goal_id=GOAL_ID, todo_id=todo_id)
    assert view["authority_read"]["source_authority"] == "file_v0", view
    assert view["authority_read"]["legacy_fallback_used"] is False, view
    return view["todo"]


def assert_unpromoted_source_is_not_contract_authority() -> None:
    # Detailed metadata diagnostics use canonical rows; the File/SQLite cases
    # in test_canonical_status_todos cover them without a display file.
    with tempfile.TemporaryDirectory(prefix="loopx-unpromoted-gate-diagnostics-") as tmp:
        root = Path(tmp)
        repo, state, registry = write_fixture(root, import_authority=False)
        insert_before_agent_section(
            state,
            "- [ ] Approve an intentionally unscoped gate.\n"
            "  <!-- loopx:todo todo_id=todo_unscoped_gate status=open task_class=user_gate -->",
        )
        original = state.read_bytes()
        check = check_contract(
            registry_path=registry, runtime_root_override=str(root / "runtime"),
            scan_roots=[repo], limit=1,
        )
        assert check["ok"] is False, check
        assert any(row["code"] == "canonical_authority_required"
                   and row.get("goal_id") == GOAL_ID for row in check["error_diagnostics"]), check
        assert state.read_bytes() == original


def assert_imported_scope_repairs() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-imported-gate-repair-") as tmp:
        root = Path(tmp)
        repo, state, registry = write_fixture(root, import_authority=False)
        insert_before_agent_section(
            state,
            "- [ ] Approve an intentionally unscoped gate.\n"
            "  <!-- loopx:todo todo_id=todo_unscoped_gate status=open task_class=user_gate -->\n"
            "- [ ] Track an unbound non-blocking user action.\n"
            "  <!-- loopx:todo todo_id=todo_unbound_action status=open task_class=user_action -->\n"
            "- [ ] Resolve a mismatched gate binding.\n"
            f"  <!-- loopx:todo todo_id=todo_mismatched_gate_binding status=open "
            f"task_class=user_gate blocks_agent={SIDE_AGENT} bound_agent={PRIMARY_AGENT} -->\n"
            "- [ ] Track an intentionally untyped user todo.\n"
            "  <!-- loopx:todo todo_id=todo_untyped_user status=open -->",
        )
        state.write_text(
            state.read_text(encoding="utf-8")
            + "\n- [ ] Repair removed agent gate routing.\n"
            + f"  <!-- loopx:todo todo_id=todo_removed_agent_gate status=open task_class=advancement_task blocks_agent={SIDE_AGENT} -->\n",
            encoding="utf-8",
        )
        import_fixture_authority(registry, GOAL_ID)
        for todo_id, role, changes, expected in (
            ("todo_unscoped_gate", "user", {"status": "done"}, {"status": "done"}),
            ("todo_unbound_action", "user", {"bound_agent": SIDE_AGENT}, {"bound_agent": SIDE_AGENT}),
            ("todo_mismatched_gate_binding", "user", {"bound_agent": SIDE_AGENT}, {"bound_agent": SIDE_AGENT}),
            # The caller now declares the missing type/binding explicitly;
            # closing old source data does not infer an approval or audience.
            ("todo_untyped_user", "user", {"status": "done", "task_class": "user_action", "goal_bound": True},
             {"status": "done", "task_class": "user_action", "goal_bound": True}),
            ("todo_removed_agent_gate", "agent", {"clear_blocks_agent": True}, {"blocks_agent": None}),
        ):
            original = read_todo(registry, todo_id)
            source = state.read_bytes()
            preview = update_goal_todo(
                registry_path=registry, goal_id=GOAL_ID, todo_id=todo_id,
                role=role, agent_id=PRIMARY_AGENT, dry_run=True, **changes,
            )
            assert preview["changed"] is True, preview
            assert read_todo(registry, todo_id) == original
            assert state.read_bytes() == source
            changed = update_goal_todo(
                registry_path=registry, goal_id=GOAL_ID, todo_id=todo_id,
                role=role, agent_id=PRIMARY_AGENT, **changes,
            )
            assert changed["changed"] is True, changed
            current = read_todo(registry, todo_id)
            assert current["text"] == original["text"], current
            for key, value in expected.items():
                assert current.get(key) == value, current
        repaired_check = check_contract(
            registry_path=registry, runtime_root_override=str(root / "runtime"),
            scan_roots=[repo], limit=1,
        )
        assert repaired_check["ok"] is True, repaired_check


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
        user_action = {**user_action, **read_todo(registry, user_action["todo_id"])}
        assert user_action["task_class"] == "user_action", user_action
        assert user_action["bound_agent"] == SIDE_AGENT, user_action
        assert user_action.get("claimed_by") is None, user_action

        goal_bound_action = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="user",
            text="Track a goal-wide owner note.",
            task_class="user_action",
            goal_bound=True,
            dry_run=True,
        )
        assert goal_bound_action["status"] == "planned", goal_bound_action
        assert goal_bound_action["changed"] is True, goal_bound_action
        assert len(list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]) == 1

        other_lane_only_status = collect_status(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
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
        side_agent_todo = {**side_agent_todo, **read_todo(registry, side_agent_todo["todo_id"])}
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
        approval_before = read_todo(registry, approval["todo_id"])
        target_before = read_todo(registry, blocked_agent_todo["todo_id"])
        source_before = state.read_bytes()
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
            "bound_agent_mismatch",
        )
        assert read_todo(registry, approval["todo_id"]) == approval_before
        assert read_todo(registry, blocked_agent_todo["todo_id"]) == target_before
        assert state.read_bytes() == source_before
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
        scoped = {**scoped, **read_todo(registry, scoped["todo_id"])}
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
        global_gate = {**global_gate, **read_todo(registry, global_gate["todo_id"])}
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

        goal_bound_action = add_goal_todo(
            registry_path=registry, goal_id=GOAL_ID, role="user",
            text="Track a goal-wide owner note.", task_class="user_action",
            goal_bound=True,
        )
        goal_bound_readback = read_todo(registry, goal_bound_action["todo_id"])
        assert goal_bound_readback["goal_bound"] is True, goal_bound_readback
        assert goal_bound_readback.get("bound_agent") is None, goal_bound_readback
        assert goal_bound_readback.get("global_gate") is not True, goal_bound_readback
        complete_goal_todo(
            registry_path=registry, goal_id=GOAL_ID,
            todo_id=goal_bound_action["todo_id"], role="user", agent_id=PRIMARY_AGENT,
            evidence="fixture owner note acknowledged",
        )
        assert read_todo(registry, goal_bound_action["todo_id"])["status"] == "done"

        clean_check = check_contract(
            registry_path=registry,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[repo],
            limit=1,
        )
        assert clean_check["ok"] is True, clean_check

        original = state.read_bytes()
        before = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)
        insert_before_agent_section(
            state,
            "- [ ] An injected projection cannot add a gate.\n"
            f"  <!-- loopx:todo todo_id=todo_projection_gate status=open "
            f"task_class=user_gate blocks_agent={PRIMARY_AGENT} -->",
        )
        after = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)
        assert after["todos"] == before["todos"], after
        assert after["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"], after
        assert state.read_bytes() != original
        state.write_bytes(original)

    assert_unpromoted_source_is_not_contract_authority()
    assert_imported_scope_repairs()

    print("todo-user-gate-scope-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
