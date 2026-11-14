"""Private context consumption; never creates or reprioritizes Todos."""

import json
import os
from pathlib import Path
from ..agent_registry import registered_agent_ids_for_goal
from ..capabilities.manager_context import (
    acknowledge,
    configure_delivery_target,
    configure_delivery_scope,
    configure_evidence_scope,
)
from ..control_plane.projects.registry_codec import load_project_registry
from ..control_plane.collaboration.peers import InboxReadResponseAmbiguous
from ..presentation.answer_instruction import collaboration_answer_instruction


class ManagerInboxCommandError(ValueError):
    """Typed refusal whose recovery details must remain visible to the caller."""

    def __init__(self, message: str, *, error_code: str, **details: object) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.details = details


def _share_item_guidance(result: dict) -> dict:
    items = result.get("items")
    shared = {}
    if isinstance(items, list) and len(items) > 1 and all(isinstance(item, dict) for item in items):
        for key in ("instruction", "next_action"):
            text = items[0].get(key)
            if isinstance(text, str) and text and all(item.get(key) == text for item in items[1:]):
                shared[key] = text
        advice = items[0].get("receiver_followthrough")
        if isinstance(advice, dict) and advice:
            encoded = json.dumps(advice)
            if all(json.dumps(item.get("receiver_followthrough")) == encoded for item in items[1:]):
                shared["receiver_followthrough"] = advice

    if shared and "shared_item_guidance" not in result:
        return {
            "shared_item_guidance": shared,
            **result,
            "items": [{key: value for key, value in item.items() if key not in shared} for item in items],
        }
    return result


def _render_read_markdown(result: dict) -> str:
    lines = ["# Manager Inbox"]
    if result.get("shared_item_guidance"):
        lines.append("Apply the exact fields in `shared_item_guidance` to every item on this page.")
    body = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join(lines) + "\n\n```json\n" + body + "\n```"


