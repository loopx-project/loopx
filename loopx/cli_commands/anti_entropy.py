from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..capabilities.anti_entropy.cli import run_anti_entropy_verify
from ..capabilities.anti_entropy.contract import (
    ANTI_ENTROPY_CAPABILITY_ID,
    RetirementBasis,
)

PrintPayload = Callable[
    [dict[str, Any], str, Callable[[dict[str, Any]], str]], str | None
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]


def register_anti_entropy_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: AddFormat,
) -> None:
    parser = subparsers.add_parser(
        "anti-entropy",
        help="Verify diff hygiene and delete-first code retirement governance.",
    )
    commands = parser.add_subparsers(
        dest="anti_entropy_command", required=True
    )
    verify = commands.add_parser(
        "verify",
        help="Verify diff against a declared retirement basis and audit diff hygiene.",
    )
    add_subcommand_format(verify)
    verify.add_argument("--goal-id", help="Associated LoopX goal identifier.")
    verify.add_argument(
        "--diff-file",
        help="Path to unified diff patch file, or - for stdin.",
    )
    verify.add_argument(
        "--basis-json",
        help="JSON string or file path containing RetirementBasis specification.",
    )
    verify.add_argument(
        "--workspace",
        help=(
            "Directory containing the complete post-change sources; required to "
            "certify symbol retirement. Omitting it limits the verdict to "
            "diff-only evidence."
        ),
    )
    inspect = commands.add_parser(
        "inspect",
        help=(
            "In-flight agent check: verify the diff and return actionable "
            "feedback. Rejections are progress facts, not final delivery "
            "failures (exit 0 whenever the check runs)."
        ),
    )
    add_subcommand_format(inspect)
    inspect.add_argument("--goal-id", help="Associated LoopX goal identifier.")
    inspect.add_argument(
        "--diff-file",
        help="Path to unified diff patch file, or - for stdin.",
    )
    inspect.add_argument(
        "--basis-json",
        help="JSON string or file path containing RetirementBasis specification.",
    )
    inspect.add_argument(
        "--workspace",
        help=(
            "Directory containing the complete post-change sources; required to "
            "certify symbol retirement."
        ),
    )


def _render(payload: dict[str, Any]) -> str:
    lines = [f"# Anti-Entropy Governance ({payload.get('command')})", ""]
    lines.append(f"- verdict: `{payload.get('verdict')}`")
    lines.append(f"- ok: `{payload.get('ok')}`")
    if payload.get("goal_id"):
        lines.append(f"- goal_id: `{payload.get('goal_id')}`")
    lines.append(f"- finding_count: `{payload.get('finding_count', 0)}`")
    lines.append(f"- blocking_finding_count: `{payload.get('blocking_finding_count', 0)}`")
    lines.append(f"- advisory_finding_count: `{payload.get('advisory_finding_count', 0)}`")

    receipt = payload.get("receipt", {})
    if receipt:
        lines.append("")
        lines.append("## Receipt")
        lines.append(f"- diff_hash: `{receipt.get('diff_hash')}`")
        lines.append(f"- source_evidence: `{receipt.get('source_evidence', 'diff_only')}`")
        if receipt.get("source_root"):
            lines.append(f"- source_root: `{receipt.get('source_root')}`")
        retired = receipt.get("retired_symbols", [])
        if retired:
            lines.append(f"- retired_symbols: `{', '.join(retired)}`")
        findings = receipt.get("findings", [])
        if findings:
            lines.append("")
            lines.append("## Findings")
            for item in findings:
                sev = item.get("severity", "").upper()
                rule = item.get("rule_id")
                msg = item.get("message")
                lines.append(f"- [{sev}] {rule}: {msg}")
    if payload.get("command") == "inspect":
        lines.append("")
        lines.append("## Feedback")
        lines.append(f"- is_progress_fact: `{payload.get('is_progress_fact')}`")
        lines.append(f"- delivery_ready: `{payload.get('delivery_ready')}`")
        lines.append(f"- delivery_route: `{payload.get('delivery_route')}`")
        for item in payload.get("actionable_feedback", []):
            lines.append("")
            lines.append(f"### [{item.get('severity', '').upper()}] {item.get('rule_id')}")
            if item.get("location"):
                lines.append(f"- location: `{item.get('location')}`")
            lines.append(f"- next_action: {item.get('next_action')}")
    return "\n".join(lines) + "\n"


def _read_diff_text(args: argparse.Namespace) -> str:
    if args.diff_file == "-":
        return sys.stdin.read()
    if args.diff_file:
        diff_path = Path(args.diff_file).expanduser()
        if diff_path.is_file():
            return diff_path.read_text(encoding="utf-8")
        raise ValueError(f"diff file not found: {args.diff_file}")
    return ""


def _read_basis_data(args: argparse.Namespace) -> Any:
    if args.basis_json is None:
        return None
    raw_basis = args.basis_json.strip()
    if raw_basis.startswith("{") or raw_basis.startswith("["):
        return json.loads(raw_basis)
    path = Path(raw_basis).expanduser()
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        return json.loads(raw_basis)
    except Exception:
        raise ValueError(f"basis file not found: {raw_basis}")


def _resolve_workspace(args: argparse.Namespace) -> Path | None:
    if not getattr(args, "workspace", None):
        return None
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace directory not found: {args.workspace}")
    return workspace


def handle_anti_entropy_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "anti-entropy":
        return None
    try:
        command = args.anti_entropy_command
        # Shared input reading: both entries bind the same diff, basis, and
        # post-change workspace; only the outcome contract differs.
        diff_text = _read_diff_text(args)
        basis_data = _read_basis_data(args)
        workspace = _resolve_workspace(args)

        if command == "verify":
            # A delivery-gate verification without a declared retirement basis
            # would pass without checking anything; diff hygiene alone belongs
            # to inspect and can never certify a retirement.
            if basis_data is None:
                raise ValueError(
                    "verify requires an explicit retirement basis "
                    "(--basis-json <json|path>)"
                )
            declared_basis = RetirementBasis.from_dict(basis_data)
            if not declared_basis.target_symbols and not declared_basis.target_paths:
                raise ValueError(
                    "verify requires at least one declared retirement target "
                    "(target_symbols or target_paths)"
                )
            payload = run_anti_entropy_verify(
                diff_text=diff_text,
                basis_data=basis_data,
                goal_id=args.goal_id,
                workspace=workspace,
            )
        elif command == "inspect":
            from ..capabilities.anti_entropy.feedback import run_anti_entropy_feedback_cycle

            payload = run_anti_entropy_feedback_cycle(
                diff_text=diff_text,
                basis=basis_data,
                goal_id=args.goal_id,
                workspace=workspace,
            )
            payload = {"command": "inspect", **payload}
        else:
            raise ValueError(f"unknown anti-entropy command `{command}`")
    except (OSError, ValueError, TypeError) as exc:
        print(f"error: {ANTI_ENTROPY_CAPABILITY_ID}: {exc}", file=sys.stderr)
        return 2

    print_payload(payload, output_format(args), _render)
    return 0 if payload.get("ok") else 1
