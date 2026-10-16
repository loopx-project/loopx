#!/usr/bin/env python3
"""Smoke-test generic role-declared successor todo execution."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from demo.multi_agent.role_successor import (  # noqa: E402
    MULTI_AGENT_ROLE_SUCCESSOR_TODOS_SCHEMA_VERSION,
    apply_role_successor_todos,
    first_successor_followup,
)
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted  # noqa: E402
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402


GOAL_ID = "multi-agent-successor-smoke"
SOURCE_TODO_ID = "todo_source123456"


def write_minimal_state(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "# Multi-Agent Successor Smoke",
                "",
                "## User Todo / Owner Review Reading Queue",
                "",
                "## Agent Todo",
                "",
                "- [ ] [P0] Run source action.",
                "  <!-- loopx:todo todo_id=todo_source123456 status=open task_class=advancement_task action_kind=run_dev_eval claimed_by=agent-a -->",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_registry(path: Path, *, project: Path, state_file: Path, runtime_root: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "state_file": str(state_file),
                        "coordination": {
                            "registered_agents": ["agent-a", "agent-b"],
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def successor_spec(*, target_agent_id: str = "agent-b", condition: object = "always") -> dict[str, object]:
    return {
        "condition": condition,
        "target_agent_id": target_agent_id,
        "target_role_id": "evidence_runner",
        "task_class": "advancement_task",
        "action_kind": "run_holdout_eval",
        "text": "[P0] Run holdout evidence.",
        "todo_command_template": (
            "loopx todo add --goal-id {goal_id_shell} --role agent "
            "--text {text_shell} --task-class {task_class_shell} "
            "--action-kind {action_kind_shell} --claimed-by {target_agent_id_shell} "
            "--unblocks-todo-id {source_todo_id_shell}"
        ),
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        project = root / "project"
        project.mkdir()
        state_file = project / "ACTIVE_GOAL_STATE.md"
        registry = root / "registry.json"
        runtime_root = root / "runtime"
        write_minimal_state(state_file)
        write_registry(registry, project=project, state_file=state_file, runtime_root=runtime_root)
        import_fixture_authority(registry, GOAL_ID)
        before = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=GOAL_ID)
        display_before = state_file.read_bytes()

        preview = apply_role_successor_todos(
            registry_path=registry,
            goal_id=GOAL_ID,
            source_todo_id=SOURCE_TODO_ID,
            current_agent_id="agent-a",
            role_id="evidence_runner",
            action="run_dev_eval",
            successor_specs=[successor_spec()],
            decision_summary={"dev_promotion_candidate_count": 1},
            execute=False,
        )
        assert preview["schema_version"] == MULTI_AGENT_ROLE_SUCCESSOR_TODOS_SCHEMA_VERSION, preview
        assert preview["source"] == "role_profile_todo_command_template", preview
        assert preview["needed"] is True, preview
        assert preview["executed"] is False, preview
        successor = preview["successors"][0]
        assert successor["target_agent_id"] == "agent-b", successor
        assert "--claimed-by agent-b" in str(successor["todo_command"]), successor
        assert first_successor_followup(preview)["action_kind"] == "run_holdout_eval", preview

        skipped = apply_role_successor_todos(
            registry_path=registry,
            goal_id=GOAL_ID,
            source_todo_id=SOURCE_TODO_ID,
            current_agent_id="agent-a",
            role_id="evidence_runner",
            action="run_dev_eval",
            successor_specs=[
                successor_spec(
                    condition={
                        "all": [
                            {
                                "path": "decision_summary.dev_promotion_candidate_count",
                                "op": "gt",
                                "value": 1,
                                "fail_reason": "no_second_candidate",
                            }
                        ]
                    }
                )
            ],
            decision_summary={"dev_promotion_candidate_count": 1},
            execute=False,
        )
        assert skipped["needed"] is False, skipped
        assert skipped["reason"] == "no_second_candidate", skipped
        assert read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=GOAL_ID) == before
        assert state_file.read_bytes() == display_before

        executed = apply_role_successor_todos(
            registry_path=registry,
            goal_id=GOAL_ID,
            source_todo_id=SOURCE_TODO_ID,
            current_agent_id="agent-a",
            role_id="evidence_runner",
            action="run_dev_eval",
            successor_specs=[successor_spec()],
            decision_summary={"dev_promotion_candidate_count": 1},
            execute=True,
        )
        assert executed["executed"] is True, executed
        added = executed["successors"][0]
        assert added["added"] is True, added
        assert added["claimed_by"] == "agent-b", added
        state_text = state_file.read_text(encoding="utf-8")
        assert "claimed_by=agent-b" in state_text, state_text
        assert f"unblocks_todo_id={SOURCE_TODO_ID}" in state_text, state_text
        after = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=GOAL_ID)
        assert after["source_authority"] == "file_v0", after
        records = {todo["todo_id"]: todo for todo in after["todos"]}
        assert len(records) == 2, records
        assert records[added["todo_id"]]["claimed_by"] == "agent-b", records
        assert records[added["todo_id"]]["unblocks_todo_id"] == SOURCE_TODO_ID, records
        original_source = next(todo for todo in before["todos"] if todo["todo_id"] == SOURCE_TODO_ID)
        assert records[SOURCE_TODO_ID] == original_source, records
        replay = apply_role_successor_todos(
            registry_path=registry, goal_id=GOAL_ID, source_todo_id=SOURCE_TODO_ID,
            current_agent_id="agent-a", role_id="evidence_runner", action="run_dev_eval",
            successor_specs=[successor_spec()], decision_summary={"dev_promotion_candidate_count": 1},
            execute=True,
        )
        assert replay["successors"][0]["already_exists"] is True, replay
        assert replay["successors"][0]["todo_id"] == added["todo_id"], replay
        assert read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=GOAL_ID) == after
        display_after = state_file.read_bytes()

        try:
            apply_role_successor_todos(
                registry_path=registry,
                goal_id=GOAL_ID,
                source_todo_id=SOURCE_TODO_ID,
                current_agent_id="agent-a",
                role_id="evidence_runner",
                action="run_dev_eval",
                successor_specs=[successor_spec(target_agent_id="missing-agent")],
                decision_summary={},
                execute=False,
            )
        except ValueError as exc:
            assert "successor target_agent_id 'missing-agent' is not registered" in str(exc), exc
        else:
            raise AssertionError("missing successor target agent should fail closed")
        assert read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=GOAL_ID) == after
        assert state_file.read_bytes() == display_after

    print("multi-agent-role-successor-todos-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
