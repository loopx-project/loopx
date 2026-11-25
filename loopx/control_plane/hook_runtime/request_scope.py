from __future__ import annotations

import copy
import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast


class RegistryLoader(Protocol):
    """Typed registry loader interface for request-scoped hook evaluation."""

    def __call__(self, path: Path) -> dict[str, object]: ...


class SnapshotRegistryLoader(Protocol):
    """Registry loader accepting an optional `source_snapshots` dict."""

    def __call__(
        self,
        path: Path,
        *,
        source_snapshots: dict[Path, bytes | None] | None = None,
    ) -> dict[str, object]: ...


def _safe_absolute(path: Path) -> Path | None:
    try:
        return path.expanduser().absolute()
    except (OSError, RuntimeError):
        return None


def _safe_resolve(path: Path) -> Path | None:
    try:
        return path.expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def _supports_source_snapshots(loader: RegistryLoader) -> bool:
    try:
        return "source_snapshots" in inspect.signature(loader).parameters
    except (TypeError, ValueError):
        return False


def _invoke_registry_loader(
    loader: RegistryLoader,
    path: Path,
    *,
    source_snapshots: dict[Path, bytes | None],
) -> dict[str, object]:
    if _supports_source_snapshots(loader):
        snapshot_loader = cast(SnapshotRegistryLoader, loader)
        return dict(snapshot_loader(path, source_snapshots=source_snapshots))
    return dict(loader(path))


def _extract_goals_by_id(
    payload: dict[str, object],
) -> dict[str, dict[str, object]]:
    goals = payload.get("goals")
    if not isinstance(goals, list):
        return {}
    indexed: dict[str, dict[str, object]] = {}
    for goal in goals:
        if isinstance(goal, dict) and goal.get("id"):
            goal_id = str(goal["id"])
            if goal_id not in indexed:
                indexed[goal_id] = copy.deepcopy(goal)
    return indexed


@dataclass(slots=True)
class CapabilityHookRequestScope:
    """Single-request registry snapshot cache bound to one hook dispatch pass."""

    registry_snapshots: dict[Path, bytes | None] = field(default_factory=dict)
    _decoded_registries: dict[tuple[Path, str], dict[str, object]] = field(
        default_factory=dict
    )
    _goals_by_path: dict[tuple[Path, str], dict[str, dict[str, object]]] = field(
        default_factory=dict
    )
    _alias_to_targets: dict[Path, set[Path]] = field(default_factory=dict)

    def _record_targets(self, route: Path, *candidates: Path | None) -> None:
        targets = self._alias_to_targets.setdefault(route, set())
        for candidate in candidates:
            if candidate is not None:
                targets.add(candidate)

    def load_registry(
        self,
        path: Path,
        *,
        loader: RegistryLoader,
    ) -> dict[str, object]:
        """Return a fresh copy of `path` using this request's cached snapshot."""

        expanded = path.expanduser()
        route = _safe_absolute(expanded) or expanded
        cache_key = (route, expanded.name)
        if cache_key in self._decoded_registries:
            return copy.deepcopy(self._decoded_registries[cache_key])
        resolved_before = _safe_resolve(expanded)
        before_keys = set(self.registry_snapshots)
        payload = _invoke_registry_loader(
            loader,
            path,
            source_snapshots=self.registry_snapshots,
        )
        resolved_after = _safe_resolve(expanded)
        new_keys = set(self.registry_snapshots) - before_keys - {route}
        self._record_targets(route, resolved_before, resolved_after, *new_keys)
        self._decoded_registries[cache_key] = copy.deepcopy(payload)
        self._goals_by_path[cache_key] = _extract_goals_by_id(payload)
        return payload

    def load_goal(
        self,
        path: Path,
        goal_id: str,
        *,
        loader: RegistryLoader,
    ) -> dict[str, object] | None:
        """Return an isolated Goal copy from this request's cached snapshot."""

        expanded = path.expanduser()
        route = _safe_absolute(expanded) or expanded
        cache_key = (route, expanded.name)
        if cache_key not in self._decoded_registries:
            if route not in self.registry_snapshots and not expanded.exists():
                self.registry_snapshots.setdefault(route, None)
                self._record_targets(route, _safe_resolve(expanded))
                self._decoded_registries[cache_key] = {}
                self._goals_by_path[cache_key] = {}
                return None
            self.load_registry(path, loader=loader)
        goal = self._goals_by_path.get(cache_key, {}).get(str(goal_id))
        return copy.deepcopy(goal) if goal is not None else None

    def invalidate(self, path: Path) -> None:
        """Drop cached state and all alias snapshots resolving to `path`."""

        expanded = path.expanduser()
        route = _safe_absolute(expanded) or expanded
        resolved = _safe_resolve(expanded)
        targets = {route}
        if resolved is not None:
            targets.add(resolved)
        known_targets = self._alias_to_targets.get(route)
        if known_targets is not None:
            targets.update(known_targets)
        matching_aliases = {
            alias
            for alias, mapped_targets in self._alias_to_targets.items()
            if alias in targets or bool(mapped_targets & targets)
        }
        targets.update(matching_aliases)
        for snap_key in list(self.registry_snapshots):
            if snap_key in targets:
                continue
            snap_resolved = _safe_resolve(snap_key)
            snap_absolute = _safe_absolute(snap_key)
            if (snap_resolved is not None and snap_resolved in targets) or (
                snap_absolute is not None and snap_absolute in targets
            ):
                targets.add(snap_key)
        for key in targets:
            self.registry_snapshots.pop(key, None)
            self._alias_to_targets.pop(key, None)
        for cache_key in list(self._decoded_registries):
            if cache_key[0] in targets:
                self._decoded_registries.pop(cache_key, None)
        for cache_key in list(self._goals_by_path):
            if cache_key[0] in targets:
                self._goals_by_path.pop(cache_key, None)


_ACTIVE_HOOK_REQUEST_SCOPE: ContextVar[CapabilityHookRequestScope | None] = ContextVar(
    "loopx_capability_hook_request_scope",
    default=None,
)


def current_hook_request_scope() -> CapabilityHookRequestScope | None:
    """Return the active hook request scope, or `None` outside hook dispatch."""

    return _ACTIVE_HOOK_REQUEST_SCOPE.get()


def current_hook_registry_snapshots() -> dict[Path, bytes | None] | None:
    """Return the active request's `source_snapshots` dict, if any."""

    scope = _ACTIVE_HOOK_REQUEST_SCOPE.get()
    return scope.registry_snapshots if scope is not None else None


def invalidate_hook_registry_snapshot(path: Path) -> None:
    """Invalidate `path` inside the currently active hook request scope, if any."""

    scope = _ACTIVE_HOOK_REQUEST_SCOPE.get()
    if scope is not None:
        scope.invalidate(path)


def load_scoped_registry(
    path: Path,
    *,
    loader: RegistryLoader,
) -> dict[str, object]:
    """Load `path` via the active hook request scope when one is entered."""

    scope = _ACTIVE_HOOK_REQUEST_SCOPE.get()
    if scope is None:
        return dict(loader(path))
    return scope.load_registry(path, loader=loader)


@contextmanager
def capability_hook_request_scope() -> Iterator[CapabilityHookRequestScope]:
    """Scope registry snapshot reuse to a single hook dispatch pass."""

    existing = _ACTIVE_HOOK_REQUEST_SCOPE.get()
    if existing is not None:
        yield existing
        return
    scope = CapabilityHookRequestScope()
    token = _ACTIVE_HOOK_REQUEST_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _ACTIVE_HOOK_REQUEST_SCOPE.reset(token)
