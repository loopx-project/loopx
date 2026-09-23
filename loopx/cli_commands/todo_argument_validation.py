from __future__ import annotations

import argparse
from collections.abc import Iterable

from ..control_plane.todos.contract import TODO_CONTINUATION_POLICY_VALUES

TODO_OPTION_FIELDS = (
    ("--role", "role"),
    ("--text", "text"),
    ("--priority", "priority"),
    ("--clear-priority", "clear_priority"),
    ("--todo-id", "todo_id"),
    ("--operation-id", "operation_id"),
    ("--claim-operation-id", "claim_operation_id"),
    ("--update-operation-id", "update_operation_id"),
    ("--update-expected-provider-revision", "update_expected_provider_revision"),
    ("--turn-instance-id", "turn_instance_id"),
    ("--completion-identity-key", "completion_identity_key"),
    ("--replan-obligation-id", "replan_obligation_id"),
    ("--status", "status"),
    ("--note", "note"),
    ("--evidence", "evidence"),
    ("--reason", "reason"),
    ("--authority-reason", "authority_reason"),
    ("--task-class", "task_class"),
    ("--action-kind", "action_kind"),
    ("--task-domain", "task_domain"),
    ("--capability-binding-ref", "capability_binding_ref"),
    ("--task-repository", "task_repository"),
    ("--continuation-policy", "continuation_policy"),
    ("--required-write-scope", "required_write_scopes"),
    ("--required-capability", "required_capabilities"),
    ("--target-capability", "target_capabilities"),
    ("--capability-gap-status", "capability_gap_status"),
    ("--explore-result-node-ref", "explore_result_node_refs"),
    ("--clear-explore-result-node-refs", "clear_explore_result_node_refs"),
    ("--decision-scope", "decision_scope"),
    ("--required-decision-scope", "required_decision_scopes"),
    ("--decision-outcome", "decision_outcome"),
    ("--claimed-by", "claimed_by"),
    ("--task-lease-idempotency-key", "task_lease_idempotency_key"),
    ("--task-lease-expected-version", "task_lease_expected_version"),
    ("--bound-agent", "bound_agent"),
    ("--goal-bound", "goal_bound"),
    ("--blocks-agent", "blocks_agent"),
    ("--clear-blocks-agent", "clear_blocks_agent"),
    ("--excluded-agent", "excluded_agents"),
    ("--clear-excluded-agents", "clear_excluded_agents"),
    ("--global-gate", "global_gate"),
    ("--clear-global-gate", "clear_global_gate"),
    ("--unblocks-todo-id", "unblocks_todo_id"),
    ("--successor-todo-id", "successor_todo_ids"),
    ("--resume-when", "resume_when"),
    ("--validation-command", "validation_command"),
    ("--validation-command-json", "validation_command_json"),
    ("--validation-label", "validation_label"),
    ("--validation-timeout-seconds", "validation_timeout_seconds"),
    ("--clear-resume-when", "clear_resume_when"),
    ("--target-key", "monitor_target_key"),
    ("--cadence", "cadence"),
    ("--next-due-at", "next_due_at"),
    ("--expires-at", "expires_at"),
    ("--watch-only", "watch_only"),
    ("--clear-claim", "clear_claim"),
    ("--no-follow-up", "no_follow_up"),
    ("--next-agent-todo", "next_agent_todo"),
    ("--next-user-todo", "next_user_todo"),
    ("--next-user-task-class", "next_user_task_class"),
    ("--next-claimed-by", "next_claimed_by"),
    ("--next-task-class", "next_task_class"),
    ("--next-action-kind", "next_action_kind"),
    ("--next-task-repository", "next_task_repository"),
    ("--next-required-capability", "next_required_capabilities"),
    ("--next-continuation-policy", "next_continuation_policy"),
    ("--next-excluded-agent", "next_excluded_agents"),
    ("--self-merged", "self_merged"),
    ("--agent-id", "agent_id"),
    ("--limit", "todo_limit"),
    ("--thin", "todo_thin"),
    ("--state-file", "state_file"),
    ("--execute", "execute"),
    ("--provider-revision", "provider_revision"),
)

