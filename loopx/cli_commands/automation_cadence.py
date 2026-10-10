"""Explicit owner-policy configuration and readback."""

from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from ..control_plane.effect_runtime import effect_runtime_result
from ..history import load_registry
from ..paths import resolve_runtime_root


def _render_automation_cadence(result: dict[str, Any]) -> str:
    eligibility = result["eligibility"]
    next_at_ms = eligibility["next_eligible_at_ms"]
    next_at = (
        datetime.fromtimestamp(next_at_ms / 1_000, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
        if next_at_ms is not None
        else "none"
    )
    source_revisions = ", ".join(
        str(source["revision"]) for source in result["sources"]
    ) or "none"
    return (
        f"Automatic execution minimum: {result['min_interval_minutes']} minutes\n"
        f"Configuration revision: {result['configuration_revision']}\n"
        f"Source revisions: {source_revisions}\n"
        f"Minimum-interval condition: {eligibility['state']} ({eligibility['reason']})\n"
        f"Interval threshold time: {next_at}\n"
        f"Enforcement: {result['enforcement']}\n"
        "Host wake cadence and pre-model guarantees must be read back separately."
    )


def register_automation_cadence_command(subparsers: Any, add_format: Any) -> None:
    p = subparsers.add_parser(
        "automation-cadence",
        help="Inspect or explicitly configure the minimum automatic execution interval.",
    )
    add_format(p)
    p.add_argument("--goal-id", required=True)
    p.add_argument(
        "--agent-id",
        help="Omit for the Goal policy; Goal constraints inherit into every agent lane.",
    )
    p.add_argument(
        "--automation-id", help="Optional narrower automation scope; requires agent-id."
    )
    p.add_argument(
        "--min-interval-minutes",
        type=int,
        help="Minimum interval; 0 removes this scope's constraint without changing parent constraints.",
    )
    p.add_argument(
        "--expected-revision",
        type=int,
        help="Exact configuration revision from readback; required for a change.",
    )
    p.add_argument(
        "--owner-reference",
        help="Reference to the explicit owner instruction; required for a change, never inferred by a scheduler.",
    )
    p.add_argument(
        "--approve-reduction",
        action="store_true",
        help="Record the owner's explicit authorization to reduce this exact scope's floor.",
    )
    p.add_argument(
        "--execute",
        action="store_true",
        help="Apply; otherwise preview the configuration change.",
    )


def handle_automation_cadence_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: Any,
    output_format: Any,
) -> int | None:
    if args.command != "automation-cadence":
        return None
    try:
        registry = load_registry(registry_path)
        if args.goal_id not in {g.get("id") for g in registry.get("goals", [])}:
            raise ValueError("automation cadence requires a registered Goal")
        root = resolve_runtime_root(
            registry, runtime_root_arg, registry_path=registry_path
        )
        change = args.min_interval_minutes is not None
        if not change and (
            args.execute
            or args.approve_reduction
            or args.owner_reference
            or args.expected_revision is not None
        ):
            raise ValueError("configuration flags require --min-interval-minutes")
        result = effect_runtime_result(
            "quota.automation_cadence.manage",
            {
                "runtime_root": str(root),
                "goal_id": args.goal_id,
                "agent_id": args.agent_id,
                "automation_id": args.automation_id,
                "operation": "configure" if change else "read",
                "min_interval_minutes": args.min_interval_minutes,
                "expected_revision": args.expected_revision,
                "owner_reference": args.owner_reference,
                "approve_reduction": args.approve_reduction,
                "execute": args.execute,
            },
            retry_safe=not change,
        )

    except (OSError, ValueError, RuntimeError) as exc:
        print_payload(
            {"ok": False, "error": str(exc)}, output_format(args), lambda v: v["error"]
        )
        return 1
    print_payload(
        result,
        output_format(args),
        _render_automation_cadence,
    )
    return 0
