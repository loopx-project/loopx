from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ...history import load_registry
from ...paths import resolve_runtime_root
from .receipt import (
    build_change_quality_prepare_packet,
    record_change_quality_receipt,
    verify_change_quality_receipt,
)

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]


def _add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--goal-id", required=True, help="Goal whose managed policy applies."
    )
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=Path("."),
        help="Git worktree to qualify. Defaults to the current directory.",
    )
    parser.add_argument(
        "--base-ref",
        default="origin/main",
        help="Git base ref for committed diff identity. Defaults to origin/main.",
    )


def register_change_quality_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> None:
    parser = subparsers.add_parser(
        "change-quality",
        help="Prepare, record, and verify exact-scope change-quality receipts.",
    )
    commands = parser.add_subparsers(dest="change_quality_command", required=True)
    prepare = commands.add_parser(
        "prepare",
        help="Build a provider-neutral review packet for the exact current diff.",
    )
    add_subcommand_format(prepare)
    _add_scope_arguments(prepare)
    record = commands.add_parser(
        "record",
        help="Validate an agent result against the exact current diff and store its receipt.",
    )
    add_subcommand_format(record)
    _add_scope_arguments(record)
    record.add_argument(
        "--result-json",
        type=Path,
        required=True,
        help="Agent result conforming to change_quality_agent_result_v2.",
    )
    record.add_argument(
        "--execute",
        action="store_true",
        help="Write the exact-scope receipt under the LoopX runtime.",
    )
    verify = commands.add_parser(
        "verify",
        help="Verify the current exact diff against goal policy and stored receipt.",
    )
    add_subcommand_format(verify)
    _add_scope_arguments(verify)


def render_change_quality_markdown(payload: dict[str, object]) -> str:
    receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else {}
    policy_value = payload.get("policy") or receipt.get("policy")
    scope_value = payload.get("scope") or receipt.get("scope")
    policy = policy_value if isinstance(policy_value, dict) else {}
    scope = scope_value if isinstance(scope_value, dict) else {}
    pr_evidence_value = payload.get("pr_evidence")
    pr_evidence = pr_evidence_value if isinstance(pr_evidence_value, dict) else {}
    lines = [
        "# Change Quality Qualification",
        "",
        f"- ok: `{str(payload.get('ok')).lower()}`",
        f"- status: `{payload.get('status') or payload.get('decision')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- enabled: `{str(policy.get('enabled')).lower()}`",
        f"- safe_fix: `{str(policy.get('safe_fix')).lower()}`",
        f"- strict_receipt: `{str(policy.get('strict_receipt')).lower()}`",
        f"- scope_fingerprint: `{scope.get('scope_fingerprint') or payload.get('scope_fingerprint')}`",
        f"- changed_files: `{scope.get('changed_file_count')}`",
    ]
    if payload.get("receipt_id"):
        lines.append(f"- receipt_id: `{payload.get('receipt_id')}`")
    if pr_evidence:
        lines.extend(
            [
                f"- receipt_state: `{pr_evidence.get('state')}`",
                (
                    "- requalification_required: "
                    f"`{str(pr_evidence.get('requalification_required')).lower()}`"
                ),
                f"- summary: {pr_evidence.get('summary')}",
            ]
        )
        if pr_evidence.get("previous_receipt_id"):
            lines.append(
                f"- previous_receipt_id: `{pr_evidence.get('previous_receipt_id')}`"
            )
    if payload.get("required_action"):
        lines.append(f"- required_action: {payload.get('required_action')}")
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
    return "\n".join(lines).rstrip() + "\n"


def handle_change_quality_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "change-quality":
        return None
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(
        registry,
        runtime_root_arg,
        registry_path=registry_path,
    )
    common = {
        "registry_path": registry_path,
        "goal_id": str(args.goal_id),
        "repo_path": Path(args.repo_path),
        "base_ref": str(args.base_ref),
    }
    try:
        if args.change_quality_command == "prepare":
            payload = build_change_quality_prepare_packet(
                **common,
                runtime_root=runtime_root,
            )
        elif args.change_quality_command == "record":
            payload = record_change_quality_receipt(
                **common,
                runtime_root=runtime_root,
                result_path=Path(args.result_json),
                execute=bool(args.execute),
            )
        elif args.change_quality_command == "verify":
            payload = verify_change_quality_receipt(
                **common,
                runtime_root=runtime_root,
            )
        else:
            raise ValueError("change-quality requires prepare, record, or verify")
    except (OSError, ValueError, TypeError) as exc:
        payload = {
            "ok": False,
            "schema_version": "change_quality_error_v0",
            "goal_id": str(args.goal_id),
            "status": "error",
            "error": str(exc),
        }
    print_payload(payload, output_format(args), render_change_quality_markdown)
    return 0 if payload.get("ok") else 1
