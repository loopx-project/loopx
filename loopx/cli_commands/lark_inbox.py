from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections.abc import Callable
from pathlib import Path

from ..capabilities.issue_fix.provider_hooks import IssueFixReviewerProviderHooks
from ..capabilities.reward_memory.feedback_hint import build_feedback_review_hint
from ..capabilities.reward_memory.outbound import outbound_guidance_hook
from ..control_plane.capability_hooks import (
    TURN_START_HOOK_RESULT_SCHEMA_VERSION,
    TurnStartHookRegistration,
    dispatch_turn_start_hooks,
)
from ..control_plane.runtime.goal_project_route import resolve_goal_project_route
from ..extensions.lark import (
    LARK_COLLECTOR_PERMISSION,
    LARK_EXTENSION_ID,
    LARK_INBOX_READ_PERMISSION,
    LARK_INBOX_WRITE_PERMISSION,
    LARK_REPLY_PERMISSION,
    LARK_REVIEWER_NOTIFICATION_PERMISSION,
)
from ..extensions.lark.event_collector import (
    inspect_lark_event_collector,
    install_lark_event_collector,
    plan_lark_event_collector,
)
from ..extensions.lark.event_collector_routes import (
    reconcile_lark_event_collector_route,
)
from ..extensions.lark.event_collector_runtime import run_lark_event_collector
from ..extensions.lark.event_inbox import (
    acknowledge_lark_event_inbox,
    inspect_lark_event_inbox,
    lark_event_inbox_contains_text,
)
from ..extensions.lark.group_history import catch_up_lark_group_history
from ..extensions.lark.inbox_reactions import (
    complete_lark_event_inbox_reactions,
    mark_lark_event_inbox_processing,
)
from ..extensions.lark.inbox_reply import (
    reply_lark_event_inbox,
    send_lark_inbox_message,
)
from ..extensions.lark.reviewer_notification import (
    lark_reviewer_notification_sink,
)
from ..extensions.lark.routed_inbox import (
    acknowledge_routed_lark_event_inbox,
    ingest_routed_lark_event_inbox,
    inspect_routed_lark_event_inbox,
    project_routed_lark_event_inbox_urgency,
    resolve_routed_lark_inbox_config,
    resolve_routed_lark_inbox_route,
    settle_routed_lark_event_inbox_material_review,
)
from ..extensions.lark.turn_start_sync import sync_lark_turn_start_inbox
from ..extensions.runtime import (
    default_extension_state_file,
    resolve_extension_activation,
)
from ..file_lock import lock_timeout_error_fields


def _goal_inbox_config(
    goal: dict[str, object], *, agent_id: str | None = None
) -> str | None:
    control_plane = (
        goal.get("control_plane") if isinstance(goal.get("control_plane"), dict) else {}
    )
    agent_inboxes = (
        control_plane.get("lark_event_inboxes")
        if isinstance(control_plane.get("lark_event_inboxes"), dict)
        else {}
    )
    inbox = (
        agent_inboxes.get(agent_id)
        if agent_id and isinstance(agent_inboxes.get(agent_id), dict)
        else control_plane.get("lark_event_inbox")
    )
    inbox = inbox if isinstance(inbox, dict) else {}
    if inbox.get("enabled") is not True:
        return None
    return str(inbox.get("config_path") or "").strip() or None


def _inbox_context(
    args: argparse.Namespace, registry_path: Path
) -> tuple[Path, str | None]:
    if getattr(args, "config", None):
        return Path(getattr(args, "project", None) or ".").expanduser(), str(
            args.config
        )
    if getattr(args, "goal_id", None):
        goal, project, _ = resolve_goal_project_route(
            registry_path=registry_path,
            goal_id=str(args.goal_id),
            project_override=getattr(args, "project", None),
        )
        return project, _goal_inbox_config(
            goal,
            agent_id=str(getattr(args, "agent_id", None) or "").strip() or None,
        )
    raise ValueError("lark inbox requires --config or --goal-id")


