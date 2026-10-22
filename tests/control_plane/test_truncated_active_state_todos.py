from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.contract import check_contract
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from loopx.status import active_state_todo_fields, collect_status
from loopx.todos import list_goal_todos


GOAL_ID = "goal-truncated-state"


def _state_text(*, agent_heading: str | None, agent_todo: bool = False) -> str:
    sections = [
        "# Active Goal State",
        "",
        "## User Todo / Owner Review Reading Queue",
        "",
    ]
    if agent_heading is not None:
        sections.extend([f"## {agent_heading}", ""])
        if agent_todo:
            sections.extend([
                "- [ ] [P1] Implement the durable fix.",
                "  <!-- loopx:todo todo_id=todo_fix status=open task_class=advancement_task -->",
                "",
            ])
    sections.extend([
        "## Next Action",
        "",
        "- Implement the durable fix.",
        "",
    ])
    return "\n".join(sections)


def _fixture(tmp_path: Path, state_text: str) -> tuple[dict[str, object], Path, Path, Path]:
    state_path = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_path.write_text(state_text, encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.json"
    goal: dict[str, object] = {
        "id": GOAL_ID,
        "domain": "control-plane-test",
        "status": "active",
        "repo": str(tmp_path),
        "state_file": state_path.name,
        "adapter": {"kind": "test_v0", "status": "connected-read-only"},
        "authority_sources": [],
    }
    registry_path.write_text(
        json.dumps({
            "schema_version": 1,
            "common_runtime_root": str(runtime_root),
            "goals": [goal],
        }),
        encoding="utf-8",
    )
    return goal, runtime_root, registry_path, state_path


def test_truncated_legacy_agent_todo_section_is_not_projected_as_empty(tmp_path: Path) -> None:
    healthy = _state_text(agent_heading="Agent Todo", agent_todo=True)
    wounded = healthy[: healthy.index("## Agent Todo")].rstrip() + "\n"
    goal, runtime_root, registry_path, _state_path = _fixture(tmp_path, wounded)

    listed = list_goal_todos(registry_path=registry_path, goal_id=GOAL_ID)
    assert listed["ok"] is False
    assert listed["error_code"] == "state_incomplete"
    assert listed["todo_count"] == 0
    assert listed["state_incomplete"]["kind"] == "state_incomplete"
    assert listed["state_incomplete"]["roles"] == ["agent"]

    fields = active_state_todo_fields(goal, runtime_root=runtime_root)
    assert fields["state_incomplete"]["kind"] == "state_incomplete"
    assert fields["state_incomplete"]["roles"] == ["agent"]

    contract = check_contract(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        scan_roots=[],
        limit=5,
        goal_id_filter=GOAL_ID,
        include_public_boundary_scan=False,
    )
    assert contract["ok"] is False
    assert any(
        row["code"] == "state_incomplete"
        and row["goal_id"] == GOAL_ID
        for row in contract["error_diagnostics"]
    )

    status = collect_status(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        limit=5,
        goal_id=GOAL_ID,
    )
    assert status["ok"] is False
    goal_attention = next(
        item for item in status["attention_queue"]["items"] if item["goal_id"] == GOAL_ID
    )
    assert goal_attention["status"] == "state_incomplete"
    assert goal_attention["waiting_on"] == "user_or_controller"
    assert goal_attention["severity"] == "high"
    assert goal_attention["state_incomplete"]["missing_sections"] == ["Agent Todo"]
    assert goal_attention["project_asset"]["state_incomplete"]["roles"] == ["agent"]
    assert "restore the missing Agent Todo section" in goal_attention["recommended_action"]
    assert "first read-only adapter tick" not in goal_attention["recommended_action"]


@pytest.mark.parametrize("agent_heading", ["Agent Todo", "Codex Todo", "Project Agent Todo"])
def test_present_but_empty_agent_todo_section_remains_healthy(
    tmp_path: Path,
    agent_heading: str,
) -> None:
    goal, runtime_root, registry_path, _state_path = _fixture(
        tmp_path,
        _state_text(agent_heading=agent_heading),
    )

    listed = list_goal_todos(registry_path=registry_path, goal_id=GOAL_ID)
    assert listed["ok"] is True
    assert listed["todo_count"] == 0
    assert "state_incomplete" not in listed

    fields = active_state_todo_fields(goal, runtime_root=runtime_root)
    assert "state_incomplete" not in fields

    contract = check_contract(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        scan_roots=[],
        limit=5,
        goal_id_filter=GOAL_ID,
        include_public_boundary_scan=False,
    )
    assert contract["ok"] is True


def test_truncated_legacy_state_cli_paths_fail_closed(tmp_path: Path) -> None:
    healthy = _state_text(agent_heading="Agent Todo", agent_todo=True)
    wounded = healthy[: healthy.index("## Agent Todo")].rstrip() + "\n"
    _goal, runtime_root, registry_path, _state_path = _fixture(tmp_path, wounded)

    code, status = run_json_cli_result(
        "status",
        "--goal-id",
        GOAL_ID,
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    assert code != 0
    assert status["ok"] is False
    assert any(
        row["code"] == "state_incomplete" and row["goal_id"] == GOAL_ID
        for row in status["contract"]["error_diagnostics"]
    )
    goal_attention = next(
        item for item in status["attention_queue"]["items"] if item["goal_id"] == GOAL_ID
    )
    assert goal_attention["status"] == "state_incomplete"

    code, listed = run_json_cli_result(
        "todo",
        "list",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    assert code != 0
    assert listed["ok"] is False
    assert listed["error_code"] == "state_incomplete"
    assert listed["todo_count"] == 0

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["coordination"] = {
        "registered_agents": ["agent-a"],
        "agent_model": "peer_v1",
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    code, quota = run_json_cli_result(
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        "agent-a",
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    assert code != 0
    assert quota["should_run"] is False
    assert quota["state"] == "blocked_health"
    assert quota["status"] == "state_incomplete"


def test_malformed_todo_region_is_a_contract_error_not_a_crash(tmp_path: Path) -> None:
    _goal, _runtime_root, registry_path, _state_path = _fixture(
        tmp_path,
        "## Agent Todo\n\n"
        "<!-- loopx:todo-region-v0 role=agent begin -->\n"
        "- [ ] unfinished region\n",
    )

    contract = check_contract(
        registry_path=registry_path,
        runtime_root_override=str(tmp_path / "runtime"),
        scan_roots=[],
        limit=5,
        goal_id_filter=GOAL_ID,
        include_public_boundary_scan=False,
    )
    assert contract["ok"] is False
    assert any(
        row["code"] == "active_state_read_failed"
        and row["goal_id"] == GOAL_ID
        for row in contract["error_diagnostics"]
    )
