"""Production Todo consumers use retained owners, without the historic facade.

The public import remains supported; physical removal here is a disposable
qualification probe, not a supported package format or a migration grant.
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

import test_canonical_todo_writer_isolation as journeys
from test_canonical_todo_writer_isolation import (
    isolated_todo_distribution, retirement_wheel,  # noqa: F401 -- distribution fixtures
)


def _historic_todo_imports(source, package):
    """Resolve imports, including function-local relative imports and aliases."""
    for node in ast.walk(ast.parse(source)):
        targets = []
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            target = (importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                      if node.level else node.module or "")
            targets = [target, *(target + "." + alias.name for alias in node.names)]
        if any(target == "loopx.todos" or target.startswith("loopx.todos.") for target in targets):
            yield node.lineno


@pytest.mark.parametrize("source,package", [
    ("import loopx.todos as historic", "loopx.capabilities.explore"),
    ("from loopx import todos as historic", "loopx.capabilities.explore"),
    ("def read():\n from ...todos import list_goal_todos", "loopx.capabilities.explore"),
    ("from . import todos", "loopx"),
])
def test_import_guard_recognizes_historic_owner(source, package):
    assert list(_historic_todo_imports(source, package))


def test_import_guard_keeps_retained_todo_package_distinct():
    assert not list(_historic_todo_imports("from ..todos import legacy_mutation", "loopx.control_plane.todos"))
    assert not list(_historic_todo_imports(
        "from ...control_plane.todos.list_readback import list_goal_todos", "loopx.capabilities.explore"))


def test_historic_facade_has_no_production_consumers():
    # An earlier caller inventory cannot cover imports added by a later feature.
    # Dynamic imports still need the physically isolated production journeys.
    root = Path(__file__).resolve().parents[2]
    consumers = []
    for path in sorted((root / "loopx").rglob("*.py")):
        relative = path.relative_to(root)
        package = ".".join(relative.parent.parts)
        consumers.extend((str(relative), line) for line in _historic_todo_imports(path.read_text(encoding="utf-8"), package))
    assert consumers == [], consumers


@pytest.fixture(params=[True, False], ids=["facade-present", "facade-absent"])
def consumer_distribution(request, isolated_todo_distribution):  # noqa: F811
    package, command = isolated_todo_distribution
    journeys._remove_source_todo_writers(package)
    journeys._remove_monitor_source_modules(package)
    if not request.param:
        (package / "todos.py").unlink()
    probe = subprocess.run(
        [sys.executable, "-S", "-c", "import importlib.util,json,loopx;"
         "print(json.dumps({'package':loopx.__file__,"
         "'facade':importlib.util.find_spec('loopx.todos') is not None}))"],
        cwd=package.parent, capture_output=True, text=True, check=True,
    )
    provenance = json.loads(probe.stdout)
    assert provenance["package"] == str(package / "__init__.py")
    assert provenance["facade"] is request.param
    try:
        yield package, command
    finally:
        # Both fixture seeding and the isolated CLI start owned runtimes under
        # this fixture's private TMPDIR, never the shared developer service.
        stopped = subprocess.run(
            [sys.executable, "-S", "-c",
             "from loopx.control_plane.effect_runtime import restart_effect_runtime;restart_effect_runtime()"],
            cwd=package.parent, capture_output=True, text=True, timeout=15,
        )
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        from loopx.control_plane.effect_runtime import restart_effect_runtime
        restart_effect_runtime()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cli_lifecycle_uses_retained_owners(tmp_path, provider, consumer_distribution):
    journeys.test_canonical_cli_lifecycle_without_source_todo_writers(
        tmp_path, provider, consumer_distribution[1])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_creation_recovery_uses_retained_owners(tmp_path, provider, consumer_distribution):
    journeys.test_new_goal_and_original_creation_recovery_without_source_todo_writers(
        tmp_path, provider, consumer_distribution[1])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_once_only_settlement_uses_retained_owners(tmp_path, provider, consumer_distribution):
    journeys.test_leased_delivery_settlement_and_recovery_without_source_todo_writers(
        tmp_path, provider, consumer_distribution[1])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("material_change", [False, True])
def test_monitor_replay_uses_retained_owners(
    tmp_path, request, provider, material_change, consumer_distribution,
):
    journeys.test_monitor_recovery_with_source_writer_isolation(
        tmp_path, request, provider, material_change, True, consumer_distribution)


def test_public_imports_keep_same_owners():
    # Historic external callers keep function identity and the normal lazy IO
    # arm; removing internal imports does not revoke source-format support.
    import loopx.todos as historic
    from loopx.control_plane.todos import active_state_editing, list_readback, mutation_api, path_resolution

    for owner, names in (
        (mutation_api, ("add_goal_todo", "update_goal_todo", "complete_goal_todo",
                        "supersede_goal_todo", "archive_completed_todos")),
        (list_readback, ("list_goal_todos",)),
        (path_resolution, ("resolve_todo_state", "resolve_todo_state_path")),
        (active_state_editing, ("section_bounds", "todo_blocks")),
    ):
        for name in names:
            assert getattr(historic, name) is getattr(owner, name)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_full_cli_status_loads_without_historic_consumers(tmp_path, provider, consumer_distribution):
    from canonical_authority_fixture import promoted_create_fixture

    registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for root in (runtime, state.parent) for p in root.rglob("*") if p.is_file()}
    result = subprocess.run(
        [*consumer_distribution[1], "--registry", str(registry), "--runtime-root", str(runtime),
         "--format", "json", "authority-shadow", "status", "--goal-id", "goal-a"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] and payload["candidate"]["status"] == "missing"
    assert {str(p.relative_to(tmp_path)): p.read_bytes()
            for root in (runtime, state.parent) for p in root.rglob("*") if p.is_file()} == before
