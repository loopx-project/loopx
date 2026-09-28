from __future__ import annotations

import argparse

from ..control_plane.quota.error_codes import QuotaCommandValidationError
from ..control_plane.todos.contract import TODO_CONTINUATION_POLICY_VALUES

QUOTA_SHOULD_RUN_DETAIL_SECTIONS = (
    "scheduler",
    "agent-todos",
    "user-todos",
    "goal-boundary",
    "vision",
)
QUOTA_MONITOR_POLL_DETAIL_SECTIONS = ("decisions",)
QUOTA_PLAN_DETAIL_SECTIONS = ("agent-todos", "user-todos")
QUOTA_COMMAND_DETAIL_SECTIONS = {
    "status": QUOTA_PLAN_DETAIL_SECTIONS,
    "plan": QUOTA_PLAN_DETAIL_SECTIONS,
    "should-run": QUOTA_SHOULD_RUN_DETAIL_SECTIONS,
    "monitor-poll": QUOTA_MONITOR_POLL_DETAIL_SECTIONS,
}
QUOTA_DETAIL_SECTIONS = (
    *QUOTA_SHOULD_RUN_DETAIL_SECTIONS,
    *QUOTA_MONITOR_POLL_DETAIL_SECTIONS,
)


def register_quota_monitor_poll_request_arguments(
    quota_parser: argparse.ArgumentParser,
) -> None:
    quota_parser.add_argument(
        "--todo-id",
        help=(
            "For Codex App `quota should-run`, select one currently projected "
            "eligible action through typed same-turn qualification. For "
            "`quota monitor-poll`, name the observed monitor Todo; a committed "
            "advancement settlement Todo remains separate under the auxiliary "
            "no-spend observation contract. Otherwise name the accountable "
            "Todo settlement target."
        ),
    )
    quota_parser.add_argument("--target-key", help="Stable monitor target key for `quota monitor-poll` metadata writeback.")
    quota_parser.add_argument("--result-hash", help="Public-safe result hash observed by `quota monitor-poll`.")
    quota_parser.add_argument("--material-change", action="store_true", help="Mark a monitor poll as a material transition instead of unchanged evidence.")
    quota_parser.add_argument("--cadence", help="Monitor cadence used to compute the next due timestamp, e.g. 30m, 2h, or 1d.")
    quota_parser.add_argument("--next-due-at", help="Explicit ISO timestamp for the next monitor poll.")
    quota_parser.add_argument(
        "--next-agent-todo",
        help=(
            "Independent runnable advancement_task emitted when a monitor poll "
            "uses --material-change; the continuous_monitor remains observe-only."
        ),
    )
    quota_parser.add_argument(
        "--next-action-kind",
        help="Explicit action kind for a material monitor's --next-agent-todo successor.",
    )
    quota_parser.add_argument(
        "--next-task-repository",
        help=(
            "Credential-free Git repository identity for a material monitor's "
            "--next-agent-todo successor."
        ),
    )
    quota_parser.add_argument(
        "--next-required-capability",
        dest="next_required_capabilities",
        action="append",
        help=(
            "Execution capability required by a material monitor's --next-agent-todo "
            "successor. Repeat for multiple capabilities."
        ),
    )
    quota_parser.add_argument(
        "--next-continuation-policy",
        choices=sorted(TODO_CONTINUATION_POLICY_VALUES),
        help=(
            "Continuation policy for a material monitor's --next-agent-todo successor. "
            "Defaults to independent_handoff."
        ),
    )
    quota_parser.add_argument(
        "--next-target-key",
        help=(
            "Stable public-safe target key for a material monitor's --next-agent-todo "
            "successor. Defaults to a deterministic monitor-transition key."
        ),
    )
    quota_parser.add_argument("--next-user-todo", help="User follow-up todo to add when `--material-change` is set.")
    quota_parser.add_argument(
        "--next-user-task-class",
        choices=["user_gate", "user_action"],
        help=(
            "Required with monitor-poll `--next-user-todo`: user_gate for a "
            "blocking owner decision or user_action for a visible reminder "
            "that must not block the bound agent lane."
        ),
    )
    quota_parser.add_argument("--task-lease-idempotency-key",
        help="Current Monitor execution key for canonical quota monitor-poll; requires --task-lease-expected-version.")
    quota_parser.add_argument("--task-lease-expected-version", type=int,
        help="Current Monitor lease version, checked atomically with observation and successors; never renews the lease.")
    quota_parser.add_argument("--use-current-task-lease", action="store_true",
        help=("For an executing, turn-scoped monitor-poll with --todo-id, resolve the canonical "
              "lease proof or replay the exact prior transaction proof. Does not acquire or renew a lease."))
    quota_parser.add_argument("--next-claimed-by", help="Registered agent id to claim the `--next-agent-todo` follow-up.")


