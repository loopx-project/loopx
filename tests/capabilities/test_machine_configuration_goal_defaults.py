from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from loopx.capabilities.change_quality.machine_defaults import (
    normalize_change_quality_machine_defaults,
)
from loopx.capabilities.change_quality.receipt import (
    build_change_quality_prepare_packet,
)
from loopx.capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from loopx.capabilities.machine_configuration.store import (
    configure_machine_configuration,
)
from loopx.capabilities.todo_replan_cadence.machine_defaults import (
    resolve_todo_replan_cadence_goal,
    normalize_todo_replan_cadence_machine_defaults,
)
from loopx.configure_goal import configure_goal
from loopx.control_plane.goals.goal_vision_policy import (
    completed_todo_replan_threshold,
)
from loopx.history import collect_history

GOAL_ID = "machine-default-fixture"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _machine_configuration() -> dict[str, object]:
    return {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {
            "todo_replan_cadence": {
                "schema_version": "todo_replan_cadence_machine_defaults_v0",
                "completed_todos": 2,
            },
            "change_quality_qualification": {
                "schema_version": "change_quality_machine_defaults_v0",
                "enabled": True,
                "safe_fix": False,
                "strict_receipt": False,
            },
        },
    }


def _apply_machine_configuration(runtime_root: Path) -> None:
    registry = build_builtin_machine_configuration_registry()
    configuration = _machine_configuration()
    preview = configure_machine_configuration(
        runtime_root=runtime_root,
        configuration=configuration,
        registry=registry,
    )
    configure_machine_configuration(
        runtime_root=runtime_root,
        configuration=configuration,
        registry=registry,
        execute=True,
        expected_plan_revision=preview["plan_revision"],
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Machine Defaults Fixture")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "fixture")
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [{"id": GOAL_ID, "repo": str(repo), "status": "active"}],
            }
        ),
        encoding="utf-8",
    )
    _apply_machine_configuration(runtime_root)
    return repo, registry_path, runtime_root


def _history_goal(registry_path: Path, runtime_root: Path) -> dict[str, object]:
    history = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        limit=5,
    )
    return history["goals"][0]


@pytest.mark.parametrize("completed_todos", [True, 0, 6, "3"])
def test_cadence_machine_default_rejects_values_outside_the_typed_contract(
    completed_todos: object,
) -> None:
    with pytest.raises(ValueError, match="integer from 1 to 5"):
        normalize_todo_replan_cadence_machine_defaults(
            {
                "schema_version": "todo_replan_cadence_machine_defaults_v0",
                "completed_todos": completed_todos,
            }
        )


@pytest.mark.parametrize("field", ["enabled", "safe_fix", "strict_receipt"])
def test_change_quality_machine_default_requires_boolean_policy_fields(
    field: str,
) -> None:
    configuration: dict[str, object] = {
        "schema_version": "change_quality_machine_defaults_v0",
        "enabled": False,
        "safe_fix": False,
        "strict_receipt": False,
    }
    configuration[field] = 1
    with pytest.raises(TypeError, match=rf"{field} must be a boolean"):
        normalize_change_quality_machine_defaults(configuration)


