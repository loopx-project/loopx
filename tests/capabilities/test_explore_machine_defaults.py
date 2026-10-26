"""Live preference, explicit override, and the actual Explore entrypoints."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.capabilities.explore.activation import sync_explore_graph_after_material_refresh
from loopx.capabilities.explore.machine_defaults import (
    EXPLORE_MACHINE_DEFAULTS_SCHEMA, apply_explore_machine_default,
    normalize_explore_machine_defaults, resolve_explore_goal,
)
from loopx.capabilities.explore.turn_context import (
    explore_turn_context, extend_turn_start_dispatch, project_settlement_attachment,
)
from loopx.capabilities.configuration_inspection import inspect_machine_namespaces, project_goal_configuration
from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
from loopx.capabilities.machine_configuration.store import configure_machine_configuration
from loopx.chat_goal_configuration_api import _goal_capability_options
from loopx.configure_goal import configure_goal
from loopx.control_plane.coordination.goal_storage_initialization import initialize_goal_storage_target
from loopx.history import collect_history


def configure_machine(root: Path, mode: str):
    config = {"schema_version": "loopx_machine_configuration_v0", "namespaces": {
        "explore_harness": {"schema_version": EXPLORE_MACHINE_DEFAULTS_SCHEMA, "mode": mode},
    }}
    args = dict(runtime_root=root, configuration=config, registry=build_builtin_machine_configuration_registry())
    preview = configure_machine_configuration(**args)
    return configure_machine_configuration(**args, execute=True, expected_plan_revision=preview["plan_revision"])


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "runtime"
    (tmp_path / "goal.md").write_text("# Goal\n\n## Agent Todos\n")
    path = tmp_path / "registry.json"
    goal = {"id": "research", "creation_operation_id": "goal-create:research", "status": "active",
            "repo": str(tmp_path), "state_file": "goal.md",
            "spawn_policy": {"spawn_allowed": False, "max_children": 2},
            "coordination": {"registered_agents": ["worker"], "storage_target": {
                "schema_version": "loopx_new_goal_storage_target_v1", "provider": "file", "handoff_mode": "soft_claim"}}}
    path.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(root), "goals": [goal]}))
    initialize_goal_storage_target(root, goal, registry_path=path)
    return path, root, goal


@pytest.mark.parametrize("bad", [None, True, 1, "unknown", [], {}])
def test_invalid_mode_cannot_enable_planning(bad):
    with pytest.raises((ValueError, TypeError, RuntimeError)):
        normalize_explore_machine_defaults({"schema_version": EXPLORE_MACHINE_DEFAULTS_SCHEMA, "mode": bad})


def test_existing_and_new_goals_inherit_live_without_registry_writes(setup):
    path, root, goal = setup
    original = path.read_bytes()
    assert resolve_explore_goal(goal, root) == goal
    for mode, evidence, planning in [("planning", True, True), ("evidence", True, False), ("off", False, False)]:
        configure_machine(root, mode)
        for raw in (goal, {**goal, "id": "new-goal"}):
            effective = resolve_explore_goal(raw, root)
            assert effective["explore_graph"]["enabled"] is evidence
            assert effective["spawn_policy"]["explore_harness"]["enabled"] is planning
            assert effective["spawn_policy"]["spawn_allowed"] is False
            assert effective["spawn_policy"]["max_children"] == 2
        assert path.read_bytes() == original
        context = explore_turn_context(registry_path=path, runtime_root=root, goal_id="research", agent_id="worker")
        assert context["graph_enabled"] is evidence
        assert context["harness_enabled"] is planning
    assert apply_explore_machine_default(goal, None) == goal


def test_explicit_off_and_legacy_settings_override_machine(setup):
    _, root, goal = setup
    configure_machine(root, "planning")
    for override in ({"explore_graph": {"enabled": False}},
                     {"spawn_policy": {"explore_harness": {"enabled": False}}},
                     {"explore_graph": {"enabled": True}}):
        raw = {**goal, **override}
        assert resolve_explore_goal(raw, root) == raw
    metadata = deepcopy(goal)
    metadata["spawn_policy"]["explore_harness"] = {"profile": "generic", "composition_mode": "disabled"}
    effective = resolve_explore_goal(metadata, root)
    assert effective["spawn_policy"]["explore_harness"] == {
        "enabled": True, "profile": "generic", "composition_mode": "disabled"}
    assert "enabled" not in metadata["spawn_policy"]["explore_harness"]


def test_goal_override_clear_restores_inheritance_in_settings_and_runtime(setup):
    path, root, _ = setup
    configure_machine(root, "planning")
    def settings():
        packet = project_goal_configuration(
            configure_goal(registry_path=path, goal_id="research"),
            machine_namespaces=inspect_machine_namespaces(root))
        return next(c for c in packet["capability_catalog"]["capabilities"] if c["capability_id"] == "explore_harness")
    inherited = settings()
    assert inherited["effective_configuration"]["source"] == "machine_default"
    assert inherited["effective_configuration"]["configuration"]["mode"] == "planning"
    assert "current" not in inherited
    configure_goal(registry_path=path, goal_id="research", explore_mode="off", execute=True)
    assert settings()["effective_configuration"]["source"] == "goal_override"
    clear = _goal_capability_options("explore_harness", None)
    original = path.read_bytes()
    configure_goal(registry_path=path, goal_id="research", **clear)
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="Clear Explore"):
        configure_goal(registry_path=path, goal_id="research", explore_mode="planning", execute=True, **clear)
    assert path.read_bytes() == original
    configure_goal(registry_path=path, goal_id="research", execute=True, **clear)
    assert settings()["effective_configuration"]["source"] == "machine_default"
    goal = json.loads(path.read_text())["goals"][0]
    assert goal["spawn_policy"]["spawn_allowed"] is False
    assert "explore_harness" not in goal["spawn_policy"]
    assert "explore_graph" not in goal
    assert resolve_explore_goal(goal, root)["spawn_policy"]["explore_harness"]["enabled"] is True


def test_runtime_hooks_history_cli_and_material_refresh_use_same_default(setup):
    path, root, _ = setup
    configure_machine(root, "planning")
    history = collect_history(registry_path=path, runtime_root=root, goal_id="research", limit=1)
    assert history["goals"][0]["spawn_policy"]["explore_harness"]["enabled"] is True
    hooks = extend_turn_start_dispatch({}, registry_path=path, runtime_root=root, goal_id="research", agent_id="worker")
    assert any(read["kind"] == "explore_turn_context" for read in hooks["required_reads"])
    plan = {"identity": {"goal_id": "research", "agent_id": "worker", "todo_id": "todo-one"},
            "ordered_steps": [{"kind": "durable_writeback", "command_template": "loopx refresh-state"}]}
    assert project_settlement_attachment(plan, registry_path=path)["ordered_steps"][0]["optional_attachments"]
    calls = []
    def syncer(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "status": "not_configured"}
    sync_explore_graph_after_material_refresh(registry_path=path, goal_id="research", syncer=syncer)
    assert calls
    assert calls[0]["external_sink_delivery_authorized"] is False
    for command in ("turn-context", "todo-branch-plan", "worker-branch-plan"):
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(path),
                                 "--runtime-root", str(root), "--format", "json", "explore", command,
                                 "--goal-id", "research", "--agent-id", "worker"], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        packet = json.loads(result.stdout)
        assert packet.get("harness_enabled", packet.get("enabled")) is True, packet


def test_runtime_roots_do_not_share_defaults_and_null_legacy_fields_are_absent(setup, tmp_path):
    _, root, goal = setup
    configure_machine(root, "planning")
    isolated = tmp_path / "isolated-runtime"
    assert resolve_explore_goal(goal, isolated) == goal
    legacy = {**goal, "spawn_policy": None, "explore_graph": None}
    assert resolve_explore_goal(legacy, root)["spawn_policy"]["explore_harness"]["enabled"] is True
    configure_machine(isolated, "off")
    assert resolve_explore_goal(goal, root)["spawn_policy"]["explore_harness"]["enabled"] is True
    assert resolve_explore_goal(goal, isolated)["spawn_policy"]["explore_harness"]["enabled"] is False


@pytest.mark.parametrize("registry_mode,selected_mode", [("off", "planning"), ("planning", "off")])
def test_todo_settlement_plan_uses_selected_runtime(setup, tmp_path, registry_mode, selected_mode):
    import argparse
    from loopx.cli_commands.todo import _todo_settlement_plan
    from loopx.control_plane.quota.settlement import SettlementIdentity

    path, root, _ = setup
    selected = tmp_path / "selected-runtime"
    configure_machine(root, registry_mode)
    configure_machine(selected, selected_mode)
    plan = _todo_settlement_plan(
        SettlementIdentity(goal_id="research", agent_id="worker", todo_id="todo-one", turn_instance_id="fixture-turn"),
        args=argparse.Namespace(), registry_path=path, runtime_root=selected, goal_ref=None,
    )
    writeback = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
    assert bool(writeback.get("optional_attachments")) is (selected_mode == "planning")
    assert str(selected) in writeback["command_template"]


@pytest.mark.parametrize("override", [
    {"profile": "adaptive-resilient"},
    {"composition_mode": "explicit_only", "composition_scope_id": "bounded-scope"},
    {"mode": "evidence", "profile": "adaptive-resilient"},
    {"mode": "off"},
])
def test_sparse_goal_form_roundtrip_preserves_mode_intent(setup, override):
    path, root, _ = setup
    configure_machine(root, "planning")
    configure_goal(registry_path=path, goal_id="research", execute=True,
                   **_goal_capability_options("explore_harness", override))

    def settings():
        packet = project_goal_configuration(configure_goal(registry_path=path, goal_id="research"),
                                            machine_namespaces=inspect_machine_namespaces(root))
        return next(c for c in packet["capability_catalog"]["capabilities"] if c["capability_id"] == "explore_harness")

    before = settings()
    # Use the shipped form projector and writer, then the real configuration
    # handler. Neither the readback nor the submitted mode is hand-filled.
    module = Path(__file__).resolve().parents[2] / "apps/presentation/dashboard/src/data/capability-configuration.ts"
    script = """
