from __future__ import annotations

import json
from pathlib import Path

from loopx.control_plane.todos.contract import encode_metadata_value
from loopx.control_plane.todos.markdown import render_todo_markdown
from loopx.todos import list_goal_todos

GOAL_ID = "todo-list-agent-lane-goal"
AGENT_ID = "codex-quality-qualification"
OTHER_AGENT_ID = "codex-other-agent"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _todo_line(
    *,
    todo_id: str,
    text: str,
    status: str,
    task_class: str,
    metadata: str,
) -> list[str]:
    marker = " " if status in {"open", "blocked"} else "x"
    return [
        f"- [{marker}] {text}",
        (
            "  <!-- loopx:todo "
            f"todo_id={todo_id} status={status} task_class={task_class} "
            f"{metadata} -->"
        ),
    ]


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    state_relative = Path(".local/goals") / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_file = project / state_relative
    state_file.parent.mkdir(parents=True)

    lines = [
        "---",
        "status: active",
        "updated_at: 2026-01-01T00:00:00+00:00",
        "---",
        "",
        "# Todo List Agent Lane Fixture",
        "",
        "## User Todo",
        "",
    ]
    lines.extend(
        _todo_line(
            todo_id="todo_user_done_000",
            text="Completed current-agent user action 0.",
            status="done",
            task_class="user_action",
            metadata=f"bound_agent={AGENT_ID}",
        )
    )
    lines.extend(
        _todo_line(
            todo_id="todo_user_current_action",
            text="Review the current agent result.",
            status="open",
            task_class="user_action",
            metadata=f"bound_agent={AGENT_ID} action_kind=review_current",
        )
    )
    lines.extend(
        _todo_line(
            todo_id="todo_user_other_action",
            text="Review another agent result.",
            status="open",
            task_class="user_action",
            metadata=f"bound_agent={OTHER_AGENT_ID} action_kind=review_other",
        )
    )
    lines.extend(
        _todo_line(
            todo_id="todo_user_current_gate",
            text="Approve the current agent decision.",
            status="open",
            task_class="user_gate",
            metadata=f"blocks_agent={AGENT_ID} action_kind=approve_current",
        )
    )
    lines.extend(
        _todo_line(
            todo_id="todo_user_other_gate",
            text="Approve another agent decision.",
            status="open",
            task_class="user_gate",
            metadata=f"blocks_agent={OTHER_AGENT_ID} action_kind=approve_other",
        )
    )
    lines.extend(
        _todo_line(
            todo_id="todo_user_legacy",
            text="Resolve one legacy unscoped user item.",
            status="open",
            task_class="user_action",
            metadata="action_kind=legacy_unscoped",
        )
    )
    for index in range(1, 50):
        lines.extend(
            _todo_line(
                todo_id=f"todo_user_done_{index:03d}",
                text=f"Completed current-agent user action {index}.",
                status="done",
                task_class="user_action",
                metadata=f"bound_agent={AGENT_ID}",
            )
        )

    lines.extend(["", "## Agent Todo", ""])
    lines.extend(
        _todo_line(
            todo_id="todo_agent_current",
            text="Execute the current agent advancement.",
            status="open",
            task_class="advancement_task",
            metadata=(
                f"claimed_by={AGENT_ID} action_kind=current_work "
                "note="
                + encode_metadata_value(
                    "phase A validated; resume from durable state"
                )
            ),
        )
    )
    lines.extend(
        _todo_line(
            todo_id="todo_agent_unclaimed",
            text="Execute one unclaimed advancement.",
            status="open",
            task_class="advancement_task",
            metadata="action_kind=unclaimed_work",
        )
    )
    lines.extend(
        _todo_line(
            todo_id="todo_agent_other",
            text="Execute another agent advancement.",
            status="open",
            task_class="advancement_task",
            metadata=f"claimed_by={OTHER_AGENT_ID} action_kind=other_work",
        )
    )
    lines.extend(
        _todo_line(
            todo_id="todo_agent_deferred",
            text="Resume the current agent work when network capacity returns.",
            status="deferred",
            task_class="advancement_task",
            metadata=(f"claimed_by={AGENT_ID} resume_when=capacity_available:network"),
        )
    )
    long_done_text = "Completed historical detail " + ("that stays cold " * 20)
    for index in range(220):
        lines.extend(
            _todo_line(
                todo_id=f"todo_agent_done_{index:03d}",
                text=f"{long_done_text}{index}.",
                status="done",
                task_class="advancement_task",
                metadata=f"claimed_by={AGENT_ID} action_kind=historical_work",
            )
        )
    state_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    registry_path = project / ".loopx/registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_relative),
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID, OTHER_AGENT_ID],
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path, state_file


def test_agent_lane_default_is_bounded_and_keeps_active_identity(
    tmp_path: Path,
) -> None:
    registry_path, _ = _write_fixture(tmp_path)

    payload = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )

    assert payload["todo_count"] == 276
    assert payload["returned_todo_count"] == 6
    assert payload["todo_list_projection"] == {
        "schema_version": "agent_lane_todo_list_projection_v0",
        "view": "agent_lane_hot_path",
        "matched_todo_count": 276,
        "returned_todo_count": 6,
        "item_limit_per_role": 12,
        "counts_cover_full_match": True,
        "full_detail_cold_paths": [
            "todo list with --role, --status, or --todo-id",
            "todo list without --agent-id",
            "active state",
        ],
    }
    assert {item["todo_id"] for item in payload["user_todos"]["items"]} == {
        "todo_user_current_action",
        "todo_user_current_gate",
        "todo_user_legacy",
    }
    assert {item["todo_id"] for item in payload["agent_todos"]["items"]} == {
        "todo_agent_current",
        "todo_agent_unclaimed",
        "todo_agent_deferred",
    }
    current = next(
        item
        for item in payload["agent_todos"]["items"]
        if item["todo_id"] == "todo_agent_current"
    )
    assert current["note"] == "phase A validated; resume from durable state"
    assert payload["user_todos"]["done_count"] == 50
    assert payload["agent_todos"]["done_count"] == 221
    assert all(item["status"] != "done" for item in payload["todos"])
    assert len(json.dumps(payload, ensure_ascii=False, indent=2)) < 30_000
    assert len(render_todo_markdown(payload)) < 8_500


def test_agent_lane_retains_bounded_blocked_advancement_reason(
    tmp_path: Path,
) -> None:
    registry_path, state_file = _write_fixture(tmp_path)
    blocked = _todo_line(
        todo_id="todo_agent_blocked",
        text="[P0] Resume the blocked deliverable.",
        status="blocked",
        task_class="advancement_task",
        metadata=(
            f"claimed_by={AGENT_ID} reason="
            + encode_metadata_value("A required dependency is unavailable")
        ),
    )
    with state_file.open("a", encoding="utf-8") as stream:
        stream.write("\n".join(blocked) + "\n")

    payload = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    projected = next(
        item for item in payload["agent_todos"]["items"]
        if item["todo_id"] == "todo_agent_blocked"
    )
    assert projected["reason"] == "A required dependency is unavailable"


def test_explicit_done_filter_remains_a_full_detail_cold_path(
    tmp_path: Path,
) -> None:
    registry_path, _ = _write_fixture(tmp_path)

    payload = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        role="agent",
        status="done",
        agent_id=AGENT_ID,
    )

    assert payload["todo_count"] == 220
    assert len(payload["todos"]) == 220
    assert len(payload["agent_todos"]["items"]) == 220
    assert "returned_todo_count" not in payload
    assert "todo_list_projection" not in payload
