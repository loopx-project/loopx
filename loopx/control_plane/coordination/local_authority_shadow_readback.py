"""Read-only shadow status and typed candidate-store transport.

The operator can inspect retained bytes without any source capture producer.
No write, drain, promotion or replacement-store initialization is performed.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..projects.registry_codec import load_registry
from ...paths import resolve_runtime_root
from ...registry import find_registry_goal
from ..effect_runtime import effect_runtime_result
from . import local_authority_shadow_history as outbox
from .coordination_state_contract_generated import (
    LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA,
)
from .local_authority_shadow_projection import head_digest
from .configuration import resolve_coordination_runtime_shadow_config, local_authority_shadow_summary
from .shadow_management import read_shadow_capture_binding

RETENTION_PRESSURE_BYTES = 8 * 1024 * 1024

def read_local_authority_shadow(
    *,
    runtime_root: Path,
    goal_id: str,
    store_kind: str = "runtime_shadow",
    scan_after_cursor: str | None = None,
    scan_limit: int = 0,
    receipt_operation_id: str | None = None,
    read_model: str = "full",
) -> dict[str, Any]:
    """Read-only candidate view through the TypeScript store boundary."""

    result = effect_runtime_result(
        "coordination.runtime_shadow.outbox_read",
        {
            "schema_version": LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
            "runtime_root": str(runtime_root),
            "goal_id": goal_id,
            "store_kind": store_kind,
            "scan_after_cursor": scan_after_cursor,
            "scan_limit": scan_limit,
            "receipt_operation_id": receipt_operation_id,
            "read_model": read_model,
        },
        timeout=15.0,
    )
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA
        or result.get("goal_id") != goal_id
    ):
        raise RuntimeError("local authority shadow read result is invalid")
    return dict(result)

class _CandidateMissing(Exception):
    """The candidate store directory does not exist yet."""


def _store_bytes(runtime_root: Path, goal_id: str, *, legacy_observation: bool) -> int:
    directory = (
        runtime_root / "authority-shadow" / "file" / goal_id
        if legacy_observation
        else runtime_root / "authority-shadow" / "file-v0"
    )
    if not directory.is_dir():
        return 0
    return sum(path.stat().st_size for path in directory.iterdir() if path.is_file())


def local_authority_shadow_status(
    *,
    registry_path: Path,
    runtime_root: Path | None,
    goal_id: str,
) -> dict[str, Any]:
    """Operator readback: configuration, outbox backlog, and candidate head facts."""

    registry = load_registry(registry_path)
    goal = find_registry_goal(registry, goal_id)
    if not isinstance(goal, dict):
        raise ValueError(f"goal {goal_id!r} is not registered")
    if runtime_root is None:
        runtime_root = resolve_runtime_root(registry, None, registry_path=registry_path)
    config = local_authority_shadow_summary(goal)
    runtime_config = resolve_coordination_runtime_shadow_config(goal)
    management = read_shadow_capture_binding(runtime_root, goal_id)
    legacy_observation = (
        config.get("configured") is True
        and not runtime_config.enabled
        and management["status"] == "missing"
    )
    backlog = outbox.outbox_summary(runtime_root, goal_id)
    candidate: dict[str, Any]
    try:
        directory = (
            runtime_root / "authority-shadow" / "file" / goal_id
            if legacy_observation
            else runtime_root / "authority-shadow" / "file-v0"
        )
        if not directory.is_dir() or not any(directory.glob("authority-store-*.json")):
            # Reading through the store boundary would mint a store identity;
            # a status probe must not create candidate lineage.
            raise _CandidateMissing
        view = read_local_authority_shadow(
            runtime_root=runtime_root,
            goal_id=goal_id,
            store_kind=(
                "legacy_observation" if legacy_observation else "runtime_shadow"
            ),
        )
        head = view.get("head") if isinstance(view.get("head"), dict) else None
        candidate = {
            "status": view.get("status"),
            "reason_code": view.get("reason_code"),
            "store_identity": view.get("store_identity"),
            "provider_revision": view.get("provider_revision"),
            "cursor": view.get("cursor"),
            "head_digest": view.get("head_digest"),
            "head_schema_version": head.get("schema_version") if head else None,
            "partitions": view.get("partitions"),
            "codec_agreement": (head_digest(head) == view.get("head_digest"))
            if head
            else None,
        }
    except _CandidateMissing:
        candidate = {
            "status": "missing",
            "reason_code": None,
            "store_identity": None,
            "provider_revision": None,
            "cursor": None,
            "head_digest": None,
            "head_schema_version": None,
            "partitions": None,
            "codec_agreement": None,
        }
    except Exception:
        candidate = {
            "status": "unavailable",
            "reason_code": "shadow_read_failed",
            "store_identity": None,
            "provider_revision": None,
            "cursor": None,
            "head_digest": None,
            "head_schema_version": None,
            "partitions": None,
            "codec_agreement": None,
        }
    candidate["store_kind"] = "legacy_observation" if legacy_observation else "runtime_shadow"
    candidate["historical_only"] = legacy_observation
    try:
        store_bytes = _store_bytes(
            runtime_root, goal_id, legacy_observation=legacy_observation
        )
        storage_error = None
    except OSError:
        store_bytes = None
        storage_error = "shadow_store_unavailable"
    return {
        "ok": all(item["invalid"] is None for item in backlog.values())
        and storage_error is None
        and management["status"] != "hold",
        "action": "status",
        "goal_id": goal_id,
        "config": config,
        "runtime_config": asdict(runtime_config),
        "management": management,
        "storage_error": storage_error,
        "runtime_root_digest": outbox.runtime_root_digest(runtime_root),
        "outbox": backlog,
        "candidate": candidate,
        "store_bytes": store_bytes,
        "retention_pressure": store_bytes > RETENTION_PRESSURE_BYTES
        if store_bytes is not None
        else None,
    }
