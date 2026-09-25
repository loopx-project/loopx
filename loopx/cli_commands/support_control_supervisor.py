from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from ..agent_registry import load_goal_from_registry, require_registered_agent_id
from ..control_plane.agents.supervisor import (
    build_supervisor_observation_packet,
    build_supervisor_prompt,
    peer_supervisor_for_goal,
    render_supervisor_observation_markdown,
    render_supervisor_prompt_markdown,
)
from ..control_plane.agents.supervisor_events import (
    load_supervisor_event_projection,
    record_supervisor_proposal,
    record_supervisor_receipt,
    render_supervisor_event_markdown,
    supervisor_event_log_path,
)
from ..control_plane.runtime.agent_scoped_evidence_log import (
    build_agent_scoped_evidence_log,
    goal_history_runs,
)
from ..history import collect_history, load_registry
from ..paths import default_public_scan_root, resolve_runtime_root
from ..rollout_event_log import load_rollout_events, rollout_event_log_path
from ..status import collect_status
from .support_control_registry import (
    fallback_global_registry,
    resolve_heartbeat_active_state,
)


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]

SUPERVISOR_CONTROL_COMMANDS = {
    "supervisor-event",
    "supervisor-observe",
    "supervisor-prompt",
}


def register_supervisor_control_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> None:
    prompt_parser = subparsers.add_parser(
        "supervisor-prompt",
        help="Generate the dedicated prompt for an opt-in proposal-only peer supervisor.",
    )
    add_subcommand_format(prompt_parser)
    prompt_parser.add_argument("--goal-id", required=True, help="Stable LoopX goal id.")
    prompt_parser.add_argument(
        "--agent-id",
        required=True,
        help="Registered peer configured as coordination.supervisor.agent_id.",
    )
    prompt_parser.add_argument(
        "--active-state",
        help="Active goal state file. Defaults to the registry goal state_file.",
    )
    prompt_parser.add_argument(
        "--cli-bin",
        default="loopx",
        help="Command name embedded in generated observation commands.",
    )

    observe_parser = subparsers.add_parser(
        "supervisor-observe",
        help="Build one read-only supervisor packet over peer status and evidence.",
    )
    add_subcommand_format(observe_parser)
    observe_parser.add_argument("--goal-id", required=True, help="Stable LoopX goal id.")
    observe_parser.add_argument(
        "--agent-id",
        required=True,
        help="Registered peer configured as coordination.supervisor.agent_id.",
    )

    event_parser = subparsers.add_parser(
        "supervisor-event",
        help="Preview, append, or read durable supervisor proposals and host receipts.",
    )
    add_subcommand_format(event_parser)
    event_parser.add_argument(
        "supervisor_event_action",
        choices=("propose", "receipt", "list"),
    )
    event_parser.add_argument("--goal-id", required=True)
    event_parser.add_argument(
        "--agent-id",
        required=True,
        help="Registered peer configured as coordination.supervisor.agent_id.",
    )
    event_parser.add_argument(
        "--decision-json",
        help="Local JSON object for `propose`; its path is never recorded.",
    )
    event_parser.add_argument(
        "--receipt-json",
        help="Local JSON object for `receipt`; its path is never recorded.",
    )
    event_parser.add_argument(
        "--execute",
        action="store_true",
        help="Append the validated event. Omit for a no-write preview.",
    )


def _configured_supervisor(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    disabled_message: str = "peer supervisor is disabled for this goal",
) -> tuple[str, dict[str, object]]:
    normalized_agent_id = require_registered_agent_id(
        registry_path=registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
        field="agent_id",
    )
    supervisor = peer_supervisor_for_goal(load_goal_from_registry(registry_path, goal_id))
    if supervisor is None:
        raise ValueError(disabled_message)
    if supervisor.get("agent_id") != normalized_agent_id:
        raise ValueError(
            f"agent_id={normalized_agent_id!r} is not the configured supervisor; "
            f"supervisor_agent={supervisor.get('agent_id')!r}"
        )
    return normalized_agent_id, supervisor


