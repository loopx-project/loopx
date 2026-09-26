from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from ..attached_session import (
    bind_attached_agent_session,
    claim_attached_agent_turn,
    complete_attached_agent_turn,
    load_attached_session_registry,
    render_attached_session_broker_markdown,
)
from ..chat_store import CHAT_SESSION_MODE_ATTACHED, ChatSessionStore
from ..paths import resolve_runtime_root
from ..worker_bridge import (
    DEFAULT_ACTIVE_USER_CODEX_BIN,
    DEFAULT_ACTIVE_USER_SIMULATOR_CONTEXT_DIR,
    DEFAULT_ACTIVE_USER_SIMULATOR_OUTPUT_JSON,
    DEFAULT_ACTIVE_USER_SIMULATOR_OUTPUT_SCHEMA_JSON,
    DEFAULT_ACTIVE_USER_SIMULATOR_PROMPT_JSON,
    DEFAULT_WORKER_BRIDGE_ACTIVE_USER_FEED_JSONL,
    DEFAULT_WORKER_BRIDGE_ACTIVE_USER_OBSERVATION_JSON,
    DEFAULT_WORKER_BRIDGE_COUNTER_TRACE_JSON,
    DEFAULT_WORKER_BRIDGE_MODULE,
    DEFAULT_WORKER_BRIDGE_PYTHON_BIN,
    LOOPX_PROJECT_ROOT_PLACEHOLDER,
    LOOPX_RUNTIME_ROOT_PLACEHOLDER,
    append_worker_bridge_counter_trace_row,
    build_active_user_codex_simulator_contract,
    build_active_user_intervention,
    build_active_user_intervention_channel_contract,
    build_active_user_intervention_from_simulator_output,
    build_worker_bridge_install_contract,
    observe_active_user_intervention_feed,
    render_worker_bridge_install_contract_markdown,
    write_active_user_observation_file,
)


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
OutputFormat = Callable[[argparse.Namespace], str]

WORKER_BRIDGE_COMMANDS = {
    "active-user-codex-simulator-contract",
    "active-user-contract",
    "active-user-intervention",
    "active-user-observe",
    "active-user-simulator-output",
    "contract",
    "attached-session-bind",
    "attached-session-claim",
    "attached-session-complete",
    "attached-session-list",
}


