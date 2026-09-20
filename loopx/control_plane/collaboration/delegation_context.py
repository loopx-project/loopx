"""Public-safe planning observations for authorized local delegation routes.

The local delegation configuration remains the execution-grant owner.  This
module only turns its requester-scoped directory and existing operation journal
into a bounded context observation; it never launches, resumes or accepts work.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..effect_runtime import EffectRuntimeRemoteError
from ..operator_provider import operator_provider_environ
from ..turn_driver.host_binding import managed_executor_binding_from_host_args


MAX_PROJECTED_ROUTES = 6
MAX_OPERATION_RECEIPTS = 10


def _observed_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _configuration_path(project: Path, config_ref: str) -> Path:
    root = project.expanduser().resolve()
    candidate = root / config_ref
    current = root
    for part in Path(config_ref).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("delegation configuration must not use symbolic links")
    path = candidate.resolve()
    if not path.is_relative_to(root):
        raise ValueError("delegation configuration escaped the Goal project")
    if not path.is_file():
        raise ValueError("delegation configuration is unavailable")
    return path


def _route(binding: dict[str, Any], *, runtime_root: Path) -> dict[str, Any]:
    executor = managed_executor_binding_from_host_args(
        binding["host_args"],
        environ=operator_provider_environ(runtime_root),
    )
    available = executor.get("available")
    readiness = (
        "ready" if available is True else "blocked" if available is False else "unknown"
    )
    row: dict[str, Any] = {
        "binding_id": binding["id"],
        "agent_id": binding["agent_id"],
        "todo_id": binding["todo_id"],
        "runtime_id": executor.get("executor") or "unknown",
        "executor_kind": executor.get("executor_kind") or "generic",
        "readiness": readiness,
    }
    profile = str(executor.get("execution_profile") or "").strip()
    if profile:
        row["execution_profile"] = profile
    reason = str(executor.get("unavailable_reason") or "").strip()
    if reason:
        row["reason_code"] = reason
    return row


def project_delegation_context(
    *,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    project: Path,
    execution_config: str | None,
    include_operation_receipts: bool = False,
) -> dict[str, Any]:
    """Return one bounded requester-scoped delegation observation.

    Route discovery is safe on planning hot paths. Operation inventory is
    deliberately opt-in because accepted-row readback reruns owner-pinned
    validation commands.
    """

    observed_at = _observed_at()
    if not execution_config:
        result: dict[str, Any] = {
            "schema_version": "loopx_delegation_context_v0",
            "configuration_state": "not_configured",
            "observed_at": observed_at,
            "authorized_count": 0,
            "projected_count": 0,
            "routes": [],
        }
        if include_operation_receipts:
            result["operation_receipts"] = {"observed": 0}
        return result

    try:
        from ...collaboration_mcp import Delegations

        config_path = _configuration_path(project, execution_config)
        service = Delegations(
            runtime_root, registry_path, goal_id, agent_id, config_path
        )
        directory = service.directory()
        bindings = directory.get("bindings")
        if not isinstance(bindings, list):
            raise ValueError("delegation directory is unavailable")
        routes = [
            _route(service.binding(str(row["id"])), runtime_root=runtime_root)
            for row in bindings[:MAX_PROJECTED_ROUTES]
        ]
        result = {
            "schema_version": "loopx_delegation_context_v0",
            "configuration_state": "ready",
            "observed_at": observed_at,
            "authorized_count": len(bindings),
            "projected_count": len(routes),
            "routes": routes,
        }
        if include_operation_receipts:
            inventory = service.operations(limit=MAX_OPERATION_RECEIPTS)
            items = inventory.get("items") if isinstance(inventory, dict) else []
            statuses = Counter(
                str(item.get("status") or "unavailable")
                for item in items
                if isinstance(item, dict)
            )
            recovery_required = sum(
                1
                for item in items
                if isinstance(item, dict) and item.get("recovery_required") is True
            )
            receipt_summary: dict[str, Any] = {
                "observed": len(items),
                **{
                    key: statuses[key]
                    for key in (
                        "prepared",
                        "running",
                        "turn_returned",
                        "accepted",
                        "rejected",
                        "unavailable",
                    )
                    if statuses[key]
                },
            }
            if recovery_required:
                receipt_summary["recovery_required"] = recovery_required
            if inventory.get("has_more") is True:
                receipt_summary["has_more"] = True
            result["operation_receipts"] = receipt_summary
        return result
    except (OSError, ValueError, KeyError, TypeError, EffectRuntimeRemoteError):
        result = {
            "schema_version": "loopx_delegation_context_v0",
            "configuration_state": "blocked",
            "reason_code": "delegation_context_unavailable",
            "observed_at": observed_at,
            "authorized_count": 0,
            "projected_count": 0,
            "routes": [],
        }
        if include_operation_receipts:
            result["operation_receipts"] = {"observed": 0}
        return result


__all__ = ["project_delegation_context"]
