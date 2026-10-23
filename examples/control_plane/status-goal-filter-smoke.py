#!/usr/bin/env python3
"""Smoke-test goal-focused status consumers against unrelated goal failures."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


GOAL_A = "status-filter-a"
GOAL_B = "status-filter-b"


def write_state(project: Path, goal_id: str, todo_text: str) -> str:
    state_file = f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
    path = project / state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    unrelated_malformed_user_todo = ""
    if goal_id == GOAL_B:
        unrelated_malformed_user_todo = (
            "\n## User Todo\n\n"
            "- [ ] This malformed todo belongs only to goal B.\n"
            "  <!-- loopx:todo todo_id=todo_bad_goal_b status=open -->\n"
        )
    path.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-06-30T00:00:00+00:00\n"
        "---\n\n"
        f"# {goal_id}\n\n"
        "## Agent Todo\n\n"
        f"- [ ] {todo_text}\n"
        f"  <!-- loopx:todo todo_id=todo_{goal_id} status=open task_class=advancement_task -->\n"
        f"{unrelated_malformed_user_todo}",
        encoding="utf-8",
    )
    return state_file


def write_registry(project: Path, runtime: Path) -> Path:
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    goals = []
    for goal_id, todo_text in (
        (GOAL_A, "Advance the selected status goal."),
        (GOAL_B, "Do not leak into the filtered status view."),
    ):
        goals.append(
            {
                "id": goal_id,
                "domain": "status-filter-smoke",
                "status": "active",
                "repo": str(project),
                "state_file": write_state(project, goal_id, todo_text),
                "adapter": {"kind": "smoke_v0", "status": "connected-read-only"},
                "authority_sources": [],
            }
        )
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-06-30T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": goals,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path


def write_run_history(runtime: Path) -> None:
    for goal_id, minute in ((GOAL_A, "01"), (GOAL_B, "02")):
        runs_dir = runtime / "goals" / goal_id / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run = {
            "goal_id": goal_id,
            "generated_at": f"2026-06-30T00:{minute}:00+00:00",
            "classification": "validated_progress",
            "recommended_action": f"continue {goal_id}",
            "health_check": "status goal filter smoke",
        }
        (runs_dir / "index.jsonl").write_text(
            json.dumps(run, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def run_goal_command(
    registry_path: Path,
    runtime: Path,
    project: Path,
    command_name: str,
    *extra: str,
    goal_id: str | None = GOAL_A,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime),
        command_name,
    ]
    if goal_id:
        command.extend(["--goal-id", goal_id])
    command.extend(
        [
            "--limit",
            "10",
            *extra,
        ]
    )
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def assert_goal_scoped(payload: dict) -> None:
    assert payload["ok"] is True, payload
    assert payload["goal_filter"] == GOAL_A, payload
    queue = payload["attention_queue"]
    assert queue["goal_filter"] == GOAL_A, queue
    assert queue["goal_filter_applied"] is True, queue
    assert queue["items"], queue
    assert {item["goal_id"] for item in queue["items"]} == {GOAL_A}, queue["items"]

    run_history = payload["run_history"]
    assert run_history["goal_count"] == 1, run_history
    assert [goal["id"] for goal in run_history["goals"]] == [GOAL_A], run_history
    assert {run["goal_id"] for run in run_history["recent_runs"]} == {GOAL_A}, run_history

    todo_items = payload["todo_index"]["items"]
    assert todo_items, payload["todo_index"]
    assert {item["goal_id"] for item in todo_items} == {GOAL_A}, todo_items

    payload_text = json.dumps(payload, sort_keys=True)
    assert GOAL_B not in payload_text, payload_text


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-status-goal-filter-") as tmp:
        root = Path(tmp)
        project = root / "project"
        runtime = root / "runtime"
        project.mkdir(parents=True)
        (project / "PUBLIC.md").write_text("# Public scan surface\n", encoding="utf-8")
        registry_path = write_registry(project, runtime)
        write_run_history(runtime)
        from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture

        # Goal B deliberately stays malformed/unmigrated to test scoped reads.
        initialize_canonical_source_fixture(
            runtime, GOAL_A,
            state_path=project / f".codex/goals/{GOAL_A}/ACTIVE_GOAL_STATE.md",
        )

        json_result = run_goal_command(
            registry_path,
            runtime,
            project,
            "status",
            "--format",
            "json",
        )
        assert json_result.returncode == 0, (json_result.stdout, json_result.stderr)
        assert_goal_scoped(json.loads(json_result.stdout))

        markdown_result = run_goal_command(
            registry_path,
            runtime,
            project,
            "status",
        )
        assert markdown_result.returncode == 0, (markdown_result.stdout, markdown_result.stderr)
        assert f"- goal_filter: `{GOAL_A}`" in markdown_result.stdout, markdown_result.stdout
        assert GOAL_B not in markdown_result.stdout, markdown_result.stdout

        for command_name in ("diagnose", "review-packet"):
            result = run_goal_command(
                registry_path,
                runtime,
                project,
                command_name,
                "--format",
                "json",
            )
            assert result.returncode == 0, (result.stdout, result.stderr)
            assert GOAL_B not in result.stdout, result.stdout

        ready_score = run_goal_command(
            registry_path,
            runtime,
            project,
            "ready-score",
            "--format",
            "json",
            goal_id=None,
        )
        assert ready_score.returncode == 0, (
            ready_score.stdout,
            ready_score.stderr,
        )
        ready_payload = json.loads(ready_score.stdout)
        assert ready_payload["goal_id"] == GOAL_A, ready_payload
        assert GOAL_B not in ready_score.stdout, ready_score.stdout

    print("status-goal-filter-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
