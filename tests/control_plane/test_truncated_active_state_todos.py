from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.contract import check_contract
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from loopx.status import active_state_todo_fields, collect_status
from loopx.todos import list_goal_todos
from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable, CanonicalAuthorityRequired
from loopx.control_plane.todos.active_state_todo_parser import active_state_incomplete_warning
from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture


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
    goal, runtime_root, registry_path, state_path = _fixture(tmp_path, wounded)
    original = state_path.read_bytes()
    # Explicit source inspection still diagnoses the migration input. Ordinary
    # readers must not use that input as a substitute for canonical authority.
    source = active_state_incomplete_warning(wounded)
    assert source["kind"] == "state_incomplete"
    assert source["roles"] == ["agent"]
    with pytest.raises(CanonicalAuthorityRequired) as error:
        list_goal_todos(registry_path=registry_path, goal_id=GOAL_ID)
    assert error.value.payload["write_admitted"] is False
    assert error.value.payload["legacy_fallback_used"] is False
    with pytest.raises(LocalCoordinationAuthorityUnavailable):
        active_state_todo_fields(goal, runtime_root=runtime_root)
    contract = check_contract(registry_path=registry_path, runtime_root_override=str(runtime_root),
        scan_roots=[], limit=5, goal_id_filter=GOAL_ID, include_public_boundary_scan=False)
    assert contract["ok"] is False
    assert any(row["code"] == "canonical_authority_required" and row["goal_id"] == GOAL_ID
               for row in contract["error_diagnostics"])
    with pytest.raises(LocalCoordinationAuthorityUnavailable):
        collect_status(registry_path=registry_path, runtime_root_override=str(runtime_root),
                       limit=5, goal_id=GOAL_ID)
    assert state_path.read_bytes() == original


@pytest.mark.parametrize("agent_heading", ["Agent Todo", "Codex Todo", "Project Agent Todo"])
def test_present_but_empty_agent_todo_section_remains_healthy(
    tmp_path: Path,
    agent_heading: str,
) -> None:
    goal, runtime_root, registry_path, _state_path = _fixture(
        tmp_path,
        _state_text(agent_heading=agent_heading),
    )
    initialize_canonical_source_fixture(runtime_root, GOAL_ID, state_path=_state_path)

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
    _goal, runtime_root, registry_path, state_path = _fixture(tmp_path, wounded)
    original = state_path.read_bytes()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["coordination"] = {
        "registered_agents": ["agent-a"], "agent_model": "peer_v1",
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    for arguments in [("status", "--goal-id", GOAL_ID),
                      ("todo", "list", "--goal-id", GOAL_ID, "--role", "agent"),
                      ("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", "agent-a")]:
        code, payload = run_json_cli_result(*arguments, registry_path=registry_path,
                                           runtime_root=runtime_root)
        assert code != 0 and payload["ok"] is False
        assert payload["error_code"] == "canonical_authority_required", payload
        proof = payload.get("authority_read", payload)
        assert proof["write_admitted"] is False and proof["legacy_fallback_used"] is False
        if arguments[0] == "quota":
            assert payload["should_run"] is False and payload["state"] == "blocked_health"
        assert state_path.read_bytes() == original


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
        row["code"] == "canonical_authority_required"
        and row["goal_id"] == GOAL_ID
        for row in contract["error_diagnostics"]
    )