_TODO_UPDATE_MUTABLE_FIELDS = (
    "text", "priority", "clear_priority", "status", "note", "evidence", "reason", "task_class",
    "action_kind", "task_domain", "task_repository", "continuation_policy", "required_write_scopes",
    "required_capabilities", "target_capabilities", "capability_gap_status",
    "explore_result_node_refs", "clear_explore_result_node_refs", "decision_scope",
    "required_decision_scopes", "claimed_by", "bound_agent", "goal_bound",
    "blocks_agent", "clear_blocks_agent", "excluded_agents", "clear_excluded_agents",
    "global_gate", "clear_global_gate", "unblocks_todo_id", "successor_todo_ids",
    "resume_when", "clear_resume_when", "no_follow_up", "monitor_target_key",
    "cadence", "next_due_at", "expires_at", "watch_only", "clear_claim",
    "validation_command", "validation_command_json", "validation_label",
    "validation_timeout_seconds",
)

_TODO_UPDATE_UNSUPPORTED_FIELDS = (
    (
        "decision_outcome",
        "todo update does not accept --decision-outcome; use todo complete",
    ),
    ("next_claimed_by", "todo update does not support --next-claimed-by"),
    (
        "next_task_repository",
        "todo update does not support --next-task-repository",
    ),
    (
        "next_required_capabilities",
        "todo update does not support --next-required-capability",
    ),
    (
        "next_continuation_policy",
        "todo update does not support --next-continuation-policy",
    ),
    ("next_excluded_agents", "todo update does not support --next-excluded-agent"),
    ("self_merged", "todo update does not support --self-merged"),
)

_TODO_ADD_INITIAL_RULES = (
    ("decision_outcome", True, "does not accept --decision-outcome; record it on completion"),
    ("role", False, "requires --role"),
    ("text", False, "requires --text"),
    ("clear_claim", True, "accepts --claimed-by but not --clear-claim"),
    (
        "clear_explore_result_node_refs",
        True,
        "accepts --explore-result-node-ref but not --clear-explore-result-node-refs",
    ),
)
_TODO_ADD_UNSUPPORTED_FIELDS = (
    "next_claimed_by", "next_task_repository", "next_required_capabilities",
    "next_continuation_policy", "next_excluded_agents", "clear_excluded_agents",
    "clear_blocks_agent", "self_merged", "no_follow_up", "clear_priority",
)
_TODO_OPTION_FLAGS = {field: flag for flag, field in TODO_OPTION_FIELDS}


def register_todo_linkage_arguments(
    todo_parser: argparse.ArgumentParser,
) -> None:
    todo_parser.add_argument(
        "--unblocks-todo-id",
        help=(
            "For todo add/update, link this todo to the blocked todo it unblocks, "
            "for example todo_ab12cd34ef56. Completing an exactly linked user_gate "
            "also consumes the target required decision scopes covered by that gate."
        ),
    )
    todo_parser.add_argument(
        "--successor-todo-id",
        dest="successor_todo_ids",
        action="append",
        help=(
            "For todo update/complete, link an existing successor todo to the "
            "current todo. Repeat for multiple successors."
        ),
    )
    todo_parser.add_argument(
        "--resume-when",
        help=(
            "For deferred todo add/update, or for an open advancement todo update "
            "paired with --successor-todo-id, declare a machine-readable resume "
            "condition such as todo_done:todo_ab12cd34ef56, "
            "monitor_changed:todo_monitor123, pr_merged:#532, or "
            "capacity_available:short_pool; use "
            "resume_at:2026-09-14T09:30:00+08:00 for an exact dated wakeup. "
            "resume_at requires an RFC3339 timezone and is normalized to UTC. "
            "monitor_changed binds the monitor's "
            "current material-change generation and resumes only after it advances; "
            "the waiting advancement todo must remain status=open and pair with an "
            "independent runnable --successor-todo-id."
        ),
    )
    todo_parser.add_argument(
        "--clear-resume-when",
        action="store_true",
        help=(
            "For todo update, remove the existing resume condition after its "
            "successor replan has made the todo runnable."
        ),
    )


