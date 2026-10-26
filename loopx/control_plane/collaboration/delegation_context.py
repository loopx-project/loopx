"""Host facts behind the typed delegation planning observation.

``subagent_context_ts.projectDelegationContext`` owns classification; this
module only reads host state and never launches or accepts work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...agent_registry import registered_agent_ids_for_goal
from ..effect_runtime import EffectRuntimeRemoteError
from ..operator_provider import operator_provider_environ
from ..projects.registry_codec import load_registry
from ..turn_driver.host_binding import managed_executor_binding_from_host_args
from .inbox import _read

MAX_PROBED_ROUTES = 6
MAX_OPERATION_RECEIPTS = 10


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


def read_delegation_facts(
    *,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    project: Path,
    execution_config: str,
    include_operation_inventory: bool = False,
) -> dict[str, Any]:
    """Return raw requester-scoped facts; ``config_error`` marks an unreadable host."""

    facts: dict[str, Any] = {"config": None, "config_error": None, "registered_agent_ids": None,
                             "executors": [], "operation_inventory": None}
    try:
        path = _configuration_path(project, execution_config)
        config = _read(path)
        try:
            # stat() raises ENOTDIR/ELOOP, which load_registry's Path.exists() hides.
            registry_path.expanduser().stat()
            goals = load_registry(registry_path).get("goals", [])
        except FileNotFoundError:
            goals = []  # No registry yet; zero requester rows stay observable.
        goal = next((row for row in goals if isinstance(row, dict) and row.get("id") == goal_id), None)
        facts["registered_agent_ids"] = None if goal is None else registered_agent_ids_for_goal(goal)
        # I/O budget only: TS projectDelegationContext owns requester/grant rules.
        rows = [row for row in (config.get("bindings") or [])
                if isinstance(row, dict) and isinstance(row.get("requesters"), list) and agent_id in row["requesters"]]
        executors: dict[tuple[str, ...], Any] = {}
        environ = operator_provider_environ()
        for host_args in (row.get("host_args") for row in rows[:MAX_PROBED_ROUTES]):
            if (isinstance(host_args, list) and all(isinstance(arg, str) for arg in host_args)
                    and tuple(host_args) not in executors):
                executors[tuple(host_args)] = managed_executor_binding_from_host_args(host_args, environ=environ)
        facts["executors"] = [{"host_args": list(key), "executor": value} for key, value in executors.items()]
        facts["config"] = config
        if include_operation_inventory:
            from ...collaboration_mcp import Delegations

            inventory = Delegations(runtime_root, registry_path, goal_id, agent_id, path).operations(
                limit=MAX_OPERATION_RECEIPTS)
            facts["operation_inventory"] = (
                {"items": inventory.get("items"), "has_more": inventory.get("has_more")}
                if isinstance(inventory, dict) else None)
    except (OSError, ValueError, KeyError, TypeError, EffectRuntimeRemoteError):
        facts["config_error"] = "unavailable"
    return facts


__all__ = ["read_delegation_facts"]