def register_lark_inbox_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "lark-inbox",
        help="Inspect and acknowledge a host-collected local Lark event inbox.",
    )
    sub = parser.add_subparsers(dest="lark_inbox_command", required=True)
    drain = sub.add_parser(
        "drain",
        help="Return bounded unprocessed local-private events without acknowledging them.",
    )
    add_subcommand_format(drain)
    drain.add_argument("--project")
    drain.add_argument("--config")
    drain.add_argument("--goal-id")
    drain.add_argument("--agent-id")
    drain.add_argument("--limit", type=int, default=20)
    ack = sub.add_parser(
        "ack",
        help="Acknowledge events only after their actionable feedback is written back.",
    )
    add_subcommand_format(ack)
    ack.add_argument("--project")
    ack.add_argument("--config")
    ack.add_argument("--goal-id")
    ack.add_argument("--agent-id")
    ack.add_argument("--message-id", action="append", required=True)
    ack.add_argument("--execute", action="store_true")
    material_review = sub.add_parser(
        "material-review",
        help=(
            "Settle one unaddressed message or attachment with a committed "
            "effect receipt or an explicit no-follow-up rationale."
        ),
    )
    add_subcommand_format(material_review)
    material_review.add_argument("--project")
    material_review.add_argument("--config")
    material_review.add_argument("--goal-id")
    material_review.add_argument("--agent-id")
    material_review.add_argument("--message-id", required=True)
    disposition = material_review.add_mutually_exclusive_group(required=True)
    disposition.add_argument("--effect-receipt-json")
    disposition.add_argument("--no-follow-up")
    material_review.add_argument("--execute", action="store_true")
    reply = sub.add_parser(
        "reply",
        help=(
            "Reply once in the source thread with the inbox-configured bot profile; "
            "never falls back to the default app."
        ),
    )
    add_subcommand_format(reply)
    reply.add_argument("--project")
    reply.add_argument("--config")
    reply.add_argument("--goal-id")
    reply.add_argument("--agent-id")
    reply.add_argument("--message-id", required=True)
    reply.add_argument("--text", required=True)
    reply.add_argument(
        "--provider-preflight",
        action="store_true",
        help="Run identity, membership, and provider dry-run checks without sending.",
    )
    reply.add_argument("--execute", action="store_true")
    send = sub.add_parser(
        "send",
        help=(
            "Send one verified chat-root message with the inbox-configured bot; "
            "structured mentions must match provider readback exactly."
        ),
    )
    add_subcommand_format(send)
    send.add_argument("--project")
    send.add_argument("--config")
    send.add_argument("--goal-id")
    send.add_argument("--agent-id")
    send.add_argument("--route-key")
    send.add_argument("--text", required=True)
    send.add_argument(
        "--provider-preflight",
        action="store_true",
        help="Run identity, membership, mention, and provider dry-run checks.",
    )
    send.add_argument("--execute", action="store_true")
    for outbound in (send, reply):
        outbound.add_argument(
            "--message-purpose",
            choices=("unspecified", "help", "progress", "urgent"),
            default="unspecified",
            help="Context for opt-in guidance recall, not send authority.",
        )
        outbound.add_argument(
            "--reviewed-guidance-digest",
            help="Agent acknowledgement of the exact current pre-send guidance digest; not user approval.",
        )
    processing = sub.add_parser(
        "processing",
        help=(
            "Mark one captured message as actively processing and replace its "
            "received reaction when configured."
        ),
    )
    add_subcommand_format(processing)
    processing.add_argument("--project")
    processing.add_argument("--config")
    processing.add_argument("--goal-id")
    processing.add_argument("--agent-id")
    processing.add_argument("--message-id", required=True)
    processing.add_argument("--execute", action="store_true")
    reaction_complete = sub.add_parser(
        "reaction-complete",
        help="Remove bot-owned lifecycle reactions for one captured message.",
    )
    add_subcommand_format(reaction_complete)
    reaction_complete.add_argument("--project")
    reaction_complete.add_argument("--config")
    reaction_complete.add_argument("--goal-id")
    reaction_complete.add_argument("--agent-id")
    reaction_complete.add_argument("--message-id", required=True)
    reaction_complete.add_argument("--execute", action="store_true")
    ingest = sub.add_parser(
        "ingest",
        help=(
            "Persist canonical compact events from stdin JSON/NDJSON for host "
            "collection or bounded history reconciliation."
        ),
    )
    add_subcommand_format(ingest)
    ingest.add_argument("--project")
    ingest.add_argument("--config")
    ingest.add_argument("--goal-id")
    ingest.add_argument("--agent-id")
    ingest.add_argument("--execute", action="store_true")
    history_catch_up = sub.add_parser(
        "history-catch-up",
        help=(
            "Read one bounded group-history page into the configured route inbox; "
            "preview by default and commit the inbox plus private cursor with --execute."
        ),
    )
    add_subcommand_format(history_catch_up)
    history_catch_up.add_argument("--project")
    history_catch_up.add_argument("--config")
    history_catch_up.add_argument("--goal-id")
    history_catch_up.add_argument("--agent-id")
    history_catch_up.add_argument("--route-key", required=True)
    history_catch_up.add_argument("--start", required=True)
    history_catch_up.add_argument("--page-size", type=int, default=50)
    history_catch_up.add_argument("--lark-cli-executable", default="lark-cli")
    history_catch_up.add_argument("--node-executable")
    history_catch_up.add_argument("--execute", action="store_true")
    collector_plan = sub.add_parser(
        "collector-plan",
        help="Validate a local-private collector config and preview host setup.",
    )
    add_subcommand_format(collector_plan)
    collector_plan.add_argument("--project", default=".")
    collector_plan.add_argument("--config", required=True)
    collector_install = sub.add_parser(
        "collector-install",
        help="Preview or explicitly install the configured launchd/systemd collector.",
    )
    add_subcommand_format(collector_install)
    collector_install.add_argument("--project", default=".")
    collector_install.add_argument("--config", required=True)
    collector_install.add_argument("--execute", action="store_true")
    collector_route = sub.add_parser(
        "collector-route-reconcile",
        help=(
            "Plan or atomically add one collector route; receipts redact chat and "
            "local inbox bindings."
        ),
    )
    add_subcommand_format(collector_route)
    collector_route.add_argument("--project", default=".")
    collector_route.add_argument("--config", required=True)
    collector_route.add_argument("--route-key", required=True)
    collector_route.add_argument("--chat-id", required=True)
    collector_route.add_argument("--event-inbox-config", required=True)
    collector_route.add_argument("--execute", action="store_true")
    collector_status = sub.add_parser(
        "collector-status",
        help="Inspect collector installation, supervisor state, and event evidence.",
    )
    add_subcommand_format(collector_status)
    collector_status.add_argument("--project", default=".")
    collector_status.add_argument("--config", required=True)
    collector_status.add_argument("--probe-event-bus", action="store_true")
    collector_run = sub.add_parser("collector-run", help=argparse.SUPPRESS)
    add_subcommand_format(collector_run)
    collector_run.add_argument("--project", required=True)
    collector_run.add_argument("--config", required=True)
    collector_run.add_argument("--lark-cli-executable", required=True)
    collector_run.add_argument("--node-executable")


