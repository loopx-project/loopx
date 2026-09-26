from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DIRECT_LOADER_ALLOWLIST = {
    "loopx/authority.py",
    "loopx/attached_session.py",
    "loopx/bootstrap.py",
    "loopx/claude_goal_mode/scripts/connect.py",
    "loopx/configure_goal.py",
    "loopx/control_plane/projects/registry.py",
    "loopx/kunluncode_goal_mode/cli.py",
    "loopx/state_migration.py",
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
    for relative in callers - {
        "loopx/attached_session.py",
        "loopx/control_plane/projects/registry.py",
    }:
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "require_runtime_compatible_project_registry(" in source, relative
    attached_owner = (REPO_ROOT / "loopx/attached_session.py").read_text(
        encoding="utf-8"
    )
    assert "SOURCE_SESSION_PROFILE_ID" in attached_owner
    assert "source_session_goal_lifetime" in attached_owner


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
