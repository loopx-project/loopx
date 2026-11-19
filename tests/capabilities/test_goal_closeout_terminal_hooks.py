from __future__ import annotations

import json
import sys
from pathlib import Path

from loopx.capabilities.goal_closeout.hooks import GOAL_CLOSEOUT_LEDGER_FILENAME
from loopx.capabilities.goal_closeout.machine_defaults import (
    GOAL_CLOSEOUT_CONFIGURATION_SCHEMA,
)
from loopx.capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from loopx.capabilities.machine_configuration.store import (
    configure_machine_configuration,
)
from loopx.control_plane.goals.activation_service import set_goal_activation_state
from loopx.global_registry import retire_global_registry_goals, sync_project_registry_to_global
from loopx.runtime import archive_runtime_goal


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _configure_closeout(root: Path, payload_path: Path) -> None:
    configuration = {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {
            "goal_closeout": {
                "schema_version": GOAL_CLOSEOUT_CONFIGURATION_SCHEMA,
                "command": [
                    sys.executable,
                    "-c",
                    "import sys; open(sys.argv[1], 'a').write(sys.stdin.read())",
                    str(payload_path),
                ],
                "timeout_seconds": 5,
            }
        },
    }
    registry = build_builtin_machine_configuration_registry()
    preview = configure_machine_configuration(
        runtime_root=root,
        configuration=configuration,
        registry=registry,
    )
    payload = configure_machine_configuration(
        runtime_root=root,
        configuration=configuration,
        registry=registry,
        execute=True,
        expected_plan_revision=preview["plan_revision"],
    )
    assert payload["ok"], payload


def _ledger(root: Path, goal_id: str) -> list[dict[str, object]]:
    path = root / "goals" / goal_id / GOAL_CLOSEOUT_LEDGER_FILENAME
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_stop_runs_closeout_once_and_dry_run_does_not(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    source_registry = project / ".loopx" / "registry.json"
    _write_json(
        source_registry,
        {
            "schema_version": "0.1",
            "common_runtime_root": str(runtime_root),
            "goals": [
                {
                    "id": "goal-one",
                    "display_name": "Closeout fixture",
                    "repo": str(project),
                    "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
                }
            ],
        },
    )
    synced = sync_project_registry_to_global(
        registry_path=source_registry,
        runtime_root_override=str(runtime_root),
        goal_id="goal-one",
        dry_run=False,
    )
    assert synced["ok"] is True
    global_registry = runtime_root / "registry.global.json"
    payload_path = tmp_path / "payloads.jsonl"
    _configure_closeout(runtime_root, payload_path)

    preview = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        actor_kind="owner",
        execute=False,
    )
    assert "closeout" not in preview

    stopped = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    assert stopped["ok"] is True, stopped
    assert stopped["closeout"]["status"] == "success", stopped
    payload = json.loads(payload_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["goal_id"] == "goal-one"
    assert payload["terminal_reason"] == "stopped"
    assert payload["repo"] == str(project)
    rows = _ledger(runtime_root, "goal-one")
    assert len(rows) == 1

    repeated = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    assert "closeout" not in repeated
    assert len(_ledger(runtime_root, "goal-one")) == 1


def test_retire_runs_closeout_for_each_retired_goal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    global_registry = runtime_root / "registry.global.json"
    project = tmp_path / "project"
    _write_json(
        global_registry,
        {
            "schema_version": "0.1",
            "registry_role": "global-local",
            "goals": [
                {
                    "id": "retired-goal",
                    "display_name": "Retired fixture",
                    "repo": str(project),
                    "source_registry": str(tmp_path / "missing" / "registry.json"),
                    "state_file": str(tmp_path / "missing" / "ACTIVE_GOAL_STATE.md"),
                }
            ],
        },
    )
    payload_path = tmp_path / "payloads.jsonl"
    _configure_closeout(runtime_root, payload_path)

    result = retire_global_registry_goals(
        runtime_root_override=str(runtime_root),
        goal_ids=["retired-goal"],
        execute=True,
    )
    assert result["ok"] is True, result
    assert len(result["closeouts"]) == 1
    assert result["closeouts"][0]["status"] == "success", result
    payload = json.loads(payload_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["goal_id"] == "retired-goal"
    assert payload["terminal_reason"] == "retired"
    assert payload["repo"] == str(project)
    ledger_path = Path(result["closeouts"][0]["ledger_path"])
    assert ledger_path.is_file()
    assert json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[0])["status"] == "success"


def test_archive_runs_closeout_after_move_without_recreating_source(tmp_path: Path) -> None:
    project = tmp_path / "project"
    registry_path = project / ".loopx" / "registry.json"
    runtime_root = project / ".loopx" / "runtime"
    source = runtime_root / "goals" / "archived-goal"
    source.mkdir(parents=True)
    (source / "ACTIVE_GOAL_STATE.md").write_text("state\n", encoding="utf-8")
    (source / "expected.txt").write_text("kept\n", encoding="utf-8")
    _write_json(
        registry_path,
        {
            "schema_version": 1,
            "common_runtime_root": ".loopx/runtime",
            "goals": [
                {
                    "id": "archived-goal",
                    "display_name": "Archive fixture",
                    "repo": str(project),
                    "state_file": ".loopx/runtime/goals/archived-goal/ACTIVE_GOAL_STATE.md",
                }
            ],
        },
    )
    payload_path = tmp_path / "payloads.jsonl"
    _configure_closeout(runtime_root, payload_path)

    result = archive_runtime_goal(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id="archived-goal",
        archive_root=None,
        allow_registered=True,
        execute=True,
    )
    assert result["ok"] is True, result
    assert result["closeout"]["status"] == "success", result
    archive_path = Path(result["archive_path"])
    assert not source.exists()
    assert (archive_path / "expected.txt").read_text(encoding="utf-8") == "kept\n"
    assert (archive_path / GOAL_CLOSEOUT_LEDGER_FILENAME).is_file()
    payload = json.loads(payload_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["goal_id"] == "archived-goal"
    assert payload["terminal_reason"] == "archived"
    assert payload["state_file"] == str(archive_path / "ACTIVE_GOAL_STATE.md")