def register_todo_successor_creation_arguments(
    todo_parser: argparse.ArgumentParser,
) -> None:
    todo_parser.add_argument(
        "--next-agent-todo",
        help="For complete/supersede, atomically add or update the next agent todo.",
    )
    todo_parser.add_argument(
        "--next-user-todo",
        help="For complete/supersede, atomically add or update the next user todo.",
    )
    todo_parser.add_argument(
        "--next-user-task-class",
        choices=["user_gate", "user_action"],
        help=(
            "Required with --next-user-todo: user_gate for a blocking owner "
            "decision or user_action for a visible reminder that must not block "
            "the bound agent lane."
        ),
    )
    todo_parser.add_argument(
        "--next-claimed-by",
        help=(
            "For complete/supersede with --next-agent-todo, soft-claim the successor "
            "todo for a registered agent. Independent handoffs remain unclaimed unless "
            "explicitly assigned, while same-agent non-delivery continuations keep the "
            "current owner. Use --self-merged with --evidence for an eligible same-agent "
            "delivery."
        ),
    )
    todo_parser.add_argument(
        "--self-merged",
        action="store_true",
        help=(
            "For todo complete, record that a small validated change was self-merged; "
            "requires --evidence."
        ),
    )
    todo_parser.add_argument(
        "--next-task-class",
        choices=["advancement_task", "continuous_monitor", "blocker"],
        help="Task class for --next-agent-todo. Defaults to advancement_task.",
    )
    todo_parser.add_argument(
        "--next-action-kind",
        help="Action kind for --next-agent-todo.",
    )
    todo_parser.add_argument(
        "--next-task-repository",
        help=(
            "Credential-free Git repository identity for --next-agent-todo, such as "
            "git:github.com/owner/repo."
        ),
    )
    todo_parser.add_argument(
        "--next-required-capability",
        dest="next_required_capabilities",
        action="append",
        help=(
            "Execution capability required by --next-agent-todo. Repeat for multiple "
            "capabilities."
        ),
    )
    todo_parser.add_argument(
        "--next-continuation-policy",
        choices=sorted(TODO_CONTINUATION_POLICY_VALUES),
        help="Continuation policy for --next-agent-todo.",
    )
    todo_parser.add_argument(
        "--next-excluded-agent",
        dest="next_excluded_agents",
        action="append",
        help=(
            "For complete/supersede with --next-agent-todo, exclude one registered "
            "peer from claiming or executing the successor. Repeat for multiple peers."
        ),
    )


def unsupported_todo_options(
    args: argparse.Namespace,
    *,
    allowed_fields: Iterable[str],
) -> list[str]:
    allowed = set(allowed_fields)
    return [
        flag
        for flag, field in TODO_OPTION_FIELDS
        if field not in allowed and getattr(args, field, None)
    ]


def _validate_todo_option_subset(args: argparse.Namespace, allowed_fields: Iterable[str], error_prefix: str) -> None:
    unsupported = unsupported_todo_options(args, allowed_fields=allowed_fields)
    if unsupported:
        raise ValueError(error_prefix + ", ".join(unsupported))


def validate_todo_list_options(args: argparse.Namespace) -> None:
    _validate_todo_option_subset(
        args,
        {
            "role",
            "todo_id",
            "status",
            "agent_id",
            "todo_limit",
            "todo_thin",
            "state_file",
        },
        "todo list only accepts --goal-id, optional --role, --status, --todo-id, "
        "--agent-id, --limit, --thin, --project, --state-file, --dry-run, and "
        "--format; "
        "unsupported: ",
    )


