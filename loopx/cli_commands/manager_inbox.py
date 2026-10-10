"""Private context consumption; never creates or reprioritizes Todos."""

import json
from pathlib import Path
from ..agent_registry import registered_agent_ids_for_goal
from ..capabilities.manager_context import (
    acknowledge,
    configure_delivery_target,
    configure_delivery_scope,
    configure_evidence_scope,
)
from ..control_plane.projects.registry_codec import load_project_registry
from ..presentation.answer_instruction import collaboration_answer_instruction


def register_manager_inbox(subparsers, add_format):
    parser = subparsers.add_parser(
        "manager-inbox",
        help="Read context handed to an Agent and record its replan decision.",
    )
    add_format(parser)
    parser.add_argument(
        "manager_inbox_action",
        choices=(
            "read",
            "request",
            "acknowledge-return",
            "acknowledge",
            "link",
            "report",
            "status",
            "configure-read-scope",
            "configure-delivery-scope",
            "configure-ssh-read-scope",
            "grant-delivery-target",
            "revoke-delivery-target",
        ),
    )
    parser.add_argument("--peer-agent-id", help="For request: a registered peer of the same Goal.")
    parser.add_argument(
        "--require-host-route", action="store_true",
        help="For request: require an observed exact host task before recording a peer request.",
    )
    parser.add_argument(
        "--peer-thread-link",
        help="For request: select the peer's canonical Codex task link when bindings are ambiguous.",
    )
    parser.add_argument(
        "--peer-host-surface",
        help="For request: narrow a task link to one bound host surface.",
    )
    parser.add_argument("--operation-id", help="For request: stable retry identity; use a new id for another review round.")
    parser.add_argument("--brief-file", help="For request: collaboration_brief_v0 JSON file.")
    parser.add_argument("--parent-request-id", help="For request: an inbox request received by the sender.")
    parser.add_argument("--goal-id")
    parser.add_argument("--agent-id", help="Agent identity; for delivery grants, omit to cover all current/future Agents in --goal-id.")
    parser.add_argument("--channel-id")
    parser.add_argument("--ssh-host")
    parser.add_argument("--read-goal-id", action="append", default=[])
    parser.add_argument("--local-delivery-scope", choices=("all_registered", "selected"))
    parser.add_argument("--sender-id", help="For configuring a new delivery source: its independently verified sender.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--request-id")
    parser.add_argument(
        "--phase", choices=("decision", "conclusion"), default="conclusion"
    )
    parser.add_argument("--reply-text")
    parser.add_argument("--attachment-ref", action="append", default=[],
                        help="For report: explicit relative file in the registered Goal workspace; bound-owner App only, at most four files.")
    parser.add_argument("--update-id", help="For report: stable identity for a later conclusion; retry the same id and text.")
    parser.add_argument("--result-key", help="For acknowledge-return: the exact peer result read; defaults to the initial conclusion.")
    parser.add_argument("--related-todo-id", action="append", default=[])
    parser.add_argument("--evidence-id", action="append", default=[])
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--cursor", help="For read: continue with the previous page's next_cursor.")
    parser.add_argument("--operation-cursor", help="For read: continue the independent original-operation page.")
    parser.add_argument("--decision", choices=("adopt", "defer", "reject", "no_change"))
    parser.add_argument("--reason")


