from __future__ import annotations

import json
from pathlib import Path

from canonical_authority_fixture import initialize_canonical_authority
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.testing.canary_harness import (
    run_json_cli,
    write_fixture_registry,
)


GOAL_ID = "todo-capability-gap-cli-validation"
AGENT_ID = "codex-quality"
TARGET_CAPABILITY = "change_quality_qualification"


def _write_fixture(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "---\n\n"
        "# Active Goal State\n\n"
        "## Objective\n\n"
        "Keep capability-gap lifecycle evidence coherent.\n\n"
        "## Next Action\n\n"
        "- Close the validated capability gap.\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n"
        "## Completed Work Archive\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        domain="todo-capability-gap-cli-validation",
        adapter_kind="generic_project_goal_v0",
    )
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    payload["goals"][0]["coordination"] = {
        "agent_model": "peer_v1",
        "registered_agents": [AGENT_ID],
    }
    registry_path.write_text(json.dumps(payload), encoding="utf-8")
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            handoff_mode="soft_claim",
            todos=[],
        ),
        state_path=state_file,
    )
    return registry_path


def test_todo_complete_records_fixed_capability_gap_with_completion(
    tmp_path: Path,
) -> None:
    registry_path = _write_fixture(tmp_path)
    added = run_json_cli(
        "todo",
        "add",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        "--task-class",
        "advancement_task",
        "--text",
        "Repair the capability gap.",
        "--target-capability",
        TARGET_CAPABILITY,
        "--capability-gap-status",
        "found",
        "--claimed-by",
        AGENT_ID,
        registry_path=registry_path,
    )

    completed = run_json_cli(
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        "--todo-id",
        added["todo_id"],
        "--target-capability",
        TARGET_CAPABILITY,
        "--capability-gap-status",
        "fixed",
        "--evidence",
        "focused test passed",
        "--no-follow-up",
        "--agent-id",
        AGENT_ID,
        registry_path=registry_path,
    )

    assert completed["status"] == "done", completed
    assert completed["capability_gap_event"]["status"] == "fixed", completed
    assert completed["capability_gap_event"]["event_kind"] == "capability_gap"
