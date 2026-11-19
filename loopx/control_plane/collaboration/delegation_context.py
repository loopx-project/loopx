"""Read-only delegation facts and original work for the shared Turn context.

``subagent_context_ts.projectDelegationContext`` owns classification; this
module reads existing owners and never launches or accepts work.
"""

from __future__ import annotations

import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
import shlex
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


def delegated_turn_context_arguments(service, row: dict) -> list[str]:
    """Fresh input only; historical operations keep their recorded profile."""
    if "work_context_required" not in row:
        return []
    if row["work_context_required"] is not True:
        raise ValueError("unsupported delegation work-context profile")
    return ["--delegation-context-json", json.dumps({
        "requester_agent_id": service.agent_id, "execution_config": str(service.config),
        "operation_id": row["identity"]["operation_id"]})]


def delegated_turn_context(
    locator_json: str, *, registry_path: Path, runtime_root: Path,
    goal_id: str, agent_id: str, todo_id: str | None, turn_instance_id: str | None,
    workspace: Path, preview: bool = False,
) -> dict[str, Any]:
    """Read original work through its existing operation, never a model brief.

    The locator is private Host metadata. Current binding, source grants and
    original-request checks stay with Delegations and its TypeScript owners.
    The generic work-context owner carries the returned material into capture.
    """
    from ...collaboration_mcp import Delegations
    from .goal_instance_scope import collaboration_goal_scope, decide_collaboration_lifecycle
    from .inbox import _entry, _root
    from .peers import _operation_address
    from .source_chat_observation import observe_source_chat
    from .source_grant_observation import external_source_policy
    from ..effect_runtime import effect_runtime_result

    locator = json.loads(locator_json)
    preview_fields = {"requester_agent_id", "execution_config", "source_request_id", "binding_id"}
    if isinstance(locator, dict) and set(locator) == preview_fields:
        if not preview or any(not isinstance(locator[key], str) or not locator[key] for key in preview_fields):
            raise ValueError("a source request preview cannot execute or resume a Turn")
        from .source_grant_observation import require_delegation_source_execution
        service = Delegations(runtime_root, registry_path, goal_id, locator["requester_agent_id"],
                              Path(locator["execution_config"]))
        binding = service.binding(locator["binding_id"], require_active=True)
        require_delegation_source_execution(runtime_root, registry_path, goal_id=goal_id,
            requester_agent_id=service.agent_id, source_request_id=locator["source_request_id"],
            binding=binding, caller_goal_ref=service._caller_goal_ref())
        if workspace.resolve() != Path(binding["workspace"]).resolve():
            raise ValueError("request preview workspace changed")
        with collaboration_goal_scope(registry_path, goal_id=goal_id, agents=(service.agent_id, agent_id),
                caller_goal_ref=service._caller_goal_ref(), require_active=True) as scope:
            source_id = locator["source_request_id"]
            source = _entry(runtime_root, goal_id, agent_id, source_id, scope=scope)
            decide_collaboration_lifecycle(scope, operation="history_inspect", record=source)
            route = _read(_root(runtime_root) / "roundtrips" / (source_id + ".json"))
            session, turn = observe_source_chat(runtime_root, route)
            ingress, _ = external_source_policy(runtime_root, session, turn)
            content = effect_runtime_result("collaboration.delegation.turn_context", {
                "config": _read(service.config), "binding_id": binding["id"], "agent_id": service.agent_id,
                "record": None, "source_request": source, "original_message": ingress["source_message"],
                "turn": {"goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id,
                    "turn_instance_id": turn_instance_id, "workspace": binding["workspace"]}})
        return _context_delivery(content, service=service, request_id=source_id)
    fields = {"requester_agent_id", "execution_config", "operation_id"}
    if not isinstance(locator, dict) or set(locator) != fields or any(
        not isinstance(locator[key], str) or not locator[key] for key in fields
    ):
        raise ValueError("invalid delegation context locator")
    service = Delegations(runtime_root, registry_path, goal_id, locator["requester_agent_id"],
                          Path(locator["execution_config"]))
    operation = locator["operation_id"]
    row = _read(service.path(operation))
    binding = service._bound(row, require_active=True)
    if workspace.resolve() != Path(binding["workspace"]).resolve():
        raise ValueError("delegation context workspace changed")
    with collaboration_goal_scope(registry_path, goal_id=goal_id, agents=(service.agent_id, agent_id),
            caller_goal_ref=service._caller_goal_ref(), require_active=True) as scope:
        peer = _entry(runtime_root, goal_id, agent_id, row["identity"]["request_id"], scope=scope)
        _, operation_path = _operation_address(runtime_root, scope, service.agent_id, operation)
        source = None
        original_message = None
        source_id = row["identity"].get("source_request_id")
        if source_id is not None:
            source = _entry(runtime_root, goal_id, agent_id, source_id, scope=scope)
            route = _read(_root(runtime_root) / "roundtrips" / (source_id + ".json"))
            session, turn = observe_source_chat(runtime_root, route)
            ingress, _ = external_source_policy(runtime_root, session, turn)
            original_message = ingress["source_message"]
        for entry in (peer, source):
            if entry is not None:
                decide_collaboration_lifecycle(scope, operation="history_inspect", record=entry)
        content = effect_runtime_result("collaboration.delegation.turn_context", {
            "config": _read(service.config), "binding_id": binding["id"],
            "agent_id": service.agent_id, "record": row, "operation_id": operation,
            "stop": service._read_stop(service.path(operation)),
            "turn_instance_id": service._turn_instance_id(row),
            "turn": {"goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id,
                     "turn_instance_id": turn_instance_id, "workspace": binding["workspace"]},
            "peer_request": peer, "peer_operation": _read(operation_path),
            "source_request": source, "original_message": original_message,
        })
    service._bound(row, require_active=True)
    return _context_delivery(content, service=service, request_id=source_id or peer["request_id"])