def _read_stdin_events() -> list[object]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("lark inbox ingest requires JSON or NDJSON on stdin")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = [json.loads(line) for line in raw.split("\n") if line.strip()]
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    raise ValueError("lark inbox ingest input must be an event object or event array")


def _required_extension_permissions(command: str) -> tuple[str, ...]:
    if command == "drain":
        return (LARK_INBOX_READ_PERMISSION,)
    if command in {"ack", "material-review", "ingest"}:
        return (LARK_INBOX_WRITE_PERMISSION,)
    if command == "history-catch-up":
        return (LARK_COLLECTOR_PERMISSION, LARK_INBOX_WRITE_PERMISSION)
    if command in {"reply", "send", "processing", "reaction-complete"}:
        return (LARK_REPLY_PERMISSION,)
    return (LARK_COLLECTOR_PERMISSION,)


def _resolve_lark_activation(
    command: str,
    *,
    runtime_root_arg: str | None,
) -> dict[str, object]:
    return resolve_extension_activation(
        LARK_EXTENSION_ID,
        state_file=default_extension_state_file(runtime_root_arg),
        required_permissions=_required_extension_permissions(command),
    )


def build_lark_operator_inbox_urgency_projector(
    *, runtime_root_arg: str | Path | None
) -> Callable[..., dict[str, object]]:
    """Compose activation proof with the extension-owned private config read."""

    def project(*, project: str | Path, config_path: str | Path) -> dict[str, object]:
        _resolve_lark_activation(
            "drain",
            runtime_root_arg=(
                str(runtime_root_arg) if runtime_root_arg is not None else None
            ),
        )
        return project_routed_lark_event_inbox_urgency(
            project=project,
            config_path=config_path,
        )

    return project


