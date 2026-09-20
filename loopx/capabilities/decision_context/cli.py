from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Collection
from datetime import datetime, timezone
from pathlib import Path

from .assembler import DecisionEvidenceRecords
from .architecture import build_decision_context_architecture_packet
from .profile import resolve_decision_context_activation
from .private_state import (
    load_private_pending_decision_settlement,
    write_private_pending_decision_settlement,
)
from .review_settlement import settle_profile_decision_review
from .runtime import (
    assemble_profile_decision_evidence,
    decision_evidence_records_from_mapping,
    recall_profile_decision_context,
)
from .sources import build_decision_source_manifest

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]


def _load_json_mapping(path: str, *, label: str) -> dict[str, object]:
    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    return payload


def _collection_size(value: object) -> int:
    if isinstance(value, Collection) and not isinstance(value, (str, bytes)):
        return len(value)
    return 0


def _render(payload: dict[str, object]) -> str:
    capability = payload.get("capability")
    capability_id = (
        capability.get("capability_id")
        if isinstance(capability, dict)
        else "decision_context"
    )
    return "\n".join(
        [
            "# Decision Context",
            "",
            f"- status: `{payload.get('status')}`",
            f"- capability_id: `{capability_id}`",
            f"- packet_schemas: `{_collection_size(payload.get('packet_schemas'))}`",
            f"- source_schemas: `{_collection_size(payload.get('source_schemas'))}`",
            f"- source_count: `{payload.get('source_count', 0)}`",
            "",
        ]
    )


