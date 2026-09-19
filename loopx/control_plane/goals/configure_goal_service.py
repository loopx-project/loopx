from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...configuration_transaction import goal_capability_configuration_revision
from ...configure_goal import configure_goal
from ...file_lock import exclusive_file_lock
from ...global_registry import (
    sanitize_goal_for_global,
    sync_project_registry_to_global,
)
from ...history import load_registry
from ...paths import global_registry_path, resolve_runtime_root
from ...registry import registry_goals
from ...registry_writability import probe_registry_write_path
from ..runtime.runtime_projection_route import (
    compact_runtime_projection_route,
    resolve_goal_source_runtime_route,
    resolve_runtime_projection_route,
)


CONFIGURE_GOAL_GLOBAL_SYNC_SCHEMA_VERSION = "configure_goal_global_sync_v0"
CONFIGURE_GOAL_GLOBAL_SYNC_READBACK_SCHEMA_VERSION = (
    "configure_goal_global_sync_readback_v0"
)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except OSError:
        return str(left.expanduser()) == str(right.expanduser())


def _goal(payload: dict[str, Any], goal_id: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in registry_goals(payload)
            if str(item.get("id") or "") == goal_id
        ),
        None,
    )


def _resolve_authoritative_source_registry(
    *, registry_path: Path, goal_id: str
) -> Path:
    """Resolve a goal write to its project registry.

    The shared registry is a read model. A caller may still provide its path
    when a Dashboard or CLI process is configured against the shared runtime,
    so route that request before previewing, locking, or applying the Goal.
    ``runtime_root_override`` is deliberately absent here: that option chooses
    the projection target and must not change source authority.
    """

    invoked_registry = registry_path.expanduser().resolve()
    route = resolve_goal_source_runtime_route(
        registry_path=invoked_registry,
        goal_id=goal_id,
    )
    source_text = str(route.get("source_registry") or "").strip()
    if not source_text:
        raise ValueError(
            f"goal {goal_id!r} source registry route did not resolve; refusing to configure"
        )
    return Path(source_text).expanduser().resolve()