def _read_markdown(result: dict, *, runtime_root: Path) -> tuple[str, bool]:
    """Deliver the already-recorded scoped read without repeating its effects."""
    from ..control_plane.turn_driver.native_context_profile import NATIVE_CONTEXT_FIRST_READ_FALLBACK_BYTES

    limit = NATIVE_CONTEXT_FIRST_READ_FALLBACK_BYTES
    view = _share_item_guidance(result)
    body = _render_read_markdown(view)
    size = len((body + os.linesep).encode("utf-8"))
    if size <= limit:
        return body, True

    from ..command_invocation import current_loopx_cli_argv
    from ..control_plane.effect_runtime import MAX_REQUEST_BYTES
    from ..control_plane.turn_driver.decision_capture import (
        CONTEXT_CAPTURE_CODE_MODE_GUIDANCE,
        context_capture_read_command,
        private_capture_supported,
        write_context_read_reference,
    )
    from .context_machine import capture_context_recovery

    if size <= MAX_REQUEST_BYTES:
        reference = capture_context_recovery(view, runtime_root=runtime_root, index_and_select=False)
        if reference.get("available") is not False:
            path = Path(reference["path"])
            digest = reference["sha256"]
            reader_argv = current_loopx_cli_argv()
            direct_command = context_capture_read_command(path, digest,
                page_bytes=limit, max_page_output_bytes=limit, reader_argv=reader_argv)
            opening = {"ok": True, "read_complete": False, "sha256": digest, "command": direct_command,
                "instruction": "Run command to read this exact Inbox page; do not call manager-inbox read again for it. "
                    "Follow next_command through eof in UTF-8 byte-offset order. Compare each reported digest with "
                    "this opening's SHA-256. Check each "
                    "page's content length against its offsets, and complete coverage through total_bytes. "
                    "If output is missing or truncated, reread that range of the same capture with a smaller page. "
                    + CONTEXT_CAPTURE_CODE_MODE_GUIDANCE
                    + "Apply any shared_item_guidance to the items on this page. After the page is complete, "
                      "follow its next_cursor, operation_handoff_next_cursor, or native_child_results.next_cursor with the original Inbox command. "
                      "A captured page is not request adoption, result consumption or completion."}
            if private_capture_supported():
                try:
                    reader = write_context_read_reference(path, digest)
                except (OSError, ValueError):
                    reader = None
                if reader is not None:
                    short_command = context_capture_read_command(path, digest, reference_file=reader,
                        page_bytes=limit, max_page_output_bytes=limit, reader_argv=reader_argv)
                    short_opening = _render_read_markdown({**opening, "command": short_command,
                        "recovery_command": direct_command,
                        "instruction": opening["instruction"] + " If the reader reference is unavailable or the "
                            "digest differs, restart this same capture "
                            "with recovery_command."})
                    if len((short_opening + os.linesep).encode("utf-8")) <= limit:
                        return short_opening, True
            direct_opening = _render_read_markdown(opening)
            if len((direct_opening + os.linesep).encode("utf-8")) <= limit:
                return direct_opening, True

    return _render_read_markdown({"ok": False, "read_complete": False,
        "error": "The Inbox page could not be published within the reader limits.",
        "instruction": "This read may already have recorded request or result delivery receipts. Keep the same "
            "scope and cursor; this is not an empty Inbox. Inspect existing receipts before retrying this read "
            "after repairing private capture storage. Use the JSON view only in a tool that can retain its full "
            "output. Do not rerun the Turn guard or skip to another cursor."}), False


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
            "control",
            "configure-read-scope",
            "configure-delivery-scope",
            "configure-ssh-read-scope",
            "grant-delivery-target",
            "revoke-delivery-target",
            "original-conversation",
        ),
    )
    parser.add_argument("--peer-agent-id", help="For request: a registered peer of the same Goal.")
    parser.add_argument("--conversation-operation", choices=("discover", "request", "control"),
                        help="For original-conversation: discover an explicit recipient, request help, or control the retained request.")
    parser.add_argument("--conversation-file", help="For original-conversation: bounded JSON parameters; source identity comes from --goal-id/--agent-id.")
    parser.add_argument("--chat-url", help="For original-conversation: intended local Chat service URL (default http://127.0.0.1:8767).")
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
    parser.add_argument("--goal-instance-id", help="Exact source Goal instance for request control or original-conversation.")
    parser.add_argument("--control-operation", choices=("inspect", "revise", "cancel", "recover"),
                        help="For control: inspect, correct, cancel or recover this original request.")
    parser.add_argument("--control-file", help="For control: bounded JSON operation parameters; no source or effect facts.")
    parser.add_argument("--request-revision", type=int, help="For acknowledge/report: the supplied request revision; omitted means original revision 1.")
    parser.add_argument("--request-digest", help="For acknowledge: the supplied request revision digest.")
    parser.add_argument("--agent-id", help="Agent identity; for delivery grants, omit to cover all current/future Agents in --goal-id.")
    parser.add_argument("--channel-id")
    parser.add_argument("--ssh-host")
    parser.add_argument("--read-goal-id", action="append", default=[])
    parser.add_argument("--read-goal-scope", choices=("all_registered", "selected"),
                        help="Read scope only: all current/future registered Goals, or explicit --read-goal-id entries.")
    parser.add_argument("--local-delivery-scope", choices=("all_registered", "selected"))
    parser.add_argument("--sender-id", help="For configuring a new delivery source: its independently verified sender.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--request-id", help="For read: inspect this exact current or concluded request instead of the pending inbox.")
    parser.add_argument(
        "--phase", choices=("decision", "conclusion"), default="conclusion"
    )
    parser.add_argument("--reply-text")
    parser.add_argument("--remaining-work", help="For report: accepted work still owed after this partial conclusion; pair with --resume-when. No Todo or execution is created.")
    parser.add_argument("--resume-when", help="For report: concrete continuation condition for --remaining-work; descriptive only, not a scheduler rule.")
    parser.add_argument("--attachment-ref", action="append", default=[],
                        help="For report: explicit relative file in the registered Goal workspace; bound-owner App only, at most four files.")
    parser.add_argument("--update-id", help="For report: stable identity for a later conclusion; retry the same id and text.")
    parser.add_argument("--result-key", help="For acknowledge-return: the exact peer result read; defaults to the initial conclusion.")
    parser.add_argument("--related-todo-id", action="append", default=[])
    parser.add_argument("--evidence-id", action="append", default=[])
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--cursor", help="For read: continue with the previous request or native child result cursor.")
    parser.add_argument("--operation-cursor", help="For read: continue the independent original-operation page.")
    parser.add_argument("--decision", choices=("adopt", "defer", "reject", "no_change"))
    parser.add_argument("--reason")


