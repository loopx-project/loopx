#!/usr/bin/env python3
"""Smoke-test multiple independent LoopX goals in one repository."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_GOAL = "repo-main-control"
BYPASS_GOAL = "repo-side-bypass"
EXTERNAL_GOAL = "repo-external-state"


def state_text(title: str, next_action: str) -> str:
    return (
        "---\n"
        "status: active-read-only\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Authority Sources\n\n- README.md\n\n"
        "## Operating Contract\n\n- Keep this smoke fixture read-only.\n\n"
        "## Work Clusters\n\n- Map this goal independently.\n\n"
        "## Validation Surfaces\n\n- Smoke script assertions.\n\n"
        "## Private/Public Boundary\n\n- Public-safe fixture only.\n\n"
        f"## Next Action\n\n- {next_action}\n\n"
        "## Progress Ledger\n\n- Connected.\n"
    )


def goal_entry(project: Path, goal_id: str, state_file: str | Path) -> dict:
    return {
        "id": goal_id,
        "domain": "same-repo-multi-goal",
        "status": "active-read-only",
        "repo": str(project),
        "state_file": str(state_file),
        "authority_sources": [{"kind": "doc", "role": "primary", "path": "README.md"}],
        "adapter": {
            "kind": "read_only_project_map_v0",
            "status": "connected-read-only",
        },
    }


def write_fixture(root: Path) -> Path:
    project = root / "project"
    project.mkdir()
    (project / "README.md").write_text("# Same Repo Multi Goal\n", encoding="utf-8")
    registry_path = project / ".loopx" / "registry.json"

    connect_goal(root, registry_path, project, MAIN_GOAL, "Run the main control lane.")
    connect_goal(root, registry_path, project, BYPASS_GOAL, "Run the side bypass lane.")

    external_state = root / "external-state" / "ACTIVE_GOAL_STATE.md"
    external_state.parent.mkdir(parents=True)
    external_state.write_text(state_text("External State", "Create project-local state."), encoding="utf-8")

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"].append(goal_entry(project, EXTERNAL_GOAL, external_state))
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return registry_path


def connect_goal(root: Path, registry_path: Path, project: Path, goal_id: str, objective: str) -> None:
    payload = run_cli(
        root,
        registry_path,
        "connect",
        "--project",
        str(project),
        "--goal-id",
        goal_id,
        "--objective",
        objective,
        "--domain",
        "same-repo-multi-goal",
        "--adapter-kind",
        "read_only_project_map_v0",
        "--adapter-status",
        "connected-read-only",
        "--no-global-sync",
    )
    assert payload["ok"] is True, payload
    assert payload["goal_id"] == goal_id, payload
    assert payload["state_action"] == "created", payload


def run_cli(root: Path, registry_path: Path, *args: str, check: bool = True) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(root / "runtime"),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        check=check,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def assert_local_goal_map(root: Path, registry_path: Path, goal_id: str) -> None:
    payload = run_cli(root, registry_path, "read-only-map", "--goal-id", goal_id, "--dry-run")
    assert payload["ok"] is True, payload
    assert payload["dry_run"] is True, payload
    assert payload["project_map"]["project_registry_exists"] is True, payload
    assert payload["project_map"]["goal_state_dir_exists"] is True, payload
    assert payload["project_map"]["active_state_file_exists"] is True, payload
    assert "project_local_goal_state_not_detected" not in payload["residual_risks"], payload
    assert f"goal_state_dir 1/1" in payload["health_check"], payload


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-multi-goal-same-repo-") as tmp:
        root = Path(tmp)
        registry_path = write_fixture(root)

        registry_payload = run_cli(root, registry_path, "registry")
        assert registry_payload["ok"] is True, registry_payload
        assert registry_payload["goal_count"] == 3, registry_payload
        goals_by_id = {goal["id"]: goal for goal in registry_payload["goals"]}
        assert goals_by_id[MAIN_GOAL]["repo_goal_count"] == 3, registry_payload
        assert goals_by_id[BYPASS_GOAL]["repo_goal_count"] == 3, registry_payload
        assert goals_by_id[MAIN_GOAL]["state_file_abs"] != goals_by_id[BYPASS_GOAL]["state_file_abs"], registry_payload

        assert_local_goal_map(root, registry_path, MAIN_GOAL)
        assert_local_goal_map(root, registry_path, BYPASS_GOAL)

        external_map = run_cli(root, registry_path, "read-only-map", "--goal-id", EXTERNAL_GOAL, "--dry-run")
        assert external_map["ok"] is True, external_map
        assert external_map["project_map"]["project_registry_exists"] is True, external_map
        assert external_map["project_map"]["active_state_file_exists"] is True, external_map
        assert external_map["project_map"]["goal_state_dir_exists"] is False, external_map
        assert f"project_goal_state_dir_not_detected:{EXTERNAL_GOAL}" in external_map["residual_risks"], external_map
        assert "project_local_goal_state_not_detected" in external_map["residual_risks"], external_map

        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["goals"][2]["state_file"] = next(
            goal["state_file"] for goal in registry["goals"] if goal["id"] == MAIN_GOAL
        )
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        duplicate_payload = run_cli(root, registry_path, "registry", check=False)
        assert duplicate_payload["ok"] is False, duplicate_payload
        assert any("state_file shared by multiple goals" in item for item in duplicate_payload["problems"]), duplicate_payload

    print("multi-goal-same-repo-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
