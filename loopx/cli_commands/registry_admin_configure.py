from __future__ import annotations

import argparse

from ..execution_profile import TURN_GRANULARITY_CHOICES
from ..orchestration import EXPLORE_HARNESS_PROFILES
from .registry_admin_peer import (
    register_peer_runtime_arguments,
    register_peer_supervisor_arguments,
)


def register_configure_goal_command(subparsers: argparse._SubParsersAction) -> None:
    configure_goal_parser = subparsers.add_parser(
        "configure-goal",
        help=(
            "Inspect current goal settings and optional features, or preview/apply an "
            "incremental configuration change."
        ),
        description=(
            "Inspect current per-goal settings, or preview/apply an incremental change. "
            "With no setting flags this is read-only and includes the on-demand optional "
            "feature catalog; first-run setup requires no optional configuration."
        ),
    )
    configure_goal_parser.add_argument(
        "--goal-id", required=True, help="Goal id to configure."
    )
    configure_goal_parser.add_argument(
        "--execution-turn-granularity",
        choices=TURN_GRANULARITY_CHOICES,
        help=(
            "Set sticky goal turn granularity. fine plans small checkpoints within "
            "a coherent work slice; completed-Todo review cadence defaults to 5 in both modes."
        ),
    )
    configure_goal_parser.add_argument(
        "--execution-replan-after-todos",
        type=int,
        choices=range(1, 6),
        help=(
            "Require goal review after this many same-agent advancement Todo completions "
            "without a covering outcome checkpoint. Default 5; use 2 or 3 for earlier "
            "review. This writes a Goal override; use the clear flag to inherit the "
            "machine default. Applies to standard and fine modes."
        ),
    )
    configure_goal_parser.add_argument(
        "--clear-execution-replan-after-todos",
        action="store_true",
        help=(
            "Remove the Goal review-cadence override and restore live machine-default "
            "inheritance."
        ),
    )
    configure_goal_parser.add_argument(
        "--quota-compute",
        type=float,
        help="Per-goal quota compute multiplier. 0 hard-pauses the whole Goal (all automatic agent turns stop); negative values are rejected.",
    )
    configure_goal_parser.add_argument(
        "--quota-window-hours", type=float, help="Quota rolling window in hours."
    )
    configure_goal_parser.add_argument(
        "--self-repair-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable control_plane.self_repair for this goal.",
    )
    configure_goal_parser.add_argument(
        "--self-repair-health",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable control-plane health blocker repair for this goal.",
    )
    configure_goal_parser.add_argument(
        "--self-repair-waiting-projection",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable waiting-projection repair for this goal.",
    )
    configure_goal_parser.add_argument("--pr-review-wait-for-ci", action=argparse.BooleanOptionalAction, default=None, help="Whether this Goal waits for CI during PR review; omitted inherits the machine default (true).")
    configure_goal_parser.add_argument("--clear-pr-review-configuration", action="store_true", help="Remove Goal PR review overrides and restore machine defaults.")
    configure_goal_parser.add_argument(
        "--change-quality-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable exact-scope change-quality qualification for this goal.",
    )
    configure_goal_parser.add_argument(
        "--change-quality-safe-fix",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow or forbid one bounded safe-fix pass during change qualification.",
    )
    configure_goal_parser.add_argument(
        "--change-quality-strict-receipt",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Require a valid exact-scope quality receipt at premerge.",
    )
    configure_goal_parser.add_argument(
        "--clear-change-quality-configuration",
        action="store_true",
        help=(
            "Remove the complete Goal change-quality override and restore live "
            "machine-default inheritance."
        ),
    )
    configure_goal_parser.add_argument(
        "--multi-subagent-feature",
        choices=["off", "enabled"],
        help=(
            "Default-off product switch for bounded child-agent orchestration. "
            "`enabled` maps to multi_subagent with spawn allowed; `off` maps to single-agent mode."
        ),
    )
    configure_goal_parser.add_argument(
        "--orchestration-mode",
        choices=["default", "multi_subagent"],
        help="Per-goal orchestration mode.",
    )
    configure_goal_parser.add_argument(
        "--spawn-allowed",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow or block sub-agent spawning for this goal.",
    )
    configure_goal_parser.add_argument(
        "--max-children", type=int, help="Maximum child agents for orchestration."
    )
    configure_goal_parser.add_argument(
        "--align-codex-subagent-capacity",
        action="store_true",
        help=(
            "Preview or apply the Codex host child-thread limit needed by this Goal. "
            "Apply is explicit, raises only, preserves higher limits, and affects new sessions."
        ),
    )
    configure_goal_parser.add_argument(
        "--subagent-model",
        help="Persist a child model preference; does not enable spawning or change the parent model.",
    )
    configure_goal_parser.add_argument(
        "--subagent-reasoning-effort",
        help="Persist child reasoning effort; requires a configured child model. An empty string clears only effort.",
    )
    configure_goal_parser.add_argument(
        "--clear-subagent-model-config",
        action="store_true",
        help="Remove child model and effort preferences together.",
    )
    configure_goal_parser.add_argument(
        "--subagent-execution-config",
        help=(
            "Repo-relative operator binding file under .loopx/config/ used by "
            "the existing local delegation entrypoint."
        ),
    )
    configure_goal_parser.add_argument(
        "--clear-subagent-execution-config",
        action="store_true",
        help="Remove the Goal's local delegation binding pointer.",
    )
    configure_goal_parser.add_argument(
        "--allowed-domain",
        action="append",
        default=None,
        help="Allowed child-agent domain. Repeatable; comma-separated values are also accepted.",
    )
    configure_goal_parser.add_argument(
        "--clear-allowed-domains",
        action="store_true",
        help="Clear allowed child-agent domains.",
    )
    configure_goal_parser.add_argument(
        "--explore-graph-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable or disable automatic Explore Graph projection at material "
            "refresh boundaries. This is independent from Explore Harness planning."
        ),
    )
    configure_goal_parser.add_argument(
        "--lark-kanban-heartbeat-sync",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable or disable best-effort generic Lark Kanban sync from goal "
            "heartbeats. Default is off; a local board binding alone never enables it."
        ),
    )
    configure_goal_parser.add_argument(
        "--explore-harness-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable or disable analysis-only explore planning for this goal. "
            "This does not grant child-agent spawn permission."
        ),
    )
    configure_goal_parser.add_argument(
        "--explore-harness-profile",
        choices=EXPLORE_HARNESS_PROFILES,
        help="Pin the explore planner profile on this goal's orchestration boundary.",
    )
    configure_goal_parser.add_argument(
        "--clear-explore-harness-profile",
        action="store_true",
        help="Remove the goal-pinned explore profile while preserving the opt-in bit.",
    )
    configure_goal_parser.add_argument(
        "--registered-agent",
        dest="registered_agents",
        action="append",
        default=None,
        help=(
            "Registered public-safe agent id allowed to claim todos and receive scoped "
            "heartbeat prompts. Repeatable; comma-separated values are also accepted."
        ),
    )
    configure_goal_parser.add_argument(
        "--clear-registered-agents",
        action="store_true",
        help="Clear coordination.registered_agents.",
    )
    configure_goal_parser.add_argument(
        "--peer-task-coordinator",
        help=(
            "Explicitly select one registered peer to coordinate peer-owned task "
            "lanes. Registration alone never enables coordination."
        ),
    )
    configure_goal_parser.add_argument(
        "--clear-peer-task-coordinator",
        action="store_true",
        help="Disable registered-peer task coordination for this goal.",
    )
    configure_goal_parser.add_argument(
        "--agent-profile-json",
        dest="agent_profile_jsons",
        action="append",
        default=None,
        help=(
            "Validated agent_profile_v1 JSON object for a registered peer. "
            "Repeatable; writes advisory task routing hints plus the peer lane's "
            "vision requirement."
        ),
    )
    configure_goal_parser.add_argument(
        "--clear-agent-profile",
        dest="clear_agent_profiles",
        action="append",
        default=None,
        help="Registered agent id whose advisory profile should be removed. Repeatable.",
    )
    configure_goal_parser.add_argument(
        "--agent-work-mode",
        dest="agent_work_modes",
        action="append",
        default=None,
        metavar="AGENT_ID=MODE",
        help=(
            "Set one registered peer's runtime work mode to active or monitor_only. "
            "Repeatable."
        ),
    )
    configure_goal_parser.add_argument(
        "--clear-agent-work-mode",
        dest="clear_agent_work_modes",
        action="append",
        default=None,
        help="Registered agent id whose explicit runtime work mode should be removed.",
    )
    configure_goal_parser.add_argument(
        "--todo-lifecycle-authority-json",
        dest="todo_lifecycle_authority_jsons",
        action="append",
        default=None,
        help=(
            "JSON grant with agent_id, actions, and requires_reason for explicit "
            "cross-owner todo lifecycle overrides. Repeatable."
        ),
    )
    configure_goal_parser.add_argument(
        "--clear-todo-lifecycle-authority",
        action="append",
        default=None,
        help=(
            "Registered agent id whose todo lifecycle authority grant should be "
            "removed. Repeatable."
        ),
    )
    register_peer_runtime_arguments(configure_goal_parser)
    register_peer_supervisor_arguments(configure_goal_parser)
    configure_goal_parser.add_argument(
        "--write-scope",
        action="append",
        default=None,
        help=(
            "Allowed repository/local-state write scope to add to coordination.write_scope. "
            "Repeatable; comma-separated values are also accepted."
        ),
    )
    configure_goal_parser.add_argument(
        "--replace-write-scope",
        action="store_true",
        help="Replace coordination.write_scope with the supplied --write-scope values instead of merging.",
    )
    configure_goal_parser.add_argument(
        "--clear-write-scope",
        action="store_true",
        help="Clear coordination.write_scope.",
    )
    configure_goal_parser.add_argument(
        "--local-authority-shadow-file",
        action="store_true",
        help=(
            "Enable default-off, one-way capture of post-commit local snapshots "
            "in FileAuthorityStore. This does not compare source and candidate."
        ),
    )
    configure_goal_parser.add_argument(
        "--clear-local-authority-shadow",
        action="store_true",
        help=(
            "Disable the local authority shadow. This does not delete retained "
            "candidate observations."
        ),
    )
    configure_goal_parser.add_argument(
        "--waiting-on",
        choices=["codex", "user_or_controller", "controller", "external_evidence"],
        help="Override registry waiting owner for status/quota routing.",
    )
    configure_goal_parser.add_argument(
        "--clear-waiting-on",
        action="store_true",
        help="Remove the registry waiting_on override.",
    )
    configure_goal_parser.add_argument(
        "--boundary-authority-scope",
        action="append",
        default=None,
        help=(
            "Checkpointed write scope approved by an operator/controller decision. "
            "Repeatable; comma-separated values are also accepted."
        ),
    )
    configure_goal_parser.add_argument(
        "--boundary-authority-source",
        help="Public-safe provenance for the checkpointed boundary authority.",
    )
    configure_goal_parser.add_argument(
        "--boundary-authority-decision-id",
        help="Public-safe decision/run/gate id for the checkpointed boundary authority.",
    )
    configure_goal_parser.add_argument(
        "--boundary-authority-recorded-at",
        help="ISO timestamp for the checkpointed decision. Defaults to now.",
    )
    configure_goal_parser.add_argument(
        "--boundary-authority-expires-at",
        help="Optional ISO timestamp after which the checkpointed authority is no longer fresh.",
    )
    configure_goal_parser.add_argument(
        "--clear-boundary-authority",
        action="store_true",
        help="Clear coordination.checkpointed_boundary_authority.",
    )
    configure_goal_parser.add_argument(
        "--issue-fix-reviewer-notification-config",
        help=(
            "Register a repo-relative local-private JSON config pointer under "
            ".loopx/config/ for automatic issue-fix reviewer notifications."
        ),
    )
    configure_goal_parser.add_argument(
        "--clear-issue-fix-reviewer-notification-config",
        action="store_true",
        help="Remove the goal's automatic reviewer-notification config pointer.",
    )
    configure_goal_parser.add_argument(
        "--lark-event-inbox-config",
        help=(
            "Register a repo-relative local-private generic Lark event inbox "
            "config pointer under .loopx/config/."
        ),
    )
    configure_goal_parser.add_argument(
        "--lark-event-inbox-agent-id",
        help=(
            "Bind the Lark event inbox pointer to one registered Agent. "
            "Without this option the legacy Goal-wide pointer is configured."
        ),
    )
    configure_goal_parser.add_argument(
        "--clear-lark-event-inbox-config",
        action="store_true",
        help="Remove the goal's generic Lark event inbox config pointer.",
    )
    configure_goal_parser.add_argument(
        "--reward-memory-config",
        help=(
            "Register a repo-relative ignored provider-neutral Reward Memory "
            "experiment config under .loopx/config/."
        ),
    )
    configure_goal_parser.add_argument(
        "--reward-memory-agent",
        dest="reward_memory_agents",
        action="append",
        default=None,
        help=(
            "Registered agent id allowed to use the Reward Memory experiment. "
            "Repeatable; comma-separated values are also accepted."
        ),
    )
    configure_goal_parser.add_argument(
        "--clear-reward-memory-config",
        action="store_true",
        help="Disable the Reward Memory experiment and clear its agent allowlist.",
    )
    configure_goal_parser.add_argument(
        "--execute",
        action="store_true",
        help="Write the registry. Without this flag, configure-goal is a dry-run preview.",
    )
