from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from ...extensions.runtime import (
    execute_extension_runtime_binding,
)
from ...history import load_registry
from ...paths import resolve_runtime_root
from ...rollout_event_log import iter_rollout_events
from .core import TRIGGER_DECISION_SCHEMA, build_periodic_report_run
from .extension_envelope import build_openviking_archive_execution_envelope
from .machine_defaults import (
    SUBSCRIPTION_ERROR_SCHEMA,
    PeriodicReportSubscriptionConfigurationError,
    build_goal_periodic_report_delivery_plan,
)
from .machine_store import (
    configure_periodic_report_machine_defaults,
    inspect_periodic_report_machine_defaults,
    read_periodic_report_machine_defaults,
    rollback_periodic_report_machine_defaults,
)
from .pending_intent import consume_pending_periodic_report_intent
from .presets import (
    PERIODIC_REPORT_PROFILE_PRESET_ALIASES,
    build_periodic_report_preset_activation,
)
from .profile import build_periodic_report_activation
from .request_action import (
    discover_periodic_report_request_ports,
    record_periodic_report_request,
)
from .runtime_producer import build_periodic_report_runtime_trigger_decision
from .triggers import build_periodic_report_trigger_decision


MAX_PERIODIC_REPORT_INPUT_BYTES = 16 * 1024 * 1024

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]


class ProviderCommandRegistrar(Protocol):
    def __call__(
        self,
        subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
        add_subcommand_format: AddFormat,
    ) -> None: ...


ProviderCommandHandler = Callable[..., int | None]


def _load_json_object(path_text: str) -> dict[str, Any]:
    if path_text == "-":
        binary_stdin = getattr(sys.stdin, "buffer", None)
        raw = (
            binary_stdin.read(MAX_PERIODIC_REPORT_INPUT_BYTES + 1)
            if binary_stdin is not None
            else sys.stdin.read(MAX_PERIODIC_REPORT_INPUT_BYTES + 1).encode("utf-8")
        )
    else:
        with Path(path_text).expanduser().open("rb") as input_file:
            raw = input_file.read(MAX_PERIODIC_REPORT_INPUT_BYTES + 1)
    if len(raw) > MAX_PERIODIC_REPORT_INPUT_BYTES:
        raise ValueError(
            "periodic report input exceeds the 16777216-byte limit"
        )
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{path_text} must contain a JSON object")
    return payload


