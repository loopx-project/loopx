"""Queue overviews retain source truth without becoming execution admission."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.cli_output_budget import (
    CLI_OUTPUT_MODE_VARIANT_BY_ID, assert_cli_output_mode_variant, measure_cli_output,
)

REPO = Path(__file__).resolve().parents[2]
GOAL = "queue-overview"


@pytest.fixture(params=["file", "sqlite"])
def queue_cli(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    project = tmp_path / "project with spaces"
    project.mkdir()
    state = project / "state.md"
    state.write_text("# Disposable queue display\n", encoding="utf-8")
    runtime, registry = tmp_path / "runtime", tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "repo": str(project), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}), encoding="utf-8")
    rows = []
    for role in ("user", "agent"):
        for index in range(9):
            status = {3: "blocked", 4: "deferred", 8: "done"}.get(index, "open")
            text = (("Inspect this | complete requirement\n" if index == 0 else f"{role} requirement {index} ")
                    + "汉🙂" * 140)
            row = {"schema_version": "todo_item_v0", "todo_id": f"todo_{role}_{index}",
                   "role": role, "text": text, "priority": "P1", "status": status,
                   "done": status == "done", "archive_state": "active", "index": index + 1,
                   "source_section": f"{role.title()} Todo",
                   "task_class": "user_action" if role == "user" else "advancement_task"}
            if role == "agent" and index in (0, 1):
                row["claimed_by"] = "agent-a" if index == 0 else "agent-b"
            if role == "user" and index in (0, 1):
                row.update(task_class="user_gate", blocks_agent="agent-a" if index == 0 else "agent-b")
            if role == "user" and index in (3, 4):
                row["bound_agent"] = "agent-a"
            if role == "agent" and index == 6:
                row.update(task_class="continuous_monitor", target_key="synthetic-source", cadence="1h",
                           next_due_at="2090-01-01T00:00:00Z")
            if status == "done":
                row.update(no_followup=True, completion_continuation="no_followup")
            rows.append(row)
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL, todos=rows, handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, GOAL, projection, state_path=state, provider=request.param)
    prefix = [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
              "--runtime-root", str(runtime), "--format"]

    def run(*options, output_format="json", expected_exit=0, todo_command="list"):
        result = subprocess.run([*prefix, output_format, "todo", todo_command, "--goal-id", GOAL, *options],
                                cwd=REPO, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
                                env={**os.environ, "PYTHONPATH": str(REPO)})
        assert result.returncode == expected_exit, result.stdout + result.stderr
        return json.loads(result.stdout) if output_format == "json" else result.stdout

    yield request.param, run, state, rows
    subprocess.run([sys.executable, "-c", (
        "from loopx.control_plane.effect_runtime import effect_runtime_result;"
        "effect_runtime_result('runtime.shutdown', {}, retry_safe=False)")],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)


def test_queue_summary_is_opt_in_bounded_and_recovers_exact_requirements(queue_cli):
    provider, run, state, rows = queue_cli
    original_display = state.read_bytes()
    full, thin = run(), run("--thin")
    summary = run("--summary")
    assert summary["summary"] and summary["read_only"]
    assert summary["todo_list_summary_projection"]["schema_version"] == "todo_list_summary_v0"
    assert (summary["matched_todo_count"], summary["returned_todo_count"], summary["omitted_todo_count"]) == (18, 16, 2)
    expected = [row["todo_id"] for role in ("user", "agent")
                for row in [item for item in full["todos"] if item["role"] == role][:8]]
    assert [row["todo_id"] for row in summary["todos"]] == expected
    assert "user_todos" not in summary and "agent_todos" not in summary
    assert "state_file" not in summary and "project" not in summary
    first = summary["todos"][0]
    assert set(first) == {"todo_id", "role", "title", "status", "priority", "claimed_by", "title_truncated"}
    assert first["title_truncated"] and len(first["title"]) == 180
    exact = run("--todo-id", rows[0]["todo_id"])
    assert exact["todo"]["text"] == rows[0]["text"]
    assert exact["todo_detail_projection"]["source_complete"]
    coverage = summary["projection_envelope"]["coverage"]
    assert coverage["complete"] and coverage["available_count"] == 18
    assert coverage["shown_count"] == 16 and coverage["truncated"]
    assert summary["authority_read"]["source_authority"] == f"{provider}_v0"
    assert summary["authority_read"]["provider_revision"] == full["authority_read"]["provider_revision"]
    assert run()["todos"] == full["todos"]
    assert run("--thin")["todos"] == thin["todos"]
    assert thin["todo_list_field_projection"]["item_limit_per_role"] == 2
    assert state.read_bytes() == original_display


def test_queue_summary_preserves_filters_limits_and_hot_display_selection(queue_cli):
    _, run, _, _ = queue_cli
    for options, matched, returned in [(("--limit", "1"), 18, 2),
                                       (("--limit", "99"), 18, 16),
                                       (("--role", "agent"), 9, 8),
                                       (("--status", "done"), 2, 2)]:
        result = run("--summary", *options)
        assert result["matched_todo_count"] == matched
        assert result["returned_todo_count"] == returned
        assert result["omitted_todo_count"] == matched - returned
    lane = run("--agent-id", "agent-a")
    summary = run("--summary", "--agent-id", "agent-a")
    assert [row["todo_id"] for row in summary["todos"]] == [row["todo_id"] for row in lane["todos"]]
    assert {row["status"] for row in summary["todos"]} >= {"open", "blocked", "deferred"}
    assert "todo_agent_1" not in {row["todo_id"] for row in summary["todos"]}
    assert "todo_user_1" not in {row["todo_id"] for row in summary["todos"]}
    empty = run("--summary", "--role", "user", "--status", "deferred", "--agent-id", "agent-b")
    assert empty["matched_todo_count"] == empty["returned_todo_count"] == 0
    assert empty["todos"] == []
    # The source and recovery path remain canonical even without the display.
    _, _, state, _ = queue_cli
    state.unlink()
    assert run("--summary")["returned_todo_count"] == 16


def test_queue_summary_limit_preserves_title_truncation_and_exact_readback(queue_cli):
    _, run, state, rows = queue_cli
    display = state.read_bytes()
    full, thin = run(), run("--thin")
    summary = run("--summary")
    limited = run("--summary", "--limit", "1")
    original_by_id = {row["todo_id"]: row for row in summary["todos"]}
    for row in limited["todos"]:
        assert row == original_by_id[row["todo_id"]]
        assert row["title_truncated"] is True
        assert len(row["title"]) == 180
        source = next(item for item in rows if item["todo_id"] == row["todo_id"])
        exact = run("--todo-id", row["todo_id"])
        assert exact["todo"]["text"] == source["text"]
        assert exact["todo_detail_projection"]["source_complete"]
    markdown = run("--summary", "--limit", "1", output_format="markdown")
    assert "Title truncated; the exact read retains the full requirement." in markdown
    assert run()["todos"] == full["todos"]
    assert run("--thin")["todos"] == thin["todos"]
    assert limited["authority_read"] == summary["authority_read"]
    assert state.read_bytes() == display


def test_queue_summary_markdown_and_invalid_mode_combinations(queue_cli):
    _, run, state, _ = queue_cli
    display = state.read_bytes()
    revision = run()["authority_read"]["provider_revision"]
    markdown = run("--summary", output_format="markdown")
    assert "# LoopX Todo List Summary" in markdown
    assert "| Role | Priority | Status | Claimed by | Todo | ID |" in markdown
    assert "Inspect this \\| complete requirement" in markdown
    assert "todo list --goal-id <goal-id> --todo-id <todo-id>" in markdown
    assert "execution admission" in markdown
    for options in [("--summary", "--thin"), ("--summary", "--todo-id", "todo_user_0")]:
        assert not run(*options, expected_exit=1)["ok"]
    rejected = run("--summary", "--role", "agent", "--text", "Must not create a Todo",
                   todo_command="add", expected_exit=1)
    assert "--summary" in rejected["error"]
    assert run()["authority_read"]["provider_revision"] == revision
    assert state.read_bytes() == display


def test_summary_fits_its_own_budget_in_both_public_formats(queue_cli):
    _, run, _, _ = queue_cli
    spec = CLI_OUTPUT_MODE_VARIANT_BY_ID["todo_list_summary"]
    for output_format in ("json", "markdown"):
        payload = run("--summary", output_format=output_format)
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n" if output_format == "json" else payload
        assert_cli_output_mode_variant(spec, output_format=output_format, text=rendered,
                                       measurement=measure_cli_output(rendered, output_format=output_format))
