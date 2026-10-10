from __future__ import annotations

import os
import stat
from enum import StrEnum
from pathlib import Path


DEFAULT_RUNTIME_ROOT = Path.home() / ".loopx"
LEGACY_RUNTIME_ROOT = Path.home() / ".codex" / "loopx"
DEFAULT_PROJECT_REGISTRY = Path(".loopx") / "registry.json"
DEFAULT_PROJECT_GOALS = Path(".loopx") / "goals"
LEGACY_PROJECT_GOALS = Path(".codex") / "goals"
GLOBAL_REGISTRY_FILENAME = "registry.global.json"
SHELL_DEFAULT_GLOBAL_REGISTRY = '"$HOME/.loopx/registry.global.json"'
SHELL_LEGACY_GLOBAL_REGISTRY = '"$HOME/.codex/loopx/registry.global.json"'


class RuntimeRouteStatus(StrEnum):
    """Local filesystem discovery vocabulary; never persisted Goal authority."""
    FRESH = "fresh"
    CURRENT = "current"
    LEGACY = "legacy"
    CONFLICT = "conflict"
    INVALID = "invalid"
    CONFIGURED = "configured"


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


def _is_redirected_path(path: Path) -> bool:
    """Reject symlinks, junctions and other Windows reparse-point routes."""
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _runtime_root_has_machine_state(root: Path) -> bool:
    """Separate a HOME project's declared files from machine-owned state."""

    if not root.is_dir():
        return False
    project_registry = root / DEFAULT_PROJECT_REGISTRY.name
    project_owned: set[str] = set()
    if root == DEFAULT_RUNTIME_ROOT and project_registry.is_file() and not _is_redirected_path(project_registry):
        from .control_plane.projects.registry_codec import load_registry
        from .file_lock import _lock_path, lock_holder_path, lock_incident_path

        try:
            registry = load_registry(project_registry)
        except (OSError, ValueError):
            return True  # Unclassified existing state requires an explicit route.
        project_owned = {
            project_registry.name, _lock_path(project_registry).name,
            lock_holder_path(project_registry).name, lock_incident_path(project_registry).name,
        }
        goal_root = root / DEFAULT_PROJECT_GOALS.name
        declared_dirs = set()
        for goal in registry.get("goals", []):
            if not isinstance(goal, dict) or not isinstance(goal.get("state_file"), str):
                continue
            state = Path(goal["state_file"]).expanduser()
            state = state if state.is_absolute() else root.parent / state
            if state.name == "ACTIVE_GOAL_STATE.md" and state.parent.parent == goal_root:
                declared_dirs.add(state.parent)
        if goal_root.is_dir() and not _is_redirected_path(goal_root) and all(
            child in declared_dirs and child.is_dir() and not _is_redirected_path(child)
            for child in goal_root.iterdir()
        ):
            project_owned.add(goal_root.name)
    return any(
        child.name not in project_owned and not _is_route_observation(child)
        for child in root.iterdir()
    )


def _is_route_observation(path: Path) -> bool:
    """Known host observations/leases do not declare a machine state route.

    Keep every unknown file, directory or redirected entry authoritative for
    discovery. This recognizes existing producer layouts, never deletes them.
    """
    from .rollout_event_log import DEFAULT_ROLLOUT_EVENT_LOG_NAME

    if _is_redirected_path(path):
        return False
    if path.name == GLOBAL_REGISTRY_FILENAME + ".lock":
        return path.is_file()
    # The native effect runtime locks every registry it writes through, so a
    # mere lock file must not turn a default root into a second authority.
    # Spelled here because file_lock imports this module.
    if path.name == GLOBAL_REGISTRY_FILENAME + ".ts-effect.lock":
        return path.is_file()
    if path.name == "lark-consumers" and path.is_dir():
        import re

        return all(
            not _is_redirected_path(child) and child.is_file()
            and re.fullmatch(r"[0-9a-f]{32}\.lock", child.name)
            for child in path.iterdir()
        )
    if path.name != "runtime" or not path.is_dir():
        return False
    for goals in path.iterdir():
        if goals.name != "goals" or _is_redirected_path(goals) or not goals.is_dir():
            return False
        for goal in goals.iterdir():
            if _is_redirected_path(goal) or not goal.is_dir():
                return False
            for entry in goal.iterdir():
                if (_is_redirected_path(entry) or not entry.is_file()
                        or entry.name not in {DEFAULT_ROLLOUT_EVENT_LOG_NAME,
                                              DEFAULT_ROLLOUT_EVENT_LOG_NAME + ".lock"}):
                    return False
    return True


