from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from loopx.control_plane.testing.cli_output_budget import (
    CLI_OUTPUT_MODE_VARIANT_BY_ID,
    assert_cli_output_mode_variant,
    measure_cli_output,
)
from loopx.control_plane.todos.contract import encode_metadata_value
from loopx.control_plane.todos.list_projection import (
    compact_thin_todo_list_payload,
)
from loopx.control_plane.todos.markdown import render_todo_markdown
from loopx.todos import list_goal_todos

GOAL_ID = "todo-list-thin-goal"
AGENT_ID = "codex-thin-output"
REPO_ROOT = Path(__file__).resolve().parents[2]
THIN_ITEM_LIMIT_PER_ROLE = 2


def _todo_line(*, text: str, metadata: str) -> list[str]:
    return [
        f"- [ ] {text}",
        f"  <!-- loopx:todo {metadata} -->",
    ]


def _write_fixture(tmp_path: Path) -> Path:
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
        "# Todo List Thin Fixture",
        "",
        "## User Todo",
        "",
        *_todo_line(
            text="Approve the public output contract.",
            metadata=(
                "todo_id=todo_user_gate status=open priority=P0 "
                "task_class=user_gate action_kind=approve_output "
                f"blocks_agent={AGENT_ID} "
                "decision_scope=direction:action:output-contract "
                f"note={encode_metadata_value('Public fixture review detail.')}"
            ),
        ),
        "",
        "## Agent Todo",
        "",
        *_todo_line(
            text="Observe the public output budget.",
            metadata=(
                "todo_id=todo_agent_monitor status=open priority=P1 "
                "task_class=continuous_monitor action_kind=observe_budget "
                f"claimed_by={AGENT_ID} target_key=public-output "
                "cadence=PT5M next_due_at=2026-01-01T00:05:00Z "
                f"note={encode_metadata_value('Public fixture monitor detail.')} "
                f"evidence={encode_metadata_value('Public fixture evidence.')}"
            ),
        ),
    ]
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


