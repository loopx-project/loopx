from __future__ import annotations

import errno
import json
import sys
from pathlib import Path
from types import MappingProxyType

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
from loopx.control_plane.goals.activation_service import set_goal_activation_state
from loopx.global_registry import (
    retire_global_registry_goals,
    sync_project_registry_to_global,
)
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


def test_stop_resolves_relative_state_file_to_project_root(tmp_path: Path) -> None:
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
                    "id": "relative-stop",
                    "display_name": "Relative stop fixture",
                    "repo": str(project),
                    "state_file": "goals/relative-stop/ACTIVE_GOAL_STATE.md",
                    "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
                }
            ],
        },
    )
    synced = sync_project_registry_to_global(
        registry_path=source_registry,
        runtime_root_override=str(runtime_root),
        goal_id="relative-stop",
        dry_run=False,
    )
    assert synced["ok"] is True
    global_registry = runtime_root / "registry.global.json"
    global_payload = json.loads(global_registry.read_text(encoding="utf-8"))
    global_payload["goals"][0]["state_file"] = (
        "goals/relative-stop/ACTIVE_GOAL_STATE.md"
    )
    _write_json(global_registry, global_payload)
    payload_path = tmp_path / "payloads.jsonl"
    _configure_closeout(runtime_root, payload_path)

    stopped = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="relative-stop",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    assert stopped["ok"] is True, stopped
    assert stopped["closeout"]["status"] == "success", stopped
    payload = json.loads(payload_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["state_file"] == str(
        project / "goals" / "relative-stop" / "ACTIVE_GOAL_STATE.md"
    )


def test_stop_keeps_committed_receipt_when_registry_read_fails_after_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from loopx.control_plane.goals import activation_service

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
                    "id": "masked-stop",
                    "display_name": "Masked stop fixture",
                    "repo": str(project),
                    "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
                }
            ],
        },
    )
    synced = sync_project_registry_to_global(
        registry_path=source_registry,
        runtime_root_override=str(runtime_root),
        goal_id="masked-stop",
        dry_run=False,
    )
    assert synced["ok"] is True
    global_registry = runtime_root / "registry.global.json"
    payload_path = tmp_path / "payloads.jsonl"
    _configure_closeout(runtime_root, payload_path)

    original_load_registry = activation_service.load_registry

    def _flaky_load_registry(path: Path) -> dict[str, object]:
        snapshot = original_load_registry(path)
        for goal in snapshot.get("goals", []):
            if isinstance(goal, dict) and goal.get("state") == "stopped":
                raise RuntimeError("registry re-read failed after commit")
        return snapshot

    monkeypatch.setattr(activation_service, "load_registry", _flaky_load_registry)

    stopped = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="masked-stop",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    assert stopped["ok"] is True, stopped
    assert stopped["written"] is True, stopped
    assert stopped["after_state"] == "stopped", stopped
    # The closeout reads the transaction's confirmed snapshot.  A registry
    # load that only fails once the stop is committed must never run, so the
    # committed receipt keeps its successful hook result.
    assert stopped["closeout"]["status"] == "success", stopped
    ledger_path = runtime_root / "goals" / "masked-stop" / GOAL_CLOSEOUT_LEDGER_FILENAME
    assert ledger_path.is_file()


