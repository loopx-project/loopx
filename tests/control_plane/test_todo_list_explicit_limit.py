from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from loopx.todos import list_goal_todos

GOAL_ID = "todo-list-explicit-limit-goal"
AGENT_ID = "codex-explicit-limit"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _todo_line(
    *,
    todo_id: str,
    text: str,
    status: str = "open",
    metadata: str = "",
) -> list[str]:
    marker = "x" if status == "done" else " "
    metadata_suffix = f" {metadata}" if metadata else ""
    return [
        f"- [{marker}] {text}",
        (
            "  <!-- loopx:todo "
            f"todo_id={todo_id} status={status}{metadata_suffix} -->"
        ),
    ]


def _write_fixture(
    tmp_path: Path,
    *,
    item_count_per_role: int = 8,
    agent_id: str | None = None,
    terminal_agent_index: int | None = None,
) -> Path:
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
        "# Todo List Explicit Limit Fixture",
        "",
        "## User Todo",
        "",
    ]
    for index in range(item_count_per_role):
        lines.extend(
            _todo_line(
                todo_id=f"todo_user_open_{index:03d}",
                text=f"Owner decision {index} for the crowded goal.",
                metadata=(f"bound_agent={agent_id}" if agent_id else ""),
            )
        )
    lines.extend(["", "## Agent Todo", ""])
    for index in range(item_count_per_role):
        status = "done" if index == terminal_agent_index else "open"
        lines.extend(
            _todo_line(
                todo_id=f"todo_agent_{status}_{index:03d}",
                text=f"Advancement step {index} for the crowded goal.",
                status=status,
                metadata=(f"claimed_by={agent_id}" if agent_id else ""),
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
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path


def test_explicit_limit_keeps_top_n_per_role_and_discloses_truncation(
    tmp_path: Path,
) -> None:
    registry_path = _write_fixture(tmp_path)

    unlimited = list_goal_todos(registry_path=registry_path, goal_id=GOAL_ID)
    payload = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        limit=3,
    )

    assert [item["todo_id"] for item in payload["user_todos"]["items"]] == [
        item["todo_id"]
        for item in unlimited["user_todos"]["items"][:3]
    ]
    assert [item["todo_id"] for item in payload["agent_todos"]["items"]] == [
        item["todo_id"]
        for item in unlimited["agent_todos"]["items"][:3]
    ]
    # Per-section unfiltered totals survive the cap: each summary's
    # total_count is the pre-cap filtered count for its role.
    assert payload["user_todos"]["total_count"] == 8
    assert payload["agent_todos"]["total_count"] == 8
    assert payload["explicit_limit"] == 3
    assert payload["unfiltered_todo_count"] == 16
    # todo_count keeps the cold-path invariant todo_count == len(todos):
    # it reports the returned (post-cap) totals, while the pre-cap match
    # total stays disclosed via todo_list_projection.matched_todo_count.
    assert payload["todo_count"] == 6
    assert len(payload["todos"]) == 6
    assert payload["returned_todo_count"] == 6
    assert payload["todo_list_projection"] == {
        "schema_version": "agent_lane_todo_list_projection_v0",
        "view": "explicit_limit_cold_path",
        "matched_todo_count": 16,
        "returned_todo_count": 6,
        "item_limit_per_role": 3,
        "counts_cover_full_match": True,
        "full_detail_cold_paths": [
            "todo list without --limit",
            "active state",
        ],
    }


def test_explicit_limit_is_stable_under_role_filter(tmp_path: Path) -> None:
    registry_path = _write_fixture(tmp_path)

    payload = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        role="agent",
        limit=3,
    )

    assert len(payload["agent_todos"]["items"]) == 3
    assert payload["agent_todos"]["total_count"] == 8
    assert "user_todos" not in payload
    assert payload["explicit_limit"] == 3
    assert payload["todo_count"] == 3
    assert payload["returned_todo_count"] == 3
    assert payload["todo_list_projection"]["matched_todo_count"] == 8
    assert payload["unfiltered_todo_count"] == 8