import {goalExploreEditorValue, goalExploreWriteValue, projectEditableCapabilityConfiguration} from process.env.EDITOR_MODULE;
""".replace("from process.env.EDITOR_MODULE", f"from {json.dumps(module.as_uri())}") + """
const selected = JSON.parse(process.argv[1]);
const draft = projectEditableCapabilityConfiguration(selected.configuration_editor,
  goalExploreEditorValue(selected.current, selected.effective_configuration.configuration), selected.default);
console.log(JSON.stringify({mode: draft.mode, write: goalExploreWriteValue({...draft, profile: 'generic'},
  Object.hasOwn(selected.current ?? {}, 'mode'))}));
"""
    process = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", script,
                              json.dumps(before)], capture_output=True, text=True, check=True)
    form = json.loads(process.stdout)
    assert form["mode"] == override.get("mode", "planning")
    assert ("mode" in form["write"]) is ("mode" in override)
    configure_goal(registry_path=path, goal_id="research", execute=True,
                   **_goal_capability_options("explore_harness", form["write"]))
    after = settings()["effective_configuration"]
    assert after["configuration"]["profile"] == "generic"
    assert after["configuration"]["mode"] == override.get("mode", "planning")
    assert after["source"] == ("goal_override" if "mode" in override else "machine_default")
    configure_machine(root, "off")
    assert settings()["effective_configuration"]["configuration"]["mode"] == override.get("mode", "off")
