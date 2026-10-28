from __future__ import annotations

import json
from pathlib import Path

from loopx.bootstrap_command_pack import (
    build_start_goal_guided_packet,
    render_start_goal_guided_markdown,
)
from loopx.control_plane.effect_program import effect_program_from_ordered_steps

GOAL_ID = "guided-program-goal"
AGENT_ID = "codex-guided-program"


def _write_connected_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text("# Active Goal State\n", encoding="utf-8")
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(project / ".loopx/runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_file.relative_to(project)),
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    from canonical_authority_fixture import initialize_canonical_source_fixture
    initialize_canonical_source_fixture(project / ".loopx/runtime", GOAL_ID, state_path=state_file, provider="sqlite")
    return project



def test_ordered_steps_map_to_effect_program() -> None:
    steps = [
        {
            "id": "select_host_surface",
            "kind": "host_surface_selection_gate",
            "purpose": "select the current host before mutation",
        },
        {
            "id": "bootstrap_goal",
            "kind": "bootstrap",
            "command": "loopx start-goal --guided",
            "purpose": "connect the goal before planning",
        },
        {
            "id": "add_todo",
            "kind": "todo_add",
            "command_template": "loopx todo add --goal-id loopx-meta",
            "purpose": "record the first bounded business todo",
        },
    ]

    program = effect_program_from_ordered_steps(
        steps,
        execution_mode="serial",
    )

    assert program.execution_mode == "serial"
    assert len(program.steps) == 3
    assert program.steps[0].step_id == "select_host_surface"
    assert program.steps[0].kind == "host_surface_selection_gate"
    assert program.steps[1].command == "loopx start-goal --guided"
    assert program.steps[2].command == "loopx todo add --goal-id loopx-meta"
    assert program.steps[2].purpose == "record the first bounded business todo"


def test_ordered_steps_ignore_non_mapping_entries() -> None:
    program = effect_program_from_ordered_steps(
        [
            "not-a-step",
            {"id": "step-a", "kind": "bootstrap"},
        ]
    )

    assert len(program.steps) == 1
    assert program.steps[0].step_id == "step-a"
    assert program.steps[0].command is None


def test_real_bootstrap_ordered_steps_map_to_effect_program(
    tmp_path: Path,
) -> None:
    project = _write_connected_project(tmp_path)

    payload = build_start_goal_guided_packet(
        project=project,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        cli_bin="loopx",
        host_surface="codex-app",
        goal_text="Ship a bounded public issue triage workflow.",
        available_capabilities=["network"],
        include_command_pack_detail=False,
    )
    transaction = payload["guided_transaction"]
    program = effect_program_from_ordered_steps(
        transaction["ordered_steps"],
        execution_mode="serial",
    )

    assert program.execution_mode == "serial"
    assert program.steps
    assert program.steps[0].step_id == "inspect_connection"
    assert program.steps[0].kind == "read_only"


def test_start_goal_guided_render_consumes_effect_program(
    tmp_path: Path,
) -> None:
    project = _write_connected_project(tmp_path)
    payload = build_start_goal_guided_packet(
        project=project,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        cli_bin="loopx",
        host_surface="codex-app",
        goal_text="Ship a bounded public issue triage workflow.",
        available_capabilities=["network"],
        include_command_pack_detail=False,
    )

    rendered = render_start_goal_guided_markdown(payload)

    assert "inspect_connection" in rendered
    assert "connect_if_needed" in rendered
    assert "quota_guard" in rendered
