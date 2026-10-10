from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..control_plane.coordination.local_authority_shadow_adapter import (
    CLI_DRAIN_LOCK_TIMEOUT_SECONDS,
    drain_local_authority_shadow_outbox,
    local_authority_shadow_status,
)
from ..control_plane.coordination.local_authority_shadow_outbox import OutboxError
from ..paths import effective_runtime_root
from ..file_lock import LockAcquireTimeoutError


AUTHORITY_SHADOW_CLI_SCHEMA = "loopx_authority_shadow_cli_v0"
CLI_DRAIN_MAX_ENTRIES = 256
CLI_DRAIN_BUDGET_SECONDS = 30.0

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]


_DRAIN_FIELDS = (
    "outcome",
    "reason_code",
    "delivered",
    "replayed",
    "reconciled",
    "no_op",
    "reseeded",
    "reclaimed_residue",
    "pending_after",
    "prepared_only_after",
    "budget_exhausted",
    "last_cursor",
    "candidate_readback_verified",
)


def _drain_markdown_lines(payload: dict[str, object]) -> list[str]:
    lines = [f"- {key}: `{payload.get(key)}`" for key in _DRAIN_FIELDS]
    stopped_at = payload.get("stopped_at")
    if isinstance(stopped_at, dict):
        lines.append(
            "- stopped_at: "
            f"`{stopped_at.get('partition')}#{stopped_at.get('seq')}` "
            f"→ `{stopped_at.get('outcome')}` ({stopped_at.get('reason_code')})"
        )
    return lines


def _status_markdown_lines(payload: dict[str, object]) -> list[str]:
    lines: list[str] = []
    config = payload.get("config")
    if isinstance(config, dict):
        lines.append(f"- config: `{config.get('status')}`")
    backlog = payload.get("outbox")
    partitions = backlog.items() if isinstance(backlog, dict) else []
    for partition, facts in partitions:
        if isinstance(facts, dict):
            lines.append(
                f"- outbox.{partition}: committed_pending="
                f"`{facts.get('committed_pending')}` prepared_only="
                f"`{facts.get('prepared_only')}` cursor_last_seq="
                f"`{facts.get('cursor_last_seq')}`"
            )
    candidate = payload.get("candidate")
    if isinstance(candidate, dict):
        lines.append(
            f"- candidate: status=`{candidate.get('status')}` cursor="
            f"`{candidate.get('cursor')}` store_identity=`{candidate.get('store_identity')}`"
        )
    lines.append(f"- store_bytes: `{payload.get('store_bytes')}`")
    lines.append(f"- retention_pressure: `{payload.get('retention_pressure')}`")
    return lines


def render_authority_shadow_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# LoopX Authority Shadow",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- action: `{payload.get('action')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
    if payload.get("error_code"):
        lines.append(f"- error_code: `{payload.get('error_code')}`")
    if payload.get("action") == "drain":
        lines.extend(_drain_markdown_lines(payload))
    elif payload.get("action") == "status":
        lines.extend(_status_markdown_lines(payload))
    return "\n".join(lines) + "\n"


def register_authority_shadow_command(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "authority-shadow",
        help=(
            "Drain or inspect the transaction-bound local authority shadow outbox "
            "for one goal. The candidate store is evidence only; it never decides."
        ),
    )
    add_subcommand_format(parser)
    parser.add_argument(
        "authority_shadow_command",
        choices=["drain", "status"],
        help="drain delivers pending outbox entries; status reports backlog and candidate facts.",
    )
    parser.add_argument("--goal-id", required=True, help="Goal id whose shadow outbox to operate on.")
    parser.add_argument(
        "--max-entries",
        type=int,
        default=CLI_DRAIN_MAX_ENTRIES,
        help=f"Maximum entries one drain pass delivers (default {CLI_DRAIN_MAX_ENTRIES}).",
    )
    parser.add_argument(
        "--budget-seconds",
        type=float,
        default=CLI_DRAIN_BUDGET_SECONDS,
        help=f"Wall-clock budget for one drain pass (default {CLI_DRAIN_BUDGET_SECONDS:g}).",
    )
    parser.add_argument(
        "--lock-timeout-seconds",
        type=float,
        default=CLI_DRAIN_LOCK_TIMEOUT_SECONDS,
        help=(
            "How long to wait for the per-goal drain lock before reporting drain_deferred "
            f"(default {CLI_DRAIN_LOCK_TIMEOUT_SECONDS:g})."
        ),
    )


def handle_authority_shadow_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: Callable[..., str],
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "authority-shadow":
        return None
    action = str(getattr(args, "authority_shadow_command", None))
    payload: dict[str, object]
    try:
        # The same resolver every writer hook uses, so drain and status address
        # the lineage those hooks wrote.
        runtime_root = effective_runtime_root(registry_path, runtime_root_arg)
        if action == "drain":
            if args.max_entries < 1:
                raise ValueError("--max-entries must be at least 1")
            result = drain_local_authority_shadow_outbox(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                max_entries=args.max_entries,
                budget_seconds=args.budget_seconds,
                lock_timeout_seconds=args.lock_timeout_seconds,
            )
            payload = {
                "schema_version": AUTHORITY_SHADOW_CLI_SCHEMA,
                "action": "drain",
                **result.to_payload(),
            }
        else:
            payload = {
                "schema_version": AUTHORITY_SHADOW_CLI_SCHEMA,
                **local_authority_shadow_status(
                    registry_path=registry_path,
                    runtime_root=runtime_root,
                    goal_id=args.goal_id,
                ),
            }
    except OutboxError as exc:
        payload = {
            "ok": False,
            "schema_version": AUTHORITY_SHADOW_CLI_SCHEMA,
            "action": action,
            "goal_id": args.goal_id,
            "error": str(exc),
            "error_code": exc.reason_code,
        }
    except LockAcquireTimeoutError as exc:
        payload = {
            "ok": False,
            "schema_version": AUTHORITY_SHADOW_CLI_SCHEMA,
            "action": action,
            "goal_id": args.goal_id,
            "error": str(exc),
            **exc.to_payload(),
        }
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": AUTHORITY_SHADOW_CLI_SCHEMA,
            "action": action,
            "goal_id": args.goal_id,
            "error": str(exc),
            "error_code": exc.__class__.__name__,
        }
    print_payload(payload, output_format(args), render_authority_shadow_markdown)
    return 0 if payload.get("ok") else 1


__all__ = [
    "AUTHORITY_SHADOW_CLI_SCHEMA",
    "handle_authority_shadow_command",
    "register_authority_shadow_command",
    "render_authority_shadow_markdown",
]