def test_retire_resolves_relative_state_file_to_registry_root(tmp_path: Path) -> None:
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
                    "id": "relative-retired",
                    "display_name": "Relative retire fixture",
                    "repo": str(project),
                    "source_registry": str(tmp_path / "missing" / "registry.json"),
                    "state_file": "goals/relative-retired/ACTIVE_GOAL_STATE.md",
                }
            ],
        },
    )
    payload_path = tmp_path / "payloads.jsonl"
    _configure_closeout(runtime_root, payload_path)

    result = retire_global_registry_goals(
        runtime_root_override=str(runtime_root),
        goal_ids=["relative-retired"],
        execute=True,
    )
    assert result["ok"] is True, result
    assert result["closeouts"][0]["status"] == "success", result
    payload = json.loads(payload_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["state_file"] == str(
        project / "goals" / "relative-retired" / "ACTIVE_GOAL_STATE.md"
    )


def test_archive_resolves_global_relative_state_file_from_project_repo(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    # The project owns the state file; the runtime holds only artifacts, so a
    # runtime-relative resolution would hand the hook an unreadable path.
    project_state = project / "ACTIVE_GOAL_STATE.md"
    project_state.write_text("project state\n", encoding="utf-8")
    source = runtime_root / "goals" / "global-archive"
    source.mkdir(parents=True)
    (source / "artifact.txt").write_text("kept\n", encoding="utf-8")
    global_registry = runtime_root / "registry.global.json"
    _write_json(
        global_registry,
        {
            "schema_version": 0.1,
            "registry_role": "global-local",
            "goals": [
                {
                    "id": "global-archive",
                    "display_name": "Global archive fixture",
                    "repo": str(project),
                    "state_file": "ACTIVE_GOAL_STATE.md",
                }
            ],
        },
    )
    payload_path = tmp_path / "payloads.jsonl"
    _configure_closeout(runtime_root, payload_path)

    result = archive_runtime_goal(
        registry_path=global_registry,
        runtime_root_override=str(runtime_root),
        goal_id="global-archive",
        archive_root=None,
        allow_registered=True,
        execute=True,
    )
    assert result["ok"] is True, result
    assert result["closeout"]["status"] == "success", result
    assert not source.exists()
    payload = json.loads(payload_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["state_file"] == str(project_state)
    assert Path(payload["state_file"]).read_text(encoding="utf-8") == "project state\n"


def test_orphaned_global_stop_resolves_relative_state_file_from_repo(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    project_state = project / "ACTIVE_GOAL_STATE.md"
    project_state.write_text("orphan state\n", encoding="utf-8")
    global_registry = runtime_root / "registry.global.json"
    _write_json(
        global_registry,
        {
            "schema_version": 0.1,
            "registry_role": "global-local",
            "goals": [
                {
                    "id": "orphaned-stop",
                    "display_name": "Orphaned stop fixture",
                    "repo": str(project),
                    "source_registry": str(tmp_path / "missing" / "registry.json"),
                    "state_file": "ACTIVE_GOAL_STATE.md",
                    "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
                }
            ],
        },
    )
    payload_path = tmp_path / "payloads.jsonl"
    _configure_closeout(runtime_root, payload_path)

    stopped = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="orphaned-stop",
        state="stopped",
        runtime_root_override=str(runtime_root),
        actor_kind="owner",
        execute=True,
    )
    assert stopped["ok"] is True, stopped
    assert stopped["closeout"]["status"] == "success", stopped
    payload = json.loads(payload_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["state_file"] == str(project_state)
    assert Path(payload["state_file"]).read_text(encoding="utf-8") == "orphan state\n"


def test_archive_without_hook_keeps_success_for_unusable_state_file(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    registry_path = project / ".loopx" / "registry.json"
    runtime_root = project / ".loopx" / "runtime"
    source = runtime_root / "goals" / "stale-state-goal"
    source.mkdir(parents=True)
    (source / "artifact.txt").write_text("kept\n", encoding="utf-8")
    _write_json(
        registry_path,
        {
            "schema_version": 1,
            "common_runtime_root": ".loopx/runtime",
            "goals": [
                {
                    "id": "stale-state-goal",
                    "display_name": "Stale state fixture",
                    "repo": str(project),
                    # A stale user alias whose expansion raises; with no
                    # hook configured this must not be touched at all.
                    "state_file": "~loopx-retired-operator/goals/stale/ACTIVE_GOAL_STATE.md",
                }
            ],
        },
    )

    result = archive_runtime_goal(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id="stale-state-goal",
        archive_root=None,
        allow_registered=True,
        execute=True,
    )
    assert result["ok"] is True, result
    archive_path = Path(result["archive_path"])
    assert not source.exists()
    assert (archive_path / "artifact.txt").read_text(encoding="utf-8") == "kept\n"
    assert result["closeout"]["status"] == "not_configured", result
    assert result["closeout"]["ledger_recorded"] is False
    assert not (archive_path / GOAL_CLOSEOUT_LEDGER_FILENAME).exists()


def _two_goal_global_registry(tmp_path: Path) -> tuple[Path, Path]:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    global_registry = runtime_root / "registry.global.json"
    _write_json(
        global_registry,
        {
            "schema_version": "0.1",
            "registry_role": "global-local",
            "goals": [
                {
                    "id": "retired-first",
                    "display_name": "First retired fixture",
                    "repo": str(project),
                    "source_registry": str(tmp_path / "missing" / "registry.json"),
                    "state_file": "ACTIVE_GOAL_STATE.md",
                },
                {
                    "id": "retired-second",
                    "display_name": "Second retired fixture",
                    "repo": str(project),
                    "state_file": "ACTIVE_GOAL_STATE.md",
                },
            ],
        },
    )
    return runtime_root, global_registry


def test_retire_keeps_committed_receipt_when_hook_preparation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from loopx.lifecycle import goal_closeout

    runtime_root, global_registry = _two_goal_global_registry(tmp_path)

    def _broken_configuration(root: Path):
        raise RuntimeError("hook preparation failed after commit")

    monkeypatch.setattr(
        goal_closeout,
        "goal_closeout_configuration",
        _broken_configuration,
    )

    result = retire_global_registry_goals(
        runtime_root_override=str(runtime_root),
        goal_ids=["retired-first", "retired-second"],
        execute=True,
    )
    assert result["ok"] is True, result
    assert result["wrote"] is True, result
    assert result["global_goal_count_after"] == 0
    assert not (runtime_root / "registry.global.json").read_text(encoding="utf-8").count(
        '"retired'
    )
    # Both removed records still report their own diagnostic; one unusable
    # preparation does not hide the other or the retirement itself.
    assert [item["status"] for item in result["closeouts"]] == [
        "preparation_error",
        "preparation_error",
    ], result
    assert all(item["ledger_recorded"] is False for item in result["closeouts"])
    assert not (runtime_root / "closeout").exists()


def test_retire_without_hook_ignores_unusable_state_file(tmp_path: Path) -> None:
    runtime_root, global_registry = _two_goal_global_registry(tmp_path)
    payload = json.loads(global_registry.read_text(encoding="utf-8"))
    for goal in payload["goals"]:
        # A stale alias raises on expansion; with no hook configured nothing
        # may resolve it, and the retirement receipt must stay successful.
        goal["state_file"] = "~loopx-retired-operator/goals/stale/ACTIVE_GOAL_STATE.md"
    _write_json(global_registry, payload)

    result = retire_global_registry_goals(
        runtime_root_override=str(runtime_root),
        goal_ids=["retired-first", "retired-second"],
        execute=True,
    )
    assert result["ok"] is True, result
    assert result["wrote"] is True, result
    assert result["global_goal_count_after"] == 0
    assert [item["status"] for item in result["closeouts"]] == [
        "not_configured",
        "not_configured",
    ], result
    assert not (runtime_root / "closeout").exists()


def test_stop_runs_closeout_for_committed_transition_when_projection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from loopx.control_plane.goals import activation_service

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
                    "id": "projected-stop",
                    "display_name": "Projected stop fixture",
                    "repo": str(project),
                    "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
                }
            ],
        },
    )
    synced = sync_project_registry_to_global(
        registry_path=source_registry,
        runtime_root_override=str(runtime_root),
        goal_id="projected-stop",
        dry_run=False,
    )
    assert synced["ok"] is True
    global_registry = runtime_root / "registry.global.json"
    payload_path = tmp_path / "payloads.jsonl"
    _configure_closeout(runtime_root, payload_path)

    real_sync = activation_service.sync_project_registry_to_global
    failures = {"remaining": 1}

    def _flaky_sync(**kwargs):
        if failures["remaining"]:
            failures["remaining"] -= 1
            return {
                "ok": False,
                "error": "shared registry write is temporarily unavailable",
                "recommended_action": "rerun loopx sync-global --goal-id projected-stop",
            }
        return real_sync(**kwargs)

    monkeypatch.setattr(
        activation_service, "sync_project_registry_to_global", _flaky_sync
    )

    first = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="projected-stop",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    # The projection failed, but the source stop committed and the hook still
    # ran once for that committed transition.
    assert first["ok"] is False, first
    assert first["partial_write"] is True, first
    assert first["written"] is True, first
    assert first["closeout"]["status"] == "success", first
    assert len(_ledger(runtime_root, "projected-stop")) == 1

    second = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="projected-stop",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    assert second["ok"] is True, second
    assert second["projection_reconciled"] is True, second
    assert "closeout" not in second
    # The retry reconciles the projection without a second hook invocation.
    assert len(_ledger(runtime_root, "projected-stop")) == 1
    assert len(payload_path.read_text(encoding="utf-8").splitlines()) == 1



def test_stop_runs_closeout_when_shared_registry_writer_raises_eio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An EIO from the shared-registry writer must not erase the stop."""

    from loopx import global_registry as global_registry_module

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
                    "id": "eio-stop",
                    "display_name": "EIO stop fixture",
                    "repo": str(project),
                    "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
                }
            ],
        },
    )
    synced = sync_project_registry_to_global(
        registry_path=source_registry,
        runtime_root_override=str(runtime_root),
        goal_id="eio-stop",
        dry_run=False,
    )
    assert synced["ok"] is True
    global_registry = runtime_root / "registry.global.json"
    payload_path = tmp_path / "payloads.jsonl"
    _configure_closeout(runtime_root, payload_path)

    real_write = global_registry_module.write_json
    failures = {"remaining": 1}

    target = global_registry.resolve()

    def _flaky_write(path, payload):
        if failures["remaining"] and Path(path).resolve() == target:
            failures["remaining"] -= 1
            raise OSError(errno.EIO, "Input/output error")
        return real_write(path, payload)

    monkeypatch.setattr(global_registry_module, "write_json", _flaky_write)

    first = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="eio-stop",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    # The projection raised, but the committed source stop keeps its receipt
    # and still dispatches the configured hook exactly once.
    assert first["written"] is True, first
    assert first["ok"] is False, first
    assert first["partial_write"] is True, first
    assert first["global_sync"]["error_kind"] == "global_sync_failed", first
    assert first["closeout"]["status"] == "success", first
    assert len(_ledger(runtime_root, "eio-stop")) == 1

    second = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="eio-stop",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    assert second["ok"] is True, second
    assert second["projection_reconciled"] is True, second
    assert "closeout" not in second
    assert len(_ledger(runtime_root, "eio-stop")) == 1
    assert len(payload_path.read_text(encoding="utf-8").splitlines()) == 1


def test_stop_runs_closeout_when_readback_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read fault after the commit must not erase the committed stop."""

    from loopx.control_plane.goals import activation_service

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
                    "id": "readback-stop",
                    "display_name": "Readback stop fixture",
                    "repo": str(project),
                    "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
                }
            ],
        },
    )
    synced = sync_project_registry_to_global(
        registry_path=source_registry,
        runtime_root_override=str(runtime_root),
        goal_id="readback-stop",
        dry_run=False,
    )
    assert synced["ok"] is True
    global_registry = runtime_root / "registry.global.json"
    payload_path = tmp_path / "payloads.jsonl"
    _configure_closeout(runtime_root, payload_path)

    def _raising_readback(**kwargs):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(
        activation_service, "_readback", _raising_readback
    )

    first = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="readback-stop",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    assert first["written"] is True, first
    assert first["ok"] is False, first
    assert first["partial_write"] is True, first
    assert first["readback"]["status"] == "readback_failed", first
    assert "Input/output error" in first["readback"]["error"], first
    assert first["closeout"]["status"] == "success", first
    assert len(_ledger(runtime_root, "readback-stop")) == 1


