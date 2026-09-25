from __future__ import annotations

import argparse
from collections.abc import Callable

from ..paths import default_public_scan_root


def register_status_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    check_parser = subparsers.add_parser(
        "check",
        help="Run a read-only contract and public/private boundary check.",
    )
    check_parser.add_argument(
        "--scan-root",
        default=".",
        help="Public files to scan for obvious private material.",
    )
    check_parser.add_argument(
        "--scan-path",
        action="append",
        default=[],
        help=(
            "Specific public file or directory to scan. Repeatable. "
            "Overrides --scan-root when set."
        ),
    )
    check_parser.add_argument("--limit", type=int, default=5)

    status_parser = subparsers.add_parser(
        "status",
        help="Show a first-screen goal status and attention queue.",
    )
    add_subcommand_format(status_parser)
    status_parser.add_argument(
        "--scan-root",
        default=default_public_scan_root(),
        help=(
            "Public files to scan for obvious private material. "
            "Defaults to the LoopX install root."
        ),
    )
    status_parser.add_argument(
        "--scan-path",
        action="append",
        default=[],
        help=(
            "Specific public file or directory to scan. Repeatable. "
            "Overrides --scan-root when set."
        ),
    )
    status_parser.add_argument("--limit", type=int, default=5)
    status_parser.add_argument(
        "--goal-id",
        help=(
            "Optional goal id to focus the status projection. The default remains "
            "the global dashboard/status view."
        ),
    )
    status_parser.add_argument(
        "--agent-id",
        help=(
            "Registered agent id for adding agent-lane next-action projection "
            "to matching status queue items."
        ),
    )
    status_parser.add_argument(
        "--available-capability",
        dest="available_capabilities",
        action="append",
        help=(
            "Declare a capability available in the current execution envelope. "
            "Repeat for multiple capabilities; capability-gated status fields "
            "remain absent by default."
        ),
    )
    status_parser.add_argument(
        "--include-task-graph",
        action="store_true",
        help=(
            "Include the optional task_graph_projection_v0 on status items. "
            "Default status output keeps this graph on the cold path to stay "
            "inside the dashboard hot-path budget."
        ),
    )
    status_parser.add_argument(
        "--use-projection-cache",
        action="store_true",
        help=(
            "Read a fresh status_projection_cache_v0 snapshot before running "
            "the full status collector. Misses and expired snapshots fall back "
            "to the full collector."
        ),
    )
    status_parser.add_argument(
        "--write-projection-cache",
        action="store_true",
        help=(
            "Write the collected status projection to the cache after a full "
            "collection."
        ),
    )
    status_parser.add_argument(
        "--projection-cache-ttl-seconds",
        type=int,
        default=120,
        help="Freshness window for --use-projection-cache. Defaults to 120 seconds.",
    )

    diagnose_parser = subparsers.add_parser(
        "diagnose",
        help=(
            "Build a LoopX diagnostic evidence packet for the user's agent to "
            "reason over."
        ),
    )
    add_subcommand_format(diagnose_parser)
    diagnose_parser.add_argument(
        "--goal-id",
        help="Goal id to diagnose. Defaults to the first attention item.",
    )
    diagnose_parser.add_argument(
        "--agent-id",
        help=(
            "Registered agent id for identity-scoped quota/todo projection. "
            "Use this for multi-agent goals and heartbeat-driven diagnosis."
        ),
    )
    diagnose_parser.add_argument(
        "--available-capability",
        dest="available_capabilities",
        action="append",
        help=(
            "Declare a capability available in the current agent environment. "
            "Repeat for multiple capabilities so diagnose uses the same runtime "
            "envelope as quota should-run."
        ),
    )
    diagnose_parser.add_argument(
        "--scan-root",
        default=default_public_scan_root(),
        help=(
            "Public files to scan for obvious private material. "
            "Defaults to the LoopX install root."
        ),
    )
    diagnose_parser.add_argument(
        "--scan-path",
        action="append",
        default=[],
        help=(
            "Specific public file or directory to scan. Repeatable. "
            "Overrides --scan-root when set."
        ),
    )
    diagnose_parser.add_argument("--limit", type=int, default=5)

    review_packet_parser = subparsers.add_parser(
        "review-packet",
        help=(
            "Generate a CLI-visible Review Packet from the current status contract, "
            "including agent-scoped evidence-log read hints when available."
        ),
    )
    review_packet_parser.add_argument(
        "--goal-id",
        required=True,
        help="Goal id to package for review or handoff.",
    )
    review_packet_parser.add_argument(
        "--action-kind",
        choices=["reward", "controller", "codex", "evidence", "health"],
        help=(
            "Override inferred action kind. Defaults to the goal's current "
            "attention item."
        ),
    )
    review_packet_parser.add_argument(
        "--review-url",
        help="Optional dashboard review URL to include in the packet.",
    )
    review_packet_parser.add_argument(
        "--scan-root",
        default=default_public_scan_root(),
        help=(
            "Public files to scan for obvious private material. "
            "Defaults to the LoopX install root."
        ),
    )
    review_packet_parser.add_argument(
        "--scan-path",
        action="append",
        default=[],
        help=(
            "Specific public file or directory to scan. Repeatable. "
            "Overrides --scan-root when set."
        ),
    )
    review_packet_parser.add_argument(
        "--handoff-only",
        action="store_true",
        help=(
            "Print only the target project-agent handoff in markdown output; "
            "JSON output returns a minimized handoff payload with complete text. "
            "Overflow Markdown carries all shards; verify with loopx handoff restore."
        ),
    )
    review_packet_parser.add_argument(
        "--format",
        dest="review_packet_format",
        choices=["markdown", "json"],
        help=(
            "Output format for review-packet. This mirrors the global --format "
            "flag and may appear after the subcommand."
        ),
    )
    review_packet_parser.add_argument(
        "--agent-id",
        help=(
            "Registered agent id for adding read-only agent-member status to "
            "the review packet."
        ),
    )
    review_packet_parser.add_argument(
        "--available-capability",
        dest="available_capabilities",
        action="append",
        help=(
            "Declare a capability available in the current execution envelope. "
            "Repeat for multiple capabilities; capability-gated review fields "
            "remain absent by default."
        ),
    )
    review_packet_parser.add_argument("--limit", type=int, default=5)