def validate_todo_receipt_options(args: argparse.Namespace) -> None:
    _validate_todo_option_subset(
        args, {"operation_id"},
        "todo receipt only accepts --goal-id, --operation-id, and --format; unsupported: ",
    )
    if not args.operation_id:
        raise ValueError("todo receipt requires --operation-id")


def validate_todo_plan_options(args: argparse.Namespace) -> None:
    _validate_todo_option_subset(
        args, {"text", "agent_id"},
        "todo plan only accepts --goal-id, --agent-id, --text, --project and --format; unsupported: ",
    )
    if not args.text or not args.agent_id:
        raise ValueError("todo plan requires --text and a registered --agent-id")


def validate_todo_project_markdown_options(args: argparse.Namespace) -> None:
    if not getattr(args, "provider_revision", None):
        raise ValueError("todo project-markdown requires --provider-revision")
    _validate_todo_option_subset(
        args,
        {"state_file", "execute", "provider_revision"},
        "todo project-markdown only accepts --goal-id, --provider-revision, "
        "--project, --state-file, --execute, and "
        "--format; unsupported: ",
    )


def validate_todo_add_options(args: argparse.Namespace) -> None:
    for field, reject_when_present, message in _TODO_ADD_INITIAL_RULES:
        if bool(getattr(args, field)) is reject_when_present:
            raise ValueError(f"todo add {message}")
    for field in _TODO_ADD_UNSUPPORTED_FIELDS:
        if getattr(args, field):
            raise ValueError(f"todo add does not support {_TODO_OPTION_FLAGS[field]}")
    if args.successor_todo_ids:
        raise ValueError("todo add does not support --successor-todo-id; use todo update/complete to link existing successor work")


def validate_todo_claim_options(args: argparse.Namespace) -> None:
    if not args.todo_id:
        raise ValueError("todo claim requires --todo-id")
    if not args.claimed_by:
        raise ValueError("todo claim requires --claimed-by")
    if args.clear_claim:
        raise ValueError(
            "todo claim requires --claimed-by and does not support --clear-claim"
        )
    _validate_todo_option_subset(
        args,
        {
            "role", "todo_id", "claimed_by", "agent_id", "state_file",
            "claim_operation_id", "task_lease_idempotency_key",
            "task_lease_expected_version",
        },
        "todo claim only accepts --todo-id, --claimed-by, --agent-id, optional --role, "
        "--claim-operation-id, --task-lease-idempotency-key, "
        "--task-lease-expected-version, --project, --state-file, and --dry-run; unsupported: ",
    )
    if args.task_lease_expected_version is not None and not args.task_lease_idempotency_key:
        raise ValueError(
            "--task-lease-expected-version requires --task-lease-idempotency-key"
        )


def validate_todo_update_options(args: argparse.Namespace) -> None:
    if not args.todo_id:
        raise ValueError("todo update requires --todo-id")
    if args.claimed_by and args.clear_claim:
        raise ValueError(
            "todo update accepts either --claimed-by or --clear-claim, not both"
        )
    if args.explore_result_node_refs and args.clear_explore_result_node_refs:
        raise ValueError(
            "todo update accepts either --explore-result-node-ref or "
            "--clear-explore-result-node-refs, not both"
        )
    if not any(getattr(args, field) for field in _TODO_UPDATE_MUTABLE_FIELDS):
        raise ValueError("todo update requires at least one mutable todo field")
    validation_fields = (
        args.validation_command,
        args.validation_command_json,
        args.validation_label,
        args.validation_timeout_seconds,
    )
    if any(value is not None for value in validation_fields):
        if bool(args.validation_command) == bool(args.validation_command_json):
            raise ValueError(
                "todo update validation revision requires exactly one of "
                "--validation-command or --validation-command-json"
            )
        if not args.update_operation_id or not args.update_expected_provider_revision:
            raise ValueError(
                "todo update validation revision requires --update-operation-id "
                "and --update-expected-provider-revision"
            )
        if not args.agent_id:
            raise ValueError(
                "todo update validation revision requires a registered --agent-id"
            )
        other_fields = tuple(
            field for field in _TODO_UPDATE_MUTABLE_FIELDS
            if field not in {
                "validation_command", "validation_command_json",
                "validation_label", "validation_timeout_seconds",
            }
        )
        if any(getattr(args, field) for field in other_fields):
            raise ValueError(
                "todo update validation revision cannot be combined with another Todo edit"
            )
    if args.no_follow_up and not (args.note or args.reason or args.evidence):
        raise ValueError("--no-follow-up requires --note, --reason, or --evidence")
    for field, message in _TODO_UPDATE_UNSUPPORTED_FIELDS:
        if getattr(args, field):
            raise ValueError(message)