def test_stop_without_hook_ignores_unusable_state_file(tmp_path: Path) -> None:
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
                    "id": "stale-stop",
                    "display_name": "Stale alias stop fixture",
                    "repo": str(project),
                    # A stale user alias raises on expansion; with no hook
                    # configured the stop must never resolve it.
                    "state_file": "~loopx-stale-operator/goals/stale/ACTIVE_GOAL_STATE.md",
                    "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
                }
            ],
        },
    )
    synced = sync_project_registry_to_global(
        registry_path=source_registry,
        runtime_root_override=str(runtime_root),
        goal_id="stale-stop",
        dry_run=False,
    )
    assert synced["ok"] is True
    global_registry = runtime_root / "registry.global.json"

    stopped = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="stale-stop",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    assert stopped["ok"] is True, stopped
    assert stopped["written"] is True, stopped
    assert stopped["closeout"]["status"] == "not_configured", stopped
    assert stopped["closeout"]["ledger_recorded"] is False, stopped
    assert "error" not in stopped["closeout"], stopped
    assert not (runtime_root / "goals" / "stale-stop").exists()


def test_stop_closeout_accepts_a_read_only_registry_mapping(tmp_path: Path) -> None:
    from loopx.control_plane.goals.activation_service import _stop_closeout_payload

    runtime_root = tmp_path / "runtime"
    source_registry = tmp_path / "project" / ".loopx" / "registry.json"
    payload_path = tmp_path / "payloads.jsonl"
    _configure_closeout(runtime_root, payload_path)
    snapshot = MappingProxyType({
        "common_runtime_root": str(runtime_root),
        "goals": [],
    })
    result = _stop_closeout_payload(
        source_registry=source_registry,
        registry_snapshot=snapshot,
        runtime_root_override=None,
        goal_id="mapping-stop",
        closeout_goal={"id": "mapping-stop", "repo": str(tmp_path / "project")},
    )

    assert result["status"] == "success", result
    assert len(_ledger(runtime_root, "mapping-stop")) == 1
