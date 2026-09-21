from __future__ import annotations

import json
from pathlib import Path
import os
import subprocess
import sys
from typing import Any

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from loopx.history import load_registry
from loopx.registry import registry_goals


REPO_ROOT = Path(__file__).resolve().parents[2]
GOAL_ID = "goal-actions-fixture"


def _run_projected_argv(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the catalog's complete argv without injecting hidden context."""
    assert argv and argv[0] == "loopx"
    # Use the checkout's launcher as the executable while preserving every
    # argument emitted by the public action contract verbatim.
    command = [str(REPO_ROOT / "scripts" / "loopx"), *argv[1:]]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["LOOPX_PYTHON"] = sys.executable
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def _write_registry(tmp_path: Path, *, activation_state: str = "active") -> tuple[Path, Path]:
    project = tmp_path / "project"
    runtime_root = tmp_path / "runtime"
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    goal: dict[str, Any] = {
        "id": GOAL_ID,
        "display_name": "Goal actions fixture",
        "repo": str(project),
        "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
    }
    if activation_state == "stopped":
        goal["activation_state"] = "stopped"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(runtime_root),
                "goals": [goal],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return project, registry_path


def _run_cli(registry_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_typescript_projection_owns_legal_lifecycle_action() -> None:
    active = effect_runtime_result(
        "goal.operator_actions.project",
        {
            "schema_version": "loopx_goal_action_projection_request_v2",
            "goal_id": GOAL_ID,
            "registry_locator": "/tmp/registry.json",
            "runtime_root_locator": "/tmp/runtime",
            "activation_state": "active",
            "state_fingerprint": "a" * 64,
        },
    )
    stopped = effect_runtime_result(
        "goal.operator_actions.project",
        {
            "schema_version": "loopx_goal_action_projection_request_v2",
            "goal_id": GOAL_ID,
            "registry_locator": "/tmp/registry.json",
            "runtime_root_locator": "/tmp/runtime",
            "activation_state": "stopped",
            "state_fingerprint": "b" * 64,
        },
    )

    assert active["schema_version"] == "loopx_goal_action_catalog_v1"
    assert [action["action_id"] for action in active["actions"]] == ["goal.stop"]
    assert active["actions"][0]["target_activation_state"] == "stopped"
    assert active["actions"][0]["target_operator_state"] == "quiet"
    assert stopped["activation_state"] == "stopped"
    assert [action["action_id"] for action in stopped["actions"]] == ["goal.resume"]
    assert stopped["actions"][0]["target_activation_state"] == "active"


def test_typescript_projection_rejects_invalid_state_and_fingerprint() -> None:
    with pytest.raises(EffectRuntimeRejected):
        effect_runtime_result(
            "goal.operator_actions.project",
            {
                "schema_version": "loopx_goal_action_projection_request_v2",
                "goal_id": GOAL_ID,
                "registry_locator": "/tmp/registry.json",
                "runtime_root_locator": "/tmp/runtime",
                "activation_state": "watching",
                "state_fingerprint": "not-a-digest",
            },
        )


def test_catalog_contains_only_fresh_lifecycle_action(
    tmp_path: Path,
) -> None:
    from loopx.control_plane.goals.operator_actions import build_goal_action_catalog

    _project, registry_path = _write_registry(tmp_path)

    packet = build_goal_action_catalog(
        registry_path=registry_path,
        goal_id=GOAL_ID,
    )

    assert [action["action_id"] for action in packet["actions"]] == ["goal.stop"]
    assert all(action["goal_id"] == GOAL_ID for action in packet["actions"])
    assert all(action["requires_confirmation"] is True for action in packet["actions"])
    assert packet["authority_owner"] == "typescript_control_plane"


def test_goal_actions_cli_projects_exact_fresh_execution_identity(
    tmp_path: Path,
) -> None:
    project, registry_path = _write_registry(tmp_path)

    result = _run_cli(registry_path, "goal-actions", "--goal-id", GOAL_ID)

    assert result.returncode == 0, result.stderr
    packet = json.loads(result.stdout)
    assert packet["ok"] is True
    assert packet["goal_id"] == GOAL_ID
    assert len(packet["state_fingerprint"]) == 64
    action = next(
        item for item in packet["actions"] if item["action_id"] == "goal.stop"
    )
    assert action["action_id"] == "goal.stop"
    assert action["execution"]["expected_state_fingerprint"] == packet["state_fingerprint"]
    assert action["execution"]["argv"] == [
        "loopx",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(tmp_path / "runtime"),
        "--format",
        "json",
        "goal-lifecycle",
        "--goal-id",
        GOAL_ID,
        "--operation",
        "stop",
        "--actor-kind",
        "owner",
        "--expected-state-fingerprint",
        packet["state_fingerprint"],
        "--execute",
    ]
    assert project.exists()


def test_projected_action_runs_verbatim_against_non_default_registry(
    tmp_path: Path,
) -> None:
    _project, registry_path = _write_registry(tmp_path)
    projected = _run_cli(registry_path, "goal-actions", "--goal-id", GOAL_ID)
    action = json.loads(projected.stdout)["actions"][0]

    executed = _run_projected_argv(action["execution"]["argv"])

    assert executed.returncode == 0, executed.stderr
    payload = json.loads(executed.stdout)
    assert payload["ok"] is True
    assert payload["written"] is True
    assert payload["readback"]["verified"] is True


def test_projected_lifecycle_action_rejects_stale_registry_without_writing(
    tmp_path: Path,
) -> None:
    _project, registry_path = _write_registry(tmp_path)
    projected = _run_cli(registry_path, "goal-actions", "--goal-id", GOAL_ID)
    assert projected.returncode == 0, projected.stderr
    action = next(
        item
        for item in json.loads(projected.stdout)["actions"]
        if item["action_id"] == "goal.stop"
    )
    before = load_registry(registry_path)
    registry_goals(before)[0]["display_name"] = "Changed after projection"
    registry_path.write_text(
        json.dumps(before, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    changed_bytes = registry_path.read_bytes()

    executed = _run_projected_argv(action["execution"]["argv"])

    assert executed.returncode == 1
    payload = json.loads(executed.stdout)
    assert payload["ok"] is False
    assert payload["error_kind"] == "goal_action_stale"
    assert payload["written"] is False
    assert registry_path.read_bytes() == changed_bytes


def test_fresh_projected_lifecycle_action_applies_and_projects_resume(
    tmp_path: Path,
) -> None:
    _project, registry_path = _write_registry(tmp_path)
    projected = _run_cli(registry_path, "goal-actions", "--goal-id", GOAL_ID)
    stop = next(
        item
        for item in json.loads(projected.stdout)["actions"]
        if item["action_id"] == "goal.stop"
    )

    executed = _run_projected_argv(stop["execution"]["argv"])

    assert executed.returncode == 0, executed.stderr
    applied = json.loads(executed.stdout)
    assert applied["readback"]["verified"] is True
    follow_up = _run_cli(registry_path, "goal-actions", "--goal-id", GOAL_ID)
    assert follow_up.returncode == 0, follow_up.stderr
    resume = next(
        item
        for item in json.loads(follow_up.stdout)["actions"]
        if item["action_id"] == "goal.resume"
    )
    assert resume["action_id"] == "goal.resume"
    assert resume["execution"]["argv"][-1] == "--execute"