def validate_todo_complete_options(args: argparse.Namespace) -> None:
    if not args.todo_id:
        raise ValueError("todo complete requires --todo-id")
    if args.explore_result_node_refs or args.clear_explore_result_node_refs:
        raise ValueError("todo complete does not update --explore-result-node-ref; use todo update first")
    if args.claimed_by and args.clear_claim:
        raise ValueError("todo complete accepts either --claimed-by or --clear-claim, not both")
    if args.task_lease_expected_version is not None and not args.task_lease_idempotency_key:
        raise ValueError(
            "--task-lease-expected-version requires --task-lease-idempotency-key"
        )
    if args.turn_instance_id and args.completion_identity_key:
        raise ValueError(
            "todo complete accepts either --turn-instance-id or "
            "--completion-identity-key, not both"
        )
    if args.completion_identity_key and not args.no_follow_up:
        raise ValueError("--completion-identity-key is only valid with --no-follow-up")
    if any(getattr(args, field) for field in ("task_repository", "bound_agent", "goal_bound", "blocks_agent", "clear_blocks_agent", "excluded_agents", "clear_excluded_agents", "global_gate", "clear_global_gate", "unblocks_todo_id", "resume_when")):
        raise ValueError("todo complete does not update current todo routing metadata; use todo update first")
    if any(getattr(args, field) for field in ("monitor_target_key", "cadence", "next_due_at", "expires_at")):
        raise ValueError("todo complete does not update target or monitor schedule metadata; use todo update before completion")
    if args.no_follow_up and (args.next_agent_todo or args.next_user_todo):
        raise ValueError("--no-follow-up cannot be combined with successor todos")
    if args.no_follow_up and args.successor_todo_ids:
        raise ValueError("--no-follow-up cannot be combined with successor todos")
    if args.successor_todo_ids and (args.next_agent_todo or args.next_user_todo):
        raise ValueError("--successor-todo-id links existing work and cannot be combined with --next-agent-todo or --next-user-todo")
    if args.no_follow_up and not (args.note or args.evidence):
        raise ValueError("--no-follow-up requires --note or --evidence")
    if args.continuation_policy:
        raise ValueError("todo complete does not update --continuation-policy; use todo update first")
    validate_successor_routing_options(args)


