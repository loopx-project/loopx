"""Read the Agent-facing peer directory of one Goal (read-only).

`peer_agent_directory_v0` is the contract for one Agent discovering, observing
and delivering to another. This command is its local producer: the steward
channel and a peer Agent inside the Goal call the same surface, and the packet
they get names its own limits instead of implying presence it cannot see.
"""

from __future__ import annotations

from pathlib import Path

from ..agent_registry import load_goal_from_registry
from ..control_plane.agents.directory import build_peer_agent_directory
from ..control_plane.effect_runtime import EffectRuntimeRemoteError
from ..status import AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK, collect_status


def register_agent_directory(subparsers, add_format):
    parser = subparsers.add_parser(
        "agent-directory",
        help="Read the peer agent directory of one Goal (read-only).",
    )
    add_format(parser)
    parser.add_argument("--goal-id", required=True)
    parser.add_argument(
        "--agent-id",
        default=None,
        help=(
            "The calling Agent. Supplying it proves membership against the Goal "
            "registry; an unregistered caller receives a scope gap instead of rows."
        ),
    )
    parser.add_argument(
        "--scan-path",
        action="append",
        default=[],
        help="Project root to scan for Goal state; defaults to the current directory.",
    )
    parser.add_argument(
        "--cursor",
        default=None,
        help=(
            "Continue a previous scan after this Agent id (the previous "
            "packet's next_cursor); rows follow agent-id order. Omit to start "
            "a fresh scan of all registered Agents."
        ),
    )


def handle_agent_directory(args, registry_path, runtime_root, print_payload, output_format):
    goal = load_goal_from_registry(registry_path, args.goal_id)
    if not goal:
        payload = {
            "ok": False,
            "schema_version": "peer_agent_directory_v0",
            "error_code": "goal_not_registered",
            "goal_id": args.goal_id,
            "reason": (
                "the registry does not know this Goal, so there is no Agent "
                "identity to report and no scope to read"
            ),
        }
        print_payload(payload, output_format(args), render_agent_directory)
        return 1
    try:
        scan_roots = [Path(item).expanduser() for item in args.scan_path] or [Path.cwd()]
        status_payload = collect_status(
            registry_path=registry_path,
            runtime_root_override=str(runtime_root) if runtime_root else args.runtime_root,
            scan_roots=scan_roots,
            limit=AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK,
            goal_id=args.goal_id,
        )
        payload = build_peer_agent_directory(
            status_payload,
            goal_id=args.goal_id,
            caller_agent_id=args.agent_id,
            cursor=args.cursor,
        )
    except (ValueError, TypeError, OSError, KeyError, EffectRuntimeRemoteError) as exc:
        payload = {
            "ok": False,
            "schema_version": "peer_agent_directory_v0",
            "error_code": "agent_directory_collection_failed",
            "goal_id": args.goal_id,
            "reason": str(exc),
        }
    print_payload(payload, output_format(args), render_agent_directory)
    return 0 if payload.get("ok") else 1


def render_agent_directory(payload):
    if not payload.get("ok"):
        return f"Peer agent directory unavailable: {payload.get('error_code')} — {payload.get('reason')}"
    row_count = payload.get("row_count") or 0
    omitted = payload.get("omitted_row_count") or 0
    registered = payload.get("registered_agent_count") or 0
    diagnostic = payload.get("diagnostic_agent_count") or 0
    lines = [
        f"Peer agent directory: {payload.get('goal_id')} "
        f"({row_count} of {row_count + omitted} directory rows, "
        f"{registered} registered Agents)"
    ]
    if diagnostic:
        lines.append(
            f"non-member rows: {diagnostic} Agent(s) the Goal does not register, "
            "carried for their work evidence"
        )
    if payload.get("cursor"):
        lines.append(f"page: rows after {payload['cursor']} (agent id order, exclusive)")
    if payload.get("next_cursor"):
        lines.append(
            f"continuation: rerun with --cursor {payload['next_cursor']} "
            "for the remaining rows"
        )
    scope = payload.get("scope") or {}
    lines.append(
        f"caller: {scope.get('caller_agent_id') or '(not supplied)'} "
        f"({scope.get('caller_membership')})"
    )
    for gap in scope.get("gaps") or []:
        lines.append(f"scope gap: {gap.get('kind')} — {gap.get('detail')}")
    for row in payload.get("rows") or []:
        work = row.get("work") or {}
        membership = "" if row.get("registered") else " [not a registered member]"
        lines.append(
            f"- {row.get('agent_id')} [{row.get('agent_model') or 'unknown'}] "
            f"{work.get('todo_id') or 'no projected work'} "
            f"{work.get('todo_status') or ''} {work.get('priority') or ''} "
            f"claim_age={work.get('claim_age_state') or 'n/a'}{membership}"
        )
    rollup = payload.get("rollup")
    if rollup:
        lines.append(
            "rollup (typed attention only; assigns nothing): "
            f"needs_decision={rollup['counts']['needs_decision']} "
            f"stale_claims={rollup['counts']['stale_claims']} "
            f"without_claim={rollup['counts']['without_claim']}"
        )
    presence = payload.get("presence_coverage") or {}
    lines.append(f"presence: {presence.get('state')} — {presence.get('note')}")
    lines.append("limitations: " + ", ".join(payload.get("limitations") or []))
    lines.append(
        "Observation and delivery grant no claim, lease, priority or work edit."
    )
    return "\n".join(lines)