def read_goal_configuration_with_source_route(
    *, registry_path: Path, goal_id: str, execute: bool = False
) -> dict[str, Any]:
    """Read Goal configuration from the canonical source registry.

    ``execute`` is accepted to preserve the shared reader callable shape; a
    read route never performs a write.
    """

    if execute:
        raise ValueError("Goal configuration reads cannot execute a write")
    source_registry_path = _resolve_authoritative_source_registry(
        registry_path=registry_path,
        goal_id=goal_id,
    )
    return configure_goal(
        registry_path=source_registry_path,
        goal_id=goal_id,
        execute=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def resolve_configure_goal_sync_target(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root_override: str | None,
) -> dict[str, Any]:
    source_registry = registry_path.expanduser().resolve()
    source_payload = load_registry(source_registry)
    if _goal(source_payload, goal_id) is None:
        raise ValueError(f"goal id not found in source registry: {goal_id}")
    source_runtime = (
        resolve_runtime_root(
            source_payload,
            None,
            registry_path=source_registry,
        )
        .expanduser()
        .resolve()
    )

    if runtime_root_override:
        target_runtime = Path(runtime_root_override).expanduser().resolve()
        route = {
            "schema_version": "runtime_projection_route_v0",
            "status": "explicit_override",
            "projection_required": not _same_path(source_runtime, target_runtime),
            "declaration_source": "cli.runtime_root_override",
            "source_registry": str(source_registry),
            "source_runtime_root": str(source_runtime),
            "target_runtime_root": str(target_runtime),
            "target_registry": str(global_registry_path(target_runtime)),
            "match_count": 1,
            "source_mirror_match_count": 0,
            "conflict_count": 0,
            "unreadable_target_count": 0,
        }
    else:
        route = resolve_runtime_projection_route(
            registry_path=source_registry,
            goal_id=goal_id,
            source_runtime_root=source_runtime,
        )
        status = str(route.get("status") or "missing")
        if status not in {"resolved", "single_runtime"}:
            raise ValueError(
                f"configure-goal global sync route is {status}; refusing to write "
                "the source registry without one authoritative shared runtime"
            )
        target_text = str(route.get("target_runtime_root") or "").strip()
        if not target_text:
            raise ValueError(
                "configure-goal global sync route has no target runtime; refusing partial write"
            )
        target_runtime = Path(target_text).expanduser().resolve()

    target_registry = global_registry_path(target_runtime).expanduser().resolve()
    return {
        "ok": True,
        "schema_version": CONFIGURE_GOAL_GLOBAL_SYNC_SCHEMA_VERSION,
        "goal_id": goal_id,
        "status": route.get("status"),
        "declaration_source": route.get("declaration_source"),
        "explicit_runtime_override": bool(runtime_root_override),
        "source_registry": str(source_registry),
        "source_runtime_root": str(source_runtime),
        "target_runtime_root": str(target_runtime),
        "target_global_registry": str(target_registry),
        "route": compact_runtime_projection_route(route),
    }


def _readback(
    *,
    source_registry: Path,
    target_registry: Path,
    goal_id: str,
    sync_payload: dict[str, Any],
) -> dict[str, Any]:
    source_payload = load_registry(source_registry)
    target_payload = load_registry(target_registry)
    source_goal = _goal(source_payload, goal_id)
    target_goal = _goal(target_payload, goal_id)
    synced_at = str(sync_payload.get("updated_at") or "").strip()
    source_is_target = _same_path(source_registry, target_registry)

    expected_goal = source_goal
    if source_goal is not None and not source_is_target:
        expected_goal = sanitize_goal_for_global(
            source_goal,
            source_registry=source_registry,
            synced_at=synced_at,
        )
    expected_digest = _digest(expected_goal) if expected_goal is not None else None
    target_digest = _digest(target_goal) if target_goal is not None else None
    target_source_registry = str(
        (target_goal or {}).get("source_registry") or ""
    ).strip()
    source_registry_match = bool(
        target_goal is not None
        and (
            source_is_target
            or (
                target_source_registry
                and _same_path(Path(target_source_registry), source_registry)
            )
        )
    )
    synced_at_match = bool(
        source_is_target
        or (
            target_goal is not None
            and str(target_goal.get("synced_at") or "") == synced_at
        )
    )
    verified = bool(
        source_goal is not None
        and target_goal is not None
        and expected_digest == target_digest
        and source_registry_match
        and synced_at_match
    )
    return {
        "schema_version": CONFIGURE_GOAL_GLOBAL_SYNC_READBACK_SCHEMA_VERSION,
        "status": "verified" if verified else "mismatch",
        "verified": verified,
        "goal_id": goal_id,
        "target_global_registry": str(target_registry),
        "goal_present": target_goal is not None,
        "source_registry_match": source_registry_match,
        "synced_at_match": synced_at_match,
        "expected_goal_sha256_16": expected_digest,
        "target_goal_sha256_16": target_digest,
    }


def _sync_plan(
    *,
    changed: bool,
    target_resolution: dict[str, Any] | None,
    execute: bool,
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": CONFIGURE_GOAL_GLOBAL_SYNC_SCHEMA_VERSION,
        "enabled": bool(changed),
        "required": bool(changed),
        "executed": False,
        "target_resolution": target_resolution,
        "selected_target": (
            {
                "runtime_root": target_resolution.get("target_runtime_root"),
                "global_registry": target_resolution.get("target_global_registry"),
                "declaration_source": target_resolution.get("declaration_source"),
                "explicit_runtime_override": target_resolution.get(
                    "explicit_runtime_override"
                ),
            }
            if target_resolution
            else None
        ),
        "readback": {
            "schema_version": CONFIGURE_GOAL_GLOBAL_SYNC_READBACK_SCHEMA_VERSION,
            "status": "not_required" if not changed else "not_executed",
            "verified": False,
        },
        "reason": (
            "no configuration change"
            if not changed
            else "dry-run preview; source and shared registries are unchanged"
            if not execute
            else None
        ),
    }


def _configure_goal_with_global_sync_unlocked(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root_override: str | None,
    execute: bool,
    **configure_options: Any,
) -> dict[str, Any]:
    """Configure one source goal and keep its authoritative shared read model current."""

    preview = configure_goal(
        registry_path=registry_path,
        goal_id=goal_id,
        execute=False,
        **configure_options,
    )
    changed = bool(preview.get("changed"))
    target_resolution = (
        resolve_configure_goal_sync_target(
            registry_path=registry_path,
            goal_id=goal_id,
            runtime_root_override=runtime_root_override,
        )
        if changed
        else None
    )
    preview["global_sync"] = _sync_plan(
        changed=changed,
        target_resolution=target_resolution,
        execute=execute,
    )
    if not execute:
        return preview

    if not changed:
        applied = configure_goal(
            registry_path=registry_path,
            goal_id=goal_id,
            execute=True,
            **configure_options,
        )
        applied["global_sync"] = preview["global_sync"]
        return applied

    if target_resolution is None:
        raise RuntimeError("configure-goal sync target was not resolved")
    target_registry = Path(str(target_resolution["target_global_registry"]))
    writability = probe_registry_write_path(target_registry, create_parent=True)
    if not writability.get("ok"):
        preview.update(
            {
                "ok": False,
                "dry_run": False,
                "execute": True,
                "written": False,
                "error": str(
                    writability.get("error")
                    or "authoritative shared registry is not writable"
                ),
                "recommended_action": writability.get("recommended_action"),
            }
        )
        preview["global_sync"].update(
            {
                "ok": False,
                "reason": "authoritative shared registry preflight failed",
                "global_registry_writability": writability,
            }
        )
        return preview

    applied = configure_goal(
        registry_path=registry_path,
        goal_id=goal_id,
        execute=True,
        **configure_options,
    )
    if not applied.get("written"):
        applied["global_sync"] = _sync_plan(
            changed=False,
            target_resolution=target_resolution,
            execute=True,
        )
        return applied

    sync_payload = sync_project_registry_to_global(
        registry_path=registry_path,
        runtime_root_override=str(target_resolution["target_runtime_root"]),
        goal_id=goal_id,
        dry_run=False,
    )
    readback = (
        _readback(
            source_registry=registry_path.expanduser().resolve(),
            target_registry=target_registry,
            goal_id=goal_id,
            sync_payload=sync_payload,
        )
        if sync_payload.get("ok")
        else {
            "schema_version": CONFIGURE_GOAL_GLOBAL_SYNC_READBACK_SCHEMA_VERSION,
            "status": "not_run",
            "verified": False,
        }
    )
    sync_ok = bool(sync_payload.get("ok") and readback.get("verified"))
    applied["global_sync"] = {
        **_sync_plan(
            changed=True,
            target_resolution=target_resolution,
            execute=True,
        ),
        "ok": sync_ok,
        "executed": True,
        "sync": sync_payload,
        "readback": readback,
        "global_registry_writability": writability,
    }
    applied["ok"] = bool(applied.get("ok") and sync_ok)
    applied["partial_write"] = bool(applied.get("written") and not sync_ok)
    if not sync_ok:
        applied["error"] = "configure-goal shared registry readback did not verify"
        applied["recommended_action"] = (
            sync_payload.get("recommended_action")
            or f"rerun loopx sync-global --goal-id {goal_id} after repairing the shared runtime route"
        )
    return applied


def configure_goal_with_global_sync(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root_override: str | None,
    execute: bool,
    expected_goal_configuration_revision: str | None = None,
    align_codex_subagent_capacity: bool = False,
    codex_home_override: Path | None = None,
    codex_host_capacity_planner: Callable[..., dict[str, Any]] | None = None,
    codex_host_capacity_applier: Callable[..., dict[str, Any]] | None = None,
    **configure_options: Any,
) -> dict[str, Any]:
    """Configure one Goal under the shared registry mutation lock.

    Browser callers may bind apply to the Goal-owned catalog revision they
    previewed. The lock keeps that recheck and the source write in one critical
    section across concurrent Dashboard and CLI configuration requests.
    """

    source_registry_path = _resolve_authoritative_source_registry(
        registry_path=registry_path,
        goal_id=goal_id,
    )
    def add_host_capacity(
        payload: dict[str, Any],
        *,
        receipt: dict[str, Any] | None = None,
        plan_before_apply: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        orchestration = (payload.get("after") or {}).get("orchestration") or {}
        required_children = (
            int(orchestration.get("max_children") or 0)
            if orchestration.get("mode") == "multi_subagent"
            and orchestration.get("spawn_allowed") is True
            else 0
        )
        if align_codex_subagent_capacity and codex_host_capacity_planner is None:
            raise ValueError(
                "Codex host-capacity alignment requires a host adapter"
            )
        if required_children == 0:
            plan = {
                "schema_version": "codex_subagent_host_capacity_v0",
                "host": "codex",
                "status": "not_required",
                "action": "none",
                "required_children": 0,
                "configured_children": None,
                "configured_source_key": None,
                "canonical_config_key": (
                    "agents.max_concurrent_threads_per_session"
                ),
                "legacy_alias_present": False,
                "counts_main_thread": False,
                "write_required": False,
                "never_lower": True,
                "config_path": None,
                "source_sha256": None,
                "new_session_required_after_write": False,
                "reason": "Goal sub-agents are disabled",
            }
        elif align_codex_subagent_capacity:
            plan = codex_host_capacity_planner(
                required_children,
                home=codex_home_override,
            )
        else:
            plan = {
                "schema_version": "codex_subagent_host_capacity_v0",
                "host": "codex",
                "status": "not_requested",
                "action": "preview_with_explicit_alignment_request",
                "required_children": required_children,
                "configured_children": None,
                "configured_source_key": None,
                "canonical_config_key": (
                    "agents.max_concurrent_threads_per_session"
                ),
                "legacy_alias_present": False,
                "counts_main_thread": False,
                "write_required": False,
                "never_lower": True,
                "config_path": None,
                "source_sha256": None,
                "new_session_required_after_write": False,
                "reason": (
                    "Codex host capacity was not inspected because alignment was not requested"
                ),
            }
        goal_changed = bool(payload.get("changed"))
        host_change = bool(
            (align_codex_subagent_capacity and plan["write_required"])
            or (receipt or {}).get("written")
        )
        payload["goal_configuration_changed"] = goal_changed
        payload["codex_host_capacity"] = {
            **plan,
            "alignment_requested": bool(align_codex_subagent_capacity),
            "receipt": receipt,
        }
        if host_change:
            payload["changed"] = True
            changed_fields = list(payload.get("changed_fields") or [])
            if "codex_host_capacity" not in changed_fields:
                changed_fields.append("codex_host_capacity")
            payload["changed_fields"] = changed_fields
        if receipt is not None:
            payload["codex_host_capacity"]["plan_before_apply"] = (
                plan_before_apply or plan
            )
            payload["codex_host_capacity"].update(
                {
                    key: value
                    for key, value in receipt.items()
                    if key
                    not in {
                        "schema_version",
                        "config_path",
                        "source_sha256",
                    }
                }
            )
            payload["written"] = bool(
                payload.get("written") or receipt.get("written")
            )
            payload["ok"] = bool(
                payload.get("ok") and receipt.get("readback_verified")
            )
        return payload

    if not execute:
        preview = _configure_goal_with_global_sync_unlocked(
            registry_path=source_registry_path,
            goal_id=goal_id,
            runtime_root_override=runtime_root_override,
            execute=False,
            **configure_options,
        )
        return add_host_capacity(preview)
    with exclusive_file_lock(
        source_registry_path,
        operation="configure_goal_with_global_sync",
    ):
        if expected_goal_configuration_revision is not None:
            current = configure_goal(
                registry_path=source_registry_path,
                goal_id=goal_id,
                execute=False,
            )
            catalog = current.get("configuration_catalog")
            capability_catalog = (
                catalog.get("capability_catalog")
                if isinstance(catalog, dict)
                else None
            )
            if not isinstance(capability_catalog, dict):
                raise ValueError("Goal capability catalog is unavailable")
            actual_revision = goal_capability_configuration_revision(
                goal_id,
                capability_catalog,
            )
            if actual_revision != expected_goal_configuration_revision:
                raise ValueError("Goal configuration changed; preview again")
        preview = _configure_goal_with_global_sync_unlocked(
            registry_path=source_registry_path,
            goal_id=goal_id,
            runtime_root_override=runtime_root_override,
            execute=False,
            **configure_options,
        )
        preview = add_host_capacity(preview)
        capacity_plan = preview["codex_host_capacity"]
        applied = _configure_goal_with_global_sync_unlocked(
            registry_path=source_registry_path,
            goal_id=goal_id,
            runtime_root_override=runtime_root_override,
            execute=True,
            **configure_options,
        )
        if not applied.get("ok"):
            return add_host_capacity(
                applied,
                plan_before_apply=capacity_plan,
            )
        capacity_receipt = None
        if align_codex_subagent_capacity and capacity_plan["write_required"]:
            if codex_host_capacity_applier is None:
                raise ValueError(
                    "Codex host-capacity apply requires a host adapter"
                )
            try:
                capacity_receipt = codex_host_capacity_applier(
                    int(capacity_plan["required_children"]),
                    expected_source_sha256=str(capacity_plan["source_sha256"]),
                    home=codex_home_override,
                )
            except (OSError, ValueError) as exc:
                partial = add_host_capacity(
                    applied,
                    plan_before_apply=capacity_plan,
                )
                partial["ok"] = False
                partial["partial_write"] = bool(applied.get("written"))
                partial["error"] = (
                    "Goal configuration applied, but Codex host-capacity alignment failed: "
                    f"{exc}"
                )
                partial["recommended_action"] = (
                    "repair or refresh the Codex host configuration, then preview the "
                    "same Goal capacity alignment again"
                )
                partial["codex_host_capacity"].update(
                    {
                        "status": "apply_failed",
                        "readback_verified": False,
                        "written": False,
                    }
                )
                return partial
        return add_host_capacity(
            applied,
            receipt=capacity_receipt,
            plan_before_apply=capacity_plan,
        )
