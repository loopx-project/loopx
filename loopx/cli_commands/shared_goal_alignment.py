"""CLI surface for read-only shared goal alignment projection.

``loopx shared-goal-alignment`` (alias: ``loopx goal-alignment``) projects the
Stage 1 read-only ``shared_goal_alignment_v0`` view for one registered Agent
around one shared Goal. It derives basis, frontier, claim/lease, eligible work,
drift, and conflict facts using typed source data only.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..control_plane.goals.shared_goal_alignment import (
    project_shared_goal_alignment,
)
from ..todos import resolve_todo_state_path

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]


def render_shared_goal_alignment_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# LoopX Shared Goal Alignment",
        "",
        f"- ok: `{payload.get('ok')}`",
    ]
    if payload.get("goal_id"):
        lines.append(f"- goal_id: `{payload.get('goal_id')}`")
    if payload.get("agent_id"):
        lines.append(f"- agent_id: `{payload.get('agent_id')}`")
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)
    lines.append(f"- read_only: `{payload.get('read_only')}`")
    source_basis = payload.get("source_basis")
    if isinstance(source_basis, dict):
        lines.append("- source_basis:")
        lines.append(f"  - revision_basis: `{source_basis.get('revision_basis')}`")
        lines.append(
            f"  - state_event_basis_sequence: `{source_basis.get('state_event_basis_sequence')}`"
        )
        lines.append(
            f"  - source_basis_digest: `{source_basis.get('source_basis_digest')}`"
        )
        if source_basis.get("state_updated_at"):
            lines.append(f"  - state_updated_at: `{source_basis.get('state_updated_at')}`")
    frontier_basis = payload.get("frontier_basis")
    if isinstance(frontier_basis, dict):
        lines.append("- frontier_basis:")
        lines.append(f"  - basis_source: `{frontier_basis.get('basis_source')}`")
        lines.append(
            f"  - based_on_state_event_sequence: `{frontier_basis.get('based_on_state_event_sequence')}`"
        )
        if frontier_basis.get("last_agent_event_id"):
            lines.append(
                f"  - last_agent_event_id: `{frontier_basis.get('last_agent_event_id')}`"
            )
    counts = payload.get("frontier_counts")
    if isinstance(counts, dict):
        lines.append("- frontier_counts:")
        lines.append(
            f"  - current_agent_claimed_advancement_count: {counts.get('current_agent_claimed_advancement_count', 0)}"
        )
        lines.append(
            f"  - unclaimed_advancement_count: {counts.get('unclaimed_advancement_count', 0)}"
        )
        lines.append(
            f"  - other_agent_claimed_advancement_count: {counts.get('other_agent_claimed_advancement_count', 0)}"
        )
    drift = payload.get("drift_facts")
    if isinstance(drift, list) and drift:
        lines.append("- drift_facts:")
        for fact in drift:
            lines.append(f"  - `{fact}`")
    conflict = payload.get("conflict_facts")
    if isinstance(conflict, list) and conflict:
        lines.append("- conflict_facts:")
        for fact in conflict:
            lines.append(f"  - `{fact}`")
    unclaimed = payload.get("unclaimed_eligible_work")
    if isinstance(unclaimed, list) and unclaimed:
        lines.append("- unclaimed_eligible_work:")
        for item in unclaimed:
            if isinstance(item, dict):
                lines.append(
                    f"  - `{item.get('todo_id')}` "
                    f"claim_required=`{item.get('claim_required_before_work')}`"
                )
    return "\n".join(lines)


def register_shared_goal_alignment_command(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "shared-goal-alignment",
        aliases=["goal-alignment"],
        help=(
            "Project read-only shared goal alignment facts (basis, frontier, "
            "unclaimed work, drift, conflicts) for one registered Agent."
        ),
    )
    add_subcommand_format(parser)
    parser.add_argument(
        "--goal-id",
        required=True,
        help="Registered Goal id whose alignment is being projected.",
    )
    parser.add_argument(
        "--agent-id",
        required=True,
        help="Registered Agent id for whom the read-only alignment view is computed.",
    )
    parser.add_argument(
        "--project",
        help="Project directory containing the goal or active state. Defaults to the registry goal repository.",
    )


def handle_shared_goal_alignment_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: Callable[..., str],
    print_payload: PrintPayload,
) -> int | None:
    if args.command not in {"shared-goal-alignment", "goal-alignment"}:
        return None
    try:
        project_path: Path | None = None
        if getattr(args, "project", None):
            project_path = Path(args.project).expanduser()
        else:
            resolved_project, _ = resolve_todo_state_path(
                registry_path=registry_path,
                goal_id=args.goal_id,
                require_existing=False,
            )
            if resolved_project is None:
                raise ValueError(
                    "goal project is not configured in the registry; pass --project"
                )
            project_path = resolved_project

        runtime_root = (
            Path(runtime_root_arg).expanduser() if runtime_root_arg else None
        )
        projection = project_shared_goal_alignment(
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            project=project_path,
            registry_path=registry_path,
            runtime_root=runtime_root,
        )
        payload = {"ok": True, **projection}
        exit_code = 0
    except Exception as exc:
        payload = {
            "ok": False,
            "goal_id": getattr(args, "goal_id", None),
            "agent_id": getattr(args, "agent_id", None),
            "error": str(exc),
        }
        exit_code = 1

    print_payload(payload, output_format(args), render_shared_goal_alignment_markdown)
    return exit_code
