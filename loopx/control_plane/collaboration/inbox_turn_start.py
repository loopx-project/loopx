"""Turn-start observation of an Agent's durable Inbox."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ...command_invocation import loopx_cli_argv
from ...install_contract import render_host_cli_command
from .goal_instance_scope import collaboration_goal_scope

if TYPE_CHECKING:
    from ..capability_hooks import TurnStartHookRegistration


def turn_start_hook(
    runtime_root: Path, registry_path: Path, goal_id: str, agent_id: str, *, model_read: bool = False
) -> TurnStartHookRegistration:
    """Observe scoped pending work using the original Inbox operation and route."""
    from ..capability_hooks import TURN_START_HOOK_RESULT_SCHEMA_VERSION, TurnStartHookRegistration

    snapshot = None

    def produce():
        nonlocal snapshot
        try:
            from .inbox import _pending_result, read_inbox_transaction

            with collaboration_goal_scope(
                registry_path, goal_id=goal_id, agents=(agent_id,),
            ) as goal_scope:
                observed = read_inbox_transaction(runtime_root, {
                    "operation": "turn_start", "goal_id": goal_id, "agent_id": agent_id,
                    "read_command": command,
                }, scope=goal_scope)
                observed.update(goal_id=goal_id, agent_id=agent_id)
                inbox = _pending_result(runtime_root, observed, goal_scope, None)
            snapshot = observed["context"]
            agenda = snapshot["unfinished_delegations"]
            count = (
                max(len(inbox["items"]), agenda["incoming_count"])
                + max(len(inbox.get("peer_returns", {}).get("items", [])), agenda["outgoing_count"])
                + len(inbox.get("native_child_results", {}).get("items", []))
                + int(inbox.get("native_child_results", {}).get("unavailable_count", 0))
                + int(inbox.get("native_child_results", {}).get("scan_complete") is False)
                + len(inbox.get("operation_handoffs", []))
            )
            incomplete = not agenda["scan_complete"]
            count += int(incomplete)
            status, error = ("observed" if count else "empty"), None
        except (OSError, ValueError):
            count, status, error = 0, "unavailable", "manager_context_unreadable"
        return {
            "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": "manager.context_inbox",
            "capability_id": "manager-context",
            "phase": "turn_start",
            "status": status,
            "observation_count": count,
            "agent_read_required": count > 0,
            "external_reads_performed": False,
            "external_writes_performed": False,
            "local_private_state_mutated": False,
            "private_content_returned": False,
            "provider_payload_returned": False,
            "error_code": error,
        }

    command = render_host_cli_command(
        [
            *loopx_cli_argv(),
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime_root),
            "manager-inbox",
            "read",
            *(["--format", "markdown"] if model_read else []),
            "--goal-id",
            goal_id,
            "--agent-id",
            agent_id,
        ]
    )
    return TurnStartHookRegistration(
        hook_id="manager.context_inbox",
        capability_id="manager-context",
        requested_read_scope=("owner_private_context_inbox",),
        requested_write_scope=(),
        producer=produce,
        context_reader=lambda: snapshot,
        required_read={
            "kind": "operator_inbox",
            "command": command,
            "reason": "Review owner context and decide whether the current plan should change; no priority is imposed.",
            "ordering": "before_work",
        },
    )
