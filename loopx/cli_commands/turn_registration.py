"""Argument-parser registration for the governed Turn command surface."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from ..control_plane.turn_driver.host_binding import (
    MANAGED_TURN_HOST,
    resolve_default_turn_host,
)
from ..paths import default_public_scan_root

# Explicit host choices stay per-command: planning may name any host the Turn
# driver routes, while run-once only ships built-in adapters for these three.
PLANNED_TURN_HOST_CHOICES = ["codex-cli", "claude-code", "dsh", "generic-cli"]
RUN_ONCE_TURN_HOST_CHOICES = ["codex-cli", "dsh", "generic-cli"]

AddFormat = Callable[[argparse.ArgumentParser], None]


def register_turn_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> None:
    parser = subparsers.add_parser(
        "turn",
        help="Plan, run, or inspect one governed external-host Turn.",
    )
    command_sub = parser.add_subparsers(dest="turn_command", required=True)
    inspect_journal = command_sub.add_parser(
        "inspect-journal",
        help="Inspect one canonical fenced Turn journal without executing effects.",
    )
    add_subcommand_format(inspect_journal)
    inspect_journal.add_argument("--goal-id", required=True)
    inspect_journal.add_argument("--agent-id", required=True)
    inspect_journal.add_argument("--turn-key", required=True)
    inspect_journal.add_argument(
        "--retry-failed-turn",
        action="store_true",
        help=(
            "Evaluate an explicit failed-Turn retry, including any current "
            "Host Session binding check."
        ),
    )

    plan = command_sub.add_parser(
        "plan",
        help="Build one typed read-only host decision without launching or writing.",
    )
    add_subcommand_format(plan)
    # The default host and the default execution mode are one decision: the
    # selected managed host runs bounded headless Turns, so pairing it with a
    # visible interactive mode would produce a default plan that cannot be
    # scheduled. The mode follows the *selected* host, whatever resolved it.
    resolved_default_host = resolve_default_turn_host()
    resolved_default_execution_mode = (
        "isolated-headless"
        if resolved_default_host == MANAGED_TURN_HOST
        else "interactive-visible"
    )
    _add_turn_decision_arguments(
        plan,
        default_host=resolved_default_host,
        host_choices=list(PLANNED_TURN_HOST_CHOICES),
        default_execution_mode=resolved_default_execution_mode,
    )
    plan.add_argument(
        "--include-transaction-detail",
        action="store_true",
        help="Include session binding and transaction receipt planning detail.",
    )
    plan.add_argument(
        "--scan-root",
        default=default_public_scan_root(),
        help="Public files to scan for obvious private material.",
    )
    plan.add_argument(
        "--scan-path",
        action="append",
        default=[],
        help="Specific public file or directory to scan. Repeatable.",
    )
    plan.add_argument("--limit", type=int, default=5)

    managed_step = command_sub.add_parser(
        "managed-step",
        help=(
            "Decide one bounded same-Turn continuation for a failed Turn "
            "without executing it."
        ),
        description=(
            "Read one canonical Turn journal, rebuild its validated receipt, "
            "and ask the pure Turn Loop Controller for a disposition against "
            "the current decision. Grants no execution authority: it never "
            "launches a host, writes state, or spends quota. The Turn journal "
            "remains the authority for the attempt count and retry budget."
        ),
    )
    add_subcommand_format(managed_step)
    # A managed step decides a bounded headless continuation for a Turn that
    # already failed, so it selects from the shipped run-once hosts and never
    # plans a visible interactive mode.
    _add_turn_decision_arguments(
        managed_step,
        default_host=resolved_default_host,
        host_choices=list(RUN_ONCE_TURN_HOST_CHOICES),
        execution_mode_choices=["isolated-headless"],
        default_execution_mode="isolated-headless",
        allow_todo_selection=False,
    )
    managed_step.add_argument(
        "--turn-key",
        required=True,
        help="Exact sha256 Turn key of the failed Turn to decide about.",
    )
    managed_step.add_argument(
        "--observed-attempt",
        type=int,
        help=(
            "Caller's observed attempt count, reconciled against the Turn "
            "journal. A disagreement is refused rather than adopted."
        ),
    )
    managed_step.add_argument(
        "--observed-max-attempts",
        type=int,
        help=(
            "Caller's observed retry ceiling, reconciled against the Turn "
            "journal retry policy."
        ),
    )
    managed_step.add_argument(
        "--scan-root",
        default=default_public_scan_root(),
        help="Public files to scan for obvious private material.",
    )
    managed_step.add_argument(
        "--scan-path",
        action="append",
        default=[],
        help="Specific public file or directory to scan. Repeatable.",
    )
    managed_step.add_argument("--limit", type=int, default=5)

    run_once = command_sub.add_parser(
        "run-once",
        help=(
            "Run one explicit isolated generic host command and commit only a "
            "validated public-safe result."
        ),
        description=(
            "Run one governed Turn: LoopX decides, a host adapter invokes an "
            "agent CLI, an independent validator proves the postcondition, and "
            "LoopX commits only a passing result."
        ),
        epilog=(
            "A Trae, Codex, or other conversational CLI normally needs a thin "
            "adapter: read one typed request from stdin and emit one typed result "
            "to stdout. The validation command receives that normalized result "
            "on stdin and must independently check the real postcondition. Only "
            "eligible interrupted sessions resume; terminal startup or missing-session "
            "failures start a fresh host session on the next Turn."
        ),
    )
    add_subcommand_format(run_once)
    _add_turn_decision_arguments(
        run_once,
        default_host=resolve_default_turn_host(),
        host_choices=list(RUN_ONCE_TURN_HOST_CHOICES),
        execution_mode_choices=["isolated-headless"],
        default_execution_mode="isolated-headless",
    )
    run_once.add_argument("--project", required=True)
    run_once.add_argument(
        "--host-command-json",
        "--host-adapter-command-json",
        dest="host_command_json",
        help=(
            "JSON argv array for a typed generic host adapter. The adapter reads "
            "the Turn request from stdin and emits one result JSON object; shell "
            "parsing is never used."
        ),
    )
    run_once.add_argument(
        "--validation-command-json",
        help=(
            "Trusted JSON argv array for independent task/postcondition validation. "
            "The normalized host result is provided on stdin; shell parsing is never used."
        ),
    )
    run_once.add_argument(
        "--validation-timeout-seconds",
        type=float,
        default=30.0,
        help="Timeout for the independent validation command.",
    )
    run_once.add_argument(
        "--validation-failure-kind",
        choices=["repair_required", "replan_required"],
        default="repair_required",
        help="Typed recovery disposition when the independent validator rejects the result.",
    )
    run_once.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex CLI executable used by the built-in codex-cli host.",
    )
    run_once.add_argument("--codex-model")
    run_once.add_argument(
        "--codex-sandbox",
        choices=["read-only", "workspace-write", "danger-full-access"],
        default="read-only",
        help=("Codex CLI sandbox (default: read-only). danger-full-access explicitly "
              "disables the inner sandbox; callers must provide their own isolation. "
              "The setting is passed explicitly for both new and resumed sessions."),
    )
    run_once.add_argument(
        "--dsh-provider",
        help=(
            "Provider for the built-in dsh host; defaults to the managed "
            "execution profile (LOOPX_TURN_PROVIDER)."
        ),
    )
    run_once.add_argument(
        "--dsh-model",
        help=(
            "Model for the built-in dsh host; defaults to the managed "
            "execution profile (LOOPX_TURN_MODEL)."
        ),
    )
    run_once.add_argument(
        "--dsh-reasoning-effort",
        help=(
            "Reasoning effort for the built-in dsh host; defaults to the "
            "managed execution profile (LOOPX_TURN_REASONING_EFFORT)."
        ),
    )
    run_once.add_argument(
        "--dsh-max-tokens",
        type=int,
        help=(
            "Per-model-request output-token cap; defaults to LoopX's bounded "
            "managed-host value. This is not a whole-Turn or tool-call budget."
        ),
    )
    run_once.add_argument(
        "--dsh-home",
        help=(
            "Explicit DeepSeek Harness home; defaults to DSH_HOME or "
            "<project>/.local/.dsh-sessions."
        ),
    )
    run_once.add_argument(
        "--dsh-cordis",
        help="cordis.yml used by the built-in dsh host runtime.",
    )
    run_once.add_argument("--dsh-runtime-bin")
    run_once.add_argument(
        "--dsh-runner",
        help=(
            "Explicit runner hook intended for hermetic tests; this is not a "
            "permission boundary. Path to a Python module exposing "
            "run_dsh_turn(...) used instead of the DeepSeek Harness SDK."
        ),
    )
    run_once.add_argument("--timeout-seconds", type=float, default=120.0)
    run_once.add_argument(
        "--retry-failed-turn",
        action="store_true",
        help="Retry a failed transaction from its last side-effect-safe phase.",
    )
    run_once.add_argument(
        "--resume-turn-key",
        help="Resume the exact journaled transaction without recomputing its plan.",
    )
    run_once.add_argument(
        "--no-global-sync",
        action="store_true",
        help="Keep disposable fixture writeback out of the shared global registry.",
    )
    run_once.add_argument(
        "--execute",
        action="store_true",
        help="Invoke the host and commit validated writeback/quota effects.",
    )
    run_once.add_argument(
        "--scan-root",
        default=default_public_scan_root(),
        help="Public files to scan for obvious private material.",
    )
    run_once.add_argument("--scan-path", action="append", default=[])
    run_once.add_argument("--limit", type=int, default=5)


def _add_turn_decision_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_host: str,
    host_choices: list[str] | None = None,
    execution_mode_choices: list[str] | None = None,
    default_execution_mode: str = "interactive-visible",
    allow_todo_selection: bool = True,
) -> None:
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument(
        "--host",
        choices=host_choices or ["codex-cli", "claude-code", "generic-cli"],
        default=default_host,
    )
    parser.add_argument(
        "--execution-mode",
        choices=execution_mode_choices or ["interactive-visible", "isolated-headless"],
        default=default_execution_mode,
    )
    parser.add_argument(
        "--scheduler-owner",
        choices=["host_automation", "agent_cli_loop", "outer_controller", "none"],
        help=(
            "Cadence owner; defaults to outer_controller for generic-cli and "
            "agent_cli_loop otherwise."
        ),
    )
    if allow_todo_selection:
        parser.add_argument(
            "--todo-id",
            help="Select this currently eligible Todo through the existing quota owner; never fall back to another task.",
        )
    parser.add_argument(
        "--turn-instance-id",
        help=(
            "Caller-stable public-safe identity for one logical Turn. Reusing the "
            "same id replays idempotently; use a new id for a new Turn with the "
            "same semantic action."
        ),
    )
    parser.add_argument(
        "--iteration-context",
        choices=["fresh", "resume-if-available"],
        default="resume-if-available",
        help=(
            "Host context policy for this iteration. fresh starts a clean "
            "session even when a compatible prior session exists; "
            "resume-if-available preserves the existing continuation behavior."
        ),
    )
    parser.add_argument(
        "--resume-goal-id",
        help="Goal identity bound to an available opaque host session.",
    )
    parser.add_argument(
        "--resume-agent-id",
        help="Agent identity bound to an available opaque host session.",
    )
    parser.add_argument(
        "--resume-todo-id",
        help="Todo identity bound to an available opaque host session.",
    )
    parser.add_argument(
        "--available-capability",
        dest="available_capabilities",
        action="append",
    )