def handle_manager_inbox(args, registry_path, runtime_root):
    try:
        remainder = getattr(args, "remaining_work", None)
        resume = getattr(args, "resume_when", None)
        if remainder is not None or resume is not None:
            if args.manager_inbox_action != "report" or remainder is None or resume is None:
                raise ValueError("--remaining-work and --resume-when require report and each other")
        if args.manager_inbox_action == "original-conversation":
            from ..control_plane.collaboration.peers import original_conversation_request
            if not args.conversation_operation or not args.conversation_file:
                raise ValueError("--conversation-operation and --conversation-file are required")
            caller_goal_ref = None
            if args.goal_instance_id is not None:
                from ..control_plane.goals.goal_ref_validation import exact_goal_ref
                if not args.goal_id:
                    raise ValueError("--goal-instance-id requires --goal-id")
                caller_goal_ref = exact_goal_ref(args.goal_id, args.goal_instance_id)
            with Path(args.conversation_file).open("rb") as stream:
                raw = stream.read(128001)
            if len(raw) > 128000:
                raise ValueError("original conversation parameters are too large")
            parameters = json.loads(raw)
            if not isinstance(parameters, dict) or any(key in parameters for key in ("operation", "requester", "coordination_runtime_root")):
                raise ValueError("conversation parameters cannot assert source identity or routing")
            result = original_conversation_request(runtime_root, registry_path, args.goal_id, args.agent_id,
                operation=args.conversation_operation, parameters=parameters, caller_goal_ref=caller_goal_ref,
                **({"chat_url": args.chat_url} if args.chat_url else {}))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if any(getattr(args, key, None) is not None for key in ("conversation_operation", "conversation_file", "chat_url")):
            raise ValueError("original conversation options require manager-inbox original-conversation")
        if getattr(args, "read_goal_scope", None) is not None and args.manager_inbox_action != "configure-read-scope":
            raise ValueError("--read-goal-scope is only supported for configure-read-scope")
        if args.manager_inbox_action != "control" and any(getattr(args, key, None) is not None for key in ("control_operation", "control_file", "goal_instance_id")):
            raise ValueError("request control routing flags require the control action")
        if getattr(args, "request_revision", None) is not None and args.manager_inbox_action not in {"acknowledge", "report"}:
            raise ValueError("--request-revision is only supported for acknowledge/report")
        if getattr(args, "request_digest", None) is not None and args.manager_inbox_action != "acknowledge":
            raise ValueError("--request-digest is only supported for acknowledge")
        if args.manager_inbox_action == "control":
            from ..control_plane.collaboration.request_amendment import control_request
            from ..control_plane.goals.goal_ref_validation import exact_goal_ref

            if not args.goal_instance_id or not args.request_id or not args.control_operation:
                raise ValueError("control requires --goal-instance-id, --request-id and --control-operation")
            receiver = args.peer_agent_id or args.agent_id
            if not receiver:
                raise ValueError("control requires the original receiver in --peer-agent-id")
            parameters = {}
            if args.control_file:
                with Path(args.control_file).open("rb") as stream:
                    raw = stream.read(128_001)
                if len(raw) > 128_000:
                    raise ValueError("request control parameters are too large")
                parameters = json.loads(raw)
                if not isinstance(parameters, dict) or "operation_id" in parameters:
                    raise ValueError("control parameters must be an object; use --operation-id for retry identity")
            if args.control_operation in {"revise", "cancel"}:
                parameters["operation_id"] = args.operation_id
            from ..capabilities.todo_replan_cadence.machine_defaults import resolve_todo_replan_cadence_goal

            result = control_request(registry_path=registry_path, runtime_root=runtime_root,
                replan_configuration_reader=resolve_todo_replan_cadence_goal,
                goal_ref=exact_goal_ref(args.goal_id, args.goal_instance_id), receiver_agent_id=receiver,
                request_id=args.request_id, actor_agent_id=args.agent_id, operation=args.control_operation,
                parameters=parameters, execute=args.execute)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
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
                goal_scope=args.read_goal_scope or "selected",
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
            if args.peer_agent_id not in registered_agent_ids_for_goal(goal):
                raise ManagerInboxCommandError(
                    "peer requests require both Agents to be registered to the same Goal; "
                    "no request was recorded",
                    error_code="peer_request_same_goal_required",
                    host_delivery={"status": "not_attempted"},
                    goal_id=args.goal_id,
                    sender_agent_id=args.agent_id,
                    peer_agent_id=args.peer_agent_id,
                    recommended_action=(
                        "Resolve the peer's exact Codex task link with resolve-agent-thread. "
                        "If it is bound to another Goal, use an authorized host task-message tool "
                        "and require its submission receipt; the receiver may consume the message "
                        "on its next Turn. Do not register either Agent to another Goal solely to "
                        "send a message."
                    ),
                )
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
            request_id = getattr(args, "request_id", None)
            result = read_inbox(runtime_root, registry_path, args.goal_id, args.agent_id,
                                workspace=Path.cwd(), cursor=cursor, operation_cursor=operation_cursor,
                                request_id=request_id)
            result["instruction"] += " " + collaboration_answer_instruction()
            if request_id is None:
                result["followthrough"] += (
                    " CLI: record assessment with manager-inbox acknowledge; optionally associate existing work "
                    "with manager-inbox link. Return audience-ready results with manager-inbox report "
                    "--phase conclusion --reply-text; use --phase decision for meaningful interim news."
                )
            if any(item.get("request_revision") is not None for item in result.get("items", [])):
                result["followthrough"] += (
                    " When request_revision is supplied, acknowledge with --request-revision and --request-digest, "
                    "and report with --request-revision. Omitted revision stays bound to original revision 1."
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
                request_revision=getattr(args, "request_revision", None),
                remaining_work={"summary": remainder, "resume_when": resume} if remainder is not None else None,
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
                request_revision=getattr(args, "request_revision", None),
                request_revision_digest=getattr(args, "request_digest", None),
            )
    except InboxReadResponseAmbiguous as exc:
        result = exc.result
    except ManagerInboxCommandError as exc:
        result = {
            "ok": False,
            "error": str(exc),
            "error_code": exc.error_code,
            **exc.details,
        }
    except (OSError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    fmt = getattr(args, "subcommand_format", None) or getattr(args, "format", None)
    if args.manager_inbox_action == "read" and fmt == "markdown" and result["ok"]:
        body, ok = _read_markdown(result, runtime_root=runtime_root)
        print(body)
        return 0 if ok else 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1
