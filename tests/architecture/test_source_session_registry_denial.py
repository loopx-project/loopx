from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DIRECT_LOADER_ALLOWLIST = {
    "loopx/authority.py",
    "loopx/attached_session.py",
    "loopx/bootstrap.py",
    "loopx/capabilities/manager_context/__init__.py",
    "loopx/capabilities/manager_context/roundtrip.py",
    "loopx/capabilities/native_chat/project_context.py",
    "loopx/claude_goal_mode/scripts/connect.py",
    "loopx/cli.py",
    "loopx/cli_commands/coordination_shadow.py",
    "loopx/cli_commands/manager_inbox.py",
    "loopx/configure_goal.py",
    "loopx/control_plane/collaboration/goal_instance_scope.py",
    "loopx/control_plane/collaboration/peers.py",
    "loopx/control_plane/collaboration/source_grant_observation.py",
    "loopx/control_plane/goals/first_party_host_admission.py",
    "loopx/control_plane/goals/source_session_recreation.py",
    "loopx/control_plane/goals/source_session_turn_effects.py",
    "loopx/control_plane/coordination/runtime_shadow.py",
    "loopx/control_plane/coordination/shadow_goal_scope.py",
    "loopx/control_plane/projects/registry.py",
    "loopx/control_plane/turn_driver/codex_sessions.py",
    "loopx/kunluncode_goal_mode/cli.py",
    "loopx/state_migration.py",
}


# Storage-location readers grant no runtime action. Exceptions name the exact
# functions; another direct loader in either module must still fail.
METADATA_READER_FUNCTIONS = {
    "loopx/capabilities/native_chat/project_context.py": {"coordination_runtime_root"},
    "loopx/control_plane/goals/source_session_recreation.py": {"_canonical_runtime_root"},
}


def _assert_metadata_reader_functions(tree: ast.Module, expected: set[str]) -> None:
    loader_functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "load_project_registry"
            for call in ast.walk(node)
        )
    }
    assert loader_functions == expected


def _assert_recreation_reader_is_location_only(tree: ast.Module) -> None:
    reader = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_canonical_runtime_root")
    calls = [node for node in ast.walk(reader) if isinstance(node, ast.Call)]
    assert {node.func.id for node in calls if isinstance(node.func, ast.Name)} == {
        "load_project_registry", "resolve_runtime_root",
    }
    attributes = [node for node in calls if isinstance(node.func, ast.Attribute)]
    assert len(attributes) == 1
    assert attributes[0].func.attr == "resolve"
    assert isinstance(attributes[0].func.value, ast.Call)
    assert attributes[0].func.value.func.id == "resolve_runtime_root"
    returned = [node.value for node in ast.walk(reader) if isinstance(node, ast.Return)]
    assert returned == [attributes[0]]
    guard = next(node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == "_canonical_writer_guard_path")
    guard_calls = [node for node in ast.walk(guard) if isinstance(node, ast.Call)]
    assert all(isinstance(node.func, ast.Name) for node in guard_calls)
    assert {node.func.id for node in guard_calls} == {
        "shadow_maintenance_lock_target", "_canonical_runtime_root",
    }


def test_direct_project_registry_loaders_have_source_session_denial() -> None:
    callers: set[str] = set()
    for path in (REPO_ROOT / "loopx").rglob("*.py"):
        if path.name == "registry_codec.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "load_project_registry"
            for node in ast.walk(tree)
        ):
            callers.add(path.relative_to(REPO_ROOT).as_posix())

    assert callers == DIRECT_LOADER_ALLOWLIST
    source_session_owners = {
        "loopx/attached_session.py",
        "loopx/capabilities/manager_context/__init__.py",
        "loopx/capabilities/manager_context/roundtrip.py",
        "loopx/cli.py",
        "loopx/cli_commands/coordination_shadow.py",
        "loopx/cli_commands/manager_inbox.py",
        "loopx/control_plane/collaboration/goal_instance_scope.py",
        "loopx/control_plane/collaboration/peers.py",
        "loopx/control_plane/goals/first_party_host_admission.py",
        "loopx/control_plane/goals/source_session_recreation.py",
        "loopx/control_plane/goals/source_session_turn_effects.py",
        "loopx/control_plane/coordination/runtime_shadow.py",
        "loopx/control_plane/coordination/shadow_goal_scope.py",
        "loopx/control_plane/projects/registry.py",
    }
    for relative, expected in METADATA_READER_FUNCTIONS.items():
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
        _assert_metadata_reader_functions(tree, expected)
        if relative == "loopx/control_plane/goals/source_session_recreation.py":
            _assert_recreation_reader_is_location_only(tree)
    for relative in callers - source_session_owners - METADATA_READER_FUNCTIONS.keys():
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "require_runtime_compatible_project_registry(" in source, relative
    attached_owner = (REPO_ROOT / "loopx/attached_session.py").read_text(
        encoding="utf-8"
    )
    assert "SOURCE_SESSION_PROFILE_ID" in attached_owner
    assert "source_session_goal_lifetime" in attached_owner


def test_metadata_exception_rejects_another_direct_loader() -> None:
    relative = "loopx/control_plane/goals/source_session_recreation.py"
    source = (REPO_ROOT / relative).read_text(encoding="utf-8")
    tree = ast.parse(source + "\n\ndef execute_goal(registry_path):\n    return load_project_registry(registry_path)\n")
    with pytest.raises(AssertionError):
        _assert_metadata_reader_functions(tree, METADATA_READER_FUNCTIONS[relative])


@pytest.mark.parametrize("reader_name", ["_canonical_runtime_root", "_canonical_writer_guard_path"])
@pytest.mark.parametrize("authority_call", ["effect_runtime_result", "mutate_project_registry"])
def test_recreation_metadata_exception_rejects_authority_calls(authority_call: str, reader_name: str) -> None:
    relative = "loopx/control_plane/goals/source_session_recreation.py"
    tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
    reader = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == reader_name
    )
    reader.body.insert(0, ast.parse(f"{authority_call}()").body[0])
    with pytest.raises(AssertionError):
        _assert_recreation_reader_is_location_only(tree)


def test_generic_registry_decoder_enforces_source_session_denial() -> None:
    source = (REPO_ROOT / "loopx/control_plane/projects/registry_codec.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    decoder = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "decode_registry_snapshot"
    )
    calls = {
        node.func.id
        for node in ast.walk(decoder)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "require_runtime_compatible_project_registry" in calls