def validate_quota_command_request(args: argparse.Namespace) -> None:
    command = args.quota_command
    lease_key = getattr(args, "task_lease_idempotency_key", None)
    lease_version = getattr(args, "task_lease_expected_version", None)
    use_current_lease = bool(getattr(args, "use_current_task_lease", False))
    if use_current_lease:
        if command != "monitor-poll" or not args.execute:
            raise QuotaCommandValidationError("--use-current-task-lease requires executing quota monitor-poll")
        if not args.todo_id or not args.turn_instance_id or not args.agent_id:
            raise QuotaCommandValidationError(
                "--use-current-task-lease requires --todo-id, --turn-instance-id, and --agent-id"
            )
        if lease_key is not None or lease_version is not None:
            raise QuotaCommandValidationError(
                "--use-current-task-lease cannot be combined with explicit task lease proof"
            )
    if lease_key is not None or lease_version is not None:
        if command != "monitor-poll":
            raise QuotaCommandValidationError("task lease proof is only valid with quota monitor-poll")
        if not lease_key or lease_version is None or lease_version < 1 or lease_version > 9007199254740991:
            raise QuotaCommandValidationError("Monitor lease proof requires an execution key and positive safe-integer version")
        if not (args.todo_id or args.target_key):
            raise QuotaCommandValidationError("Monitor lease proof requires --todo-id or --target-key")
    begin_turn = bool(getattr(args, "begin_turn", False))
    if command not in {"status", "plan"} and not args.goal_id:
        raise QuotaCommandValidationError(
            f"`loopx quota {command}` requires --goal-id"
        )
    scheduler_commands = {
        "scheduler-ack",
        "scheduler-ack-current",
        "scheduler-fail-current",
    }
    if command in scheduler_commands and not args.agent_id:
        raise QuotaCommandValidationError(
            f"`loopx quota {command}` requires --agent-id"
        )
    if command == "void-slot" and not args.void_generated_at:
        raise QuotaCommandValidationError(
            "`loopx quota void-slot` requires --void-generated-at"
        )
    if command == "should-run" and args.todo_id and not args.turn_instance_id:
        raise QuotaCommandValidationError(
            "`loopx quota should-run --todo-id` requires --turn-instance-id"
        )
    if begin_turn and command != "should-run":
        raise QuotaCommandValidationError(
            "--begin-turn is only valid with `loopx quota should-run`"
        )
    if begin_turn and args.turn_instance_id:
        raise QuotaCommandValidationError(
            "--begin-turn cannot be combined with --turn-instance-id"
        )
    if begin_turn and args.todo_id:
        raise QuotaCommandValidationError(
            "--begin-turn cannot select --todo-id; reuse the exact "
            "--turn-instance-id returned by the initial guard"
        )
    if begin_turn and not args.agent_id:
        raise QuotaCommandValidationError(
            "--begin-turn requires exact --agent-id identity"
        )
    if (
        command not in {"status", "plan", "should-run"}
        and args.dry_run
        and args.execute
    ):
        raise QuotaCommandValidationError(
            f"`loopx quota {command}` accepts only one of --dry-run or --execute"
        )


def quota_detail_sections_from_args(args: argparse.Namespace) -> frozenset[str]:
    sections = set(getattr(args, "include_details", None) or ())
    if bool(getattr(args, "include_scheduler_detail", False)):
        sections.add("scheduler")
    if "all" in sections:
        sections.update(
            QUOTA_COMMAND_DETAIL_SECTIONS.get(args.quota_command, ())
        )
        sections.discard("all")
    return frozenset(sections)