def test_machine_defaults_reach_real_history_and_change_quality_paths(
    tmp_path: Path,
) -> None:
    repo, registry_path, runtime_root = _fixture(tmp_path)
    goal = _history_goal(registry_path, runtime_root)
    assert goal["execution_profile"]["replan_after_completed_todos"] == 2

    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    prepared = build_change_quality_prepare_packet(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    assert prepared["status"] == "review_required"
    assert prepared["policy"]["enabled"] is True


@pytest.mark.parametrize("update", [{}, {"change_quality_enabled": False}, {"execution_turn_granularity": "fine"}])
def test_unrelated_configuration_preserves_explicit_default_cadence(tmp_path: Path, update) -> None:
    _repo, registry_path, runtime_root = _fixture(tmp_path)
    configure_goal(
        registry_path=registry_path, goal_id=GOAL_ID,
        execution_replan_after_todos=5, execute=True,
    )
    configure_goal(registry_path=registry_path, goal_id=GOAL_ID, execute=True, **update)
    persisted = json.loads(registry_path.read_text(encoding="utf-8"))["goals"][0]
    assert persisted["execution_profile"]["replan_after_completed_todos"] == 5
    assert completed_todo_replan_threshold(_history_goal(registry_path, runtime_root)["execution_profile"]) == 5


def test_goal_overrides_win_and_clearing_restores_live_machine_defaults(
    tmp_path: Path,
) -> None:
    _repo, registry_path, runtime_root = _fixture(tmp_path)
    configure_goal(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        execution_replan_after_todos=5,
        change_quality_enabled=False,
        change_quality_safe_fix=False,
        change_quality_strict_receipt=False,
        execute=True,
    )
    overridden = _history_goal(registry_path, runtime_root)
    assert completed_todo_replan_threshold(overridden["execution_profile"]) == 5
    disabled = build_change_quality_prepare_packet(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=_repo,
        base_ref="HEAD",
    )
    assert disabled["status"] == "disabled"

    cleared = configure_goal(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        clear_execution_replan_after_todos=True,
        clear_change_quality_configuration=True,
        execute=True,
    )
    assert cleared["changed"] is True
    persisted = json.loads(registry_path.read_text())["goals"][0]
    assert "replan_after_completed_todos" not in persisted["execution_profile"]
    assert "change_quality_qualification" not in persisted.get("control_plane", {})
    inherited = _history_goal(registry_path, runtime_root)
    assert inherited["execution_profile"]["replan_after_completed_todos"] == 2
    inherited_quality = build_change_quality_prepare_packet(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=_repo,
        base_ref="HEAD",
    )
    assert inherited_quality["policy"]["enabled"] is True

    capabilities = {
        item["capability_id"]: item
        for item in cleared["configuration_catalog"]["capability_catalog"][
            "capabilities"
        ]
    }
    assert "current" not in capabilities["todo_replan_cadence"]
    assert "current" not in capabilities["change_quality_qualification"]


def test_effective_turn_machine_default_migration_and_override_readback(tmp_path: Path) -> None:
    """An explicit unit switch preserves v0 storage until apply and is reversible."""
    from loopx.chat_goal_configuration_api import _goal_capability_options
    from loopx.capabilities.goal_inspection import inspect_goal_capabilities

    _repo, registry_path, runtime_root = _fixture(tmp_path)
    machine_registry = build_builtin_machine_configuration_registry()
    v1 = _machine_configuration()
    v1["namespaces"]["todo_replan_cadence"] = {
        "schema_version": "todo_replan_cadence_machine_defaults_v1",
        "count_unit": "effective_turns", "count": 2,
    }
    preview = configure_machine_configuration(runtime_root=runtime_root,
        registry=machine_registry, configuration=v1)
    assert _history_goal(registry_path, runtime_root)["execution_profile"]["replan_after_completed_todos"] == 2
    configure_machine_configuration(runtime_root=runtime_root,
        registry=machine_registry, configuration=v1, execute=True,
        expected_plan_revision=preview["plan_revision"])
    profile = _history_goal(registry_path, runtime_root)["execution_profile"]
    assert profile["replan_after_effective_turns"] == 2
    assert "replan_after_completed_todos" not in profile
    from loopx.control_plane.work_items.replan_history_codec import effective_turn_cadence_context
    raw = json.loads(registry_path.read_text())["goals"][0]
    assert effective_turn_cadence_context(
        resolve_todo_replan_cadence_goal(raw, runtime_root), runtime_root,
    )["threshold"] == 2

    options = _goal_capability_options("todo_replan_cadence", {"count_unit": "effective_turns", "count": 3})
    configure_goal(registry_path=registry_path, goal_id=GOAL_ID, execute=True, **options)
    inspected = inspect_goal_capabilities(registry_path=registry_path,
        runtime_root=runtime_root, goal_id=GOAL_ID)["configuration"]
    cadence = next(c for c in inspected["capability_catalog"]["capabilities"] if c["capability_id"] == "todo_replan_cadence")
    assert cadence["current"] == {"count_unit": "effective_turns", "count": 3}
    assert cadence["effective_configuration"]["source"] == "goal_override"
    assert _history_goal(registry_path, runtime_root)["execution_profile"]["replan_after_effective_turns"] == 3

    # Explicitly selecting legacy mode remains supported; clearing restores live v1.
    configure_goal(registry_path=registry_path, goal_id=GOAL_ID, execute=True,
        **_goal_capability_options("todo_replan_cadence", {"completed_todos": 4}))
    profile = _history_goal(registry_path, runtime_root)["execution_profile"]
    assert profile["replan_after_completed_todos"] == 4
    assert "replan_after_effective_turns" not in profile
    configure_goal(registry_path=registry_path, goal_id=GOAL_ID, execute=True,
        **_goal_capability_options("todo_replan_cadence", None))
    assert _history_goal(registry_path, runtime_root)["execution_profile"]["replan_after_effective_turns"] == 2


@pytest.mark.parametrize("configuration", [
    {"count_unit": "turns", "count": 3},
    {"count_unit": "effective_turns", "count": True},
    {"count_unit": "effective_turns", "count": 0},
    {"count_unit": "effective_turns", "count": 3, "completed_todos": 3},
    {"completed_todos": 3},
])
def test_v1_cadence_rejects_ambiguous_units(configuration) -> None:
    with pytest.raises(ValueError):
        normalize_todo_replan_cadence_machine_defaults({
            "schema_version": "todo_replan_cadence_machine_defaults_v1", **configuration,
        })


@pytest.mark.parametrize("mode", ["standard", "fine"])
def test_product_default_and_namespace_removal_use_five_settled_turns(tmp_path, mode):
    from loopx.capabilities.goal_inspection import inspect_goal_capabilities
    from loopx.control_plane.work_items.replan_history_codec import effective_turn_cadence_context

    _repo, registry_path, runtime_root = _fixture(tmp_path)
    machine_registry = build_builtin_machine_configuration_registry()
    from loopx.capabilities.machine_configuration.contract import remove_machine_configuration_namespace
    # Use the real namespace removal transaction, retaining unrelated settings.
    removed = remove_machine_configuration_namespace(_machine_configuration(),
        namespace="todo_replan_cadence", registry=machine_registry)
    preview = configure_machine_configuration(runtime_root=runtime_root,
        registry=machine_registry, configuration=removed)
    configure_machine_configuration(runtime_root=runtime_root, registry=machine_registry,
        configuration=removed, execute=True, expected_plan_revision=preview["plan_revision"])
    configure_goal(registry_path=registry_path, goal_id=GOAL_ID,
        execution_turn_granularity=mode, execute=True)
    raw = json.loads(registry_path.read_text())["goals"][0]
    assert "replan_after_effective_turns" not in raw["execution_profile"]
    projected = _history_goal(registry_path, runtime_root)
    assert projected["execution_profile"]["replan_after_effective_turns"] == 5
    assert effective_turn_cadence_context(
        resolve_todo_replan_cadence_goal(raw, runtime_root), runtime_root,
    )["threshold"] == 5
    inspected = inspect_goal_capabilities(registry_path=registry_path,
        runtime_root=runtime_root, goal_id=GOAL_ID)["configuration"]
    cadence = next(c for c in inspected["capability_catalog"]["capabilities"]
                   if c["capability_id"] == "todo_replan_cadence")
    assert cadence["effective_configuration"]["source"] == "capability_default"
    assert cadence["effective_configuration"]["configuration"]["count_unit"] == "effective_turns"
    assert cadence["effective_configuration"]["configuration"]["count"] == 5

    # Even the old default is an explicit, sticky override; compacting must
    # never silently switch it back to the new Turn unit.
    from loopx.execution_profile import compact_execution_profile
    configure_goal(registry_path=registry_path, goal_id=GOAL_ID,
        execution_replan_after_todos=5, execute=True)
    raw = json.loads(registry_path.read_text())["goals"][0]
    assert compact_execution_profile(raw["execution_profile"])["replan_after_completed_todos"] == 5
    assert effective_turn_cadence_context(raw, runtime_root) is None
    configure_goal(registry_path=registry_path, goal_id=GOAL_ID,
        clear_execution_replan_after_todos=True, execute=True)
    assert _history_goal(registry_path, runtime_root)["execution_profile"]["replan_after_effective_turns"] == 5
