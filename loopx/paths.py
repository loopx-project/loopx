from __future__ import annotations

import os
from pathlib import Path


DEFAULT_RUNTIME_ROOT = Path.home() / ".loopx"
LEGACY_RUNTIME_ROOT = Path.home() / ".codex" / "loopx"
DEFAULT_PROJECT_REGISTRY = Path(".loopx") / "registry.json"
DEFAULT_PROJECT_GOALS = Path(".loopx") / "goals"
LEGACY_PROJECT_GOALS = Path(".codex") / "goals"
GLOBAL_REGISTRY_FILENAME = "registry.global.json"
SHELL_DEFAULT_GLOBAL_REGISTRY = '"$HOME/.loopx/registry.global.json"'
SHELL_LEGACY_GLOBAL_REGISTRY = '"$HOME/.codex/loopx/registry.global.json"'


def default_goal_state_file(project: Path, goal_id: str) -> Path:
    return project / DEFAULT_PROJECT_GOALS / goal_id / "ACTIVE_GOAL_STATE.md"


def legacy_goal_state_file(project: Path, goal_id: str) -> Path:
    return project / LEGACY_PROJECT_GOALS / goal_id / "ACTIVE_GOAL_STATE.md"


def registered_goal_state_file(
    project: Path, goal_id: str, registry: dict[str, object] | None = None
) -> Path:
    """Keep an existing registration on its declared path until migration."""

    if isinstance(registry, dict):
        goals = registry.get("goals")
        if isinstance(goals, list):
            for goal in goals:
                if not isinstance(goal, dict) or goal.get("id") != goal_id:
                    continue
                value = goal.get("state_file")
                if isinstance(value, str) and value:
                    path = Path(value).expanduser()
                    return path if path.is_absolute() else project / path
        declared_root = registry.get("common_runtime_root")
        if declared_root and Path(str(declared_root)).expanduser() == LEGACY_RUNTIME_ROOT:
            return legacy_goal_state_file(project, goal_id)
    return default_goal_state_file(project, goal_id)


def require_single_goal_state_route(project: Path, goal_id: str, selected: Path) -> None:
    """Avoid bootstrapping a second default state file for the same Goal."""

    current = default_goal_state_file(project, goal_id)
    legacy = legacy_goal_state_file(project, goal_id)
    if selected == current and legacy.exists():
        raise ValueError(
            f"legacy Goal state exists at {legacy}; restore its registration or "
            "migrate it explicitly before bootstrapping this Goal"
        )
    if selected == legacy and current.exists():
        raise ValueError(
            f"new Goal state already exists at {current}; resolve the route "
            "conflict before bootstrapping this Goal"
        )


def default_public_scan_root() -> str:
    """Return the bounded LoopX package root used by public scans."""

    return str(Path(__file__).resolve().parent)


def default_registry_path() -> Path:
    value = os.environ.get("LOOPX_REGISTRY")
    if value:
        return Path(value).expanduser()
    return DEFAULT_PROJECT_REGISTRY


def default_runtime_route() -> dict[str, object]:
    """Inspect the two default routes without creating either one."""

    current = global_registry_path(DEFAULT_RUNTIME_ROOT)
    legacy = global_registry_path(LEGACY_RUNTIME_ROOT)
    current_exists = current.exists() or current.is_symlink()
    legacy_exists = legacy.exists() or legacy.is_symlink()
    invalid = any(
        path.is_symlink() or not path.is_file()
        for path, present in ((current, current_exists), (legacy, legacy_exists))
        if present
    )
    if invalid:
        status = "invalid"
    elif current_exists and legacy_exists:
        status = "conflict"
    elif legacy_exists:
        status = "legacy"
    elif current_exists:
        status = "current"
    else:
        status = "fresh"
    selected = LEGACY_RUNTIME_ROOT if status == "legacy" else DEFAULT_RUNTIME_ROOT
    recommended_action = None
    if status == "conflict":
        recommended_action = (
            "Select one registry with --registry/--runtime-root; "
            "inspect both roots before migration."
        )
    elif status == "invalid":
        recommended_action = (
            "A default registry path is not a regular file; inspect it before continuing."
        )
    elif status == "legacy":
        recommended_action = (
            "Preview `loopx migrate-local-state`; existing state remains on its legacy route."
        )
    return {
        "status": status,
        "selected_runtime_root": str(selected),
        "target_runtime_root": str(DEFAULT_RUNTIME_ROOT),
        "legacy_runtime_root": str(LEGACY_RUNTIME_ROOT),
        "target_registry_exists": current_exists,
        "legacy_registry_exists": legacy_exists,
        "recommended_action": recommended_action,
    }


def select_default_runtime_root() -> Path:
    route = default_runtime_route()
    if route["status"] == "conflict":
        raise ValueError(
            "Both default LoopX registries exist. Select an explicit --registry and "
            "--runtime-root; resolve the route conflict before using implicit defaults."
        )
    if route["status"] == "invalid":
        raise ValueError(str(route["recommended_action"]))
    return Path(str(route["selected_runtime_root"]))


def shell_selected_global_registry() -> str:
    selected = select_default_runtime_root()
    return (
        SHELL_LEGACY_GLOBAL_REGISTRY
        if selected == LEGACY_RUNTIME_ROOT
        else SHELL_DEFAULT_GLOBAL_REGISTRY
    )


def global_registry_path(runtime_root: Path | None = None) -> Path:
    selected = runtime_root if runtime_root is not None else select_default_runtime_root()
    return selected / GLOBAL_REGISTRY_FILENAME


def registry_project_root(registry_path: Path) -> Path:
    """Return the project root that owns a registry path.

    Project registries conventionally live at ``<project>/.loopx/registry.json``.
    Standalone fixtures and global registries live directly under their owning
    root.  Keeping this rule here prevents relative runtime paths from silently
    depending on the caller's current working directory.
    """

    expanded = registry_path.expanduser().resolve()
    parent = expanded.parent
    return parent.parent if parent.name == ".loopx" else parent


def resolve_runtime_root(
    registry: dict[str, object],
    override: str | None = None,
    *,
    registry_path: Path | None = None,
) -> Path:
    value = override
    if not value:
        value = registry.get("common_runtime_root") if isinstance(registry, dict) else None
    if not value:
        return select_default_runtime_root()

    runtime_root = Path(str(value)).expanduser()
    if runtime_root.is_absolute() or registry_path is None:
        return runtime_root
    return registry_project_root(registry_path) / runtime_root


def rel_or_abs(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
