"""Private context consumption; never creates or reprioritizes Todos."""

import json
from pathlib import Path
from ..agent_registry import registered_agent_ids_for_goal
from ..capabilities.manager_context import (
    acknowledge,
    configure_delivery_target,
    configure_evidence_scope,
)
from ..control_plane.projects.registry_codec import load_project_registry


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
            "configure-ssh-read-scope",
            "grant-delivery-target",
            "revoke-delivery-target",
        ),
    )
    parser.add_argument("--peer-agent-id", help="For request: a registered peer of the same Goal.")
    parser.add_argument("--operation-id", help="For request: stable retry identity; use a new id for another review round.")
    parser.add_argument("--brief-file", help="For request: collaboration_brief_v0 JSON file.")
    parser.add_argument("--parent-request-id", help="For request: an inbox request received by the sender.")
    parser.add_argument("--goal-id")
    parser.add_argument("--agent-id")
    parser.add_argument("--channel-id")
    parser.add_argument("--ssh-host")
    parser.add_argument("--read-goal-id", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--request-id")
    parser.add_argument(
        "--phase", choices=("decision", "conclusion"), default="conclusion"
    )
    parser.add_argument("--reply-text")
    parser.add_argument("--related-todo-id", action="append", default=[])
    parser.add_argument("--evidence-id", action="append", default=[])
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--cursor", help="For read: continue with the previous page's next_cursor.")
    parser.add_argument("--decision", choices=("adopt", "defer", "reject", "no_change"))
    parser.add_argument("--reason")


def handle_manager_inbox(args, registry_path, runtime_root):
    try:
        cursor = getattr(args, "cursor", None)
        if cursor is not None and args.manager_inbox_action != "read":
            raise ValueError("--cursor is only supported for read")
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
                agent_id=args.agent_id or "",
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
            if not args.brief_file:
                raise ValueError("--brief-file is required for a peer request")
            with Path(args.brief_file).open("rb") as stream:
                raw = stream.read(128_001)
            if len(raw) > 128_000:
                raise ValueError("peer brief file is too large")
            result = request(runtime_root, registry_path, args.goal_id, args.agent_id,
                             args.peer_agent_id, args.operation_id, json.loads(raw), args.parent_request_id)
        elif args.manager_inbox_action == "acknowledge-return":
            from ..control_plane.collaboration.peers import consume_return
            result = consume_return(
                runtime_root,
                args.goal_id,
                args.agent_id,
                args.request_id,
                registry=registry_path,
            )
        elif args.manager_inbox_action == "read":
            from ..control_plane.collaboration.peers import read_inbox
            result = read_inbox(runtime_root, registry_path, args.goal_id, args.agent_id,
                                workspace=Path.cwd(), cursor=cursor)
            result["followthrough"] = (
                "After reading and deciding, associate Core work with manager-inbox link. Then use manager-inbox report --phase conclusion --reply-text to return this request's concrete result, replan decision, or explicit blocker/defer reason to its original audience automatically. Use optional --phase decision only for meaningful interim news during longer work. Adoption/linking alone is not a completed exchange. Do not wait for the owner to ask again. Write audience-ready text, not private deliberation."
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
                registry=registry_path,
            )
        elif args.manager_inbox_action == "link":
            from ..capabilities.manager_context.tracking import link

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