def validate_todo_supersede_options(args: argparse.Namespace) -> None:
    if not args.todo_id:
        raise ValueError("todo supersede requires --todo-id")
    if args.task_lease_expected_version is not None and not args.task_lease_idempotency_key:
        raise ValueError(
            "--task-lease-expected-version requires --task-lease-idempotency-key"
        )
    if args.explore_result_node_refs or args.clear_explore_result_node_refs:
        raise ValueError("todo supersede does not update --explore-result-node-ref; use todo update first")
    if args.decision_outcome:
        raise ValueError("todo supersede does not accept --decision-outcome; use todo complete")
    if args.claimed_by:
        raise ValueError("todo supersede does not support --claimed-by; use --next-claimed-by to assign the successor, or omit it to inherit the superseded todo owner when present")
    if args.clear_claim:
        raise ValueError("todo supersede does not support --clear-claim")
    if args.self_merged:
        raise ValueError("todo supersede does not support --self-merged")
    if args.no_follow_up:
        raise ValueError("todo supersede does not support --no-follow-up")
    if args.continuation_policy:
        raise ValueError("todo supersede does not update --continuation-policy; use todo update first")
    validate_successor_routing_options(args)
    if any(getattr(args, field) for field in ("blocks_agent", "clear_blocks_agent", "excluded_agents", "clear_excluded_agents", "global_gate", "clear_global_gate", "unblocks_todo_id", "resume_when")):
        raise ValueError("todo supersede does not update current todo routing metadata; use todo update first")
    if args.successor_todo_ids:
        raise ValueError("todo supersede does not support --successor-todo-id; use --next-agent-todo or update the source todo before supersede")
    if any(getattr(args, field) for field in ("monitor_target_key", "cadence", "next_due_at", "expires_at")):
        raise ValueError("todo supersede does not update target or monitor schedule metadata; use todo update before supersede")


def validate_todo_archive_completed_options(args: argparse.Namespace) -> None:
    checks = (
        (args.decision_outcome, "todo archive-completed does not support --decision-outcome"),
        (args.claimed_by or args.clear_claim, "todo archive-completed does not support --claimed-by or --clear-claim"),
        (any(getattr(args, field) for field in ("clear_blocks_agent", "excluded_agents", "clear_excluded_agents", "next_excluded_agents")), "todo archive-completed does not support executor exclusions"),
        (args.next_claimed_by, "todo archive-completed does not support --next-claimed-by"),
        (args.next_task_repository or args.next_required_capabilities, "todo archive-completed does not support successor routing metadata"),
        (args.self_merged, "todo archive-completed does not support --self-merged"),
        (args.no_follow_up, "todo archive-completed does not support --no-follow-up"),
        (args.successor_todo_ids, "todo archive-completed does not support --successor-todo-id"),
    )
    for triggered, message in checks:
        if triggered:
            raise ValueError(message)


