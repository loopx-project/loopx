from __future__ import annotations

import argparse


def render_register_agent_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# LoopX Agent Registration",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- global_registry: `{payload.get('global_registry')}`",
        f"- source_registry: `{payload.get('source_registry')}`",
        f"- changed: `{payload.get('changed')}`",
        f"- written: `{payload.get('written')}`",
    ]
    if payload.get("error_kind"):
        lines.append(f"- error_kind: `{payload.get('error_kind')}`")
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
    lines.append(f"- existing_agents: `{', '.join(payload.get('existing_agents') or [])}`")
    lines.append(f"- registered_agents: `{', '.join(payload.get('registered_agents') or [])}`")
    readback = payload.get("registration_readback")
    if isinstance(readback, dict) and readback.get("performed"):
        lines.append(f"- registration_readback_verified: `{readback.get('verified')}`")
    sync_payload = payload.get("global_sync")
    if isinstance(sync_payload, dict):
        lines.append(f"- global_sync_wrote: `{sync_payload.get('wrote')}`")
        if sync_payload.get("write_denied"):
            lines.append(f"- global_sync_error_kind: `{sync_payload.get('error_kind')}`")
    if payload.get("recommended_action"):
        lines.append(f"- recommended_action: {payload.get('recommended_action')}")
    activation = payload.get("host_loop_activation")
    if isinstance(activation, dict):
        lines.append(
            f"- host_loop_activation: `{activation.get('host_surface')}` "
            f"status=`{activation.get('status')}` activated=`{activation.get('activated')}`"
        )
        if activation.get("activated") is not True:
            lines.append(f"- host_loop_action: {activation.get('recommended_action')}")
    return "\n".join(lines)


def register_peer_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent-model",
        choices=("peer_v1",),
        help=(
            "Agent runtime model. peer_v1 removes identity rank and routes work through "
            "claims, leases, deterministic task assignment, and task-scoped coordination."
        ),
    )
    parser.add_argument(
        "--ack-automation-prompt-migration",
        metavar="MIGRATION_ID",
        help=(
            "Acknowledge that the installed host automation was updated for this stable "
            "migration id, then atomically remove legacy hierarchy fields. Repeating the "
            "same completed id is a no-op."
        ),
    )


def register_peer_supervisor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--supervisor-agent",
        help=(
            "Opt in one registered peer as a proposal-only supervisor. The peer keeps "
            "equal identity authority and gains no implicit session-control rights."
        ),
    )
    parser.add_argument(
        "--supervised-agent",
        dest="supervised_agents",
        action="append",
        default=None,
        help=(
            "Registered peer observed by the supervisor. Repeatable; defaults to every "
            "registered peer except the supervisor."
        ),
    )
    parser.add_argument(
        "--clear-supervisor",
        action="store_true",
        help="Remove the optional coordination.supervisor configuration.",
    )