def test_explicit_limit_takes_precedence_over_agent_lane_hot_path(
    tmp_path: Path,
) -> None:
    registry_path = _write_fixture(
        tmp_path,
        item_count_per_role=18,
        agent_id=AGENT_ID,
        terminal_agent_index=5,
    )

    payload = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        limit=20,
    )

    # An explicit limit selects the disclosed cold path instead of layering the
    # existing 12-item agent-lane hot-path cap on top of the caller's limit.
    assert payload["todo_count"] == len(payload["todos"]) == 36
    assert payload["returned_todo_count"] == 36
    assert payload["todo_list_projection"]["matched_todo_count"] == 36
    assert payload["todo_list_projection"]["item_limit_per_role"] == 20
    assert payload["todo_list_projection"]["view"] == "explicit_limit_cold_path"
    for role_key in ("user_todos", "agent_todos"):
        compaction = payload[role_key]["payload_compaction"]
        assert compaction["view"] == "explicit_limit_cold_path"
        assert compaction["item_limit"] == 20
    assert any(item["status"] == "done" for item in payload["agent_todos"]["items"])


def test_default_payload_keeps_the_pre_limit_shape(tmp_path: Path) -> None:
    registry_path = _write_fixture(tmp_path)

    payload = list_goal_todos(registry_path=registry_path, goal_id=GOAL_ID)

    # The no-limit cold path is byte-identical to the pre---limit shape:
    # no explicit-limit disclosure fields appear.
    assert set(payload) == {
        "ok",
        "dry_run",
        "read_only",
        "command",
        "goal_id",
        "role",
        "status_filter",
        "source",
        "todo_count",
        "todos",
        "state_file",
        "project",
        "user_todos",
        "agent_todos",
    }
    assert payload["todo_count"] == 16
    assert len(payload["todos"]) == 16
    for absent_key in (
        "explicit_limit",
        "returned_todo_count",
        "todo_list_projection",
        "unfiltered_todo_count",
    ):
        assert absent_key not in payload


def test_todo_id_lookup_ignores_the_section_cap(tmp_path: Path) -> None:
    registry_path = _write_fixture(tmp_path)

    payload = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        todo_id="todo_agent_open_007",
        limit=3,
    )

    assert payload["matched"] is True
    assert payload["todo"]["todo_id"] == "todo_agent_open_007"
    assert payload["todo_count"] == 1
    assert [item["todo_id"] for item in payload["todos"]] == ["todo_agent_open_007"]


def test_explicit_limit_rejects_non_positive_values(tmp_path: Path) -> None:
    registry_path = _write_fixture(tmp_path)

    for rejected in (0, -1):
        try:
            list_goal_todos(
                registry_path=registry_path,
                goal_id=GOAL_ID,
                limit=rejected,
            )
        except ValueError as exc:
            assert str(exc) == "todo list --limit must be at least 1"
        else:
            raise AssertionError(f"limit={rejected} was not rejected")


def _run_cli(registry_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "todo",
            "list",
            "--goal-id",
            GOAL_ID,
            *extra,
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "LOOPX_REGISTRY": str(registry_path)},
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_limit_path_and_typed_rejections(tmp_path: Path) -> None:
    registry_path = _write_fixture(tmp_path)

    limited = _run_cli(registry_path, "--limit", "3")
    assert limited.returncode == 0, limited.stderr
    payload = json.loads(limited.stdout)
    assert payload["explicit_limit"] == 3
    assert payload["todo_count"] == 6
    assert payload["returned_todo_count"] == 6
    assert payload["todo_list_projection"]["matched_todo_count"] == 16

    default = _run_cli(registry_path)
    assert default.returncode == 0, default.stderr
    default_payload = json.loads(default.stdout)
    assert "explicit_limit" not in default_payload
    assert "returned_todo_count" not in default_payload
    assert "todo_list_projection" not in default_payload
    assert default_payload["todo_count"] == 16

    for rejected in ("0", "-1"):
        result = _run_cli(registry_path, "--limit", rejected)
        assert result.returncode == 1, result.stdout
        error_payload = json.loads(result.stdout)
        assert error_payload["ok"] is False
        assert error_payload["error"] == "todo list --limit must be at least 1"

    non_integer = _run_cli(registry_path, "--limit", "many")
    assert non_integer.returncode == 2
    assert "invalid int value" in non_integer.stderr