def build_lark_turn_start_inbox_hook(
    *,
    project: str | Path,
    config_path: str | Path,
    runtime_root_arg: str | Path | None,
    required_read_command: str,
) -> TurnStartHookRegistration:
    """Compose the opt-in provider sync behind the shared turn-start phase."""

    def produce() -> dict[str, object]:
        _resolve_lark_activation(
            "history-catch-up",
            runtime_root_arg=(
                str(runtime_root_arg) if runtime_root_arg is not None else None
            ),
        )
        result = sync_lark_turn_start_inbox(
            project=project,
            config_path=config_path,
        )
        status = str(result.get("status") or "failed")
        error_code = result.get("error_code")
        if error_code in {
            "provider_contract_error",
            "inbox_readback_failed",
            "cursor_readback_failed",
        }:
            status = "failed"
        if status not in {
            "not_applicable",
            "observed",
            "empty",
            "partial",
            "unavailable",
            "failed",
        }:
            status = "failed"
            error_code = "provider_result_unmapped"
        return {
            "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": "lark.turn_start_inbox_sync",
            "capability_id": "lark-event-inbox",
            "phase": "turn_start",
            "status": status,
            "observation_count": int(result.get("observation_count") or 0),
            "agent_read_required": bool(
                result.get("agent_read_required") is True
                and status in {"observed", "partial"}
            ),
            "external_reads_performed": (
                result.get("external_reads_performed") is True
            ),
            "external_writes_performed": (
                result.get("external_writes_performed") is True
            ),
            "local_private_state_mutated": (
                result.get("local_private_state_mutated") is True
            ),
            "private_content_returned": False,
            "provider_payload_returned": False,
            "error_code": error_code,
        }

    return TurnStartHookRegistration(
        hook_id="lark.turn_start_inbox_sync",
        capability_id="lark-event-inbox",
        requested_read_scope=(
            "provider_group_history",
            "owner_private_inbox_binding",
        ),
        requested_write_scope=(
            "owner_private_inbox",
            "owner_private_cursor",
            "provider_message_reaction",
        ),
        producer=produce,
        required_read={
            "kind": "operator_inbox",
            "command": required_read_command,
            "reason": "turn-start hook synchronized new operator inbox evidence",
            "ordering": "before_work",
        },
    )


def dispatch_goal_lark_turn_start_hooks(
    *,
    registry_path: Path,
    runtime_root_arg: str | Path | None,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, object]:
    """Resolve one Goal-owned inbox and run its pre-decision sync hook."""

    goal, project, _ = resolve_goal_project_route(
        registry_path=registry_path,
        goal_id=goal_id,
    )
    config_path = _goal_inbox_config(goal, agent_id=agent_id)
    control_plane = (
        goal.get("control_plane") if isinstance(goal.get("control_plane"), dict) else {}
    )
    agent_inboxes = (
        control_plane.get("lark_event_inboxes")
        if isinstance(control_plane.get("lark_event_inboxes"), dict)
        else {}
    )
    agent_scoped = bool(agent_id and isinstance(agent_inboxes.get(agent_id), dict))
    drain_parts = ["loopx", "--registry", str(registry_path.expanduser())]
    drain_parts.extend(["lark-inbox", "drain", "--goal-id", goal_id])
    if agent_scoped and agent_id:
        drain_parts.extend(["--agent-id", agent_id])
    required_read_command = shlex.join(drain_parts)
    registrations = (
        (
            build_lark_turn_start_inbox_hook(
                project=project,
                config_path=config_path,
                runtime_root_arg=runtime_root_arg,
                required_read_command=required_read_command,
            ),
        )
        if config_path
        else ()
    )
    return dispatch_turn_start_hooks(registrations)


def build_lark_issue_fix_reviewer_provider_hooks(
    *, runtime_root_arg: str | None
) -> IssueFixReviewerProviderHooks:
    activation = resolve_extension_activation(
        LARK_EXTENSION_ID,
        state_file=default_extension_state_file(runtime_root_arg),
        required_permissions=(
            LARK_INBOX_READ_PERMISSION,
            LARK_INBOX_WRITE_PERMISSION,
            LARK_REPLY_PERMISSION,
            LARK_REVIEWER_NOTIFICATION_PERMISSION,
        ),
    )
    return IssueFixReviewerProviderHooks(
        inspect=inspect_lark_event_inbox,
        acknowledge=acknowledge_lark_event_inbox,
        contains_text=lark_event_inbox_contains_text,
        notification_adapter=lark_reviewer_notification_sink,
        activation=activation,
    )