def register_decision_context_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: AddFormat,
) -> None:
    parser = subparsers.add_parser(
        "decision-context",
        help="Inspect the provider-neutral decision evidence and outcome contract.",
    )
    commands = parser.add_subparsers(dest="decision_context_command", required=True)
    for name in ("capture-diagnose", "capture-recovery"):
        recovery = commands.add_parser(
            name, help="Diagnose or explicitly recover one private capture source."
        )
        add_subcommand_format(recovery)
        for field in (
            "goal-id",
            "agent-id",
            "profile",
            "spool",
            "cursor-state",
            "source-id",
        ):
            recovery.add_argument("--" + field, required=True)
        if name == "capture-diagnose":
            recovery.add_argument(
                "--probe",
                action="store_true",
                help="Exact-read transiently to check replay; never settle.",
            )
        else:
            recovery.add_argument(
                "--action", choices=("hold", "restart", "rollback"), required=True
            )
            recovery.add_argument("--expected-token")
            recovery.add_argument(
                "--recovery-id", help="Applied receipt id to roll back."
            )
            recovery.add_argument("--execute", action="store_true")
    for name in ("capture", "capture-status", "prepare-captured"):
        capture = commands.add_parser(
            name, help="Operate the opt-in private source-reference spool."
        )
        add_subcommand_format(capture)
        capture.add_argument("--goal-id", required=True)
        capture.add_argument("--agent-id", required=True)
        capture.add_argument("--profile", required=True)
        capture.add_argument("--spool", required=True)
        capture.add_argument("--cursor-state")
        if name == "capture":
            capture.add_argument("--execute", action="store_true")
        if name == "prepare-captured":
            capture.add_argument("--batch-id", type=int, required=True)
            capture.add_argument("--decision-id", required=True)
            capture.add_argument("--rebase-json", required=True)
            capture.add_argument("--pending-settlement", required=True)
            capture.add_argument("--execute", action="store_true")
    architecture = commands.add_parser(
        "architecture",
        help="Render the default-off Stage-0 Decision Context contract.",
    )
    add_subcommand_format(architecture)
    status = commands.add_parser(
        "inspect-profile",
        help="Inspect a default-off goal profile without provider access.",
    )
    add_subcommand_format(status)
    status.add_argument("--goal-id", required=True)
    status.add_argument("--agent-id", required=True)
    status.add_argument(
        "--profile",
        help="Private local profile. Omit it to prove the default-off route.",
    )

    manifest = commands.add_parser(
        "source-manifest",
        help="Project an enabled private source profile into a public-safe manifest.",
    )
    add_subcommand_format(manifest)
    manifest.add_argument("--goal-id", required=True)
    manifest.add_argument("--agent-id", required=True)
    manifest.add_argument("--profile", required=True)
    manifest.add_argument(
        "--observed-at",
        help="Optional timezone-aware ISO-8601 time for a reproducible manifest.",
    )
    prepare = commands.add_parser(
        "prepare-evidence",
        help=(
            "Run bounded source scans and exact reads without applying private "
            "cursor proposals."
        ),
    )
    add_subcommand_format(prepare)
    prepare.add_argument("--goal-id", required=True)
    prepare.add_argument("--agent-id", required=True)
    prepare.add_argument("--profile", required=True)
    prepare.add_argument("--decision-id", required=True)
    prepare.add_argument(
        "--cursor-state",
        help="Optional private JSON object mapping source ids to opaque cursors.",
    )
    prepare.add_argument(
        "--source-id",
        action="append",
        help=(
            "Explicit enabled source to scan. Repeat to include on-demand "
            "sources; omit to scan automatic sources only."
        ),
    )
    prepare.add_argument(
        "--observed-at",
        help="Optional timezone-aware ISO-8601 assembly time.",
    )
    prepare.add_argument(
        "--before",
        help="Optional timezone-aware upper bound for source changes.",
    )
    prepare.add_argument(
        "--timeout-seconds",
        type=float,
        help="Optional bounded provider timeout override.",
    )
    recall = commands.add_parser(
        "recall-context",
        help=(
            "Read one transient advisory scope without profile mutation, source "
            "scans, cursors, or settlement."
        ),
    )
    add_subcommand_format(recall)
    recall.add_argument("--goal-id", required=True)
    recall.add_argument("--agent-id", required=True)
    recall.add_argument("--profile", required=True)
    recall.add_argument("--context-scope-ref", required=True)
    recall.add_argument("--query", required=True)
    recall.add_argument("--query-summary", required=True)
    recall.add_argument("--max-results", type=int)
    recall.add_argument("--timeout-seconds", type=float)
    recall.add_argument("--observed-at")
    prepare_review = commands.add_parser(
        "prepare-review",
        help=(
            "Validate a domain semantic rebase and optionally persist one private "
            "pending settlement without applying active cursors."
        ),
    )
    add_subcommand_format(prepare_review)
    prepare_review.add_argument("--goal-id", required=True)
    prepare_review.add_argument("--agent-id", required=True)
    prepare_review.add_argument("--profile", required=True)
    prepare_review.add_argument("--decision-id", required=True)
    prepare_review.add_argument("--rebase-json", required=True)
    prepare_review.add_argument("--pending-settlement", required=True)
    prepare_review.add_argument("--cursor-state")
    prepare_review.add_argument("--source-id", action="append")
    prepare_review.add_argument("--observed-at")
    prepare_review.add_argument("--before")
    prepare_review.add_argument("--timeout-seconds", type=float)
    prepare_review.add_argument(
        "--execute",
        action="store_true",
        help="Write the private pending settlement; active cursors stay unchanged.",
    )

    settle_review = commands.add_parser(
        "settle-review",
        help=(
            "Exact-read a user-gate event or semantic no-change result, append "
            "validated writeback, and CAS-commit private cursors."
        ),
    )
    add_subcommand_format(settle_review)
    settle_review.add_argument("--goal-id", required=True)
    settle_review.add_argument("--agent-id", required=True)
    settle_review.add_argument("--profile", required=True)
    settle_review.add_argument("--cursor-state", required=True)
    settle_review.add_argument("--pending-settlement", required=True)
    settle_review.add_argument("--event-log", required=True)
    settle_review.add_argument("--proposal-json")
    settle_review.add_argument("--source-event-id")
    settle_review.add_argument("--actor-ref", required=True)
    settle_review.add_argument("--reason-code", required=True)
    settle_review.add_argument("--summary", required=True)
    settle_review.add_argument(
        "--execute",
        action="store_true",
        help="Append settlement evidence and apply the private cursor CAS.",
    )