def handle_supervisor_control_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    registry_was_supplied: bool,
    print_payload: PrintPayload,
    output_format: FormatSelector,
) -> int | None:
    if args.command not in SUPERVISOR_CONTROL_COMMANDS:
        return None

    if args.command == "supervisor-prompt":
        try:
            active_state, resolved_active_state, active_state_source = (
                resolve_heartbeat_active_state(
                    goal_id=args.goal_id,
                    active_state_arg=args.active_state,
                    registry_path=registry_path,
                    runtime_root_arg=args.runtime_root,
                    allow_global_goal_lookup_fallback=not registry_was_supplied,
                )
            )
            agent_registry_path = registry_path
            if active_state_source.startswith("registry:"):
                agent_registry_path = Path(active_state_source.removeprefix("registry:"))
            agent_id, supervisor = _configured_supervisor(
                registry_path=agent_registry_path,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                disabled_message=(
                    "peer supervisor is disabled; configure it with "
                    "loopx configure-goal --supervisor-agent ... --execute"
                ),
            )
            payload = build_supervisor_prompt(
                goal_id=args.goal_id,
                active_state=str(
                    resolved_active_state
                    or active_state
                    or "the registry-declared active state"
                ),
                supervisor=supervisor,
                cli_bin=args.cli_bin,
            )
        except Exception as exc:
            payload = {
                "ok": False,
                "goal_id": args.goal_id,
                "agent_id": args.agent_id,
                "error": str(exc),
            }
        print_payload(payload, output_format(args), render_supervisor_prompt_markdown)
        return 0 if payload.get("ok") else 1

    agent_registry_path = fallback_global_registry(registry_path, args.runtime_root)
    try:
        agent_id, supervisor = _configured_supervisor(
            registry_path=agent_registry_path,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
        )
        registry = load_registry(agent_registry_path)
        runtime_root = resolve_runtime_root(registry, args.runtime_root)
        if args.command == "supervisor-observe":
            status_payload = collect_status(
                registry_path=agent_registry_path,
                runtime_root_override=str(runtime_root),
                scan_roots=[Path(default_public_scan_root())],
                limit=5,
                goal_id=args.goal_id,
            )
            rollout_events = load_rollout_events(
                rollout_event_log_path(runtime_root, args.goal_id),
                limit=400,
            )
            history_runs = goal_history_runs(
                collect_history(
                    registry_path=agent_registry_path,
                    runtime_root=runtime_root,
                    goal_id=args.goal_id,
                    limit=80,
                ),
                args.goal_id,
            )
            evidence_logs = {
                peer: build_agent_scoped_evidence_log(
                    goal_id=args.goal_id,
                    agent_id=peer,
                    rollout_events=rollout_events,
                    history_runs=history_runs,
                    limit=6,
                )
                for peer in supervisor.get("supervised_agents") or []
            }
            payload = build_supervisor_observation_packet(
                goal_id=args.goal_id,
                supervisor=supervisor,
                status_payload=status_payload,
                evidence_logs=evidence_logs,
            )
            renderer = render_supervisor_observation_markdown
        else:
            log_path = supervisor_event_log_path(runtime_root, args.goal_id)
            if args.supervisor_event_action == "list":
                if args.execute or args.decision_json or args.receipt_json:
                    raise ValueError("list does not accept event JSON or --execute")
                payload = load_supervisor_event_projection(log_path, goal_id=args.goal_id)
            elif args.supervisor_event_action == "propose":
                if not args.decision_json or args.receipt_json:
                    raise ValueError("propose requires only --decision-json")
                decision = json.loads(Path(args.decision_json).read_text(encoding="utf-8"))
                payload = record_supervisor_proposal(
                    log_path=log_path,
                    goal_id=args.goal_id,
                    supervisor=supervisor,
                    decision=decision,
                    execute=bool(args.execute),
                )
            else:
                if not args.receipt_json or args.decision_json:
                    raise ValueError("receipt requires only --receipt-json")
                receipt = json.loads(Path(args.receipt_json).read_text(encoding="utf-8"))
                payload = record_supervisor_receipt(
                    log_path=log_path,
                    goal_id=args.goal_id,
                    receipt=receipt,
                    execute=bool(args.execute),
                )
            payload.setdefault("goal_id", args.goal_id)
            payload.setdefault("supervisor_agent_id", agent_id)
            renderer = render_supervisor_event_markdown
    except Exception as exc:
        payload = {
            "ok": False,
            "goal_id": args.goal_id,
            "supervisor_agent_id": args.agent_id,
            "error": str(exc),
        }
        if args.command == "supervisor-observe":
            payload["schema_version"] = "supervisor_observation_v0"
        renderer = (
            render_supervisor_observation_markdown
            if args.command == "supervisor-observe"
            else render_supervisor_event_markdown
        )
    print_payload(payload, output_format(args), renderer)
    return 0 if payload.get("ok") else 1
