from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..thread_agent_binding import (
    THREAD_BINDING_RESOLUTION_SCHEMA_VERSION,
    ThreadBindingRequestError,
    resolve_registry_thread_agent_binding,
)


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]

REGISTRY_THREAD_RESOLUTION_COMMANDS = {"resolve-agent-thread", "resolve-peer-route"}


def _render_thread_binding_resolution_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# LoopX Host Thread Binding",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- status: `{payload.get('status')}`",
        f"- host_surface: `{payload.get('host_surface')}`",
        f"- thread_id: `{payload.get('thread_id')}`",
    ]
    if payload.get("goal_id"):
        lines.append(f"- goal_id: `{payload.get('goal_id')}`")
    if payload.get("agent_id"):
        lines.append(f"- agent_id: `{payload.get('agent_id')}`")
    locator = payload.get("session_locator")
    if isinstance(locator, dict) and locator.get("authority"):
        lines.append(f"- locator_authority: `{locator.get('authority')}`")
    if payload.get("error_kind"):
        lines.append(f"- error_kind: `{payload.get('error_kind')}`")
    if payload.get("error"):
        lines.extend(["", str(payload["error"])])
    return "\n".join(lines)


def register_registry_thread_resolution_command(
    subparsers: argparse._SubParsersAction,
) -> None:
    parser = subparsers.add_parser(
        "resolve-agent-thread",
        help=(
            "Resolve one exact host thread or copied Codex task deep link across "
            "the current project registry."
        ),
    )
    reference = parser.add_mutually_exclusive_group(required=True)
    reference.add_argument(
        "--thread-id", help="Stable opaque host thread id."
    )
    reference.add_argument(
        "--thread-link",
        help="Canonical Codex task deep link copied from the task menu.",
    )
    parser.add_argument(
        "--host-surface",
        help=(
            "Exact host surface token. Required with --thread-id; optional with "
            "--thread-link to narrow project-local Codex-family lookup."
        ),
    )
    peer = subparsers.add_parser(
        "resolve-peer-route",
        help="Preview the exact observed host task for an existing registered peer.",
    )
    peer.add_argument("--goal-id", required=True)
    peer.add_argument("--agent-id", required=True)
    peer.add_argument(
        "--thread-link", help="Explicit Codex task link when several bindings exist."
    )
    peer.add_argument(
        "--host-surface", help="Narrow an explicit link to one host surface."
    )


def _render_peer_route_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# LoopX Peer Host Route",
        "",
        f"- status: `{payload.get('status')}`",
        f"- reason: `{payload.get('reason')}`",
        f"- candidate_count: `{payload.get('candidate_count')}`",
        f"- host_delivery: `{payload.get('host_delivery')}`",
    ]
    selected = payload.get("selected_route")
    if isinstance(selected, dict):
        lines.append(f"- selected_host_surface: `{selected.get('host_surface')}`")
        lines.append(f"- selected_thread_id: `{selected.get('thread_id')}`")
    return "\n".join(lines)


def handle_registry_thread_resolution_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    print_payload: PrintPayload,
) -> int | None:
    if args.command not in REGISTRY_THREAD_RESOLUTION_COMMANDS:
        return None
    if args.command == "resolve-peer-route":
        from ..control_plane.collaboration.peer_host_route import (
            resolve_peer_host_route,
        )

        try:
            payload = resolve_peer_host_route(
                registry_path,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                thread_link=args.thread_link,
                host_surface=args.host_surface,
            )
        except (OSError, ValueError):
            payload = {
                "ok": False,
                "status": "unavailable",
                "reason": "registry_unavailable",
                "selected_route": None,
                "host_delivery": "not_attempted",
            }
        print_payload(payload, args.format, _render_peer_route_markdown)
        return 0 if payload.get("ok") else 1
    host_surface = args.host_surface
    try:
        payload = resolve_registry_thread_agent_binding(
            registry_path=registry_path,
            host_surface=host_surface,
            thread_id=args.thread_id,
            thread_link=args.thread_link,
        )
    except Exception as exc:
        invalid_request = isinstance(exc, ThreadBindingRequestError)
        payload = {
            "ok": False,
            "schema_version": THREAD_BINDING_RESOLUTION_SCHEMA_VERSION,
            "host_surface": None if invalid_request else host_surface,
            "thread_id": None if invalid_request else args.thread_id,
            "session_locator": None,
            "status": "unavailable",
            "goal_id": None,
            "agent_id": None,
            "matches": [],
            "error_kind": (
                "thread_agent_binding_invalid_request"
                if invalid_request
                else "thread_agent_binding_resolution_failed"
            ),
            "error": (
                "thread binding request is invalid"
                if invalid_request
                else "thread binding authority could not be read"
            ),
        }
    print_payload(payload, args.format, _render_thread_binding_resolution_markdown)
    return 0 if payload.get("ok") else 1
