"""Callback IO into the existing operator-granted delegation/Turn owner.

The callback confirms terms; configuration separately grants this one launch.
No new queue, scheduler, approval, result store or public identity issuer.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...chat_action_store import ChatActionStore
from ..turn_driver.codex_cli import load_codex_cli_session
from ..turn_driver.host_binding import turn_host_arg_option
from .operation_handoff import managed_operation_binding_current
from .goal_instance_scope import collaboration_goal_scope, decide_collaboration_lifecycle


def require_current_operation_scope(registry_path: Path, parameters: Mapping[str, Any]) -> None:
    """Reuse the Goal lifetime owner before either queueing or native resume."""
    with collaboration_goal_scope(registry_path, goal_id=parameters["goal_id"],
                                  agents=(parameters["agent_id"],), require_active=True) as scope:
        decision = decide_collaboration_lifecycle(
            scope, operation="inbox_observe", record={"goal_ref": parameters.get("origin_goal_ref")}
        )
        if decision.get("kind") == "omit":
            raise ValueError("operation belongs to a different Goal lifetime")


def dispatch_confirmed_operation_wake(
    proposal: Mapping[str, Any], *, runtime_root: Path,
    configuration: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Request one durable delegation; accepted native start is separate evidence.

    Read/compare failures remain a pending canonical authorization. Never resume
    an uncertain launch from a callback replay or include private argv/errors.
    """
    base = {"execution_allowed": False, "external_write_performed": False,
            "native_start_verified": False}
    if configuration is None:
        return {**base, "state": "not_configured"}
    try:
        from ...collaboration_mcp import Delegations
        from .delegation_context import _configuration_path

        parameters = proposal["normalized_parameters"]
        if parameters["goal_id"] != configuration["goal_id"]:
            return {**base, "state": "out_of_scope"}
        service = Delegations(
            runtime_root, Path(configuration["registry_path"]),
            configuration["goal_id"], configuration["requester_agent_id"],
            _configuration_path(Path(configuration["project"]), configuration["execution_config"]),
        )
        binding = service.binding(configuration["binding_id"], require_active=True)
        require_current_operation_scope(service.registry, parameters)
        if service.config.is_relative_to(Path(binding["workspace"]).resolve()):
            raise ValueError("operation wake configuration must be outside the worker workspace")
        operation_id = "operation-wake-" + hashlib.sha256(
            str(proposal["proposal_id"]).encode()
        ).hexdigest()
        if service.path(operation_id).is_file():
            # Start's lost ACK is resolved by readback, never another spawn.
            # Do not run artifact validators on the callback's latency path or
            # describe a stored accepted bit as current acceptance. CLI read
            # owns result qualification/recovery after this bounded locator.
            from .inbox import _read

            row = _read(service.path(operation_id))
            service._bound(row, require_active=True)
            if row["identity"].get("confirmed_operation_id") != proposal["proposal_id"]:
                raise ValueError("operation wake identity drifted")
            return {**base, "state": "existing_delegation", "operation_id": operation_id}
        projection = ChatActionStore._agent_operation_plan(proposal, action="project")
        if projection["status"] != "authorized_pending" or projection["host_start"] is not None:
            return {**base, "state": "no_new_launch"}
        session = load_codex_cli_session(runtime_root, lineage={
            "goal_id": service.goal_id, "agent_id": binding["agent_id"],
            "todo_id": binding["todo_id"],
        }) or {}
        argv = binding["host_args"]
        ChatActionStore._agent_operation_plan(
            proposal, action="wake",
            binding_current=managed_operation_binding_current(runtime_root, parameters),
            launch_context={"host": turn_host_arg_option(argv, "--host"),
                            "operation_tools": "--codex-operation-tools" in argv,
                            "iteration_context": turn_host_arg_option(argv, "--iteration-context")},
            executor_route={"goal_id": service.goal_id, "agent_id": binding["agent_id"],
                            "todo_id": binding["todo_id"], "host_surface": "loopx-managed-codex",
                            "thread_id": session.get("session_id"),
                            "profile_digest": session.get("operation_profile_digest"),
                            "model": turn_host_arg_option(argv, "--codex-model"),
                            "reasoning_effort": turn_host_arg_option(argv, "--codex-reasoning-effort")},
        )
        brief = {
            "schema_version": "collaboration_brief_v0",
            "purpose": "Resume the exact human-confirmed canonical operation",
            "context": f"Canonical operation locator: {proposal['proposal_id']}. Read through loopx_operation in the original managed session.",
            "constraints": ["Inspect current canonical terms and consume once before any effect.",
                            "Consumed or unknown results require reconciliation, never resubmission.",
                            "The source conversation is not executor authentication."],
            "inputs": [], "acceptance": ["Preserve the pinned Todo validation and original evidence."],
            "return_requirement": "Report the exact operation outcome through loopx_operation; startup/adoption is not a domain result.",
        }
        result = service.start(configuration["binding_id"], operation_id, brief,
                               confirmed_operation_id=proposal["proposal_id"])
        return {**base, "state": "delegation_requested", "operation_id": operation_id,
                "delegation_status": result["status"],
                "recovery_required": result["recovery_required"]}
    except Exception:  # authorization remains recorded even if launch/readback fails
        return {**base, "state": "blocked", "reason_code": "operation_wake_admission_or_dispatch_failed"}