def handle_manager_inbox(args, registry_path, runtime_root):
    try:
        local_delivery_scope = getattr(args, "local_delivery_scope", None)
        sender_id = getattr(args, "sender_id", None)
        if args.manager_inbox_action != "configure-delivery-scope" and (local_delivery_scope is not None or sender_id is not None):
            raise ValueError("--local-delivery-scope and --sender-id are only supported for configure-delivery-scope")
        if args.manager_inbox_action == "configure-delivery-scope":
            result = configure_delivery_scope(runtime_root, channel=args.channel_id or "",
                local_delivery_scope=local_delivery_scope or "", sender_id=sender_id,
                execute=args.execute)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if getattr(args, "update_id", None) is not None and args.manager_inbox_action != "report":
            raise ValueError("--update-id is only supported for report")
        if getattr(args, "attachment_ref", None) and args.manager_inbox_action != "report":
            raise ValueError("--attachment-ref is only supported for report")
        if getattr(args, "result_key", None) is not None and args.manager_inbox_action != "acknowledge-return":
            raise ValueError("--result-key is only supported for acknowledge-return")
        cursor = getattr(args, "cursor", None)
        operation_cursor = getattr(args, "operation_cursor", None)
        if cursor is not None and args.manager_inbox_action != "read":
            raise ValueError("--cursor is only supported for read")
        if operation_cursor is not None and args.manager_inbox_action != "read":
            raise ValueError("--operation-cursor is only supported for read")
        if args.manager_inbox_action == "configure-ssh-read-scope":
            from ..capabilities.manager_context.ssh_evidence import configure
            result = configure(runtime_root, channel=args.channel_id or "", host=args.ssh_host,
                               goal_ids=args.read_goal_id, execute=args.execute)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.manager_inbox_action == "configure-read-scope":
            result = configure_evidence_scope(
                runtime_root,
                registry_path,
                channel=args.channel_id or "",
                goal_ids=args.read_goal_id,
                execute=args.execute,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.manager_inbox_action in {"grant-delivery-target", "revoke-delivery-target"}:
            result = configure_delivery_target(
                runtime_root,
                registry_path,
                channel=args.channel_id or "",
                goal_id=args.goal_id or "",
                agent_id=args.agent_id,
                grant=args.manager_inbox_action == "grant-delivery-target",
                execute=args.execute,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        registry = load_project_registry(registry_path)
        goal = next(
            (g for g in registry.get("goals", []) if g.get("id") == args.goal_id), None
        )
        if not goal or args.agent_id not in registered_agent_ids_for_goal(goal):
            raise ValueError("recipient is not registered")
        if args.manager_inbox_action == "request":
            from ..control_plane.collaboration.peers import request
            from ..control_plane.collaboration.peer_host_route import resolve_peer_host_route
            if not args.brief_file:
                raise ValueError("--brief-file is required for a peer request")
            if (args.peer_thread_link or args.peer_host_surface) and not args.require_host_route:
                raise ValueError("peer host selection requires --require-host-route")
            route = None
            if args.require_host_route:
                route = resolve_peer_host_route(
                    registry_path,
                    goal_id=args.goal_id,
                    agent_id=args.peer_agent_id,
                    thread_link=args.peer_thread_link,
                    host_surface=args.peer_host_surface,
                )
                if route["status"] != "resolved":
                    raise ValueError(
                        f"peer host route {route['status']}: {route['reason']}; "
                        "no request was recorded"
                    )
            with Path(args.brief_file).open("rb") as stream:
                raw = stream.read(128_001)
            if len(raw) > 128_000:
                raise ValueError("peer brief file is too large")
            result = request(runtime_root, registry_path, args.goal_id, args.agent_id,
                             args.peer_agent_id, args.operation_id, json.loads(raw), args.parent_request_id,
                             host_route=route["selected_route"] if route is not None else None)
            if route is not None:
                selected = route["selected_route"]
                result["host_delivery"] = {
                    "status": "not_attempted",
                    "host_surface": selected["host_surface"],
                    "thread_id": selected["thread_id"],
                    "message": (
                        f"LoopX peer request {result['request_id']} for Goal {args.goal_id} "
                        f"is waiting in the inbox for Agent {args.peer_agent_id}. "
                        "Read it with manager-inbox read, decide adopt/defer/reject, "
                        "and return the result with manager-inbox report."
                    ),
                }
        elif args.manager_inbox_action == "acknowledge-return":
            from ..control_plane.collaboration.peers import consume_return
            result = consume_return(
                runtime_root,
                args.goal_id,
                args.agent_id,
                args.request_id,
                registry=registry_path,
                result_key=getattr(args, "result_key", None) or "conclusion",
            )
        elif args.manager_inbox_action == "read":
            from ..control_plane.collaboration.peers import read_inbox
            result = read_inbox(runtime_root, registry_path, args.goal_id, args.agent_id,
                                workspace=Path.cwd(), cursor=cursor, operation_cursor=operation_cursor)
            result["instruction"] += " " + collaboration_answer_instruction()
            result["followthrough"] += (
                " CLI: record assessment with manager-inbox acknowledge; optionally associate existing work "
                "with manager-inbox link. Return audience-ready results with manager-inbox report "
                "--phase conclusion --reply-text; use --phase decision for meaningful interim news."
            )
        elif args.manager_inbox_action == "report":
            from ..capabilities.manager_context.roundtrip import report

            result = report(
                runtime_root,
                args.goal_id,
                args.agent_id,
                args.request_id or "",
                args.phase,
                args.reply_text or "",
                update_id=getattr(args, "update_id", None),
                attachment_refs=getattr(args, "attachment_ref", None),
                workspace=Path.cwd(),
                registry=registry_path,
            )
        elif args.manager_inbox_action == "link":
            from ..control_plane.collaboration.links import link

            result = link(
                runtime_root,
                registry_path,
                args.goal_id,
                args.agent_id,
                args.request_id or "",
                args.related_todo_id,
                args.evidence_id,
            )
        elif args.manager_inbox_action == "status":
            from ..capabilities.manager_context.tracking import query

            result = {
                "ok": True,
                **query(
                    runtime_root,
                    registry_path,
                    goal_ids=[args.goal_id],
                    owner_scope=True,
                    request_id=args.request_id,
                    agent_id=args.agent_id,
                    offset=args.offset,
                    limit=args.limit,
                ),
            }
        else:
            result = acknowledge(
                runtime_root,
                args.goal_id,
                args.agent_id,
                args.request_id or "",
                args.decision or "",
                args.reason or "",
                registry=registry_path,
            )
    except (OSError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1