def register_periodic_report_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: AddFormat,
    *,
    provider_command_registrars: Sequence[ProviderCommandRegistrar] = (),
) -> None:
    parser = subparsers.add_parser(
        "periodic-report",
        help="Compose provider-neutral periodic report run receipts.",
    )
    commands = parser.add_subparsers(dest="periodic_report_command", required=True)
    compose = commands.add_parser(
        "compose-run",
        help="Normalize one periodic_report_v0 attempt without provider effects.",
    )
    add_subcommand_format(compose)
    compose.add_argument(
        "--request-json",
        required=True,
        help="Path to periodic_report_run_request_v0 JSON; use '-' for stdin.",
    )
    evaluate = commands.add_parser(
        "evaluate-trigger",
        help="Evaluate cadence and material progress triggers without effects.",
    )
    add_subcommand_format(evaluate)
    evaluate.add_argument(
        "--request-json",
        required=True,
        help="Path to periodic_report_trigger_request_v0 JSON; use '-' for stdin.",
    )
    evaluate_runtime = commands.add_parser(
        "evaluate-runtime-trigger",
        help="Promote durable rollout events into a periodic-report trigger.",
    )
    add_subcommand_format(evaluate_runtime)
    evaluate_runtime.add_argument(
        "--request-json",
        required=True,
        help="Path to periodic_report_runtime_trigger_request_v0 JSON.",
    )
    request = commands.add_parser(
        "request",
        help=(
            "Record an Agent-authorized typed report request for one exact "
            "provider source item."
        ),
    )
    add_subcommand_format(request)
    request.add_argument("--goal-id", required=True)
    request.add_argument("--agent-id", required=True)
    request.add_argument("--source-ref", required=True)
    request.add_argument(
        "--source-adapter-id",
        help=(
            "Select the manifest-discovered source adapter. Required when more "
            "than one complete adapter is active."
        ),
    )
    request.add_argument("--execute", action="store_true")
    request.add_argument(
        "--extension-state-file",
        help="Override local extension activation state for this action.",
    )
    consume_pending = commands.add_parser(
        "consume-pending",
        help=(
            "Render one pending durable intent to frozen local artifacts and queue "
            "its configured Goal Channel delivery under the active subscription."
        ),
    )
    add_subcommand_format(consume_pending)
    consume_pending.add_argument("--goal-id", required=True)
    consume_pending.add_argument("--agent-id", required=True)
    consume_pending.add_argument("--execute", action="store_true")
    consume_pending.add_argument(
        "--extension-state-file",
        help="Override local extension activation state for source settlement.",
    )
    configure_machine_defaults = commands.add_parser(
        "configure-machine-defaults",
        help="Preview or apply the runtime-root machine periodic-report policy.",
    )
    add_subcommand_format(configure_machine_defaults)
    configure_machine_defaults.add_argument(
        "--config-json",
        required=True,
        help="Path to loopx_machine_configuration_v0 JSON; use '-' for stdin.",
    )
    configure_machine_defaults.add_argument("--execute", action="store_true")
    configure_machine_defaults.add_argument(
        "--expected-plan-revision",
        help="Exact plan_revision returned by the preceding configuration preview.",
    )
    inspect_machine_defaults = commands.add_parser(
        "inspect-machine-defaults",
        help="Read back the runtime-root machine periodic-report policy.",
    )
    add_subcommand_format(inspect_machine_defaults)
    rollback_machine_defaults = commands.add_parser(
        "rollback-machine-defaults",
        help="Preview or execute rollback of one exact machine-policy transaction.",
    )
    add_subcommand_format(rollback_machine_defaults)
    rollback_machine_defaults.add_argument("--transaction-id", required=True)
    rollback_machine_defaults.add_argument("--execute", action="store_true")
    rollback_machine_defaults.add_argument(
        "--expected-plan-revision",
        help="Exact plan_revision returned by the preceding rollback preview.",
    )
    plan_goal_delivery = commands.add_parser(
        "plan-goal-delivery",
        help=(
            "Preview delivery from a Goal override or the current machine default, "
            "then select the preferred Agent executor."
        ),
    )
    add_subcommand_format(plan_goal_delivery)
    plan_goal_delivery.add_argument(
        "--request-json",
        required=True,
        help="Path to periodic_report_goal_delivery_plan_request_v0 JSON.",
    )
    evaluate_runtime.add_argument(
        "--rollout-events-jsonl",
        required=True,
        help="Durable LoopX rollout-event-log.jsonl to evaluate.",
    )
    inspect_profile = commands.add_parser(
        "inspect-profile",
        help="Inspect a built-in preset or project profile without provider effects.",
    )
    add_subcommand_format(inspect_profile)
    profile_source = inspect_profile.add_mutually_exclusive_group(required=True)
    profile_source.add_argument(
        "--profile-json",
        help="Path to periodic_report_profile_v0 JSON; use '-' for stdin.",
    )
    profile_source.add_argument(
        "--preset",
        choices=sorted(PERIODIC_REPORT_PROFILE_PRESET_ALIASES),
        help="Built-in profile preset or short alias, such as 'weekly'.",
    )
    archive = commands.add_parser(
        "archive-openviking",
        help=(
            "Invoke the optional doctor-ready OpenViking archive extension after "
            "checking capability activation and runtime write authority."
        ),
    )
    add_subcommand_format(archive)
    archive.add_argument(
        "--request-json",
        required=True,
        help="Path to openviking_periodic_report_archive_request_v0 JSON.",
    )
    archive.add_argument("--runtime-root")
    archive.add_argument(
        "--available-capability",
        action="append",
        default=[],
        help="Observed runtime capability; repeat for multiple values.",
    )
    archive.add_argument("--openviking-url")
    archive.add_argument("--openviking-path")
    archive.add_argument("--openviking-config")
    archive.add_argument("--openviking-actor-peer-id")
    archive.add_argument(
        "--openviking-api-key-env",
        default="OPENVIKING_API_KEY",
        help="Environment variable containing the API key; never pass the key itself.",
    )
    archive.add_argument("--execute", action="store_true")
    for register_provider_commands in provider_command_registrars:
        register_provider_commands(commands, add_subcommand_format)