def _disabled_inbox_projection() -> dict[str, object]:
    return {
        "ok": True,
        "schema_version": "lark_event_inbox_projection_v0",
        "enabled": False,
        "configured": False,
        "pending_count": 0,
        "items": [],
        "local_private_content_returned": False,
        "external_reads_performed": False,
    }


def _render(payload: dict[str, object]) -> str:
    lines = [
        "# Lark Event Inbox",
        "",
        f"- ok: {payload.get('ok')}",
        f"- enabled: {payload.get('enabled')}",
        f"- pending_count: {payload.get('pending_count')}",
        f"- write_performed: {payload.get('write_performed')}",
    ]
    for item in payload.get("items") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('message_id')}: {item.get('content')}")
    guidance = payload.get("outbound_guidance")
    if isinstance(guidance, dict):
        lines.append(
            f"- agent_review_required: {guidance.get('agent_review_required')}"
        )
        for item in guidance.get("guidance") or []:
            lines.append(f"- guidance: {item.get('content_summary')}")
        if guidance.get("agent_review_required"):
            review_digest = guidance.get("review_digest")
            if isinstance(review_digest, str) and review_digest:
                lines.append(
                    "Agent: assess this guidance against current evidence and safe alternatives; "
                    "only if sending is still appropriate, rerun with --reviewed-guidance-digest "
                    + review_digest
                    + ". This is not a request for user approval."
                )
            else:
                # configuration_error blocks carry no digest (review cannot
                # waive them); surface the repair hint instead of a None
                # placeholder that would send the agent into a retry loop.
                action = guidance.get("recommended_action")
                lines.append(
                    "Agent: guidance review is required but no digest was produced"
                    + (f"; {action}" if isinstance(action, str) and action else ".")
                )
    feedback = payload.get("reward_memory_feedback_review")
    if isinstance(feedback, dict):
        lines.append(f"- Reward Memory (advisory): {feedback['instruction']}")
        lines.append(f"- preview: {feedback['preview_command']}")
        lines.append("- configured routes: " + json.dumps(feedback["routes"]))
    return "\n".join(lines).rstrip() + "\n"