def _write_crowded_fixture(tmp_path: Path, *, items_per_role: int = 20) -> Path:
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    state_relative = Path(".local/goals") / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_file = project / state_relative
    state_file.parent.mkdir(parents=True)
    long_text = "x" * 260
    long_target = "target-" + "y" * 220
    long_agent = "codex-" + "a" * 70
    long_scope = "scope-" + "s" * 80
    required_scopes = ",".join(
        f"direction:action:{long_scope}-{index}" for index in range(6)
    )
    lines = [
        "---",
        "status: active",
        "updated_at: 2026-01-01T00:00:00+00:00",
        "---",
        "",
        "# Crowded Todo List Thin Fixture",
        "",
        "## User Todo",
        "",
    ]
    for index in range(items_per_role):
        lines.extend(
            _todo_line(
                text=f"Approve bounded user projection {index:03d} {long_text}",
                metadata=(
                    f"todo_id=todo_user_{index:03d} status=open priority=P0 "
                    "task_class=user_gate action_kind=approve_output "
                    f"blocks_agent={long_agent} "
                    f"decision_scope=direction:action:{long_scope} "
                    f"target_key={long_target}-{index:03d}"
                ),
            )
        )
    lines.extend(["", "## Agent Todo", ""])
    for index in range(items_per_role):
        lines.extend(
            _todo_line(
                text=f"Observe bounded agent projection {index:03d} {long_text}",
                metadata=(
                    f"todo_id=todo_agent_{index:03d} status=open priority=P1 "
                    "task_class=continuous_monitor action_kind=observe_budget "
                    f"claimed_by={long_agent} "
                    f"required_decision_scopes={required_scopes} "
                    f"target_key={long_target}-{index:03d} cadence=PT5M "
                    "next_due_at=2026-01-01T00:05:00Z "
                    "expires_at=2026-01-02T00:05:00Z watch_only=true"
                ),
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


def _widest_retained_thin_item(*, role: str, index: int) -> dict[str, object]:
    long_value = "x" * 240
    scope = {
        "schema_version": "decision_scope_v0",
        "kind": "direction",
        "granularity": "action",
        "scope_key": "scope-" + "s" * 90,
    }
    return {
        "todo_id": "todo_" + role[0] + str(index) + "i" * 62,
        "role": role,
        "status": "open",
        "priority": "P0",
        "text": f"text-{index}-{long_value}",
        "title": f"title-{index}-{long_value}",
        "task_class": "continuous_monitor",
        "action_kind": "a" * 64,
        "claimed_by": "a" * 80,
        "bound_agent": "b" * 80,
        "goal_bound": True,
        "blocks_agent": "c" * 80,
        "global_gate": True,
        "unblocks_todo_id": "todo_" + "u" * 64,
        "decision_scope": scope,
        "required_decision_scopes": [
            {**scope, "scope_key": f"scope-{offset}-" + "s" * 87}
            for offset in range(3)
        ],
        "resume_when": "capacity_available:" + "r" * 64,
        "resume_ready": True,
        "target_key": f"target-{index}-{long_value}",
        "cadence": "999d",
        "next_due_at": "2026-12-31T23:59:59+00:00",
        "expires_at": "2027-12-31T23:59:59+00:00",
        "watch_only": True,
    }


def _widest_retained_thin_payload(*, items_per_role: int) -> dict[str, object]:
    def summary(role: str) -> dict[str, object]:
        items = [
            _widest_retained_thin_item(role=role, index=index)
            for index in range(items_per_role)
        ]
        return {
            "schema_version": "todo_summary_v0",
            "source_section": f"{role.title()} Todo",
            "total_count": len(items),
            "open_count": len(items),
            "done_count": 0,
            "deferred_count": 0,
            "monitor_due_count": 0,
            "monitor_schedule_gap_count": 0,
            "claimed_advancement_open_count": 0,
            "claimed_monitor_open_count": 0,
            "items": items,
        }

    return {
        "ok": True,
        "dry_run": True,
        "read_only": True,
        "command": "list",
        "goal_id": GOAL_ID,
        "role": "all",
        "status_filter": None,
        "source": "markdown_active_state",
        "todo_count": items_per_role * 2,
        "user_todos": summary("user"),
        "agent_todos": summary("agent"),
    }


def _run_cli(
    registry_path: Path,
    *extra: str,
    output_format: str = "json",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            output_format,
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


def test_thin_projects_actionable_identity_and_omits_detail(tmp_path: Path) -> None:
    registry_path = _write_fixture(tmp_path)

    payload = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        thin=True,
    )

    assert payload["thin"] is True
    assert payload["todo_count"] == len(payload["todos"]) == 2
    assert "state_file" not in payload
    assert "project" not in payload
    assert payload["todo_list_field_projection"] == {
        "schema_version": "todo_list_thin_projection_v0",
        "view": "thin_explicit_view",
        "source_view": "full_detail",
        "item_container": "todos",
        "matched_todo_count": 2,
        "returned_todo_count": 2,
        "omitted_todo_count": 0,
        "item_limit_per_role": THIN_ITEM_LIMIT_PER_ROLE,
        "counts_cover_full_match": True,
        "item_fields": [
            "todo_id",
            "role",
            "status",
            "priority",
            "text",
            "task_class",
            "action_kind",
            "claimed_by",
            "bound_agent",
            "goal_bound",
            "blocks_agent",
            "global_gate",
            "unblocks_todo_id",
            "decision_scope",
            "required_decision_scopes",
            "resume_when",
            "resume_ready",
            "target_key",
            "cadence",
            "next_due_at",
            "expires_at",
            "watch_only",
        ],
        "item_text_limit": 180,
        "nested_item_limit": 3,
        "nested_dict_field_limit": 8,
        "full_detail_cold_paths": [
            "todo list without --thin",
            "todo list --todo-id <id> without --thin",
            "active state",
        ],
    }

    user_item, agent_item = payload["todos"]
    assert user_item["todo_id"] == "todo_user_gate"
    assert user_item["blocks_agent"] == AGENT_ID
    assert user_item["decision_scope"] == {
        "schema_version": "decision_scope_v0",
        "kind": "direction",
        "granularity": "action",
        "scope_key": "output-contract",
    }
    assert agent_item["todo_id"] == "todo_agent_monitor"
    assert agent_item["claimed_by"] == AGENT_ID
    assert agent_item["target_key"] == "public-output"
    assert agent_item["next_due_at"] == "2026-01-01T00:05:00Z"
    for item in payload["todos"]:
        assert "note" not in item
        assert "evidence" not in item
        assert "reason" not in item
        assert "updated_at" not in item

    for summary in (payload["user_todos"], payload["agent_todos"]):
        assert summary["payload_compaction"]["view"] == "thin_explicit_view"
        assert summary["payload_compaction"]["items_projected_to"] == "todos"
        assert [
            key
            for key, value in summary.items()
            if isinstance(value, list)
        ] == []


def test_thin_is_opt_in_and_composes_with_limit(tmp_path: Path) -> None:
    registry_path = _write_fixture(tmp_path)

    default = list_goal_todos(registry_path=registry_path, goal_id=GOAL_ID)
    limited = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        thin=True,
        limit=1,
    )

    assert "thin" not in default
    assert "todo_list_field_projection" not in default
    assert default["state_file"]
    assert default["project"]
    assert any("note" in item for item in default["todos"])

    assert limited["thin"] is True
    assert limited["explicit_limit"] == 1
    assert limited["returned_todo_count"] == 2
    assert limited["todo_list_projection"]["view"] == "explicit_limit_cold_path"
    assert limited["user_todos"]["payload_compaction"]["source_view"] == (
        "explicit_limit_cold_path"
    )
    assert limited["agent_todos"]["payload_compaction"]["source_view"] == (
        "explicit_limit_cold_path"
    )


def test_cli_thin_round_trips_json_and_markdown(tmp_path: Path) -> None:
    registry_path = _write_fixture(tmp_path)

    json_result = _run_cli(registry_path, "--thin")
    assert json_result.returncode == 0, json_result.stdout
    payload = json.loads(json_result.stdout)
    assert payload["thin"] is True
    assert "state_file" not in payload

    markdown_result = _run_cli(
        registry_path,
        "--thin",
        output_format="markdown",
    )
    assert markdown_result.returncode == 0, markdown_result.stdout
    assert "# LoopX Todo List" in markdown_result.stdout
    assert "- view: `thin_explicit_view`" in markdown_result.stdout
    assert "state_file" not in markdown_result.stdout
    assert "Public fixture review detail" not in markdown_result.stdout
    assert len(markdown_result.stdout.splitlines()) <= 22


def test_thin_intrinsically_bounds_high_cardinality_and_reports_overflow(
    tmp_path: Path,
) -> None:
    registry_path = _write_crowded_fixture(tmp_path, items_per_role=191)

    default = list_goal_todos(registry_path=registry_path, goal_id=GOAL_ID)
    thin = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        thin=True,
    )
    role_filtered = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        role="agent",
        thin=True,
    )
    explicitly_limited = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        limit=1,
        thin=True,
    )
    direct_match = list_goal_todos(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        todo_id="todo_agent_190",
    )

    assert len(default["todos"]) == default["todo_count"] == 382
    assert thin["todo_count"] == thin["matched_todo_count"] == 382
    assert thin["returned_todo_count"] == len(thin["todos"]) == 4
    assert thin["omitted_todo_count"] == 378
    assert thin["todo_list_field_projection"]["item_limit_per_role"] == 2
    assert thin["todo_list_field_projection"]["counts_cover_full_match"] is True
    assert {
        role: sum(item["role"] == role for item in thin["todos"])
        for role in ("user", "agent")
    } == {"user": 2, "agent": 2}
    for role in ("user", "agent"):
        summary = thin[f"{role}_todos"]
        compaction = summary["payload_compaction"]
        assert compaction["items_matched"] == 191
        assert compaction["items_returned"] == 2
        assert compaction["items_omitted"] == 189
        assert compaction["compacted_lanes"]["items"] == {
            "shown": 2,
            "total": 191,
        }

    bounded_agent_item = next(item for item in thin["todos"] if item["role"] == "agent")
    assert len(bounded_agent_item["target_key"]) <= 180
    assert len(bounded_agent_item["required_decision_scopes"]) == 3

    assert role_filtered["todo_count"] == 191
    assert role_filtered["returned_todo_count"] == 2
    assert role_filtered["omitted_todo_count"] == 189
    assert {item["role"] for item in role_filtered["todos"]} == {"agent"}
    assert explicitly_limited["matched_todo_count"] == 382
    assert explicitly_limited["returned_todo_count"] == 2
    assert explicitly_limited["omitted_todo_count"] == 380
    assert explicitly_limited["todo_list_field_projection"]["item_limit_per_role"] == 1
    assert direct_match["todo_count"] == 1
    assert "todos" not in direct_match
    assert direct_match["todo"]["todo_id"] == "todo_agent_190"

    spec = CLI_OUTPUT_MODE_VARIANT_BY_ID["todo_list_thin"]
    for output_format in ("json", "markdown"):
        result = _run_cli(
            registry_path,
            "--thin",
            output_format=output_format,
        )
        assert result.returncode == 0, result.stdout
        assert_cli_output_mode_variant(
            spec,
            output_format=output_format,
            text=result.stdout,
            measurement=measure_cli_output(
                result.stdout,
                output_format=output_format,
            ),
        )


def test_thin_widest_retained_shape_stays_inside_fixed_output_budget() -> None:
    payload = compact_thin_todo_list_payload(
        _widest_retained_thin_payload(items_per_role=5)
    )

    assert payload["returned_todo_count"] == 4
    assert payload["omitted_todo_count"] == 6
    assert payload["todo_list_field_projection"]["item_limit_per_role"] == 2
    assert all("title" not in item for item in payload["todos"])

    spec = CLI_OUTPUT_MODE_VARIANT_BY_ID["todo_list_thin"]
    rendered_by_format = {
        "json": json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        "markdown": render_todo_markdown(payload) + "\n",
    }
    for output_format, rendered in rendered_by_format.items():
        assert_cli_output_mode_variant(
            spec,
            output_format=output_format,
            text=rendered,
            measurement=measure_cli_output(
                rendered,
                output_format=output_format,
            ),
        )


def test_cli_rejects_thin_outside_todo_list(tmp_path: Path) -> None:
    registry_path = _write_fixture(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Do not write this rejected fixture todo.",
            "--thin",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "LOOPX_REGISTRY": str(registry_path)},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["error"] == "--thin is supported only by todo list"
