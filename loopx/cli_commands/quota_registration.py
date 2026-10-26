from __future__ import annotations

import argparse

from ..control_plane.quota.spend_sources import (
    DEFAULT_SLOT_SPEND_SOURCE,
    VALID_SLOT_SPEND_SOURCES,
)
from ..control_plane.scheduler.execution_context import SchedulerRuntimeProfile
from .quota_request import (
    QUOTA_DETAIL_SECTIONS,
    register_quota_monitor_poll_request_arguments,
)


def register_quota_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    quota_parser = subparsers.add_parser(
        "quota",
        help="Show agent-facing compute quota status or next-turn plan.",
    )
    quota_parser.add_argument(
        "quota_command",
        nargs="?",
        choices=[
            "status",
            "plan",
            "should-run",
            "context-read",
            "context-project",
            "context-hook",
            "context-config",
            "replan-view",
            "monitor-poll",
            "scheduler-ack",
            "scheduler-ack-current",
            "scheduler-fail-current",
            "spend-slot",
            "void-slot",
        ],
        default="status",
        help="Use status for all groups, plan for next-turn groups, should-run for one goal, context-read for bounded original-capture recovery without a guard, context-project to present an existing digest-bound quota capture with native full/delta and a full-read fallback, context-hook for opt-in native Codex lifecycle IO, context-config for native hook/recovery configuration without installation or trust changes, monitor-poll for no-spend quiet poll evidence, scheduler-ack for successful Codex App RRULE state, scheduler-fail-current to suppress a repeated failed host update pair, spend-slot for accounting, or void-slot for a non-destructive accounting correction.",
    )
    quota_parser.add_argument("--decision-file", help="Original captured decision for read-only replan-view.")
    quota_parser.add_argument("--outcome-ref", help="Exact captured acceptance outcome reference for replan-view.")
    quota_parser.add_argument("--source-hash", help="Original source_decision_hash for replan-view.")
    quota_parser.add_argument("--replan-stage", choices=["review", "writeback", "acceptance", "explore"], default="review",
                              help="Read decision context before opening its writeback format.")
    quota_parser.add_argument(
        "--goal-id",
        help="Goal id to check. Optional for status/plan (omit for all goals); required for should-run, scheduler ACK/failure, spend, and void. A scoped status/plan ranks only the selected goal, not the global next turn.",
    )
    quota_parser.add_argument(
        "--agent-id",
        help=(
            "Registered agent id for `quota should-run` and scoped quota accounting "
            "commands; suppresses identity-upgrade warnings and records the identity "
            "on appended monitor/scheduler/spend/void events."
        ),
    )
    quota_parser.add_argument(
        "--available-capability",
        dest="available_capabilities",
        action="append",
        help=(
            "For `quota should-run`, `quota monitor-poll`, `quota scheduler-ack`, "
            "`quota scheduler-ack-current`, and `quota spend-slot`, declare a "
            "capability observed in this current agent environment. Live should-run "
            "remembers supported runtime observations for this registered Agent on "
            "this host; inspect or correct them with agent-capabilities. Basic local "
            "shell/filesystem capabilities are assumed."
        ),
    )
    quota_parser.add_argument(
        "--include-detail",
        dest="include_details",
        action="append",
        choices=[*QUOTA_DETAIL_SECTIONS, "all"],
        help=(
            "Include one command-specific cold-path detail section. Status/plan default "
            "to bounded Todo summaries; use agent-todos or user-todos for full lists. For `quota "
            "should-run`: scheduler, agent-todos, user-todos, goal-boundary, or "
            "vision. For `quota monitor-poll`: decisions. Repeat for multiple "
            "sections or use `all`."
        ),
    )
    quota_parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Include the raw exception detail in failure payloads for maintainer "
            "diagnosis. Off by default so the public failure payload stays "
            "path-free."
        ),
    )
    quota_parser.add_argument(
        "--include-scheduler-detail",
        action="store_true",
        # Deprecated compatibility alias. Remove in the next breaking release.
        help=argparse.SUPPRESS,
    )
    quota_parser.add_argument(
        "--app-automation-current-rrule",
        help=(
            "Current RRULE observed from the selected hosted App heartbeat. "
            "This provider-neutral input reconciles host reality with LoopX's "
            "last scheduler ACK."
        ),
    )
    quota_parser.add_argument(
        "--codex-app-current-rrule",
        help=(
            "Current RRULE observed from the active Codex App heartbeat. For "
            "`quota should-run`, this reconciles host reality with LoopX's last "
            "scheduler ACK so a stale ACK cannot suppress a required update. "
            "Deprecated compatibility alias for --app-automation-current-rrule."
        ),
    )
    quota_parser.add_argument(
        "--runtime-profile",
        choices=[profile.value for profile in SchedulerRuntimeProfile],
        help=(
            "Explicit scheduler runtime shortcut for a known host boundary. "
            "Cannot be combined with --host-surface, --scheduler-owner, or "
            "--execution-mode."
        ),
    )
    quota_parser.add_argument(
        "-A",
        "--codex-app",
        action="store_true",
        help=(
            "Compact explicit alias for --runtime-profile "
            "codex_app_heartbeat. Cannot be combined with another scheduler "
            "runtime or execution context."
        ),
    )
    quota_parser.add_argument(
        "--trae_app",
        action="store_true",
        help=(
            "Compact explicit alias for --runtime-profile "
            "trae_app. Cannot be combined with another scheduler "
            "runtime or execution context."
        ),
    )
    quota_parser.add_argument(
        "-H",
        "--host-surface",
        choices=[
            "ark_managed_agent",
            "codex_app",
            "codex_app_ssh",
            "codex_cli",
            "trae_app",
            "generic_cli",
            "claude_code",
            "kiro_cli",
            "local_scheduler",
        ],
        help="Host surface that will consume this scheduler projection.",
    )
    quota_parser.add_argument(
        "-O",
        "--scheduler-owner",
        choices=[
            "host_automation",
            "agent_cli_loop",
            "goal_runtime",
            "outer_controller",
            "none",
        ],
        help="Runtime that owns the next cadence decision.",
    )
    quota_parser.add_argument(
        "-M",
        "--execution-mode",
        choices=["interactive", "isolated_headless", "hosted_automation"],
        help="Execution mode paired with --host-surface and --scheduler-owner.",
    )
    quota_parser.add_argument(
        "--turn-envelope",
        action="store_true",
        help=(
            "For `quota should-run`, return the additive bounded TurnEnvelope view. "
            "The default full decision remains unchanged."
        ),
    )
    capture_group = quota_parser.add_mutually_exclusive_group()
    capture_group.add_argument(
        "--decision-output-dir",
        help=(
            "For turn-scoped quota should-run, create a new private directory and "
            "save the complete decided JSON as decision.json before display projection. "
            "Use a unique directory per invocation; reading it never reruns the guard."
        ),
    )
    capture_group.add_argument(
        "--decision-output-root",
        help="Create a unique private decision directory below this existing root on every guard invocation.",
    )
    quota_parser.add_argument(
        "--decision-handoff-socket",
        help=("Opt-in local Host rendezvous before printing a captured should-run decision. "
              "Requires a capture destination and a same-user Unix socket in a private directory. "
              "Waits at most 120s for a digest-bound release; failure retains the original receipt."),
    )
    quota_parser.add_argument("--capture-file", help="For context-read/context-project: original captured JSON file; no guard is evaluated.")
    from ..control_plane.turn_driver.codex_context import CONTEXT_DELIVERY_SETTINGS

    quota_parser.add_argument("--context-delivery", choices=CONTEXT_DELIVERY_SETTINGS,
        help="Experimental native packet reuse; inherits the machine setting, otherwise off. Explicit off bypasses machine defaults. On uses existing host declarations: "
             "Codex App heartbeat/resume/recovery (--codex-app), or the Codex benchmark heartbeat worker "
             "(generic_cli plus its explicit heartbeat worker settings). On rejects missing, contradictory or unsupported modes. "
             "Enabled supported calls choose private context/capture paths automatically unless overridden. A directory alone never enables it; declarations are not retention proof.")
    context_location = quota_parser.add_mutually_exclusive_group()
    context_location.add_argument("--codex-context-dir",
        help="Opt-in private native Codex lifecycle directory. For context-hook, consume one native hook event on stdin. "
             "For should-run, requires JSON and a decision capture destination; unknown retention stays full. "
             "For context-read without a file/hash, select the current native session/workspace capture for --goal-id/--agent-id; "
             "start at offset zero, then follow digest-pinned next_command. Does not install hooks, enable tracing, or change native trust.")
    context_location.add_argument("--codex-context-root",
        help="Existing private native context root, with independent journals per native session. "
             "Use for shared Codex App hook configuration; cannot combine with --codex-context-dir. "
             "context-config renders native configuration for the exact Goal/Agent; no host configuration, trust, guard or spend are changed.")
    quota_parser.add_argument("--capture-sha256", help="For context-read/context-project: exact SHA-256 supplied with the capture reference.")
    quota_parser.add_argument("--offset", type=int, default=0, help="For context-read: byte offset returned by the previous page.")
    quota_parser.add_argument("--page-bytes", type=int, default=4096,
        help="For context-read: maximum UTF-8 content bytes per page, 256–8192 (default: 4096).")
    quota_parser.add_argument("--max-page-output-bytes", type=int,
        help="For JSON context-read: optional positive byte limit for successful page stdout, including JSON framing and newline. "
             "Content shrinks at UTF-8 boundaries; next_command preserves the limit. An insufficient limit returns an error.")
    quota_parser.add_argument(
        "--turn-instance-id",
        help=(
            "Stable heartbeat settlement id for `quota should-run`, "
            "`quota monitor-poll`, scheduler ACK/failure follow-ups, and "
            "`quota spend-slot`. The guard persists one idempotent receipt; "
            "reuse the same id through monitor writeback, scheduler handoff, "
            "refresh-state, spend, and retries."
        ),
    )
    quota_parser.add_argument("--goal-instance-id", help=argparse.SUPPRESS)
    quota_parser.add_argument(
        "--scheduler-host-facts-chunk",
        dest="scheduler_host_facts_chunks",
        action="append",
        default=[],
        help=argparse.SUPPRESS,
    )
    quota_parser.add_argument(
        "--begin-turn",
        action="store_true",
        help=(
            "For an initial Codex App `quota should-run`, mint and persist one "
            "new Turn identity. Any explicit Todo-selection command returned by "
            "the guard reuses the minted identity. Cannot be combined with "
            "--turn-instance-id or --todo-id."
        ),
    )
    quota_parser.add_argument(
        "--replan-obligation-id",
        help=(
            "Typed autonomous replan obligation binding for `quota spend-slot`. "
            "Use the exact value projected by the original turn-scoped guard; "
            "cannot be combined with --todo-id."
        ),
    )
    quota_parser.add_argument(
        "--slots", type=int, default=1, help="Slots to account for `quota spend-slot`."
    )
    quota_parser.add_argument(
        "--source",
        choices=sorted(VALID_SLOT_SPEND_SOURCES),
        default=DEFAULT_SLOT_SPEND_SOURCE,
        help="Source label for `quota spend-slot`.",
    )
    quota_parser.add_argument(
        "--void-generated-at",
        help="generated_at timestamp of the quota_slot_spent run to void.",
    )
    quota_parser.add_argument(
        "--reason-summary", help="Public-safe reason for `quota void-slot`."
    )
    register_quota_monitor_poll_request_arguments(quota_parser)
    quota_parser.add_argument(
        "--surface",
        help=(
            "Scheduler surface for scheduler ACK/failure commands; derived from "
            "the selected App runtime, or codex_app for a legacy unscoped call."
        ),
    )
    quota_parser.add_argument(
        "--state-key",
        help=(
            "Scheduler state key for scheduler ACK/failure commands; derived from "
            "the selected App runtime when omitted."
        ),
    )
    quota_parser.add_argument(
        "--applied-rrule",
        help="RRULE successfully applied by the host before `quota scheduler-ack --execute`.",
    )
    quota_parser.add_argument(
        "--failed-rrule",
        help="RRULE whose host update failed before `quota scheduler-fail-current --execute`.",
    )
    quota_parser.add_argument(
        "--failure-kind",
        choices=["host_tool_failure", "timeout", "rejected", "unavailable"],
        default="host_tool_failure",
        help="Bounded public-safe failure category for scheduler-fail-current.",
    )
    quota_parser.add_argument(
        "--reset-token", help="Optional reset token to validate before scheduler ack."
    )
    quota_parser.add_argument(
        "--identity-signature",
        help="Optional identity signature to validate before scheduler ack.",
    )
    quota_parser.add_argument(
        "--host-match-observed",
        action="store_true",
        help=(
            "A bound scheduler hint has authoritative host proof from a successful "
            "update or matching readback, so persist its exact reset-token/identity "
            "binding."
        ),
    )
    quota_parser.add_argument(
        "--use-current-hint",
        action="store_true",
        help=(
            "For `quota scheduler-ack`, resolve reset token and identity signature "
            "from the latest quota should-run scheduler hint; `scheduler-ack-current` "
            "sets this automatically."
        ),
    )
    quota_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Keep quota accounting or scheduler-state writes as preview-only. This is the default.",
    )
    quota_parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the quota accounting write or no-spend scheduler-state ack.",
    )
    quota_parser.add_argument(
        "--record-host-poll",
        action="store_true",
        help=(
            "For `quota should-run`, record a compact host poll receipt beside "
            "the goal state file so stale-loop projections can distinguish a "
            "live polling driver from one that died mid-wait."
        ),
    )
    quota_parser.add_argument(
        "--use-projection-cache",
        action="store_true",
        help=(
            "Read a fresh status_projection_cache_v0 snapshot before building "
            "quota decisions. Misses and expired snapshots fall back to full "
            "status collection."
        ),
    )
    quota_parser.add_argument(
        "--write-projection-cache",
        action="store_true",
        help="Write the status projection cache after a full quota status collection.",
    )
    quota_parser.add_argument(
        "--projection-cache-ttl-seconds",
        type=int,
        default=120,
        help="Freshness window for --use-projection-cache. Defaults to 120 seconds.",
    )
    quota_parser.add_argument("--limit", type=int, default=5)