def validate_shared_todo_options(args: argparse.Namespace) -> None:
    if getattr(args, "operation_id", None) and args.todo_command != "receipt":
        raise ValueError("--operation-id is supported only by todo receipt")
    agent_id_allowed_for_user_authoring = (
        args.todo_command == "add"
        and args.role == "user"
        and args.task_class in {"user_gate", "user_action"}
    )
    agent_id_allowed_for_read = args.todo_command == "list"
    agent_id_allowed_for_lifecycle = args.todo_command in {
        "claim",
        "update",
        "complete",
        "supersede",
    }
    global_gate_allowed = args.todo_command in {"add", "update"}
    clear_global_gate_allowed = args.todo_command == "update"
    authority_reason_allowed = args.todo_command in {
        "update",
        "complete",
        "supersede",
    }
    if getattr(args, "turn_instance_id", None) and args.todo_command != "complete":
        raise ValueError(
            "--turn-instance-id is supported only by todo complete settlement"
        )
    if getattr(args, "update_operation_id", None) is not None and args.todo_command != "update":
        raise ValueError("--update-operation-id is supported only by todo update")
    if getattr(args, "update_expected_provider_revision", None) is not None and args.todo_command != "update":
        raise ValueError("--update-expected-provider-revision is supported only by todo update")
    if getattr(args, "claim_operation_id", None) is not None and args.todo_command != "claim":
        raise ValueError("--claim-operation-id is supported only by todo claim")
    if (
        getattr(args, "completion_identity_key", None)
        and args.todo_command != "complete"
    ):
        raise ValueError(
            "--completion-identity-key is supported only by todo complete reentry"
        )
    if (
        getattr(args, "replan_obligation_id", None)
        and args.todo_command != "add"
    ):
        raise ValueError(
            "--replan-obligation-id is supported only by todo add"
        )
    if (
        args.todo_command not in {"claim", "update", "complete", "supersede"}
        and (
            args.task_lease_idempotency_key
            or args.task_lease_expected_version is not None
        )
    ):
        raise ValueError(
            "--task-lease-idempotency-key and --task-lease-expected-version "
            "are supported only by todo claim, todo update, todo complete, and todo supersede"
        )
    if args.capability_binding_ref and args.todo_command != "add":
        raise ValueError(
            "--capability-binding-ref is immutable and supported only by todo add"
        )
    if args.authority_reason and not authority_reason_allowed:
        raise ValueError(
            "--authority-reason is supported only by todo update/complete/supersede"
        )
    if (
        args.todo_command != "plan"
        and args.agent_id
        and not agent_id_allowed_for_user_authoring
        and not agent_id_allowed_for_read
        and not agent_id_allowed_for_lifecycle
    ):
        if args.todo_command == "add" and args.role == "agent":
            raise ValueError(
                "todo add does not support --agent-id for agent todos; omit "
                "--agent-id and use --claimed-by <registered-agent> only when "
                "assigning execution, or omit both options to leave the todo "
                "unclaimed."
            )
        raise ValueError(
            f"todo {args.todo_command} does not support --agent-id; --agent-id "
            "scopes todo list, user-todo authoring, and lifecycle actor "
            "attribution only."
        )
    if args.global_gate and not global_gate_allowed:
        raise ValueError(
            "--global-gate is supported only by todo add/update for user_gate items"
        )
    if args.clear_global_gate and not clear_global_gate_allowed:
        raise ValueError(
            "--clear-global-gate is supported only by todo update for user_gate items"
        )
    if args.clear_resume_when and args.todo_command != "update":
        raise ValueError("--clear-resume-when is supported only by todo update")
    if args.clear_resume_when and args.resume_when:
        raise ValueError(
            "todo update accepts either --resume-when or --clear-resume-when, not both"
        )
    if (
        args.todo_command != "list"
        and args.todo_limit is not None
    ):
        raise ValueError(
            "--limit is supported only by todo list"
        )
    if args.todo_thin and args.todo_command != "list":
        raise ValueError("--thin is supported only by todo list")


def validate_capability_gap_options(args: argparse.Namespace) -> None:
    if not args.capability_gap_status:
        return
    if args.todo_command not in {"add", "update", "complete"}:
        raise ValueError(
            "--capability-gap-status is supported only by todo add/update/complete"
        )
    if args.role != "agent":
        raise ValueError("--capability-gap-status requires --role agent")
    if not args.target_capabilities:
        raise ValueError(
            "--capability-gap-status requires at least one --target-capability"
        )
    if (
        args.capability_gap_status in {"fixed", "real_callsite_verified"}
        and not args.evidence
    ):
        raise ValueError(
            "fixed and real_callsite_verified capability gaps require "
            "public-safe --evidence"
        )


def validate_successor_routing_options(args: argparse.Namespace) -> None:
    if args.next_user_task_class and not args.next_user_todo:
        raise ValueError("--next-user-task-class requires --next-user-todo")
    if args.next_user_todo and not args.next_user_task_class:
        raise ValueError(
            "--next-user-todo requires explicit --next-user-task-class "
            "user_action|user_gate"
        )
    if args.next_continuation_policy and not args.next_agent_todo:
        raise ValueError("--next-continuation-policy requires --next-agent-todo")
    if args.next_task_repository and not args.next_agent_todo:
        raise ValueError("--next-task-repository requires --next-agent-todo")
    if args.next_required_capabilities and not args.next_agent_todo:
        raise ValueError("--next-required-capability requires --next-agent-todo")
    if args.next_excluded_agents and not args.next_agent_todo:
        raise ValueError("--next-excluded-agent requires --next-agent-todo")