def _request_operation(locator: dict, identity, *, registry_path: Path, runtime_root: Path, workspace: Path):
    from ...collaboration_mcp import Delegations

    if set(locator) != {"requester_agent_id", "execution_config", "operation_id"}:
        raise ValueError("request execution requires its original delegation operation")
    service = Delegations(runtime_root, registry_path, identity.goal_id, locator["requester_agent_id"],
                          Path(locator["execution_config"]))
    path = service.path(locator["operation_id"])
    row = _read(path)
    if (row["identity"].get("request_subject") != identity.request_subject
            or service._turn_instance_id(row) != identity.turn_instance_id
            or row["identity"]["binding"]["agent_id"] != identity.agent_id
            or Path(row["identity"]["binding"]["workspace"]).resolve() != workspace.resolve()):
        raise ValueError("request execution does not match the original Turn")
    return service, path, row


def require_current_request_operation(locator: dict, identity, **context) -> None:
    service, path, row = _request_operation(locator, identity, **context)
    service._raise_if_stop_requested(path)
    service._bound(row, require_active=True)


@contextmanager
def request_settlement_lifetime(locator: dict, identity, **context):
    """Reuse original operation/Stop and source-policy fences around settlement.

    No new permission record or request reservation is created. Recovery uses
    this original locator and captured identity, without replacing model input.
    """
    from ...file_lock import exclusive_file_lock
    from .inbox import _root
    from .source_policy_fence import source_policy_fence

    service, path, row = _request_operation(locator, identity, **context)
    source_id = row["identity"].get("source_request_id")
    with exclusive_file_lock(service._dispatch_lock(path)), (source_policy_fence(_root(service.root) / "policy.json")
            if source_id else nullcontext()):
        service._raise_if_stop_requested(path)
        binding = service._bound(row, require_active=True, policy_locked=source_id is not None)
        service._validate(binding)
        yield


def _context_delivery(content: dict[str, Any], *, service, request_id: str) -> dict[str, Any]:
    from ...command_invocation import loopx_cli_argv

    command = shlex.join([*loopx_cli_argv(), "--registry", str(service.registry),
        "--runtime-root", str(service.root), "manager-inbox", "read", "--goal-id", service.goal_id,
        "--agent-id", content["agent_id"], "--request-id", request_id])
    return {**({"request_subject": content["request_subject"]} if "request_subject" in content else {}),
            "required_reads": [{"command": command, "source": "delegation_work",
            "ordering": "before_work", "reason": "Read the original delegated work and source message; material grants no additional authority."}],
            "contexts": [{"command": command, "content": content}]}


__all__ = ["read_delegation_facts", "delegated_turn_context", "delegated_turn_context_arguments"]
