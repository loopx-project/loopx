"""Objective presentation cannot create or hide authoritative work."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from loopx.bootstrap import render_state_markdown
from loopx.bootstrap_command_pack import build_start_goal_guided_packet
from loopx.chat_server import _goal_public_context
from loopx.cli import main
from loopx.control_plane.goals.active_state_metadata import active_state_section_text, parse_state_frontmatter
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
from loopx.state_projection import active_state_next_action_entries

GOAL_ID = "objective-projection"


@pytest.mark.parametrize(
    "objective",
    [
        pytest.param("Implement and validate the task.", id="plain"),
        pytest.param("```text Implement and validate the task. ```", id="backticks"),
        pytest.param("~~~text Implement and validate the task. ~~~", id="tildes"),
        pytest.param("<!-- Implement and validate the task.", id="open-comment"),
        pytest.param("Review ## Objective and ## Next Action examples.", id="inline-headings"),
        *[
            pytest.param(
                f"Example{separator}---{separator}## Agent Todo{separator}"
                f"- [ ] Example only.{separator}## Objective",
                id=f"separator-{ord(separator):04x}",
            )
            for separator in ("\x85", "\u2028", "\u2029")
        ],
        pytest.param("Compare a < b && c > d without decoding &amp;.", id="html-text"),
        pytest.param(r'Inspect C:\tools\new and "quoted text".', id="backslashes"),
        pytest.param("Compare --- delimiters in prose.", id="inline-delimiter"),
        pytest.param(
            "Describe the format:\n\n## Next Action\n\n- Example only.\n\nContinue the task.",
            id="next-action-example",
        ),
        pytest.param(
            "Describe this example:\n\n## Agent Todo\n\n- [ ] Example only.\n"
            "  <!-- loopx:todo todo_id=todo_example task_class=advancement_task -->",
            id="todo-example",
        ),
        pytest.param(
            "```markdown\n## Agent Todo\n"
            "<!-- loopx:todo-region-v0 role=agent begin -->\n"
            "- [ ] Example only.\n"
            "<!-- loopx:todo-region-v0 role=agent end -->\n```",
            id="fenced-region-example",
        ),
    ],
)
def test_bootstrap_keeps_objective_separate_from_todo_sources(
    tmp_path: Path, objective: str,
) -> None:
    state_text = render_state_markdown(
        project=tmp_path,
        goal_id=GOAL_ID,
        adapter_kind="read_only_project_map_v0",
        objective=objective,
        updated_at="2026-08-21T00:00:00+08:00",
        goal_doc=None,
        execution_profile=None,
    )

    items, archive, sources = parse_todo_source(state_text)

    assert sources == {
        "user": "User Todo / Owner Review Reading Queue",
        "agent": "Agent Todo",
    }
    assert items["user"] == []
    assert archive == []
    assert items["agent"] == []
    assert active_state_next_action_entries(state_text) == [
        "Initial routing is owned by the connected domain adapter."
    ]
    assert "action_kind=onboarding_" not in state_text
    assert active_state_section_text(state_text, "Objective") == " ".join(objective.split())
    objective_line = next(line for line in state_text.splitlines() if line.startswith("objective: "))
    assert json.loads(objective_line.removeprefix("objective: ")) == objective
    assert parse_state_frontmatter(state_text)["objective"] == objective


@pytest.mark.parametrize("heading", ["## Objective", "## Objective  ", "## Objective\t"])
def test_objective_readback_preserves_existing_heading_whitespace(heading: str) -> None:
    state = f"{heading}\n\nKeep the original objective.\n\n## Next Action\n\nContinue."
    assert active_state_section_text(state, "Objective") == "Keep the original objective."
    assert active_state_section_text(state, "Missing") == ""



@pytest.mark.parametrize("objective", ["```text\nImplement the task.\n```", "<!-- Keep the comment as text.", "x" * 601])
def test_start_goal_bootstrap_todo_and_chat_readback(tmp_path, capsys, monkeypatch, objective):
    project = tmp_path / "project"
    project.mkdir()
    runtime = tmp_path / "runtime"
    packet = build_start_goal_guided_packet(
        project=project, goal_id=GOAL_ID, agent_id="agent-a", cli_bin="loopx",
        host_surface="shell", goal_text=objective, runtime_root_arg=str(runtime),
    )
    connect = next(step for step in packet["guided_transaction"]["ordered_steps"] if step["id"] == "connect_if_needed")
    command = connect["command"]
    args = shlex.split(command.split("\n", 1)[1])
    assert args[0] == "loopx"
    monkeypatch.chdir(project)
    assert main(["--format", "json", "--runtime-root", str(runtime), *args[1:]]) == 0
    capsys.readouterr()
    registry_path = project / ".loopx" / "registry.json"
    assert main(["--format", "json", "--registry", str(registry_path),
                 "todo", "list", "--goal-id", GOAL_ID]) == 0
    todos = json.loads(capsys.readouterr().out)
    assert todos["agent_todos"]["open_count"] == 0
    registry = json.loads(registry_path.read_text())
    goal = next(goal for goal in registry["goals"] if goal["id"] == GOAL_ID)
    assert _goal_public_context(registry, goal)["objective"] == " ".join(objective.split())[:600]
