from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from loopx.capabilities.goal_closeout.machine_defaults import (
    GOAL_CLOSEOUT_CONFIGURATION_SCHEMA,
)
from loopx.capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from loopx.capabilities.machine_configuration.store import (
    configure_machine_configuration,
)
from loopx.control_plane.goals import activation_service
from loopx.control_plane.goals.activation_service import set_goal_activation_state
from loopx.global_registry import retire_global_registry_goals, sync_project_registry_to_global
from loopx.lifecycle.goal_closeout import GOAL_CLOSEOUT_LEDGER_FILENAME
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

_LOCK_PROBE = r"""
import pathlib
import sys

repo_root, runtime_root, goal_id, registry_path, output_path = sys.argv[1:6]
sys.path.insert(0, repo_root)

from loopx.control_plane.coordination.shadow_management import (
    runtime_artifact_lock_target,
)
from loopx.file_lock import exclusive_cross_runtime_file_lock


def probe(target):
    try:
        with exclusive_cross_runtime_file_lock(target, timeout_seconds=0):
            return "FREE"
    except Exception:
        return "HELD"


lines = [
    probe(runtime_artifact_lock_target(pathlib.Path(runtime_root), goal_id)),
    probe(pathlib.Path(registry_path)),
]
pathlib.Path(output_path).write_text("\n".join(lines), encoding="utf-8")
"""


def _configure_lock_probe_closeout(
    root: Path,
    *,
    output_path: Path,
    goal_id: str,
    registry_path: Path,
) -> None:
    configuration = {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {
            "goal_closeout": {
                "schema_version": GOAL_CLOSEOUT_CONFIGURATION_SCHEMA,
                "command": [
                    sys.executable,
                    "-c",
                    _LOCK_PROBE,
                    str(Path(__file__).resolve().parents[2]),
                    str(root),
                    goal_id,
                    str(registry_path),
                    str(output_path),
                ],
                "timeout_seconds": 10,
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


def _registered_closeout_goal(tmp_path: Path) -> tuple[Path, Path, Path]:
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
    return source_registry, runtime_root / "registry.global.json", runtime_root


def test_stop_runs_closeout_once_and_dry_run_does_not(tmp_path: Path) -> None:
    source_registry, global_registry, runtime_root = _registered_closeout_goal(tmp_path)
    project = source_registry.parent.parent
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


@pytest.mark.parametrize("failure", ["extra_registry_read", "hook_preparation", "no_hook_configured"])
def test_stop_cli_preserves_verified_receipt_after_closeout_failure(tmp_path, monkeypatch, capsys, failure):
    from loopx.cli import main

    source_registry, global_registry, runtime_root = _registered_closeout_goal(tmp_path)
    payload_path = tmp_path / "hook-payloads.jsonl"
    if failure != "no_hook_configured":
        _configure_closeout(runtime_root, payload_path)
    original_readback = activation_service._readback
    original_load_registry = activation_service.load_registry
    original_resolve_runtime_root = activation_service.resolve_runtime_root
    readback_complete = False

    def finish_readback(**kwargs):
        nonlocal readback_complete
        result = original_readback(**kwargs)
        readback_complete = True
        return result

    def read_registry(path):
        if readback_complete:
            raise OSError("a late registry read must not be needed")
        return original_load_registry(path)

    def resolve_runtime_root(*args, **kwargs):
        if readback_complete:
            raise OSError("sensitive runtime path from preparation failure")
        return original_resolve_runtime_root(*args, **kwargs)

    monkeypatch.setattr(activation_service, "_readback", finish_readback)
    if failure == "hook_preparation":
        monkeypatch.setattr(activation_service, "resolve_runtime_root", resolve_runtime_root)
    else:
        monkeypatch.setattr(activation_service, "load_registry", read_registry)
    exit_code = main(["--registry", str(global_registry), "--format", "json", "goal-lifecycle",
        "--operation", "stop", "--goal-id", "goal-one", "--actor-kind", "owner", "--execute"])
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert readback_complete is True
    for registry in (source_registry, global_registry):
        (goal,) = json.loads(registry.read_text(encoding="utf-8"))["goals"]
        assert goal["activation"]["state"] == "stopped"
    assert exit_code == 0 and result["ok"] and result["written"], result
    assert result["readback"]["verified"] and not result["partial_write"]
    if failure == "hook_preparation":
        assert result["closeout"]["status"] == "internal_error"
        assert result["closeout"]["error_type"] == "OSError"
        assert "sensitive runtime path" not in output.out
        assert not payload_path.exists()
    elif failure == "no_hook_configured":
        assert result["closeout"]["status"] == "not_configured"
        assert not payload_path.exists()
    else:
        assert result["closeout"]["status"] == "success"
        hook_payload = json.loads(payload_path.read_text(encoding="utf-8"))
        assert hook_payload["repo"] == str(source_registry.parent.parent)
        assert hook_payload["runtime_root"] == str(runtime_root)
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


def test_archive_runs_closeout_after_canonical_locks_are_released(tmp_path: Path) -> None:
    project = tmp_path / "project"
    registry_path = project / ".loopx" / "registry.json"
    runtime_root = project / ".loopx" / "runtime"
    source = runtime_root / "goals" / "locked-goal"
    source.mkdir(parents=True)
    (source / "ACTIVE_GOAL_STATE.md").write_text("state\n", encoding="utf-8")
    _write_json(
        registry_path,
        {
            "schema_version": 1,
            "common_runtime_root": ".loopx/runtime",
            "goals": [
                {
                    "id": "locked-goal",
                    "display_name": "Archive lock fixture",
                    "repo": str(project),
                    "state_file": ".loopx/runtime/goals/locked-goal/ACTIVE_GOAL_STATE.md",
                }
            ],
        },
    )
    probe_path = tmp_path / "lock-probe.txt"
    _configure_lock_probe_closeout(
        runtime_root,
        output_path=probe_path,
        goal_id="locked-goal",
        registry_path=registry_path,
    )

    result = archive_runtime_goal(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id="locked-goal",
        archive_root=None,
        allow_registered=True,
        execute=True,
    )
    assert result["ok"] is True, result
    assert result["closeout"]["status"] == "success", result
    assert probe_path.read_text(encoding="utf-8").splitlines() == ["FREE", "FREE"]
