from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import threading
from typing import Any, Iterable

from ..goals.activation import (
    GoalActivationState,
    goal_activation_state,
    normalize_goal_activation_state,
)
from ...history import load_index, load_registry
from ...paths import global_registry_path, resolve_runtime_root, select_default_runtime_root
from ...registry import registry_goals


RUNTIME_PROJECTION_ROUTE_SCHEMA_VERSION = "runtime_projection_route_v0"
RUNTIME_PROJECTION_ROUTE_DIAGNOSTICS_SCHEMA_VERSION = (
    "runtime_projection_route_diagnostics_v0"
)
GOAL_SOURCE_RUNTIME_ROUTE_SCHEMA_VERSION = "goal_source_runtime_route_v0"
SOURCE_REGISTRY_READ_TIMEOUT_SECONDS = 1.0


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except OSError:
        return str(left.expanduser()) == str(right.expanduser())


def _path_digest(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        value = str(path.expanduser().resolve())
    except OSError:
        value = str(path.expanduser())
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _resolved_source_registry(goal: dict[str, Any], *, registry_path: Path) -> Path | None:
    value = str(goal.get("source_registry") or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = registry_path.parent / path
    return path.resolve()


def _registry_runtime_root(
    registry: dict[str, Any],
    *,
    registry_path: Path,
    goal_id: str,
) -> Path:
    runtime_root = resolve_runtime_root(registry, None).expanduser()
    if not runtime_root.is_absolute():
        goal = next(
            (
                item
                for item in registry_goals(registry)
                if str(item.get("id") or "") == goal_id
            ),
            None,
        )
        repo = Path(str((goal or {}).get("repo") or "")).expanduser()
        if not repo.is_absolute():
            repo = (
                registry_path.parent.parent
                if registry_path.parent.name == ".loopx"
                else registry_path.parent
            )
        runtime_root = repo / runtime_root
    return runtime_root.resolve()


def _goal_source_route_packet(
    *,
    status: str,
    declaration_source: str,
    goal_id: str,
    invoked_registry: Path,
    source_registry: Path,
    invoked_runtime: Path,
    source_runtime: Path,
) -> dict[str, Any]:
    return {
        "schema_version": GOAL_SOURCE_RUNTIME_ROUTE_SCHEMA_VERSION,
        "status": status,
        "declaration_source": declaration_source,
        "goal_id": goal_id,
        "invoked_registry": str(invoked_registry),
        "source_registry": str(source_registry),
        "invoked_runtime_root": str(invoked_runtime),
        "source_runtime_root": str(source_runtime),
        "routed_to_source_registry": not _same_path(
            source_registry,
            invoked_registry,
        ),
    }


def resolve_goal_source_runtime_route(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root_override: str | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve goal-scoped writes and reads to the canonical source runtime.

    A global registry is a shared read model. When its goal declares a
    ``source_registry``, goal-scoped capabilities must use that registry's
    runtime rather than treating the shared projection as the source of truth.
    An explicit runtime override remains authoritative for diagnostics and
    recovery commands.
    """

    invoked_registry = registry_path.expanduser().resolve()
    registry_payload = registry if registry is not None else load_registry(invoked_registry)
    invoked_runtime = _registry_runtime_root(
        registry_payload,
        registry_path=invoked_registry,
        goal_id=goal_id,
    )
    if runtime_root_override:
        runtime_root = Path(runtime_root_override).expanduser().resolve()
        return _goal_source_route_packet(
            status="explicit_override",
            declaration_source="cli.runtime_root_override",
            goal_id=goal_id,
            invoked_registry=invoked_registry,
            source_registry=invoked_registry,
            invoked_runtime=invoked_runtime,
            source_runtime=runtime_root,
        )

    registry_is_global = bool(registry_payload.get("registry_role") == "global-local") or _same_path(
        invoked_registry,
        global_registry_path(invoked_runtime),
    )
    if not registry_is_global:
        return _goal_source_route_packet(
            status="project_registry",
            declaration_source="invoked_registry.common_runtime_root",
            goal_id=goal_id,
            invoked_registry=invoked_registry,
            source_registry=invoked_registry,
            invoked_runtime=invoked_runtime,
            source_runtime=invoked_runtime,
        )

    goal = next(
        (
            item
            for item in registry_goals(registry_payload)
            if str(item.get("id") or "") == goal_id
        ),
        None,
    )
    if goal is None:
        raise ValueError(
            f"goal-scoped runtime route requires registered goal {goal_id!r} in the global registry"
        )
    source_registry = _resolved_source_registry(goal, registry_path=invoked_registry)
    if source_registry is None or _same_path(source_registry, invoked_registry):
        return _goal_source_route_packet(
            status="global_runtime",
            declaration_source="global_registry.common_runtime_root",
            goal_id=goal_id,
            invoked_registry=invoked_registry,
            source_registry=invoked_registry,
            invoked_runtime=invoked_runtime,
            source_runtime=invoked_runtime,
        )
    if not source_registry.exists():
        raise ValueError(
            f"goal {goal_id!r} source_registry is missing; refusing to use the shared runtime as source"
        )
    try:
        source_payload = load_registry(source_registry)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"goal {goal_id!r} source_registry is unreadable; refusing to use the shared runtime as source"
        ) from exc
    if not any(str(item.get("id") or "") == goal_id for item in registry_goals(source_payload)):
        raise ValueError(
            f"goal {goal_id!r} is absent from source_registry; refusing to use the shared runtime as source"
        )
    source_runtime = _registry_runtime_root(
        source_payload,
        registry_path=source_registry,
        goal_id=goal_id,
    )
    return _goal_source_route_packet(
        status="source_registry",
        declaration_source="global_registry.goal.source_registry",
        goal_id=goal_id,
        invoked_registry=invoked_registry,
        source_registry=source_registry,
        invoked_runtime=invoked_runtime,
        source_runtime=source_runtime,
    )


def compact_goal_source_runtime_route(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": GOAL_SOURCE_RUNTIME_ROUTE_SCHEMA_VERSION,
        "status": route.get("status"),
        "declaration_source": route.get("declaration_source"),
        "routed_to_source_registry": bool(route.get("routed_to_source_registry")),
        "invoked_registry_sha256_16": _path_digest(
            Path(str(route.get("invoked_registry") or ""))
        ),
        "source_registry_sha256_16": _path_digest(
            Path(str(route.get("source_registry") or ""))
        ),
        "invoked_runtime_sha256_16": _path_digest(
            Path(str(route.get("invoked_runtime_root") or ""))
        ),
        "source_runtime_sha256_16": _path_digest(
            Path(str(route.get("source_runtime_root") or ""))
        ),
    }


def runtime_projection_candidate_roots(
    *,
    source_runtime_root: Path,
    candidate_roots: Iterable[Path] | None = None,
) -> list[Path]:
    roots: list[Path] = []
    if candidate_roots is None:
        configured = str(os.environ.get("LOOPX_RUNTIME_ROOT") or "").strip()
        if configured:
            roots.append(Path(configured).expanduser())
        roots.append(select_default_runtime_root())
    else:
        roots.extend(Path(root).expanduser() for root in candidate_roots)
    roots.append(source_runtime_root.expanduser())

    resolved: list[Path] = []
    for root in roots:
        candidate = root.resolve()
        if any(_same_path(candidate, existing) for existing in resolved):
            continue
        resolved.append(candidate)
    return resolved


def _route_id(
    *,
    registry_path: Path,
    goal_id: str,
    source_runtime_root: Path,
    target_runtime_roots: list[Path],
) -> str:
    payload = {
        "goal_id": goal_id,
        "source_registry": str(registry_path.resolve()),
        "source_runtime_root": str(source_runtime_root.resolve()),
        "target_runtime_roots": sorted(str(root.resolve()) for root in target_runtime_roots),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def resolve_runtime_projection_route(
    *,
    registry_path: Path,
    goal_id: str,
    source_runtime_root: Path,
    candidate_roots: Iterable[Path] | None = None,
) -> dict[str, Any]:
    """Resolve the registry-declared route from one source runtime to its read model."""

    source_registry = registry_path.expanduser().resolve()
    source_runtime = source_runtime_root.expanduser().resolve()
    provided_roots = (
        [Path(root).expanduser() for root in candidate_roots]
        if candidate_roots is not None
        else None
    )
    roots = runtime_projection_candidate_roots(
        source_runtime_root=source_runtime,
        candidate_roots=provided_roots,
    )
    configured_root_text = str(os.environ.get("LOOPX_RUNTIME_ROOT") or "").strip()
    explicit_roots = (
        provided_roots
        if provided_roots is not None
        else ([Path(configured_root_text).expanduser()] if configured_root_text else [])
    )
    matches: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    available_targets: list[Path] = []
    unreadable_target_count = 0
    for root in roots:
        candidate_registry = global_registry_path(root)
        if not candidate_registry.exists():
            continue
        available_targets.append(root)
        try:
            target_registry = load_registry(candidate_registry)
        except (OSError, ValueError, json.JSONDecodeError):
            unreadable_target_count += 1
            continue
        for goal in registry_goals(target_registry):
            if str(goal.get("id") or "") != goal_id:
                continue
            declared_source = _resolved_source_registry(
                goal,
                registry_path=candidate_registry,
            )
            candidate = {
                "target_runtime_root": root,
                "target_registry": candidate_registry.resolve(),
                "declared_source_registry": declared_source,
            }
            if declared_source and _same_path(declared_source, source_registry):
                matches.append(candidate)
            else:
                conflicts.append(candidate)

    unique_matches: list[dict[str, Any]] = []
    for match in matches:
        if any(
            _same_path(match["target_runtime_root"], item["target_runtime_root"])
            for item in unique_matches
        ):
            continue
        unique_matches.append(match)

    external_matches = [
        item
        for item in unique_matches
        if not _same_path(item["target_runtime_root"], source_runtime)
    ]
    source_mirror_match_count = len(unique_matches) - len(external_matches)
    effective_matches = external_matches or unique_matches
    target_roots = [item["target_runtime_root"] for item in effective_matches]
    source_is_global = _same_path(
        source_registry,
        global_registry_path(source_runtime),
    )
    if len(effective_matches) > 1:
        status = "ambiguous"
        target_runtime = None
        target_registry = None
        projection_required = True
        declaration_source = "multiple_target_registries.goal.source_registry"
    elif effective_matches:
        match = effective_matches[0]
        target_runtime = match["target_runtime_root"]
        target_registry = match["target_registry"]
        projection_required = not _same_path(target_runtime, source_runtime)
        status = "resolved" if projection_required else "single_runtime"
        declaration_source = "target_registry.goal.source_registry"
    elif source_is_global:
        status = "single_runtime"
        target_runtime = source_runtime
        target_registry = global_registry_path(source_runtime)
        projection_required = False
        declaration_source = "source_registry_is_global_registry"
        target_roots = [source_runtime]
    else:
        external_targets = [
            root for root in available_targets if not _same_path(root, source_runtime)
        ]
        explicit_external_targets = [
            root
            for root in external_targets
            if any(_same_path(root, explicit) for explicit in explicit_roots)
        ]
        conflict_targets = [
            item["target_runtime_root"]
            for item in conflicts
            if not _same_path(item["target_runtime_root"], source_runtime)
        ]
        missing_targets = explicit_external_targets or conflict_targets
        if missing_targets:
            status = "missing"
            target_runtime = missing_targets[0] if len(missing_targets) == 1 else None
            target_registry = (
                global_registry_path(target_runtime) if target_runtime is not None else None
            )
            projection_required = True
            declaration_source = "target_registry_route_missing"
            target_roots = missing_targets
        else:
            status = "single_runtime"
            target_runtime = source_runtime
            target_registry = global_registry_path(source_runtime)
            projection_required = False
            declaration_source = "source_runtime_fallback"
            target_roots = [source_runtime]

    route_id = _route_id(
        registry_path=source_registry,
        goal_id=goal_id,
        source_runtime_root=source_runtime,
        target_runtime_roots=target_roots,
    )
    return {
        "schema_version": RUNTIME_PROJECTION_ROUTE_SCHEMA_VERSION,
        "route_id": route_id,
        "goal_id": goal_id,
        "status": status,
        "projection_required": projection_required,
        "declaration_source": declaration_source,
        "source_registry": str(source_registry),
        "source_runtime_root": str(source_runtime),
        "target_runtime_root": str(target_runtime) if target_runtime is not None else None,
        "target_registry": str(target_registry) if target_registry is not None else None,
        "candidate_count": len(roots),
        "match_count": len(effective_matches),
        "source_mirror_match_count": source_mirror_match_count,
        "conflict_count": len(conflicts),
        "unreadable_target_count": unreadable_target_count,
        "target_runtime_digests": [_path_digest(root) for root in target_roots],
    }


def compact_runtime_projection_route(route: dict[str, Any]) -> dict[str, Any]:
    source_registry = str(route.get("source_registry") or "").strip()
    source_runtime = str(route.get("source_runtime_root") or "").strip()
    target_runtime = str(route.get("target_runtime_root") or "").strip()
    return {
        "schema_version": RUNTIME_PROJECTION_ROUTE_SCHEMA_VERSION,
        "route_id": route.get("route_id"),
        "status": route.get("status"),
        "projection_required": bool(route.get("projection_required")),
        "declaration_source": route.get("declaration_source"),
        "match_count": int(route.get("match_count") or 0),
        "source_mirror_match_count": int(
            route.get("source_mirror_match_count") or 0
        ),
        "conflict_count": int(route.get("conflict_count") or 0),
        "unreadable_target_count": int(route.get("unreadable_target_count") or 0),
        "source_registry_sha256_16": _path_digest(Path(source_registry))
        if source_registry
        else None,
        "source_runtime_sha256_16": _path_digest(Path(source_runtime))
        if source_runtime
        else None,
        "target_runtime_sha256_16": _path_digest(Path(target_runtime))
        if target_runtime
        else None,
    }


def _latest_route_source_row(
    *,
    runtime_root: Path,
    goal_id: str,
    route_id: str,
) -> dict[str, Any] | None:
    rows, _ = load_index(runtime_root / "goals" / goal_id / "runs" / "index.jsonl")
    for row in reversed(rows):
        marker = row.get("runtime_projection_route")
        if (
            isinstance(marker, dict)
            and marker.get("route_id") == route_id
            and marker.get("projection_enabled") is not False
        ):
            return row
    return None


def _route_projection_is_current(
    *,
    target_runtime_root: Path,
    goal_id: str,
    route_id: str,
    source_generated_at: Any,
    marker_field: str,
) -> bool:
    rows, _ = load_index(
        target_runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    )
    return any(
        isinstance(row.get(marker_field), dict)
        and row[marker_field].get("runtime_projection_route_id")
        == route_id
        and row[marker_field].get("source_generated_at") == source_generated_at
        for row in rows
    )


def _source_routes_for_registry(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str | None,
    activation_state_filter: GoalActivationState | str | None = None,
    source_registry_read_timeout_seconds: float = SOURCE_REGISTRY_READ_TIMEOUT_SECONDS,
    registry: dict[str, Any] | None = None,
) -> list[tuple[Path, Path, str, str | None]]:
    if registry is None:
        registry = load_registry(registry_path)
    is_global = bool(registry.get("registry_role") == "global-local") or _same_path(
        registry_path,
        global_registry_path(runtime_root),
    )
    routes: list[tuple[Path, Path, str, str | None]] = []
    source_reads: dict[str, tuple[dict[str, Any] | None, str | None]] = {}
    for goal in registry_goals(registry):
        current_goal_id = str(goal.get("id") or "")
        if not current_goal_id or (goal_id and current_goal_id != goal_id):
            continue
        if (
            activation_state_filter is not None
            and goal_activation_state(goal)
            is not normalize_goal_activation_state(activation_state_filter)
        ):
            continue
        source_registry = (
            _resolved_source_registry(goal, registry_path=registry_path)
            if is_global
            else registry_path.resolve()
        )
        if source_registry is None:
            continue
        source_key = str(source_registry)
        if source_key not in source_reads:
            source_reads[source_key] = _read_source_registry_with_deadline(
                source_registry,
                timeout_seconds=source_registry_read_timeout_seconds,
            )
        source_payload, source_error = source_reads[source_key]
        if source_payload is None:
            source_runtime = runtime_root
        else:
            source_runtime = resolve_runtime_root(source_payload, None)
        item = (source_registry, source_runtime, current_goal_id, source_error)
        if item not in routes:
            routes.append(item)
    return routes


def _read_source_registry_with_deadline(
    path: Path,
    *,
    timeout_seconds: float,
) -> tuple[dict[str, Any] | None, str | None]:
    """Read one diagnostic source without letting its file provider stall callers.

    The worker is intentionally daemonized: diagnostic reads are read-only and a
    provider-level ``open`` cannot be cancelled safely from the parent thread.
    A timed-out worker therefore cannot hold CLI process shutdown or mutate the
    registry binding. Every later diagnostic call starts a fresh read, so a
    hydrated provider recovers without registry repair.
    """

    result: queue.Queue[
        tuple[dict[str, Any] | None, str | None, Exception | None]
    ] = queue.Queue(maxsize=1)

    def read() -> None:
        try:
            if not path.exists():
                result.put((None, "source_registry_missing", None))
                return
            payload = load_registry(path)
        except Exception as exc:  # Propagate the existing loader contract.
            result.put((None, None, exc))
        else:
            result.put((payload, None, None))

    threading.Thread(
        target=read,
        name="loopx-source-registry-read",
        daemon=True,
    ).start()
    try:
        payload, status, error = result.get(timeout=max(0.0, timeout_seconds))
    except queue.Empty:
        return None, "source_registry_timeout"
    if status is not None:
        return None, status
    if error is not None:
        if isinstance(error, (OSError, ValueError, json.JSONDecodeError)):
            return None, "source_registry_unreadable"
        raise error
    assert payload is not None
    return payload, None


def collect_runtime_projection_route_diagnostics(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str | None = None,
    activation_state_filter: GoalActivationState | str | None = None,
    source_registry_read_timeout_seconds: float = SOURCE_REGISTRY_READ_TIMEOUT_SECONDS,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    source_routes = _source_routes_for_registry(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        activation_state_filter=activation_state_filter,
        source_registry_read_timeout_seconds=source_registry_read_timeout_seconds,
        registry=registry,
    )
    if registry is None:
        registry = load_registry(registry_path)
    registry_is_global = bool(registry.get("registry_role") == "global-local") or _same_path(
        registry_path,
        global_registry_path(runtime_root),
    )
    for source_registry, source_runtime, current_goal_id, source_error in source_routes:
        if source_error:
            items.append(
                {
                    "goal_id": current_goal_id,
                    "status": (
                        "unavailable"
                        if source_error == "source_registry_timeout"
                        else "missing"
                    ),
                    "reason": source_error,
                }
            )
            continue
        route = resolve_runtime_projection_route(
            registry_path=source_registry,
            goal_id=current_goal_id,
            source_runtime_root=source_runtime,
            candidate_roots=(
                [
                    runtime_root,
                    *runtime_projection_candidate_roots(
                        source_runtime_root=source_runtime,
                    ),
                ]
                if registry_is_global
                else None
            ),
        )
        compact = compact_runtime_projection_route(route)
        route_status = str(route.get("status") or "missing")
        diagnostic_status = route_status
        source_row = (
            _latest_route_source_row(
                runtime_root=source_runtime,
                goal_id=current_goal_id,
                route_id=str(route.get("route_id") or ""),
            )
            if route_status == "resolved"
            else None
        )
        if source_row:
            target_text = str(route.get("target_runtime_root") or "").strip()
            source_route = source_row.get("runtime_projection_route")
            marker_field = (
                str(source_route.get("projection_marker_field") or "").strip()
                if isinstance(source_route, dict)
                else ""
            ) or "shared_runtime_projection"
            current = bool(target_text) and _route_projection_is_current(
                target_runtime_root=Path(target_text),
                goal_id=current_goal_id,
                route_id=str(route.get("route_id") or ""),
                source_generated_at=source_row.get("generated_at"),
                marker_field=marker_field,
            )
            diagnostic_status = "healthy" if current else "lagging"
        elif route_status == "resolved":
            diagnostic_status = "ready"
        items.append(
            {
                "goal_id": current_goal_id,
                "status": diagnostic_status,
                "route": compact,
                "source_projection_observed": source_row is not None,
            }
        )

    counts = {
        status: sum(1 for item in items if item.get("status") == status)
        for status in (
            "healthy",
            "ready",
            "single_runtime",
            "missing",
            "unavailable",
            "ambiguous",
            "lagging",
        )
    }
    return {
        "schema_version": RUNTIME_PROJECTION_ROUTE_DIAGNOSTICS_SCHEMA_VERSION,
        "registry": str(registry_path.resolve()),
        "runtime_root": str(runtime_root.resolve()),
        "goal_filter": goal_id,
        "activation_state_filter": (
            normalize_goal_activation_state(activation_state_filter).value
            if activation_state_filter is not None else None
        ),
        "available": bool(items),
        "goal_count": len(items),
        "healthy": not any(
            item.get("status") in {"missing", "unavailable", "ambiguous", "lagging"}
            for item in items
        ),
        "source_registry_read_timeout_seconds": source_registry_read_timeout_seconds,
        "counts": counts,
        "items": items,
    }
