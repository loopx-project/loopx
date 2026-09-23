from __future__ import annotations

import argparse
from collections.abc import Callable

from ..control_plane.todos.contract import TODO_CONTINUATION_POLICY_VALUES
from ..todos import ARCHIVE_COMPLETED_DEFAULT_MAX_ACTIVE_DONE
from .todo_argument_validation import (
    register_todo_linkage_arguments,
    register_todo_successor_creation_arguments,
)


def register_todo_command(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    todo_parser = subparsers.add_parser(
        "todo",
        help="Add a user or agent todo to a goal's active state.",
        description=(
            "Manage goal todos. The options below are the union for every todo "
            "command; each option's help names the commands that accept it, and "
            "unsupported combinations fail before state is read or written."
        ),
    )
    add_subcommand_format(todo_parser)
    todo_parser.add_argument(
        "todo_command",
        nargs="?",
        choices=[
            "add",
            "list",
            "receipt",
            "claim",
            "update",
            "complete",
            "supersede",
            "archive-completed",
            "plan",
            "project-markdown",
        ],
        default=None,
        help=(
            "Use add to append a checkbox todo, claim to soft-claim by registered "
            "agent id, list to read projected todos, update/complete/supersede to transition by todo_id, or "
            "archive-completed to move older completed todos into Completed Work Archive. "
            "Use plan with --text and --agent-id for the existing Goal's model planning checkpoint; the caller owns subsequent execution."
        ),
    )
    todo_parser.add_argument("--goal-id", required=True, help="Goal id whose active state should receive the todo.")
    todo_parser.add_argument("--role", choices=["user", "agent"], help="Todo owner. Required for add; optional todo_id search scope for lifecycle commands. Defaults to agent for archive-completed.")
    todo_parser.add_argument("--text", help="Todo text. Required for add; keep it short and public-safe enough for local status.")
    todo_parser.add_argument("--priority", choices=["P0", "P1", "P2", "P3", "P4"], help="For add/update, declare Todo priority independently of text; omission retains the current value.")
    todo_parser.add_argument("--clear-priority", action="store_true", help="For update, explicitly remove priority; cannot be combined with --priority.")
    todo_parser.add_argument("--todo-id", help="Structured todo id from status/quota, such as todo_ab12cd34ef56.")
    todo_parser.add_argument(
        "--operation-id",
        help="For todo receipt, read the exact historical canonical operation after an ambiguous response; this does not grant a retry or lease.",
    )
    todo_parser.add_argument(
        "--update-operation-id",
        help=("For promoted text/note, planning, validator revision or User completion update, reuse this operation id after a lost response; "
              "changed intent is rejected. Planning supports status, evidence, reason, resume conditions and successor links; "
              "User status=done uses terminal validation and lease release; other leased status changes remain unsupported."),
    )
    todo_parser.add_argument(
        "--update-expected-provider-revision",
        help="For promoted todo update, require this canonical revision; reuse it with the operation id when recovering a lost response.",
    )
    todo_parser.add_argument(
        "--claim-operation-id",
        help=(
            "For todo claim on promoted canonical authority only, reuse this public-safe "
            "operation id across retries. Changed intent with the same id is rejected; "
            "receipt replay proves historical acceptance, not current lease ownership. "
            "Omit to retain a fresh operation id per invocation."
        ),
    )
    todo_parser.add_argument(
        "--turn-instance-id",
        help=(
            "For todo complete, bind the lifecycle receipt to the original "
            "turn-scoped quota guard and reuse it on retries."
        ),
    )
    todo_parser.add_argument(
        "--completion-identity-key",
        help=(
            "For todo complete --no-follow-up lifecycle reentry, reuse the "
            "exact completion identity projected by LoopX. This is not a quota "
            "turn id and cannot be combined with --turn-instance-id."
        ),
    )
    todo_parser.add_argument(
        "--replan-obligation-id",
        help=(
            "For todo add, bind one newly selected runnable advancement successor "
            "to the exact open replan obligation. Requires --action-kind and a "
            "stable --target-key or --explore-result-node-ref. The Todo write "
            "becomes the semantic receipt; no follow-up ACK command is required."
        ),
    )
    todo_parser.add_argument("--status", choices=["open", "done", "blocked", "deferred"], help="For todo add/update, set the lifecycle status.")
    todo_parser.add_argument("--note", help="Public-safe note to attach to a lifecycle transition.")
    todo_parser.add_argument("--evidence", help="Public-safe evidence pointer or short result for complete/update.")
    todo_parser.add_argument(
        "--validation-command",
        help=(
            "Caller-approved validation command (no shell) to run before a "
            "todo's completion commits, e.g. 'pytest -q tests/test_x.py'. Set "
            "on `todo add`, or replace it on a promoted open Todo with `todo "
            "update` plus operation id, provider revision and agent id; completion "
            "runs it independently and blocks on a non-zero exit."
        ),
    )
    todo_parser.add_argument(
        "--validation-label",
        help="Optional public-safe label for the validation receipt.",
    )
    todo_parser.add_argument(
        "--validation-command-json",
        help=(
            "Trusted JSON string array (argv form, no shell parsing) for the "
            "completion validation command, e.g. '[\"pytest\",\"-q\",\"tests/"
            "test_x.py\"]'. Mutually exclusive with --validation-command; set "
            "on `todo add`, or replace it through the reviewed promoted `todo "
            "update` path."
        ),
    )
    todo_parser.add_argument(
        "--validation-timeout-seconds",
        type=int,
        help=(
            "Per-todo timeout for the caller-approved validation command. "
            "Only meaningful with --validation-command or "
            "--validation-command-json on `todo add` or validator revision; "
            "must be 1-29 so a "
            "timed-out validation still produces a typed receipt inside the "
            "30s outer subprocess budget. Defaults to 20."
        ),
    )
    todo_parser.add_argument("--reason", help="Public-safe reason for blocked/deferred/supersede transitions.")
    todo_parser.add_argument(
        "--authority-reason",
        help=(
            "For a delegated lifecycle override, record the public-safe reason. "
            "Required when the matching coordination.todo_lifecycle_authority "
            "grant sets requires_reason=true."
        ),
    )
    todo_parser.add_argument(
        "--task-class",
        choices=["advancement_task", "continuous_monitor", "user_gate", "user_action", "blocker"],
        help=(
            "For todo add/update, explicitly register the routing lane. Use "
            "advancement_task for executable delivery work; user_gate for blocking "
            "owner/controller decisions; user_action for non-blocking user-visible "
            "todos; continuous_monitor and blocker are non-executable lanes."
        ),
    )
    todo_parser.add_argument(
        "--action-kind",
        help=(
            "For todo add, optional public-safe action token such as run_eval, "
            "rebuild_score, compact_blocker_writeback, or monitor."
        ),
    )
    todo_parser.add_argument(
        "--task-domain",
        help=(
            "For agent todo add/update, declare the bounded responsibility domain "
            "used by adaptive child admission, such as code, docs, or validation."
        ),
    )
    todo_parser.add_argument(
        "--capability-binding-ref",
        help=(
            "For agent todo add, persist the opaque capability admission binding "
            "projected by a validated capability packet."
        ),
    )
    todo_parser.add_argument(
        "--task-repository",
        help=(
            "For agent todo add/update, declare the credential-free Git repository "
            "identity that owns the task, such as git:github.com/owner/repo. This "
            "selects workspace isolation; it does not grant write permission."
        ),
    )
    todo_parser.add_argument(
        "--continuation-policy",
        choices=sorted(TODO_CONTINUATION_POLICY_VALUES),
        help=(
            "Closed completion/handoff policy for this todo. action_kind remains "
            "an extensible domain token; defaults to independent_handoff."
        ),
    )
    todo_parser.add_argument(
        "--required-write-scope",
        dest="required_write_scopes",
        action="append",
        help=(
            "For todo add/update, declare a required relative write scope such as "
            "src/** or runners/openviking/**. Repeat for multiple scopes."
        ),
    )
    todo_parser.add_argument(
        "--required-capability",
        dest="required_capabilities",
        action="append",
        help=(
            "For todo add/update, declare an execution capability such as shell, "
            "filesystem_write, network, benchmark_runner, or external_evidence_poll. "
            "Repeat for multiple capabilities."
        ),
    )
    todo_parser.add_argument(
        "--target-capability",
        dest="target_capabilities",
        action="append",
        help=(
            "For todo add/update, declare a capability this todo is building, "
            "repairing, materializing, or parity-checking. On complete, pair it "
            "with --capability-gap-status to close that lifecycle. This is not a "
            "hard execution prerequisite."
        ),
    )
    todo_parser.add_argument(
        "--capability-gap-status",
        choices=["found", "fixed", "real_callsite_verified"],
        help=(
            "For agent todo add/update/complete, append an auditable capability-gap "
            "lifecycle event. Requires --target-capability; the todo_id is the "
            "stable gap id."
        ),
    )
    todo_parser.add_argument(
        "--explore-result-node-ref",
        dest="explore_result_node_refs",
        action="append",
        help=(
            "For todo add/update, link an explicit public-safe Explore result node id. "
            "Repeat for multiple nodes; analysis resolves only these links."
        ),
    )
    todo_parser.add_argument(
        "--clear-explore-result-node-refs",
        action="store_true",
        help="For todo update, remove all explicit Explore result node links.",
    )
    todo_parser.add_argument(
        "--decision-scope",
        help=(
            "For user_gate add/update, declare the concrete decision as "
            "kind:granularity:scope_key, for example direction:action:benchmark_target."
        ),
    )
    todo_parser.add_argument(
        "--required-decision-scope",
        dest="required_decision_scopes",
        action="append",
        help=(
            "For agent todo add/update, declare a required decision scope as "
            "kind:granularity:scope_key. Repeat for multiple scopes."
        ),
    )
    todo_parser.add_argument(
        "--decision-outcome",
        choices=["approve", "reject", "cancel"],
        help=(
            "For todo complete on a user_gate, record the explicit owner decision. "
            "Only approve consumes authority and resumes linked work."
        ),
    )
    todo_parser.add_argument(
        "--claimed-by",
        help=(
            "For agent todo add/claim/update, assign the soft execution owner to a "
            "registered public-safe agent id such as codex-main-control. This names "
            "the assignment target, not the lifecycle actor; multi-agent lifecycle "
            "commands still require --agent-id. User todos use --bound-agent or "
            "--goal-bound instead."
        ),
    )
    todo_parser.add_argument(
        "--task-lease-idempotency-key",
        help=(
            "For todo claim on promoted hard-lease authority, atomically acquire "
            "the canonical lease and claim; for promoted text/note or planning update, complete and supersede, prove the "
            "execution instance that owns the active lease."
        ),
    )
    todo_parser.add_argument(
        "--task-lease-expected-version",
        type=int,
        help=(
            "For promoted todo claim, optionally compare-and-set the canonical "
            "lease version; for promoted text/note or planning update, complete and supersede, supply the active lease "
            "version when it is effective."
        ),
    )
    todo_parser.add_argument(
        "--bound-agent",
        help=(
            "For user todo add/update, bind reminder delivery and post-response "
            "continuation to one registered agent lane. This is not a gate."
        ),
    )
    todo_parser.add_argument(
        "--goal-bound",
        action="store_true",
        help=(
            "For user todo add/update, explicitly bind the item to the whole goal "
            "instead of one agent lane. This scopes continuation, not blocking: "
            "it does not create a global gate."
        ),
    )
    todo_parser.add_argument(
        "--blocks-agent",
        help=(
            "For user_gate add/update, scope the gate to one registered agent."
        ),
    )
    todo_parser.add_argument(
        "--clear-blocks-agent",
        action="store_true",
        help="For todo update, remove the existing blocks_agent field.",
    )
    todo_parser.add_argument(
        "--excluded-agent",
        dest="excluded_agents",
        action="append",
        help=(
            "For agent todo add/update, exclude one registered peer from claiming or "
            "executing the todo. Repeat for multiple peers."
        ),
    )
    todo_parser.add_argument(
        "--clear-excluded-agents",
        action="store_true",
        help="For todo update, remove all executor exclusions from the todo.",
    )
    todo_parser.add_argument(
        "--global-gate",
        action="store_true",
        help=(
            "For todo add/update on role=user task-class=user_gate, explicitly mark "
            "that the gate blocks EVERY registered agent until resolved. This broad "
            "scope is never inferred from --agent-id, --goal-bound, or missing binding. "
            "Prefer --blocks-agent or --agent-id when only one lane is waiting."
        ),
    )
    todo_parser.add_argument(
        "--clear-global-gate",
        action="store_true",
        help=(
            "For todo update on a user_gate, remove global_gate. In a multi-agent "
            "goal, provide --blocks-agent in the same update so the gate retains "
            "an explicit lane scope."
        ),
    )
    register_todo_linkage_arguments(todo_parser)
    todo_parser.add_argument(
        "--target-key",
        "--monitor-target-key",
        dest="monitor_target_key",
        help=(
            "For agent todo add/update, declare a stable public-safe execution "
            "target key. --monitor-target-key remains a compatibility alias."
        ),
    )
    todo_parser.add_argument(
        "--cadence",
        help=(
            "For agent continuous_monitor add/update, declare the monitor cadence, "
            "such as 30m, 2h, or 1d."
        ),
    )
    todo_parser.add_argument(
        "--next-due-at",
        dest="next_due_at",
        help=(
            "For agent continuous_monitor add/update, declare the next due ISO "
            "timestamp; due monitor scheduling is based on this field."
        ),
    )
    todo_parser.add_argument(
        "--expires-at",
        dest="expires_at",
        help=(
            "For agent continuous_monitor add/update, declare the ISO timestamp "
            "after which the monitor is no longer due and must not catch up."
        ),
    )
    todo_parser.add_argument(
        "--watch-only",
        action="store_true",
        help=(
            "For agent continuous_monitor add/update, declare an intentionally "
            "unbounded liveness watch. Watch-only monitors remain schedulable but "
            "do not drive autonomous replan or block goal convergence."
        ),
    )
    todo_parser.add_argument(
        "--clear-claim",
        action="store_true",
        help="For todo update, remove the soft claimed_by owner from the todo.",
    )
    todo_parser.add_argument(
        "--no-follow-up",
        action="store_true",
        help=(
            "For todo update/complete, record a structured no-follow-up rationale "
            "when a completed todo intentionally has no successor."
        ),
    )
    register_todo_successor_creation_arguments(todo_parser)
    todo_parser.add_argument(
        "--max-active-done",
        type=int,
        default=ARCHIVE_COMPLETED_DEFAULT_MAX_ACTIVE_DONE,
        help=(
            "For archive-completed, keep this many completed todos in the active section. "
            "The default leaves a small buffer below the status warning threshold."
        ),
    )
    todo_parser.add_argument(
        "--agent-id",
        help=(
            "For user todo add, mark the authoring registered agent and bind the "
            "user response continuation to that lane; for user_gate, the gate also "
            "blocks this agent when --blocks-agent is omitted. For "
            "claim/update/complete/supersede, attribute the "
            "lifecycle actor; registered multi-agent goals require it unless an "
            "exact linked user_gate decision_scope supplies the typed owner/controller "
            "override. For list, select the project agent lane. Agent todo "
            "add intentionally does not accept this option; use --claimed-by to "
            "assign execution, or omit both options to leave the todo unclaimed."
        ),
    )
    todo_parser.add_argument(
        "--limit",
        dest="todo_limit",
        type=int,
        help=(
            "For todo list, explicit per-section cold-path cap: "
            "keep the top N todos of each role section after filtering; must "
            "be an integer >= 1, and the payload discloses the truncation via "
            "explicit_limit."
        ),
    )
    todo_parser.add_argument(
        "--thin",
        dest="todo_thin",
        action="store_true",
        help=(
            "For todo list, return the explicit field-only projection and omit "
            "detail lanes; returns at most two items per role, and --limit can "
            "lower but not expand that bound."
        ),
    )
    todo_parser.add_argument("--project", help="Project root. Defaults to the registry goal repo.")
    todo_parser.add_argument("--state-file", help="Active goal state path. Defaults to the registry goal state_file.")
    todo_parser.add_argument("--dry-run", action="store_true", help="Preview the active-state edit without writing.")
    todo_parser.add_argument(
        "--execute",
        action="store_true",
        help="For archive-completed or project-markdown, write the active-state edit.",
    )
    todo_parser.add_argument(
        "--provider-revision",
        help=(
            "For project-markdown, exact canonical authority revision rendered "
            "into the Todo section markers."
        ),
    )