def register_worker_bridge_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    worker_bridge_parser = subparsers.add_parser(
        "worker-bridge",
        help="Render runner-agnostic worker bridge/install contracts.",
    )
    worker_bridge_sub = worker_bridge_parser.add_subparsers(
        dest="worker_bridge_command"
    )

    worker_bridge_contract_parser = worker_bridge_sub.add_parser(
        "contract",
        help="Render a LoopX worker bridge/install contract.",
    )
    add_subcommand_format(worker_bridge_contract_parser)
    worker_bridge_contract_parser.add_argument(
        "--project-root",
        default=LOOPX_PROJECT_ROOT_PLACEHOLDER,
        help="Container-visible LoopX project root. Defaults to a public placeholder.",
    )
    worker_bridge_contract_parser.add_argument(
        "--runtime-root",
        dest="worker_bridge_runtime_root",
        default=LOOPX_RUNTIME_ROOT_PLACEHOLDER,
        help="Container-visible LoopX runtime root. Defaults to a public placeholder.",
    )
    worker_bridge_contract_parser.add_argument(
        "--python-bin",
        default=DEFAULT_WORKER_BRIDGE_PYTHON_BIN,
        help="Python executable inside the worker environment.",
    )
    worker_bridge_contract_parser.add_argument(
        "--module",
        default=DEFAULT_WORKER_BRIDGE_MODULE,
        help="LoopX CLI module import path.",
    )
    worker_bridge_contract_parser.add_argument(
        "--scan-path",
        help="Container-visible public scan path. Defaults to the LoopX package.",
    )
    worker_bridge_contract_parser.add_argument(
        "--counter-trace-json",
        default=DEFAULT_WORKER_BRIDGE_COUNTER_TRACE_JSON,
        help="Worker-visible compact counter trace JSONL path.",
    )
    worker_bridge_contract_parser.add_argument(
        "--classification",
        default="<classification>",
        help="Classification label for worker-side compact writeback.",
    )

    active_user_contract_parser = worker_bridge_sub.add_parser(
        "active-user-contract",
        help="Render the active-user simulator external-update channel contract.",
    )
    add_subcommand_format(active_user_contract_parser)
    active_user_contract_parser.add_argument(
        "--project-root",
        default=LOOPX_PROJECT_ROOT_PLACEHOLDER,
        help="Container-visible LoopX project root. Defaults to a public placeholder.",
    )
    active_user_contract_parser.add_argument(
        "--runtime-root",
        dest="active_user_runtime_root",
        default=LOOPX_RUNTIME_ROOT_PLACEHOLDER,
        help="Container-visible LoopX runtime root. Defaults to a public placeholder.",
    )
    active_user_contract_parser.add_argument(
        "--python-bin",
        default=DEFAULT_WORKER_BRIDGE_PYTHON_BIN,
        help="Python executable inside the worker environment.",
    )
    active_user_contract_parser.add_argument(
        "--module",
        default=DEFAULT_WORKER_BRIDGE_MODULE,
        help="LoopX CLI module import path.",
    )
    active_user_contract_parser.add_argument(
        "--feed-jsonl",
        default=DEFAULT_WORKER_BRIDGE_ACTIVE_USER_FEED_JSONL,
        help="Worker-visible active-user intervention feed JSONL path.",
    )
    active_user_contract_parser.add_argument(
        "--observation-json",
        default=DEFAULT_WORKER_BRIDGE_ACTIVE_USER_OBSERVATION_JSON,
        help="Worker-visible active-user observation JSON path.",
    )
    active_user_contract_parser.add_argument(
        "--counter-trace-json",
        default=DEFAULT_WORKER_BRIDGE_COUNTER_TRACE_JSON,
        help="Worker-visible compact counter trace JSONL path.",
    )
    active_user_contract_parser.add_argument(
        "--classification",
        default="active_user_observe_checkpoint",
        help="Compact classification label for observe checkpoints.",
    )
    active_user_contract_parser.add_argument(
        "--min-interval-seconds",
        type=int,
        default=300,
        help="Minimum interval between proactive simulator interventions.",
    )
    active_user_contract_parser.add_argument(
        "--max-interventions-per-task",
        type=int,
        default=3,
        help="Maximum proactive simulator interventions per task.",
    )

    active_user_codex_simulator_contract_parser = worker_bridge_sub.add_parser(
        "active-user-codex-simulator-contract",
        help="Render the formal Codex CLI active-user simulator launch contract.",
    )
    add_subcommand_format(active_user_codex_simulator_contract_parser)
    active_user_codex_simulator_contract_parser.add_argument(
        "--project-root",
        default=LOOPX_PROJECT_ROOT_PLACEHOLDER,
        help="LoopX project root visible to the simulator launcher.",
    )
    active_user_codex_simulator_contract_parser.add_argument(
        "--python-bin",
        default=DEFAULT_WORKER_BRIDGE_PYTHON_BIN,
        help="Python executable used to append the validated simulator output.",
    )
    active_user_codex_simulator_contract_parser.add_argument(
        "--module",
        default=DEFAULT_WORKER_BRIDGE_MODULE,
        help="LoopX CLI module import path.",
    )
    active_user_codex_simulator_contract_parser.add_argument(
        "--codex-bin",
        default=DEFAULT_ACTIVE_USER_CODEX_BIN,
        help="Codex CLI executable used for the user simulator.",
    )
    active_user_codex_simulator_contract_parser.add_argument(
        "--context-dir",
        default=DEFAULT_ACTIVE_USER_SIMULATOR_CONTEXT_DIR,
        help="Public context directory made readable to the Codex CLI simulator.",
    )
    active_user_codex_simulator_contract_parser.add_argument(
        "--prompt-json",
        default=DEFAULT_ACTIVE_USER_SIMULATOR_PROMPT_JSON,
        help="Prompt/context JSON file passed to Codex CLI on stdin.",
    )
    active_user_codex_simulator_contract_parser.add_argument(
        "--simulator-output-json",
        default=DEFAULT_ACTIVE_USER_SIMULATOR_OUTPUT_JSON,
        help="Path where Codex CLI writes the simulator JSON output.",
    )
    active_user_codex_simulator_contract_parser.add_argument(
        "--simulator-output-schema-json",
        default=DEFAULT_ACTIVE_USER_SIMULATOR_OUTPUT_SCHEMA_JSON,
        help="JSON Schema file constraining the Codex CLI simulator response.",
    )
    active_user_codex_simulator_contract_parser.add_argument(
        "--feed-jsonl",
        default=DEFAULT_WORKER_BRIDGE_ACTIVE_USER_FEED_JSONL,
        help="Worker-visible active-user intervention feed JSONL path.",
    )

    active_user_intervention_parser = worker_bridge_sub.add_parser(
        "active-user-intervention",
        help="Render one public-safe active-user simulator intervention event.",
    )
    add_subcommand_format(active_user_intervention_parser)
    active_user_intervention_parser.add_argument("--seq", type=int, required=True)
    active_user_intervention_parser.add_argument("--message", required=True)
    active_user_intervention_parser.add_argument(
        "--trigger",
        default="public_progress_or_stall_signal",
        help="Public-safe intervention trigger label.",
    )
    active_user_intervention_parser.add_argument(
        "--channel",
        default="simulator_proactive_user_message",
        help="Public-safe intervention channel label.",
    )
    active_user_intervention_parser.add_argument(
        "--before-worker-start",
        action="store_true",
        help="Mark this intervention as created before the worker start marker.",
    )
    active_user_intervention_parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Print compact single-line JSON for appending to an intervention feed.",
    )

    active_user_simulator_output_parser = worker_bridge_sub.add_parser(
        "active-user-simulator-output",
        help="Validate a Codex CLI simulator JSON output and render feed JSON.",
    )
    add_subcommand_format(active_user_simulator_output_parser)
    active_user_simulator_output_parser.add_argument("--seq", type=int, required=True)
    active_user_simulator_output_parser.add_argument(
        "--simulator-output-json",
        required=True,
        help="Path to Codex CLI simulator JSON output, or '-' for stdin.",
    )
    active_user_simulator_output_parser.add_argument(
        "--before-worker-start",
        action="store_true",
        help="Mark the resulting intervention as created before the worker start marker.",
    )
    active_user_simulator_output_parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Print compact single-line JSON for appending to an intervention feed.",
    )

    active_user_observe_parser = worker_bridge_sub.add_parser(
        "active-user-observe",
        help="Observe active-user interventions created after the worker start marker.",
    )
    add_subcommand_format(active_user_observe_parser)
    active_user_observe_parser.add_argument(
        "--feed-jsonl",
        required=True,
        help="Active-user intervention feed JSONL path to read.",
    )
    active_user_observe_parser.add_argument(
        "--worker-start-seq",
        type=int,
        default=0,
        help="Worker start marker sequence; only later interventions are observable.",
    )
    active_user_observe_parser.add_argument(
        "--observation-json",
        help="Optional path to write the compact observation JSON.",
    )
    active_user_observe_parser.add_argument(
        "--counter-trace-json",
        help="Optional worker counter trace JSONL path to append active_user_observe.",
    )
    active_user_observe_parser.add_argument(
        "--goal-id",
        default="worker-bridge-active-user",
        help="Compact goal id label for optional counter/checkpoint writeback.",
    )
    active_user_observe_parser.add_argument(
        "--bridge-mode",
        default="codex_loopx_active_worker",
        help="Compact worker bridge mode label for optional counter/checkpoint writeback.",
    )
    active_user_observe_parser.add_argument(
        "--classification",
        default="active_user_observe_checkpoint",
        help="Compact classification label for optional counter/checkpoint writeback.",
    )

    attached_bind_parser = worker_bridge_sub.add_parser(
        "attached-session-bind",
        help="Bind an already-running host session to one registered LoopX Agent.",
    )
    add_subcommand_format(attached_bind_parser)
    attached_bind_parser.add_argument("--goal-id", required=True)
    attached_bind_parser.add_argument("--agent-id", required=True)
    attached_bind_parser.add_argument("--host-surface", required=True)
    attached_bind_parser.add_argument("--host-session-id", required=True)
    attached_bind_parser.add_argument("--executor-endpoint-id", required=True)
    attached_bind_parser.add_argument("--channel-id")
    attached_bind_parser.add_argument("--execute", action="store_true")

    attached_list_parser = worker_bridge_sub.add_parser(
        "attached-session-list",
        help="List content-free attached Session descriptors.",
    )
    add_subcommand_format(attached_list_parser)
    attached_list_parser.add_argument("--goal-id")
    attached_list_parser.add_argument("--agent-id")

    attached_claim_parser = worker_bridge_sub.add_parser(
        "attached-session-claim",
        help="Claim the oldest queued Web or Connector message for an attached host.",
    )
    add_subcommand_format(attached_claim_parser)
    attached_claim_parser.add_argument("--session-id", required=True)
    attached_claim_parser.add_argument("--host-surface", required=True)
    attached_claim_parser.add_argument("--host-session-id", required=True)
    attached_claim_parser.add_argument("--claim-id", required=True)
    attached_claim_parser.add_argument(
        "--wait-seconds",
        type=float,
        default=0.0,
        help=(
            "Wait up to this many seconds for one queued message before returning. "
            "The bounded host subscription is capped at 1800 seconds."
        ),
    )

    attached_complete_parser = worker_bridge_sub.add_parser(
        "attached-session-complete",
        help="Write back one duplicate-safe attached-host response.",
    )
    add_subcommand_format(attached_complete_parser)
    attached_complete_parser.add_argument("--session-id", required=True)
    attached_complete_parser.add_argument("--turn-id", required=True)
    attached_complete_parser.add_argument("--host-surface", required=True)
    attached_complete_parser.add_argument("--host-session-id", required=True)
    attached_complete_parser.add_argument("--claim-id", required=True)
    attached_complete_parser.add_argument("--completion-id", required=True)
    attached_complete_parser.add_argument(
        "--response-json",
        required=True,
        help="Owner-local response JSON path, or '-' for stdin.",
    )


