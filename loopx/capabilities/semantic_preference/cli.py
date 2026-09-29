from __future__ import annotations

import argparse
from collections.abc import Callable
import importlib
from pathlib import Path

from ...control_plane.effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError

from .contract import (
    application_receipt,
    maintenance_receipt,
    provider_doctor,
    recall,
)


_LEGACY_OPENVIKING_PROVIDER_MODULE = (
    "loopx.extensions.openviking_semantic_preference.provider"
)


def _register_legacy_openviking_provider_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    # Keep the compatibility parser provider-free; parity is regression-tested.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--doctor", action="store_true")
    mode.add_argument("--describe-scope", action="store_true")
    parser.add_argument("--ov-bin", default="ov")
    parser.add_argument("--cli-config")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--user-space", default="default")
    parser.add_argument("--loopx-project-id")
    parser.add_argument("--remote-url")
    parser.add_argument("--include-global-fallback", action="store_true")
    parser.add_argument("--max-find-calls", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=25)


def _render(payload: dict[str, object]) -> str:
    lines = ["# Semantic Preference", ""]
    for key in (
        "status",
        "surface",
        "outcome",
        "application_id",
        "provider_id",
        "available",
        "verified",
        "error",
    ):
        if key in payload:
            lines.append(f"- {key}: `{payload.get(key)}`")
    items = payload.get("items")
    if isinstance(items, list):
        lines.append(f"- item_count: `{len(items)}`")
    return "\n".join(lines) + "\n"


def register_semantic_preference_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "semantic-preference",
        help="Recall optional provider-owned preferences and build compact receipts.",
    )
    commands = parser.add_subparsers(dest="semantic_preference_command", required=True)
    from .agent_preferences import register_agent_preference_commands
    register_agent_preference_commands(commands, add_subcommand_format)
    recall_parser = commands.add_parser("recall")
    add_subcommand_format(recall_parser)
    recall_parser.add_argument("--config", required=True)
    recall_parser.add_argument("--project", default=".")
    recall_parser.add_argument("--surface", required=True)
    recall_parser.add_argument(
        "--context",
        action="append",
        default=[],
        metavar="LOWER_SNAKE=VALUE",
        help="Repeatable bounded context entry, for example repository=owner/repo.",
    )
    recall_parser.add_argument("--execute", action="store_true")

    doctor_parser = commands.add_parser(
        "doctor",
        help="Inspect provider availability and configured setup hints without changing the host.",
    )
    add_subcommand_format(doctor_parser)
    doctor_parser.add_argument("--config", required=True)
    doctor_parser.add_argument("--project", default=".")
    doctor_parser.add_argument(
        "--execute",
        action="store_true",
        help="Run the optional read-only provider probe; never install, configure, or write credentials.",
    )

    provider_parser = commands.add_parser(
        "openviking-provider",
        help="Delegate to the legacy OpenViking provider CLI when invoked.",
    )
    _register_legacy_openviking_provider_arguments(provider_parser)

    receipt_parser = commands.add_parser("receipt")
    add_subcommand_format(receipt_parser)
    receipt_parser.add_argument("--surface", required=True)
    receipt_parser.add_argument("--application-id", required=True)
    receipt_parser.add_argument(
        "--outcome", choices=["applied", "ignored", "failed"], required=True
    )
    receipt_parser.add_argument("--preference-ref", action="append", default=[])
    receipt_parser.add_argument("--artifact-ref")

    maintenance_parser = commands.add_parser(
        "maintenance-receipt",
        help=(
            "Build a compact receipt after a provider-owned corpus "
            "maintenance decision."
        ),
    )
    add_subcommand_format(maintenance_parser)
    maintenance_parser.add_argument(
        "--trigger",
        choices=["explicit_feedback", "source_truth_changed"],
        required=True,
    )
    maintenance_parser.add_argument(
        "--outcome",
        choices=["verified", "no_write_rationale", "failed"],
        required=True,
    )
    maintenance_parser.add_argument("--corpus-id", action="append", default=[])
    maintenance_parser.add_argument("--scope-ref", action="append", default=[])
    maintenance_parser.add_argument("--evidence-ref")


def handle_semantic_preference_command(
    args: argparse.Namespace,
    *,
    runtime_root_arg: str | None,
    registry_path: Path,
    output_format: Callable[..., str],
    print_payload: Callable[[dict[str, object], str, Callable], None],
) -> int | None:
    if args.command != "semantic-preference":
        return None
    if args.semantic_preference_command == "openviking-provider":
        provider = importlib.import_module(_LEGACY_OPENVIKING_PROVIDER_MODULE)
        return provider.handle_openviking_provider(args)
    try:
        if args.semantic_preference_command == "agent":
            from .agent_preferences import handle_agent_preferences, render_agent_preferences
            payload = handle_agent_preferences(args, registry_path=registry_path, runtime_root_arg=runtime_root_arg)
            print_payload(payload, output_format(args), render_agent_preferences)
            return 0 if payload.get("ok") else 2
        if args.semantic_preference_command == "recall":
            payload = recall(
                args.config,
                project=args.project,
                surface=args.surface,
                context=args.context,
                runtime_root=runtime_root_arg,
                execute=args.execute,
            )
        elif args.semantic_preference_command == "doctor":
            payload = provider_doctor(
                args.config,
                project=args.project,
                runtime_root=runtime_root_arg,
                execute=args.execute,
            )
        elif args.semantic_preference_command == "receipt":
            payload = application_receipt(
                surface=args.surface,
                application_id=args.application_id,
                outcome=args.outcome,
                preference_refs=args.preference_ref,
                artifact_ref=args.artifact_ref,
            )
        else:
            payload = maintenance_receipt(
                trigger=args.trigger,
                outcome=args.outcome,
                corpus_ids=args.corpus_id,
                scope_refs=args.scope_ref,
                evidence_ref=args.evidence_ref,
            )
    except (ValueError, TypeError, OSError, EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
        payload = {
            "ok": False,
            "schema_version": "semantic_preference_error_v0",
            "status": "invalid_request",
            "error": str(exc),
        }
        print_payload(payload, output_format(args), _render)
        return 2
    print_payload(payload, output_format(args), _render)
    return 0