def default_runtime_route() -> dict[str, object]:
    """Inspect the two default routes without creating either one."""

    current = global_registry_path(DEFAULT_RUNTIME_ROOT)
    legacy = global_registry_path(LEGACY_RUNTIME_ROOT)
    current_exists = current.exists() or current.is_symlink()
    legacy_exists = legacy.exists() or legacy.is_symlink()
    invalid = any(
        _is_redirected_path(path) or not path.is_file()
        for path, present in ((current, current_exists), (legacy, legacy_exists))
        if present
    )
    roots = (DEFAULT_RUNTIME_ROOT, LEGACY_RUNTIME_ROOT)
    invalid = invalid or any(
        _is_redirected_path(root) or (root.exists() and not root.is_dir()) for root in roots
    )
    # Machine configuration and extension activation can predate the first
    # Goal registry. Preserve any existing state in an owned default root;
    # an empty directory alone does not establish a second authority.
    current_state, legacy_state = (
        _runtime_root_has_machine_state(root) for root in roots
    ) if not invalid else (False, False)
    if invalid:
        status = RuntimeRouteStatus.INVALID
    elif current_state and legacy_state:
        status = RuntimeRouteStatus.CONFLICT
    elif legacy_state:
        status = RuntimeRouteStatus.LEGACY
    elif current_state:
        status = RuntimeRouteStatus.CURRENT
    else:
        status = RuntimeRouteStatus.FRESH
    selected = LEGACY_RUNTIME_ROOT if status == "legacy" else DEFAULT_RUNTIME_ROOT
    recommended_action = None
    if status == "conflict":
        recommended_action = (
            "Select one registry with --registry/--runtime-root; "
            "inspect both roots before migration."
        )
    elif status == "invalid":
        recommended_action = (
            "A default runtime root is linked or not a directory, or a default "
            "registry path is not a regular file; inspect it before continuing."
        )
    elif status == "legacy":
        recommended_action = (
            "Preview `loopx migrate-local-state`; existing state remains on its legacy route."
            if legacy_exists else
            "Existing machine state remains on its legacy route without a Goal registry. "
            "Ordinary registration will use that route; directory migration requires "
            "a registered global registry."
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
        conflict = (
            "Both default LoopX registries exist."
            if route["target_registry_exists"] and route["legacy_registry_exists"]
            else "Both default LoopX runtime roots contain state."
        )
        raise ValueError(
            f"{conflict} Select an explicit --registry and "
            "--runtime-root; resolve the route conflict before using implicit defaults."
        )
    if route["status"] == "invalid":
        raise ValueError(str(route["recommended_action"]))
    return Path(str(route["selected_runtime_root"]))


def configured_runtime_route(
    *, registry_path: Path | None = None, runtime_root_override: str | None = None,
) -> dict[str, object]:
    """Diagnose the caller's selected route, preserving default-route facts."""
    route = default_runtime_route()
    from .control_plane.projects.registry_codec import load_registry

    try:
        registry = load_registry(registry_path) if registry_path and registry_path.exists() else {}
        if not runtime_root_override and not registry.get("common_runtime_root"):
            return route
        selected = resolve_runtime_root(registry, runtime_root_override, registry_path=registry_path)
        invalid = _is_redirected_path(selected) or (selected.exists() and not selected.is_dir())
        route.update(
            default_status=route["status"],
            status=RuntimeRouteStatus.INVALID if invalid else RuntimeRouteStatus.CONFIGURED,
            selected_runtime_root=str(selected),
            recommended_action="Configured runtime root must be an unlinked directory." if invalid else None,
        )
    except (OSError, ValueError) as error:
        route.update(default_status=route["status"], status=RuntimeRouteStatus.INVALID, recommended_action=str(error))
    return route


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
    value: object = override
    if not value:
        value = registry.get("common_runtime_root") if isinstance(registry, dict) else None
    if not value:
        return select_default_runtime_root()

    runtime_root = Path(str(value)).expanduser()
    if runtime_root.is_absolute() or registry_path is None:
        return runtime_root
    return registry_project_root(registry_path) / runtime_root


def effective_runtime_root(
    registry_path: Path,
    runtime_root_override: str | Path | None,
) -> Path:
    """Resolve the one runtime root every writer hook of a CLI call must share.

    ``--runtime-root`` wins when given; otherwise the registry's
    ``common_runtime_root`` applies, and a relative value resolves against the
    registry's project root rather than the caller's working directory. Todo,
    follow-up, handoff-mode, and task-lease hooks all consume this value so one
    goal never splits into two candidate lineages.
    """

    # The codec imports path constants; load it at this call boundary to avoid
    # a module cycle. No capture or drain owner participates in route selection.
    from .control_plane.projects.registry_codec import load_registry

    registry = load_registry(registry_path)
    override = str(runtime_root_override) if runtime_root_override is not None else None
    return resolve_runtime_root(registry, override, registry_path=registry_path)


def rel_or_abs(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