def _archive_openviking(
    request: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    from ...extensions.openviking_periodic_report.activation import (
        resolve_openviking_periodic_report_activation,
    )
    from ...extensions.openviking_periodic_report.provider import REQUEST_SCHEMA

    if request.get("schema_version") != REQUEST_SCHEMA:
        raise ValueError(f"request must use {REQUEST_SCHEMA}")
    if "execution_envelope" in request:
        raise ValueError("execution_envelope is created by the capability command")
    context = request.get("context")
    if not isinstance(context, dict):
        raise ValueError("request.context must be an object")
    activation_receipt = request.get("activation_receipt")
    if not isinstance(activation_receipt, dict):
        raise ValueError("request.activation_receipt must be an object")
    resolved = resolve_openviking_periodic_report_activation(
        activation_receipt,
        available_capabilities=args.available_capability,
        sink_id=str(context.get("sink_id") or ""),
        runtime_root=args.runtime_root,
    )
    binding = resolved["runtime_binding"]
    argv = [str(item) for item in binding["argv"]]
    if args.openviking_url:
        argv.extend(["--url", args.openviking_url])
    if args.openviking_path:
        argv.extend(["--path", args.openviking_path])
    if args.openviking_config:
        argv.extend(["--config", args.openviking_config])
    if args.openviking_actor_peer_id:
        argv.extend(["--actor-peer-id", args.openviking_actor_peer_id])
    if args.openviking_api_key_env:
        argv.extend(["--api-key-env", args.openviking_api_key_env])
    provider_request = {
        **request,
        "execute": args.execute,
    }
    provider_request.pop("available_capabilities", None)
    if args.execute:
        provider_request["execution_envelope"] = (
            build_openviking_archive_execution_envelope(
                provider_request,
                extension_revision=str(binding["revision"]),
            )
        )
    provider_env = dict(os.environ)
    provider_env.update(
        {
            "LOOPX_EXTENSION_ID": str(binding["extension_id"]),
            "LOOPX_EXTENSION_REVISION": str(binding["revision"]),
            "LOOPX_EXTENSION_PROTOCOL": str(binding["protocol"]),
        }
    )
    response = execute_extension_runtime_binding(
        {**binding, "argv": argv},
        request=provider_request,
        environment=provider_env,
    )
    return {
        **response,
        "extension_receipt": resolved["extension_receipt"],
    }


def render_periodic_report_markdown(payload: dict[str, object]) -> str:
    if not payload.get("ok"):
        if payload.get("schema_version") == SUBSCRIPTION_ERROR_SCHEMA:
            invalid_fields = payload.get("invalid_fields")
            normalized_invalid_fields = (
                [str(field) for field in invalid_fields]
                if isinstance(invalid_fields, list)
                else []
            )
            rendered_invalid_fields = ", ".join(
                f"`{field}`" for field in normalized_invalid_fields
            )
            return "\n".join(
                [
                    "# Periodic Report Configuration Error",
                    "",
                    f"- error: {payload.get('error')}",
                    f"- configuration_source: `{payload.get('configuration_source')}`",
                    f"- invalid_fields: {rendered_invalid_fields}",
                    f"- mutation_performed: `{payload.get('mutation_performed')}`",
                    "",
                    "## Remediation",
                    "",
                    str(payload.get("remediation") or ""),
                    "",
                ]
            )
        return f"# Periodic Report Error\n\n- error: {payload.get('error')}\n"
    if payload.get("schema_version") == "periodic_report_activation_v0":
        profile = payload.get("profile")
        normalized_profile = profile if isinstance(profile, dict) else {}
        return "\n".join(
            [
                f"# Periodic Report Profile `{normalized_profile.get('profile_id')}`",
                "",
                f"- status: `{payload.get('status')}`",
                f"- active: `{payload.get('active')}`",
                f"- extension_mode: `{payload.get('extension_mode')}`",
                "",
            ]
        )
    if payload.get("schema_version") == TRIGGER_DECISION_SCHEMA:
        return "\n".join(
            [
                f"# Periodic Report Trigger `{payload.get('decision_id')}`",
                "",
                f"- eligible: `{payload.get('eligible')}`",
                f"- reason: `{payload.get('reason')}`",
                f"- report_kind: `{payload.get('report_kind')}`",
                f"- report_key: `{payload.get('report_key')}`",
                "",
            ]
        )
    if payload.get("schema_version") == "periodic_report_sink_result_v0":
        return "\n".join(
            [
                f"# Periodic Report Archive `{payload.get('archive_id')}`",
                "",
                f"- status: `{payload.get('status')}`",
                f"- receipt_ref: `{payload.get('receipt_ref')}`",
                f"- result_id: `{payload.get('result_id')}`",
                f"- readback_verified: `{payload.get('readback_verified')}`",
                "",
            ]
        )
    if payload.get("schema_version") == "periodic_report_miaoda_delivery_result_v0":
        sink = payload.get("sink_result")
        normalized_sink = sink if isinstance(sink, dict) else {}
        return "\n".join(
            [
                "# Periodic Report Miaoda Delivery",
                "",
                f"- status: `{payload.get('status')}`",
                f"- intent_satisfied: `{payload.get('intent_satisfied')}`",
                f"- sink_status: `{normalized_sink.get('status')}`",
                f"- readback_verified: `{normalized_sink.get('readback_verified')}`",
                "",
            ]
        )
    if payload.get("schema_version") in {
        "machine_configuration_update_plan_v0",
        "machine_configuration_transaction_v0",
        "machine_configuration_inspection_v0",
        "machine_configuration_rollback_plan_v0",
        "machine_configuration_rollback_receipt_v0",
        "periodic_report_goal_delivery_plan_v0",
    }:
        machine_configuration = str(payload.get("schema_version") or "").startswith(
            "machine_configuration_"
        )
        return "\n".join(
            [
                (
                    "# Machine Configuration"
                    if machine_configuration
                    else "# Periodic Report Goal Plan"
                ),
                "",
                f"- schema: `{payload.get('schema_version')}`",
                f"- status: `{payload.get('status', 'preview')}`",
                f"- action: `{payload.get('action', 'none')}`",
                f"- writes_required: `{payload.get('writes_required', 0)}`",
                *(
                    [f"- plan_revision: `{payload.get('plan_revision')}`"]
                    if payload.get("plan_revision")
                    else []
                ),
                "",
            ]
        )
    run_state = payload.get("run_state")
    retry = payload.get("retry")
    state = run_state if isinstance(run_state, dict) else {}
    retry_info = retry if isinstance(retry, dict) else {}
    return "\n".join(
        [
            f"# Periodic Report `{payload.get('run_id')}`",
            "",
            f"- schema: `{payload.get('schema_version')}`",
            f"- status: `{state.get('status')}`",
            f"- idempotency_key: `{payload.get('idempotency_key')}`",
            f"- retry_allowed: `{retry_info.get('allowed')}`",
            "",
        ]
    )


def handle_periodic_report_command(
    args: argparse.Namespace,
    *,
    runtime_root_arg: str | None,
    registry_path: Path,
    output_format: FormatSelector,
    print_payload: PrintPayload,
    provider_command_handlers: Sequence[ProviderCommandHandler] = (),
) -> int | None:
    if args.command != "periodic-report":
        return None
    for handle_provider_command in provider_command_handlers:
        result = handle_provider_command(
            args,
            runtime_root_arg=runtime_root_arg,
            registry_path=registry_path,
            output_format=output_format,
            print_payload=print_payload,
        )
        if result is not None:
            return result
    try:
        if args.periodic_report_command == "archive-openviking":
            payload = _archive_openviking(
                _load_json_object(args.request_json),
                args,
            )
        elif args.periodic_report_command == "inspect-profile":
            payload = (
                build_periodic_report_preset_activation(args.preset)
                if args.preset
                else build_periodic_report_activation(
                    _load_json_object(args.profile_json)
                )
            )
        elif args.periodic_report_command == "evaluate-trigger":
            request = _load_json_object(args.request_json)
            payload = build_periodic_report_trigger_decision(request)
        elif args.periodic_report_command == "evaluate-runtime-trigger":
            request = _load_json_object(args.request_json)
            payload = build_periodic_report_runtime_trigger_decision(
                request,
                rollout_events=iter_rollout_events(
                    Path(args.rollout_events_jsonl).expanduser(),
                    strict=True,
                ),
            )
        elif args.periodic_report_command == "request":
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(
                registry, runtime_root_arg, registry_path=registry_path
            )
            ports = discover_periodic_report_request_ports(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                extension_state_file=(
                    Path(args.extension_state_file).expanduser()
                    if args.extension_state_file
                    else None
                ),
            )
            payload = record_periodic_report_request(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                source_ref=args.source_ref,
                request_ports=ports,
                source_adapter_id=args.source_adapter_id,
                execute=bool(args.execute),
            )
        elif args.periodic_report_command == "consume-pending":
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(
                registry, runtime_root_arg, registry_path=registry_path
            )
            ports = discover_periodic_report_request_ports(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                extension_state_file=(
                    Path(args.extension_state_file).expanduser()
                    if args.extension_state_file
                    else None
                ),
            )
            payload = consume_pending_periodic_report_intent(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                execute=bool(args.execute),
                provider_request_ports=ports,
            )
        elif args.periodic_report_command in {
            "configure-machine-defaults",
            "inspect-machine-defaults",
            "rollback-machine-defaults",
        }:
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(
                registry, runtime_root_arg, registry_path=registry_path
            )
            if args.periodic_report_command == "configure-machine-defaults":
                payload = configure_periodic_report_machine_defaults(
                    runtime_root=runtime_root,
                    machine_defaults=_load_json_object(args.config_json),
                    execute=bool(args.execute),
                    expected_plan_revision=args.expected_plan_revision,
                )
            elif args.periodic_report_command == "inspect-machine-defaults":
                payload = inspect_periodic_report_machine_defaults(runtime_root)
            else:
                payload = rollback_periodic_report_machine_defaults(
                    runtime_root=runtime_root,
                    transaction_id=args.transaction_id,
                    execute=bool(args.execute),
                    expected_plan_revision=args.expected_plan_revision,
                )
        elif args.periodic_report_command == "plan-goal-delivery":
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(
                registry, runtime_root_arg, registry_path=registry_path
            )
            payload = build_goal_periodic_report_delivery_plan(
                _load_json_object(args.request_json),
                machine_defaults=read_periodic_report_machine_defaults(runtime_root),
            )
        else:
            request = _load_json_object(args.request_json)
            payload = build_periodic_report_run(request)
    except PeriodicReportSubscriptionConfigurationError as exc:
        payload = {
            "ok": False,
            "schema_version": SUBSCRIPTION_ERROR_SCHEMA,
            "command": args.periodic_report_command,
            "error_kind": "subscription_configuration_invalid",
            "goal_id": exc.goal_id,
            "configuration_source": exc.configuration_source,
            "invalid_fields": list(exc.invalid_fields),
            "error": str(exc),
            "remediation": exc.remediation,
            "mutation_performed": False,
        }
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": "periodic_report_error_v0",
            "command": args.periodic_report_command,
            "error": str(exc),
        }
    print_payload(payload, output_format(args), render_periodic_report_markdown)
    return 0 if payload.get("ok") else 1