def handle_decision_context_command(
    args: argparse.Namespace,
    *,
    runtime_root: Path | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "decision-context":
        return None
    if args.decision_context_command in {"capture-diagnose", "capture-recovery"}:
        from .capture_recovery import diagnose_capture_source, recover_capture_source

        recovery_args = dict(
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            profile_path=Path(args.profile),
            spool_path=Path(args.spool),
            cursor_path=Path(args.cursor_state),
            source_id=args.source_id,
        )
        if args.decision_context_command == "capture-diagnose":
            payload = diagnose_capture_source(**recovery_args, probe=args.probe)
        else:
            payload = recover_capture_source(
                **recovery_args,
                action=args.action,
                execute=args.execute,
                expected_token=args.expected_token,
                recovery_id=args.recovery_id,
            )
    elif args.decision_context_command in {
        "capture",
        "capture-status",
        "prepare-captured",
    }:
        from .capture import (
            capture_profile_sources,
            assemble_captured_decision_evidence,
        )

        capture_args = dict(
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            profile_path=Path(args.profile),
            spool_path=Path(args.spool),
            cursor_path=Path(args.cursor_state) if args.cursor_state else None,
        )
        if args.decision_context_command == "prepare-captured":
            records = decision_evidence_records_from_mapping(
                _load_json_mapping(args.rebase_json, label="rebase JSON")
            )
            assembly = assemble_captured_decision_evidence(
                **capture_args,
                batch_id=args.batch_id,
                decision_id=args.decision_id,
                rebase=lambda _collection: records,
            )
            if args.execute:
                write_private_pending_decision_settlement(
                    Path(args.pending_settlement), assembly
                )
            payload = {
                "assembly": assembly.public_packet(),
                "pending_settlement_written": args.execute,
                "cursor_state_mutated": False,
            }
        else:
            payload = capture_profile_sources(
                **capture_args, execute=bool(getattr(args, "execute", False))
            )
    elif args.decision_context_command == "architecture":
        payload = build_decision_context_architecture_packet()
    elif args.decision_context_command == "recall-context":
        observed_at = (
            str(getattr(args, "observed_at", None) or "").strip()
            or datetime.now(timezone.utc).isoformat()
        )
        payload = recall_profile_decision_context(
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            profile_path=Path(args.profile),
            context_scope_ref=args.context_scope_ref,
            query=args.query,
            query_summary=args.query_summary,
            observed_at=observed_at,
            max_results=args.max_results,
            timeout_seconds=args.timeout_seconds,
            runtime_root=runtime_root,
        )
    elif args.decision_context_command in {"prepare-evidence", "prepare-review"}:
        now = datetime.now(timezone.utc).isoformat()
        observed_at = str(getattr(args, "observed_at", None) or "").strip() or now
        before = str(getattr(args, "before", None) or "").strip() or observed_at
        prepare_review = args.decision_context_command == "prepare-review"
        records = (
            decision_evidence_records_from_mapping(
                _load_json_mapping(args.rebase_json, label="rebase JSON")
            )
            if prepare_review
            else DecisionEvidenceRecords()
        )
        activation, assembly = assemble_profile_decision_evidence(
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            profile_path=Path(args.profile),
            decision_id=args.decision_id,
            observed_at=observed_at,
            before=before,
            cursor_path=(
                Path(args.cursor_state)
                if str(getattr(args, "cursor_state", None) or "").strip()
                else None
            ),
            source_ids=(
                tuple(args.source_id)
                if getattr(args, "source_id", None) is not None
                else None
            ),
            rebase=lambda _collection: records,
            timeout_seconds=getattr(args, "timeout_seconds", None),
            runtime_root=runtime_root,
        )
        settlement_required = bool(assembly is not None and assembly.proposed_cursors)
        pending_written = False
        if prepare_review and settlement_required and bool(args.execute):
            write_private_pending_decision_settlement(
                Path(args.pending_settlement),
                assembly,
            )
            pending_written = True
        payload = activation | {
            "assembly": assembly.public_packet() if assembly is not None else None,
            "semantic_rebase_performed": prepare_review,
            "validated_writeback_required": (
                settlement_required if prepare_review else assembly is not None
            ),
            "cursor_commit_allowed": False,
            "cursor_state_mutated": False,
            "pending_settlement_written": pending_written,
        }
    elif args.decision_context_command == "settle-review":
        assembly = load_private_pending_decision_settlement(
            Path(args.pending_settlement)
        )
        proposal = (
            _load_json_mapping(args.proposal_json, label="proposal JSON")
            if str(args.proposal_json or "").strip()
            else None
        )
        payload = settle_profile_decision_review(
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            profile_path=Path(args.profile),
            cursor_path=Path(args.cursor_state),
            assembly=assembly,
            lifecycle_event_log_path=Path(args.event_log),
            actor_ref=args.actor_ref,
            reason_code=args.reason_code,
            summary=args.summary,
            proposal=proposal,
            source_event_id=(
                str(args.source_event_id).strip()
                if args.source_event_id is not None
                else None
            ),
            execute=bool(args.execute),
        )
    elif args.decision_context_command in {"inspect-profile", "source-manifest"}:
        profile_value = str(getattr(args, "profile", None) or "").strip()
        status, profile = resolve_decision_context_activation(
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            profile_path=Path(profile_value) if profile_value else None,
        )
        if args.decision_context_command == "inspect-profile":
            payload = status
        else:
            observed_at = str(getattr(args, "observed_at", None) or "").strip()
            payload = status | {
                "source_manifest": (
                    build_decision_source_manifest(
                        goal_id=args.goal_id,
                        observed_at=(
                            observed_at or datetime.now(timezone.utc).isoformat()
                        ),
                        sources=profile.sources,
                    )
                    if status["available"] and profile is not None
                    else None
                )
            }
    else:
        raise ValueError("decision-context requires a supported subcommand")
    print_payload(payload, output_format(args), _render)
    return 0
