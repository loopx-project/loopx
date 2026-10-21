#!/usr/bin/env python3
"""Exercise the peer workspace guard across real git worktrees."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402

GOAL_ID = "workspace-guard-canary"
PEER_ALPHA = "codex-alpha"
PEER_BETA = "codex-beta"
TASK_REPOSITORY = "git:example.invalid/loopx/task-repo"
PROJECT_REPOSITORY = "git:example.invalid/loopx/project-repo"


def run_git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def run_cli(cwd: Path, *args: str, registry_path: Path, runtime: Path) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(REPO_ROOT), env.get("PYTHONPATH")) if value
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            *args,
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def write_fixture(root: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
    project = root / "project"
    independent = root / "peer-worktree"
    task_project = root / "task-project"
    task_independent = root / "task-peer-worktree"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"

    project.mkdir(parents=True)
    (project / "README.md").write_text("# Workspace Guard Canary\n", encoding="utf-8")
    run_git(project, "init", "--initial-branch", "main")
    run_git(
        project,
        "remote",
        "add",
        "origin",
        "https://example.invalid/loopx/project-repo.git",
    )
    run_git(project, "add", "README.md")
    run_git(
        project,
        "-c",
        "user.name=LoopX Canary",
        "-c",
        "user.email=loopx-canary@example.invalid",
        "commit",
        "-m",
        "initial fixture",
    )
    run_git(project, "worktree", "add", "-b", "peer-work", str(independent))

    task_project.mkdir(parents=True)
    (task_project / "README.md").write_text("# Task Repository\n", encoding="utf-8")
    run_git(task_project, "init", "--initial-branch", "main")
    run_git(task_project, "remote", "add", "origin", "https://example.invalid/loopx/task-repo.git")
    run_git(task_project, "add", "README.md")
    run_git(
        task_project,
        "-c",
        "user.name=LoopX Canary",
        "-c",
        "user.email=loopx-canary@example.invalid",
        "commit",
        "-m",
        "initial task fixture",
    )
    run_git(task_project, "worktree", "add", "-b", "task-peer-work", str(task_independent))

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        "---\nstatus: active\nowner_mode: goal\n"
        'objective: "Exercise peer workspace guard."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n---\n\n"
        "# Workspace Guard Canary\n\n## Agent Todo\n\n"
        "- [ ] [P1] Run one bounded peer delivery batch from an independent worktree.\n"
        f"  <!-- loopx:todo todo_id=todo_workspace_guard status=open "
        f"task_class=advancement_task action_kind=fix claimed_by={PEER_ALPHA} -->\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "workspace-guard-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "workspace_guard_fixture_v1",
                            "status": "connected-read-only",
                        },
                        "authority_sources": [],
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "allowed_slots": 5,
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [PEER_ALPHA, PEER_BETA],
                        },
                        "workspace_guard_policy": {
                            "peer_independent_worktree_required": True,
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    import_fixture_authority(registry_path, GOAL_ID)
    return project, independent, task_project, task_independent, runtime, registry_path


def set_task_repository(project: Path, runtime: Path, registry_path: Path) -> None:
    updated = run_cli(
        project,
        "todo",
        "update",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        "--todo-id",
        "todo_workspace_guard",
        "--agent-id",
        PEER_ALPHA,
        "--task-repository",
        "https://example.invalid/loopx/task-repo.git",
        registry_path=registry_path,
        runtime=runtime,
    )
    assert updated["ok"] is True, updated
    current = run_cli(
        project, "todo", "list", "--goal-id", GOAL_ID,
        "--todo-id", "todo_workspace_guard", registry_path=registry_path, runtime=runtime,
    )
    assert current["todo"]["task_repository"] == TASK_REPOSITORY, current
    assert current["authority_read"]["legacy_fallback_used"] is False, current


def should_run(
    cwd: Path,
    project: Path,
    runtime: Path,
    registry_path: Path,
) -> dict:
    return run_cli(
        cwd,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        PEER_ALPHA,
        "--host-surface", "generic_cli", "--scheduler-owner", "none", "--execution-mode", "interactive",
        registry_path=registry_path,
        runtime=runtime,
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-peer-workspace-guard-") as tmp:
        (
            project,
            independent,
            task_project,
            task_independent,
            runtime,
            registry_path,
        ) = write_fixture(Path(tmp))

        guarded = should_run(project, project, runtime, registry_path)
        assert guarded["decision"] == "workspace_guard", guarded
        assert guarded["effective_action"] == "agent_workspace_repair", guarded
        assert guarded["normal_delivery_allowed"] is False, guarded
        assert guarded["agent_identity"]["agent_model"] == "peer_v1", guarded
        assert "role" not in guarded["agent_identity"], guarded
        guard = guarded["workspace_guard"]
        assert guard["schema_version"] == "agent_workspace_guard_v1", guard
        assert guard["current_workspace"] == "canonical_checkout", guard
        assert guard["agent_id"] == PEER_ALPHA, guard
        assert "primary_agent" not in guard, guard

        allowed = should_run(independent, project, runtime, registry_path)
        assert allowed["decision"] == "run", allowed
        assert allowed["effective_action"] == "normal_run", allowed
        assert allowed["normal_delivery_allowed"] is True, allowed
        assert "workspace_guard" not in allowed, allowed

        foreign_without_route = should_run(
            task_independent,
            project,
            runtime,
            registry_path,
        )
        assert foreign_without_route["decision"] == "workspace_guard", foreign_without_route
        assert foreign_without_route["workspace_guard"]["current_workspace"] == (
            "foreign_git_worktree"
        ), foreign_without_route
        assert foreign_without_route["workspace_guard"]["repository_source"] == (
            "goal.repo"
        ), foreign_without_route

        set_task_repository(project, runtime, registry_path)

        task_allowed = should_run(task_independent, project, runtime, registry_path)
        assert task_allowed["decision"] == "run", task_allowed
        assert task_allowed["effective_action"] == "normal_run", task_allowed
        assert "workspace_guard" not in task_allowed, task_allowed
        assert task_allowed["selected_todo"]["task_repository"] == TASK_REPOSITORY, task_allowed

        task_primary_guarded = should_run(
            task_project,
            project,
            runtime,
            registry_path,
        )
        task_primary_guard = task_primary_guarded["workspace_guard"]
        assert task_primary_guard["current_workspace"] == "canonical_checkout", task_primary_guard
        assert task_primary_guard["repository_source"] == (
            "selected_todo.task_repository"
        ), task_primary_guard
        assert task_primary_guard["task_repository"] == TASK_REPOSITORY, task_primary_guard

        goal_worktree_guarded = should_run(independent, project, runtime, registry_path)
        assert goal_worktree_guarded["workspace_guard"]["current_workspace"] == (
            "foreign_git_worktree"
        ), goal_worktree_guarded
        assert goal_worktree_guarded["workspace_guard"]["task_repository"] == (
            TASK_REPOSITORY
        ), goal_worktree_guarded
        state_path = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
        original_display = state_path.read_text(encoding="utf-8")
        before = run_cli(project, "todo", "list", "--goal-id", GOAL_ID,
                         registry_path=registry_path, runtime=runtime)
        state_path.write_text(original_display.replace(TASK_REPOSITORY, PROJECT_REPOSITORY), encoding="utf-8")
        spoofed = should_run(independent, project, runtime, registry_path)
        assert spoofed["workspace_guard"]["task_repository"] == TASK_REPOSITORY, spoofed
        after = run_cli(project, "todo", "list", "--goal-id", GOAL_ID,
                        registry_path=registry_path, runtime=runtime)
        assert after["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"]
        assert after["todos"] == before["todos"]
        state_path.write_text(original_display, encoding="utf-8")

        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["goals"][0].pop("workspace_guard_policy", None)
        registry_path.write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        monitor = run_cli(
            project, "todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
            "--operation-id", "due-monitor",
            "--text", "[P0] Monitor one due repository lifecycle without repository writes.",
            "--task-class", "continuous_monitor", "--action-kind", "monitor_repository_lifecycle",
            "--task-repository", PROJECT_REPOSITORY, "--claimed-by", PEER_ALPHA,
            "--target-key", "repository-lifecycle", "--cadence", "30m",
            "--next-due-at", "2020-01-01T00:00:00+00:00", "--expires-at", "2099-01-01T00:00:00+00:00",
            registry_path=registry_path, runtime=runtime,
        )
        run_cli(
            project, "todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
            "--operation-id", "unrelated-repair",
            "--text", "[P1] Repair a separate repository-writing control-plane task.",
            "--task-class", "advancement_task", "--action-kind", "repair_workspace_route",
            "--task-repository", TASK_REPOSITORY, "--required-write-scope", "loopx/**",
            "--claimed-by", PEER_ALPHA,
            registry_path=registry_path, runtime=runtime,
        )

        monitor_selected = should_run(project, project, runtime, registry_path)
        assert monitor_selected["decision"] == "run", monitor_selected
        assert "workspace_guard" not in monitor_selected, monitor_selected
        assert "boundary_projection_gap" not in monitor_selected, monitor_selected
        assert monitor_selected["selected_todo"]["todo_id"] == (
            monitor["todo_id"]
        ), monitor_selected
        assert monitor_selected["selected_todo"]["task_repository"] == (
            PROJECT_REPOSITORY
        ), monitor_selected


if __name__ == "__main__":
    main()