def handle_lark_inbox_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: Callable[..., str],
    print_payload: Callable,
) -> int | None:
    if args.command != "lark-inbox":
        return None
    activation: dict[str, object] | None = None
    try:
        inbox_commands = {
            "drain",
            "ack",
            "material-review",
            "reply",
            "send",
            "processing",
            "reaction-complete",
            "ingest",
            "history-catch-up",
        }
        project: Path | None = None
        config_path: str | None = None
        if args.lark_inbox_command in inbox_commands:
            project, config_path = _inbox_context(args, registry_path)
            if config_path is None:
                if args.lark_inbox_command != "drain":
                    raise ValueError("goal does not configure a Lark event inbox")
                payload = _disabled_inbox_projection()
                print_payload(payload, output_format(args), _render)
                return 0

        activation = _resolve_lark_activation(
            args.lark_inbox_command,
            runtime_root_arg=runtime_root_arg,
        )
        if args.lark_inbox_command == "drain":
            payload = inspect_routed_lark_event_inbox(
                project=project,
                config_path=config_path,
                limit=args.limit,
            )
            if (
                payload.get("enabled") is True
                and payload.get("items")
                and not getattr(args, "config", None)
                and not getattr(args, "project", None)
            ):
                hint = build_feedback_review_hint(
                    registry_path=registry_path,
                    goal_id=getattr(args, "goal_id", None),
                    agent_id=getattr(args, "agent_id", None),
                )
                if hint is not None:
                    payload["reward_memory_feedback_review"] = hint
        elif args.lark_inbox_command == "ack":
            payload = acknowledge_routed_lark_event_inbox(
                project=project,
                config_path=config_path,
                message_ids=args.message_id,
                execute=args.execute,
            )
        elif args.lark_inbox_command == "material-review":
            effect_receipt = (
                json.loads(args.effect_receipt_json)
                if args.effect_receipt_json
                else None
            )
            if effect_receipt is not None and not isinstance(effect_receipt, dict):
                raise ValueError("effect receipt JSON must be an object")
            payload = settle_routed_lark_event_inbox_material_review(
                project=project,
                config_path=config_path,
                message_id=args.message_id,
                effect_receipt=effect_receipt,
                no_follow_up_reason=args.no_follow_up,
                execute=args.execute,
            )
        elif args.lark_inbox_command == "reply":
            routed_config = resolve_routed_lark_inbox_config(
                project=project,
                config_path=config_path,
                message_id=args.message_id,
            )
            payload = reply_lark_event_inbox(
                project=project,
                config_path=routed_config,
                message_id=args.message_id,
                text=args.text,
                execute=args.execute,
                provider_preflight=args.provider_preflight,
                before_send=outbound_guidance_hook(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    purpose=getattr(args, "message_purpose", "unspecified"),
                    reviewed_digest=getattr(args, "reviewed_guidance_digest", None),
                ),
            )
        elif args.lark_inbox_command == "send":
            routed_config = resolve_routed_lark_inbox_route(
                project=project,
                config_path=config_path,
                route_key=args.route_key,
            )
            payload = send_lark_inbox_message(
                project=project,
                config_path=routed_config,
                text=args.text,
                execute=args.execute,
                provider_preflight=args.provider_preflight,
                before_send=outbound_guidance_hook(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    purpose=getattr(args, "message_purpose", "unspecified"),
                    reviewed_digest=getattr(args, "reviewed_guidance_digest", None),
                ),
            )
        elif args.lark_inbox_command == "processing":
            routed_config = resolve_routed_lark_inbox_config(
                project=project,
                config_path=config_path,
                message_id=args.message_id,
            )
            payload = mark_lark_event_inbox_processing(
                project=project,
                config_path=routed_config,
                message_id=args.message_id,
                execute=args.execute,
            )
        elif args.lark_inbox_command == "reaction-complete":
            routed_config = resolve_routed_lark_inbox_config(
                project=project,
                config_path=config_path,
                message_id=args.message_id,
            )
            payload = complete_lark_event_inbox_reactions(
                project=project,
                config_path=routed_config,
                message_id=args.message_id,
                execute=args.execute,
            )
        elif args.lark_inbox_command == "ingest":
            payload = ingest_routed_lark_event_inbox(
                project=project,
                config_path=config_path,
                events=_read_stdin_events(),
                execute=args.execute,
            )
        elif args.lark_inbox_command == "history-catch-up":
            payload = catch_up_lark_group_history(
                project=project,
                config_path=config_path,
                route_key=args.route_key,
                start=args.start,
                page_size=args.page_size,
                execute=args.execute,
                lark_cli_executable=args.lark_cli_executable,
                node_executable=args.node_executable,
            )
        elif args.lark_inbox_command == "collector-plan":
            payload = plan_lark_event_collector(
                project=args.project,
                config_path=args.config,
                runtime_root=runtime_root_arg,
            )
        elif args.lark_inbox_command == "collector-install":
            payload = install_lark_event_collector(
                project=args.project,
                config_path=args.config,
                runtime_root=runtime_root_arg,
                execute=args.execute,
            )
        elif args.lark_inbox_command == "collector-route-reconcile":
            payload = reconcile_lark_event_collector_route(
                project=args.project,
                config_path=args.config,
                route_key=args.route_key,
                chat_id=args.chat_id,
                event_inbox_config=args.event_inbox_config,
                execute=args.execute,
            )
        elif args.lark_inbox_command == "collector-run":
            payload = run_lark_event_collector(
                project=args.project,
                config_path=args.config,
                lark_cli_executable=args.lark_cli_executable,
                runtime_root=runtime_root_arg,
                node_executable=args.node_executable,
            )
        else:
            payload = inspect_lark_event_collector(
                project=args.project,
                config_path=args.config,
                runtime_root=runtime_root_arg,
                probe_event_bus=args.probe_event_bus,
            )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        payload = {
            "ok": False,
            "schema_version": "lark_event_inbox_error_v0",
            "error": str(exc),
            **lock_timeout_error_fields(exc),
        }
    if activation is not None and payload.get("ok"):
        payload["extension_activation"] = activation
    print_payload(payload, output_format(args), _render)
    return 0 if payload.get("ok") else 1