def handle_worker_bridge_command(
    args: argparse.Namespace,
    *,
    print_payload: PrintPayload,
    output_format: OutputFormat,
    registry_path: Path | None = None,
) -> int | None:
    if args.command != "worker-bridge":
        return None

    if args.worker_bridge_command not in WORKER_BRIDGE_COMMANDS:
        payload = {
            "ok": False,
            "mode": "worker-bridge",
            "error": (
                "worker-bridge requires a subcommand; use `contract`, "
                "`active-user-contract`, "
                "`active-user-codex-simulator-contract`, "
                "`active-user-intervention`, `active-user-simulator-output`, "
                "`active-user-observe`, or one of the `attached-session-*` commands."
            ),
        }
        print_payload(
            payload,
            output_format(args),
            render_worker_bridge_install_contract_markdown,
        )
        return 1

    renderer = render_worker_bridge_install_contract_markdown
    try:
        if args.worker_bridge_command.startswith("attached-session-"):
            effective_registry_path = registry_path or Path(args.registry)
            registry = load_attached_session_registry(effective_registry_path)
            runtime_root = resolve_runtime_root(
                registry,
                args.runtime_root,
                registry_path=effective_registry_path,
            )
            store = ChatSessionStore(runtime_root)
            renderer = render_attached_session_broker_markdown
            if args.worker_bridge_command == "attached-session-bind":
                payload = bind_attached_agent_session(
                    store=store,
                    registry=registry,
                    registry_path=effective_registry_path,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    host_surface=args.host_surface,
                    host_session_id=args.host_session_id,
                    executor_endpoint_id=args.executor_endpoint_id,
                    channel_id=args.channel_id,
                    execute=bool(args.execute),
                )
            elif args.worker_bridge_command == "attached-session-list":
                payload = {
                    "ok": True,
                    "schema_version": "loopx_attached_agent_session_broker_v0",
                    "action": "list",
                    "sessions": [
                        session
                        for session in store.list_sessions(
                            goal_id=args.goal_id,
                            agent_id=args.agent_id,
                        )
                        if session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED
                    ],
                }
            elif args.worker_bridge_command == "attached-session-claim":
                payload = claim_attached_agent_turn(
                    store=store,
                    registry_path=effective_registry_path,
                    session_id=args.session_id,
                    host_surface=args.host_surface,
                    host_session_id=args.host_session_id,
                    claim_id=args.claim_id,
                    wait_seconds=args.wait_seconds,
                )
            else:
                if args.response_json == "-":
                    response = json.loads(sys.stdin.read())
                else:
                    response = json.loads(
                        Path(args.response_json).expanduser().read_text(encoding="utf-8")
                    )
                if not isinstance(response, dict):
                    raise ValueError("response JSON must be an object")
                payload = complete_attached_agent_turn(
                    store=store,
                    registry_path=effective_registry_path,
                    session_id=args.session_id,
                    turn_id=args.turn_id,
                    host_surface=args.host_surface,
                    host_session_id=args.host_session_id,
                    claim_id=args.claim_id,
                    completion_id=args.completion_id,
                    response=response,
                )
        elif args.worker_bridge_command == "contract":
            payload = build_worker_bridge_install_contract(
                project_root=args.project_root,
                runtime_root=args.worker_bridge_runtime_root,
                python_bin=args.python_bin,
                module=args.module,
                scan_path=args.scan_path,
                counter_trace_json=args.counter_trace_json,
                classification=args.classification,
            )
        elif args.worker_bridge_command == "active-user-contract":
            payload = build_active_user_intervention_channel_contract(
                project_root=args.project_root,
                runtime_root=args.active_user_runtime_root,
                python_bin=args.python_bin,
                module=args.module,
                feed_jsonl=args.feed_jsonl,
                observation_json=args.observation_json,
                counter_trace_json=args.counter_trace_json,
                classification=args.classification,
                min_interval_seconds=args.min_interval_seconds,
                max_interventions_per_task=args.max_interventions_per_task,
            )
        elif args.worker_bridge_command == "active-user-codex-simulator-contract":
            payload = build_active_user_codex_simulator_contract(
                project_root=args.project_root,
                python_bin=args.python_bin,
                module=args.module,
                codex_bin=args.codex_bin,
                context_dir=args.context_dir,
                prompt_json=args.prompt_json,
                simulator_output_json=args.simulator_output_json,
                simulator_output_schema_json=args.simulator_output_schema_json,
                feed_jsonl=args.feed_jsonl,
            )
        elif args.worker_bridge_command == "active-user-intervention":
            payload = build_active_user_intervention(
                seq=args.seq,
                message=args.message,
                trigger=args.trigger,
                channel=args.channel,
                created_after_worker_start=not bool(args.before_worker_start),
            )
            if args.jsonl:
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                return 0
        elif args.worker_bridge_command == "active-user-simulator-output":
            if args.simulator_output_json == "-":
                simulator_output = json.loads(sys.stdin.read())
            else:
                simulator_output = json.loads(
                    Path(args.simulator_output_json)
                    .expanduser()
                    .read_text(encoding="utf-8")
                )
            payload = build_active_user_intervention_from_simulator_output(
                seq=args.seq,
                simulator_output=simulator_output,
                created_after_worker_start=not bool(args.before_worker_start),
            )
            if args.jsonl:
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                return 0
        else:
            payload = observe_active_user_intervention_feed(
                args.feed_jsonl,
                worker_start_seq=args.worker_start_seq,
            )
            if args.observation_json:
                payload["observation_written"] = write_active_user_observation_file(
                    args.observation_json,
                    payload,
                )
            if args.counter_trace_json:
                payload["counter_trace_written"] = (
                    append_worker_bridge_counter_trace_row(
                        args.counter_trace_json,
                        command="active_user_observe",
                        ok=bool(payload.get("ok")),
                        goal_id=args.goal_id,
                        mode=args.bridge_mode,
                        classification=args.classification,
                        observed_after_worker_start=payload.get(
                            "observed_after_worker_start"
                        ),
                        worker_observation_proof=payload.get(
                            "worker_observation_proof"
                        ),
                    )
                )
    except Exception as exc:
        payload = {
            "ok": False,
            "mode": "worker-bridge",
            "error": str(exc),
        }
    print_payload(
        payload,
        output_format(args),
        renderer,
    )
    return 0 if payload.get("ok") else 1
