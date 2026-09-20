"""Shell access to the same bound work used by the collaboration MCP tools.

An existing attached Agent can use its current shell without replacing its
conversation or installing tools into an already running host session.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..control_plane.effect_runtime import EffectRuntimeRemoteError


def register_delegation(subparsers, add_format):
    parser = subparsers.add_parser(
        "delegation", help="Launch and recover authorized peer work; returns JSON."
    )
    add_format(parser)
    parser.add_argument("delegation_action", choices=("list", "operations", "inspect", "start", "read", "wait", "resume", "adopt"))
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True, help="Calling registered Agent, not the worker.")
    parser.add_argument("--execution-config", type=Path, required=True,
                        help="Existing operator-owned local delegation bindings.")
    parser.add_argument("--operation-id", help="Stable request identity; reuse after a lost response.")
    parser.add_argument("--consumer-operation-id", help="For adopt: accepted downstream execution with a version-bound uses input.")
    parser.add_argument("--binding-id", help="For start/inspect: an authorized binding from list.")
    parser.add_argument("--brief-file", type=Path, help="For start: collaboration_brief_v0 JSON file.")
    parser.add_argument("--parent-request-id", help="For start: the request received by this coordinator.")
    parser.add_argument("--limit", type=int, help="For operations: page size, 1–50 (default 20).")
    parser.add_argument("--cursor", help="For operations: next_cursor returned by the previous page.")
    parser.add_argument("--execute", action="store_true", help="Required for start/resume/adopt; grants no additional authority.")


def handle_delegation(args, registry_path, runtime_root):
    # The shared host is importable without the optional MCP server dependency.
    from ..collaboration_mcp import Delegations

    action = args.delegation_action
    try:
        if action in {"start", "resume", "adopt"} and not args.execute:
            raise ValueError(f"delegation {action} requires --execute")
        if action not in {"start", "resume", "adopt"} and args.execute:
            raise ValueError("--execute is only valid for start/resume/adopt")
        if action not in {"list", "operations", "inspect"} and not args.operation_id:
            raise ValueError(f"delegation {action} requires --operation-id")
        if action in {"list", "operations", "inspect"} and args.operation_id:
            raise ValueError(f"{action} does not select an operation; use read")
        if action != "operations" and (args.limit is not None or args.cursor is not None):
            raise ValueError("limit and cursor are only supplied on operations")
        if action not in {"start", "inspect"} and args.binding_id:
            raise ValueError("binding is only supplied on start/inspect")
        if action != "start" and (args.brief_file or args.parent_request_id):
            raise ValueError("brief and parent request are only supplied on start")
        if (action == "adopt") != bool(args.consumer_operation_id):
            raise ValueError("--consumer-operation-id is required only for adopt")
        service = Delegations(runtime_root, registry_path, args.goal_id, args.agent_id,
                              args.execution_config.expanduser())
        if action == "start":
            if not args.binding_id or not args.brief_file:
                raise ValueError("start requires --binding-id and --brief-file")
            with args.brief_file.expanduser().open("rb") as stream:
                raw = stream.read(128_001)
            if len(raw) > 128_000:
                raise ValueError("delegation brief file exceeds 128000 bytes")
            result = service.start(args.binding_id, args.operation_id, json.loads(raw),
                                   args.parent_request_id)
        elif action == "list":
            result = service.directory()
        elif action == "inspect":
            if not args.binding_id:
                raise ValueError("inspect requires --binding-id")
            result = service.inspect(args.binding_id)
        elif action == "operations":
            result = service.operations(limit=20 if args.limit is None else args.limit, cursor=args.cursor)
        elif action == "adopt":
            result = service.adopt_result(args.operation_id, args.consumer_operation_id)
        elif action == "read":
            result = service.read(args.operation_id)
        elif action == "wait":
            result = service.wait(args.operation_id)
        else:
            result = service.resume(args.operation_id)
        payload = {"ok": True, **result}
    except (OSError, ValueError, KeyError, EffectRuntimeRemoteError) as exc:
        payload = {"ok": False, "error": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1
