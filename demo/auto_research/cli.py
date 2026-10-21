from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .artifact_receipt import load_auto_research_artifact_receipt
from .human_view import render_auto_research_markdown
from .research_state import (
    build_auto_research_projection,
    build_live_auto_research_projection,
    load_auto_research_fixture,
)
from .demo_supervisor import build_auto_research_demo_supervisor_plan
from .evidence_packet import load_auto_research_evidence_packet_inputs
from .defaults import (
    AUTO_RESEARCH_DEFAULT_GOAL_ID,
    AUTO_RESEARCH_DEFAULT_OBJECTIVE,
    AUTO_RESEARCH_KNN_DEMO_PRESET_ID,
    AUTO_RESEARCH_SUPPORTED_PRESET_IDS,
)
from .demo_e2e import run_auto_research_demo_e2e
from .knn_demo_workspace import materialize_knn_demo_workspace
from .live_evidence import (
    LIVE_CODEX_E2E_DEFAULT_OUTPUT,
    capture_live_codex_e2e_evidence,
)
from .worker_loop import run_auto_research_worker_loop
from .worker_runtime import run_auto_research_worker_turn
from .rollout_append import (
    append_auto_research_rollout_events as _append_auto_research_rollout_events,
)
from .terminal_results import (
    decide_terminal_result,
    project_terminal_results,
    query_terminal_results,
    review_terminal_result,
)
from .user_contract import (
    build_auto_research_user_contract,
    infer_auto_research_output_language,
    normalize_auto_research_preset_id,
)
from ..multi_agent.visible_launch_policy import (
    make_visible_launcher_callback,
    make_visible_wake_callback,
    resolve_codex_trust_workspace,
    resolve_visible_launch_policy,
)
from loopx.control_plane.runtime.public_safety import public_safe_compact_text
from loopx.history import load_registry
from loopx.paths import resolve_runtime_root
from loopx.quota import build_quota_should_run
from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path
from loopx.status import collect_status
from ..visible_multi_agent_launcher import (
    execute_visible_multi_agent_launcher,
)


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]

AUTO_RESEARCH_DEMO_GOAL_PREFIX = "loopx-auto-research-demo"
AUTO_RESEARCH_START_AGENT_ID = "auto-research-operator"
AUTO_RESEARCH_COMMAND_FAILED_ERROR_CODE = "auto_research_command_failed"
AUTO_RESEARCH_INVALID_INPUT_ERROR_CODE = "auto_research_invalid_input"
AUTO_RESEARCH_IO_FAILED_ERROR_CODE = "auto_research_io_failed"
AUTO_RESEARCH_SUBCOMMANDS = frozenset(
    {
        "append-evidence",
        "artifact-receipt",
        "capture-live-evidence",
        "contract",
        "decide",
        "demo-e2e",
        "demo-supervisor",
        "evidence",
        "frontier",
        "project-results",
        "results",
        "review",
        "start",
        "worker-loop",
        "worker-turn",
    }
)


def _public_auto_research_error(exc: Exception) -> tuple[str, str]:
    """Project an internal exception into a stable, public-safe CLI error."""

    if isinstance(exc, json.JSONDecodeError):
        return (
            AUTO_RESEARCH_INVALID_INPUT_ERROR_CODE,
            "Auto Research input must be valid JSON.",
        )
    if isinstance(exc, OSError):
        return (
            AUTO_RESEARCH_IO_FAILED_ERROR_CODE,
            "Auto Research could not access a required resource.",
        )
    if isinstance(exc, ValueError):
        public_message = public_safe_compact_text(str(exc), limit=220)
        return (
            AUTO_RESEARCH_INVALID_INPUT_ERROR_CODE,
            public_message or "Auto Research input is invalid.",
        )
    return (
        AUTO_RESEARCH_COMMAND_FAILED_ERROR_CODE,
        "Auto Research command failed unexpectedly.",
    )


def rewrite_auto_research_question_argv(argv: list[str]) -> list[str]:
    """Map `loopx auto-research "<question>"` to the explicit contract command."""

    values = list(argv)
    try:
        command_index = values.index("auto-research")
    except ValueError:
        return values

    cursor = command_index + 1
    while cursor < len(values):
        token = values[cursor]
        if token == "--format" and cursor + 1 < len(values):
            cursor += 2
            continue
        if token.startswith("--format="):
            cursor += 1
            continue
        break
    if cursor >= len(values):
        return values

    token = values[cursor]
    if token.startswith("-") or token in AUTO_RESEARCH_SUBCOMMANDS:
        return values
    return values[:cursor] + ["contract"] + values[cursor:]


def _demo_goal_suffix(value: object, *, fallback: str = "run") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-._")
    return text[:40] or fallback


def _default_auto_research_start_workspace(goal_id: str) -> str:
    return str(
        Path.home()
        / "loopx-auto-research"
        / _demo_goal_suffix(goal_id, fallback="run")
        / "visible-workspace"
    )


def _resolve_demo_goal_surface(
    *,
    goal_id: str | None,
    demo_run_id: str | None,
    inherit_default_goal: bool,
) -> tuple[str, str]:
    if inherit_default_goal and goal_id:
        raise ValueError("--inherit-default-goal cannot be combined with --goal-id")
    if inherit_default_goal and demo_run_id:
        raise ValueError("--inherit-default-goal cannot be combined with --demo-run-id")
    if goal_id and demo_run_id:
        raise ValueError("--demo-run-id is only used when --goal-id is omitted")
    if inherit_default_goal:
        return AUTO_RESEARCH_DEFAULT_GOAL_ID, "inherited_default_goal"
    if goal_id:
        return goal_id, "explicit_goal"
    run_id = demo_run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{AUTO_RESEARCH_DEMO_GOAL_PREFIX}-{_demo_goal_suffix(run_id)}", "fresh_demo_goal"


def register_auto_research_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> None:
    auto_research_parser = subparsers.add_parser(
        "auto-research",
        help="Project public-safe decentralized auto-research frontiers.",
    )
    auto_research_parser.add_argument(
        "--format",
        dest="auto_research_format",
        choices=["markdown", "json"],
        help="Output format for the auto-research command group.",
    )
    auto_research_sub = auto_research_parser.add_subparsers(
        dest="auto_research_command",
        required=True,
    )
    contract_parser = auto_research_sub.add_parser(
        "contract",
        help="Render the fixed user-facing auto-research contract for one open question.",
    )
    add_subcommand_format(contract_parser)
    contract_parser.add_argument("open_question", help="Quoted open research question.")
    contract_parser.add_argument(
        "--max-todos",
        type=int,
        default=5,
        help="Maximum action-plan todos, capped at 5.",
    )
    contract_parser.add_argument(
        "--language",
        choices=["auto", "zh", "en"],
        default="auto",
        help="Human-readable output language for the research process.",
    )
    contract_parser.add_argument(
        "--preset",
        choices=AUTO_RESEARCH_SUPPORTED_PRESET_IDS,
        help="Explicit domain demo context, e.g. knn-demo materializes a KNN benchmark workspace.",
    )

    start_parser = auto_research_sub.add_parser(
        "start",
        help="Start visible multi-agent auto-research from one open question.",
    )
    add_subcommand_format(start_parser)
    start_parser.add_argument("open_question", help="Quoted open research question.")
    start_parser.add_argument(
        "--language",
        choices=["auto", "zh", "en"],
        default="auto",
        help="Human-readable output language for visible role panes.",
    )
    start_parser.add_argument(
        "--preset",
        choices=AUTO_RESEARCH_SUPPORTED_PRESET_IDS,
        help=(
            "Explicit domain demo context. Use knn-demo when the baseline/metric "
            "come from a generated KNN benchmark workspace rather than the question text."
        ),
    )
    start_parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Create an isolated research goal and launch visible Codex TUI lanes. "
            "Omit to preview the exact contract, commands, and runner packet."
        ),
    )
    start_parser.add_argument(
        "--headless",
        action="store_true",
        help="With --execute, run the non-interactive worker-loop proof instead of visible panes.",
    )
    start_parser.add_argument(
        "--wake-visible-after-launch",
        action="store_true",
        default=None,
        help=(
            "After launching visible panes, broadcast the fixed decentralized A2A wake prompt. "
            "Omit for the default operator-visible launch where each Codex TUI runs its own first tick."
        ),
    )
    start_parser.add_argument(
        "--no-wake-visible-after-launch",
        dest="wake_visible_after_launch",
        action="store_false",
        help=(
            "Keep the post-launch wake disabled while preserving the normal visible launch."
        ),
    )
    start_parser.add_argument(
        "--auto-wake",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Run a session-scoped background wake loop so successor todos can be picked up "
            "without a human sending `loopx multi-agent wake`. Use --no-auto-wake for full "
            "manual takeover."
        ),
    )
    start_parser.add_argument(
        "--auto-wake-interval-seconds",
        type=float,
        default=45.0,
        help=argparse.SUPPRESS,
    )
    start_parser.add_argument(
        "--no-attach",
        action="store_true",
        help="With visible --execute, start tmux in the background instead of attaching.",
    )
    start_parser.add_argument(
        "--attach",
        action="store_true",
        help=(
            "With visible --execute, attach to tmux after launch and skip the default wake so "
            "operator takeover happens first."
        ),
    )
    start_parser.add_argument(
        "--demo-run-id",
        help="Optional public-safe suffix for the isolated goal id.",
    )
    start_parser.add_argument(
        "--goal-id",
        help=argparse.SUPPRESS,
    )
    start_parser.add_argument(
        "--tracking-goal-id",
        help=argparse.SUPPRESS,
    )
    start_parser.add_argument(
        "--worker-loop-rounds",
        type=int,
        default=4,
        help=argparse.SUPPRESS,
    )
    start_parser.add_argument(
        "--configure-visible-worker-turn",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    start_parser.add_argument(
        "--session-name",
        default="loopx-auto-research",
        help="Public-safe tmux session name for visible lanes.",
    )
    start_parser.add_argument(
        "--workspace",
        help=(
            "Directory where visible Codex lanes should start. "
            "Omit to use ~/loopx-auto-research/<run>/visible-workspace."
        ),
    )
    start_parser.add_argument(
        "--create-workspace",
        action="store_true",
        help="Create --workspace when it does not already exist.",
    )
    start_parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    start_parser.add_argument("--cli-bin", default="loopx", help=argparse.SUPPRESS)
    start_parser.add_argument("--codex-bin", default="codex", help=argparse.SUPPRESS)
    start_parser.add_argument("--tmux-bin", default="tmux", help=argparse.SUPPRESS)
    start_parser.add_argument(
        "--reasoning-effort",
        default="high",
        help=argparse.SUPPRESS,
    )
    start_parser.add_argument(
        "--launcher",
        choices=["auto", "tmux"],
        default="auto",
        help=argparse.SUPPRESS,
    )
    start_parser.add_argument(
        "--replace-existing",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    start_parser.add_argument(
        "--codex-trust-workspace",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=argparse.SUPPRESS,
    )

    frontier_parser = auto_research_sub.add_parser(
        "frontier",
        help="Render a per-agent decentralized research frontier from a public fixture or live LoopX state.",
    )
    add_subcommand_format(frontier_parser)
    frontier_parser.add_argument(
        "--fixture",
        help="Path to a decentralized_auto_research_fixture_v0 JSON file.",
    )
    frontier_parser.add_argument(
        "--goal-id",
        help="Goal id for live LoopX quota/status input. Mutually exclusive with --fixture.",
    )
    frontier_parser.add_argument(
        "--agent-id",
        required=True,
        help="Agent id whose runnable frontier should be projected.",
    )

    evidence_parser = auto_research_sub.add_parser(
        "evidence",
        help="Build public-safe research hypothesis/evidence records from metric evaluator outputs.",
    )
    add_subcommand_format(evidence_parser)
    evidence_parser.add_argument("--contract", required=True, help="Path to research_contract_v0 JSON.")
    evidence_parser.add_argument(
        "--eval-result",
        action="append",
        required=True,
        help="Path to a public-safe evaluator JSON result. Repeat for dev/holdout or retry evidence.",
    )
    evidence_parser.add_argument("--hypothesis-id", required=True)
    evidence_parser.add_argument("--todo-id", required=True)
    evidence_parser.add_argument("--agent-id", required=True)
    evidence_parser.add_argument("--claimed-by", required=True)
    evidence_parser.add_argument("--mechanism-family", required=True)
    evidence_parser.add_argument("--hypothesis", required=True)
    evidence_parser.add_argument("--parent-hypothesis-id")
    evidence_parser.add_argument("--grounding-ref", action="append", default=[])
    evidence_parser.add_argument("--novelty-audit-ref")
    evidence_parser.add_argument("--branch-ref")
    evidence_parser.add_argument("--attempt-start", type=int, default=1)

    append_parser = auto_research_sub.add_parser(
        "append-evidence",
        help="Append an auto_research_evidence_packet_v0 into the LoopX rollout event log.",
    )
    add_subcommand_format(append_parser)
    append_parser.add_argument("--packet", required=True, help="Path to auto_research_evidence_packet_v0 JSON.")
    append_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview rollout events without appending them.",
    )
    append_parser.add_argument(
        "--output",
        help="Write the append result JSON to this path without recording the path in LoopX state.",
    )

    decide_parser = auto_research_sub.add_parser(
        "decide",
        help="Record an evidence-bound terminal decision for one Auto Research hypothesis.",
    )
    add_subcommand_format(decide_parser)
    decide_parser.add_argument("--goal-id", required=True)
    decide_parser.add_argument("--hypothesis-id", required=True)
    decide_parser.add_argument("--outcome", choices=["promoted", "retired"], required=True)
    decide_parser.add_argument(
        "--reason",
        choices=[
            "contradicted",
            "duplicate",
            "holdout_validated",
            "operator_rejected",
            "retry_exhausted",
            "superseded",
        ],
        required=True,
    )
    decide_parser.add_argument("--agent-id", required=True)
    decide_parser.add_argument("--evidence-ref", action="append", default=[])
    decide_parser.add_argument(
        "--execute",
        action="store_true",
        help="Append the terminal decision. Omit to preview the validated decision.",
    )

    review_parser = auto_research_sub.add_parser(
        "review",
        help="Record a review receipt bound to the current terminal decision and evidence graph revision.",
    )
    add_subcommand_format(review_parser)
    review_parser.add_argument("--goal-id", required=True)
    review_parser.add_argument("--hypothesis-id", required=True)
    review_parser.add_argument("--reviewer-agent-id", required=True)
    review_parser.add_argument(
        "--verdict",
        choices=["approve", "needs_more_evidence", "reject"],
        required=True,
    )
    review_parser.add_argument("--evidence-ref", action="append", default=[])
    review_parser.add_argument(
        "--require-independent",
        action="store_true",
        help="Reject a reviewer who produced the hypothesis or terminal decision.",
    )
    review_parser.add_argument(
        "--execute",
        action="store_true",
        help="Append the review receipt. Omit to preview the validated receipt.",
    )

    results_parser = auto_research_sub.add_parser(
        "results",
        help="Query current terminal decisions and review state by exact hypothesis id.",
    )
    add_subcommand_format(results_parser)
    results_parser.add_argument("--goal-id", required=True)
    results_parser.add_argument("--hypothesis-id")
    results_parser.add_argument("--include-history", action="store_true")

    project_results_parser = auto_research_sub.add_parser(
        "project-results",
        help="Project terminal Auto Research decisions into the existing Explore result log.",
    )
    add_subcommand_format(project_results_parser)
    project_results_parser.add_argument("--goal-id", required=True)
    project_results_parser.add_argument(
        "--execute",
        action="store_true",
        help="Append the deterministic Explore events. Omit to preview.",
    )

    artifact_receipt_parser = auto_research_sub.add_parser(
        "artifact-receipt",
        help=(
            "Build a read-only Wish-to-Artifact receipt from one delivery "
            "contract and the current Auto Research evidence."
        ),
    )
    add_subcommand_format(artifact_receipt_parser)
    artifact_receipt_parser.add_argument(
        "--contract",
        required=True,
        help="Path to auto_research_delivery_contract_v0 JSON.",
    )

    live_evidence_parser = auto_research_sub.add_parser(
        "capture-live-evidence",
        help="Build compact public-safe live Codex E2E evidence after lane-authored evidence is appended.",
    )
    add_subcommand_format(live_evidence_parser)
    live_evidence_parser.add_argument(
        "--packet",
        required=True,
        help="Path to the public auto_research_evidence_packet_v0 JSON produced by a visible lane.",
    )
    live_evidence_parser.add_argument(
        "--append-result",
        required=True,
        help="Path to the JSON output from a real auto-research append-evidence run.",
    )
    live_evidence_parser.add_argument("--agent-id", required=True)
    live_evidence_parser.add_argument(
        "--lane-count",
        type=int,
        default=4,
        help="Accepted visible lane count to record in the compact live evidence.",
    )
    live_evidence_parser.add_argument(
        "--visible-lanes-accepted",
        action="store_true",
        help="Required acknowledgement that the visible lanes were launched and accepted.",
    )
    live_evidence_parser.add_argument(
        "--output",
        default=LIVE_CODEX_E2E_DEFAULT_OUTPUT,
        help="Output path for --execute. The path is not recorded in the evidence payload.",
    )
    live_evidence_parser.add_argument(
        "--execute",
        action="store_true",
        help="Write the compact evidence JSON to --output. Omit to preview the payload.",
    )

    worker_turn_parser = auto_research_sub.add_parser(
        "worker-turn",
        help="Run one LoopX-selected auto-research worker turn from quota/frontier.",
    )
    add_subcommand_format(worker_turn_parser)
    worker_turn_parser.add_argument("--agent-id", required=True)
    worker_turn_parser.add_argument(
        "--goal-id",
        default=AUTO_RESEARCH_DEFAULT_GOAL_ID,
        help="Research goal id whose quota/frontier this worker should obey.",
    )
    worker_turn_parser.add_argument(
        "--objective",
        default=AUTO_RESEARCH_DEFAULT_OBJECTIVE,
        help="Public-safe objective used by the built-in lightweight metric kernel.",
    )
    worker_turn_parser.add_argument(
        "--output-dir",
        default="auto_research_lightweight_kernel",
        help=argparse.SUPPRESS,
    )
    worker_turn_parser.add_argument(
        "--evidence-dir",
        default=".local/auto-research-worker",
        help="Local-only evidence output directory inside the worker workspace.",
    )
    worker_turn_parser.add_argument(
        "--lane-count",
        type=int,
        default=1,
        help="Visible lane count recorded when live evidence is captured.",
    )
    worker_turn_parser.add_argument(
        "--visible-lanes-accepted",
        action="store_true",
        help="Acknowledge that the visible lanes were launched and accepted.",
    )
    worker_turn_parser.add_argument(
        "--live-evidence-output",
        default=LIVE_CODEX_E2E_DEFAULT_OUTPUT,
        help="Local filename for compact public-safe live evidence.",
    )
    worker_turn_parser.add_argument(
        "--complete-selected-todo",
        action="store_true",
        help="After a successful --execute turn, mark the selected LoopX todo done.",
    )
    worker_turn_parser.add_argument(
        "--execute",
        action="store_true",
        help="Run the selected worker action. Omit to show the plan from quota/frontier.",
    )

    worker_loop_parser = auto_research_sub.add_parser(
        "worker-loop",
        help="Run repeated LoopX-selected auto-research worker turns for a visible lane set.",
    )
    add_subcommand_format(worker_loop_parser)
    worker_loop_parser.add_argument(
        "--agent-id",
        action="append",
        required=True,
        help="Agent id to poll in loop order. Repeat for each visible worker lane.",
    )
    worker_loop_parser.add_argument(
        "--goal-id",
        default=AUTO_RESEARCH_DEFAULT_GOAL_ID,
        help="Research goal id whose quota/frontier each worker should obey.",
    )
    worker_loop_parser.add_argument(
        "--objective",
        default=AUTO_RESEARCH_DEFAULT_OBJECTIVE,
        help="Public-safe objective used by the built-in lightweight metric kernel.",
    )
    worker_loop_parser.add_argument(
        "--output-dir",
        default="auto_research_lightweight_kernel",
        help=argparse.SUPPRESS,
    )
    worker_loop_parser.add_argument(
        "--evidence-dir",
        default=".local/auto-research-worker",
        help="Local-only evidence output directory inside the worker workspace.",
    )
    worker_loop_parser.add_argument(
        "--lane-count",
        type=int,
        help="Visible lane count recorded when live evidence is captured.",
    )
    worker_loop_parser.add_argument(
        "--visible-lanes-accepted",
        action="store_true",
        help="Acknowledge that the visible lanes were launched and accepted.",
    )
    worker_loop_parser.add_argument(
        "--live-evidence-output",
        default=LIVE_CODEX_E2E_DEFAULT_OUTPUT,
        help="Local filename for compact public-safe live evidence.",
    )
    worker_loop_parser.add_argument(
        "--complete-selected-todo",
        action="store_true",
        help="After successful --execute turns, mark selected LoopX todos done.",
    )
    worker_loop_parser.add_argument(
        "--max-rounds",
        type=int,
        default=4,
        help="Maximum polling rounds across the agent list.",
    )
    worker_loop_parser.add_argument(
        "--execute",
        action="store_true",
        help="Run each selected worker action. Omit to preview loop selection.",
    )

    demo_supervisor_parser = auto_research_sub.add_parser(
        "demo-supervisor",
        help="Plan a dry-run tmux/Codex-CLI supervisor for decentralized auto-research lanes.",
    )
    add_subcommand_format(demo_supervisor_parser)
    demo_supervisor_parser.add_argument(
        "--goal-id",
        help=(
            "Research goal id whose frontier each lane should inspect. "
            "Omit to use a fresh demo-local goal surface."
        ),
    )
    demo_supervisor_parser.add_argument(
        "--demo-run-id",
        help="Public-safe suffix for the generated demo goal id when --goal-id is omitted.",
    )
    demo_supervisor_parser.add_argument(
        "--inherit-default-goal",
        action="store_true",
        help=f"Opt into the shared internal demo goal {AUTO_RESEARCH_DEFAULT_GOAL_ID}.",
    )
    demo_supervisor_parser.add_argument(
        "--agent",
        action="append",
        default=[],
        help=(
            "Agent/lane pair as agent_id:lane_id. Repeat for each visible lane. "
            "Omit to use the default LoopX auto-research demo lane set."
        ),
    )
    demo_supervisor_parser.add_argument(
        "--session-name",
        default="loopx-auto-research",
        help="Public-safe tmux session name for the generated dry-run script.",
    )
    demo_supervisor_parser.add_argument("--cli-bin", default="loopx", help="LoopX CLI executable name.")
    demo_supervisor_parser.add_argument("--codex-bin", default="codex", help="Codex CLI executable name.")
    demo_supervisor_parser.add_argument("--tmux-bin", default="tmux", help="tmux executable name.")
    demo_supervisor_parser.add_argument(
        "--reasoning-effort",
        default="high",
        help="Reasoning effort passed to visible Codex lanes through model_reasoning_effort.",
    )
    demo_supervisor_parser.add_argument(
        "--language",
        choices=["auto", "zh", "en"],
        default="auto",
        help="Human-readable output language for visible role panes.",
    )
    demo_supervisor_parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Launch visible Codex CLI lanes in tmux. Omit for the default dry-run packet. "
            "This only starts local visible terminals; LoopX writeback still happens through normal lane commands."
        ),
    )
    demo_supervisor_parser.add_argument(
        "--launcher",
        choices=["auto", "tmux"],
        default="auto",
        help="Visible process launcher for --execute. auto currently resolves to tmux.",
    )
    demo_supervisor_parser.add_argument(
        "--attach",
        action="store_true",
        help="After --execute with tmux, attach to the session. Terminal launcher opens visible windows directly.",
    )
    demo_supervisor_parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="With tmux launcher, kill an existing session with the same name before launching.",
    )
    demo_supervisor_parser.add_argument(
        "--workspace",
        help=(
            "Directory where visible Codex lanes should start. Defaults to the current directory. "
            "For demos, prefer an empty user-owned research workspace that shares LoopX state through "
            "--registry/--runtime-root."
        ),
    )
    demo_supervisor_parser.add_argument(
        "--create-workspace",
        action="store_true",
        help="Create --workspace when it does not already exist.",
    )
    demo_supervisor_parser.add_argument(
        "--codex-trust-workspace",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Pass a per-invocation Codex trust config for the selected visible workspace. "
            "Default is off for generic supervisor launches."
        ),
    )

    demo_e2e_parser = auto_research_sub.add_parser(
        "demo-e2e",
        help=(
            "Run or preview the one-command multi-round lightweight research path and "
            "launch visible Codex TUI lanes or a headless worker-loop proof."
        ),
    )
    add_subcommand_format(demo_e2e_parser)
    demo_e2e_parser.add_argument("--agent-id", required=True)
    demo_e2e_parser.add_argument(
        "--agent",
        action="append",
        default=[],
        help=(
            "Optional visible worker lane as agent_id:lane_id:role_id. "
            "Omit to use the default four-role worker loop."
        ),
    )
    demo_e2e_parser.add_argument(
        "--goal-id",
        help=(
            "Research goal id for the demo evidence. "
            "Omit to create a fresh isolated demo goal surface."
        ),
    )
    demo_e2e_parser.add_argument(
        "--demo-run-id",
        help="Public-safe suffix for the generated demo goal id when --goal-id is omitted.",
    )
    demo_e2e_parser.add_argument(
        "--inherit-default-goal",
        action="store_true",
        help=f"Opt into reusing the shared internal demo goal {AUTO_RESEARCH_DEFAULT_GOAL_ID}.",
    )
    demo_e2e_parser.add_argument(
        "--tracking-goal-id",
        help=(
            "Optional parent/productization goal id for status writeback context. "
            "Visible lanes still inspect --goal-id as the research frontier."
        ),
    )
    demo_e2e_parser.add_argument(
        "--objective",
        default=AUTO_RESEARCH_DEFAULT_OBJECTIVE,
        help="Compact public-safe research objective for the generated contract.",
    )
    demo_e2e_parser.add_argument(
        "--language",
        choices=["auto", "zh", "en"],
        default="auto",
        help="Human-readable output language for visible role panes.",
    )
    demo_e2e_parser.add_argument(
        "--output-dir",
        default="auto_research_lightweight_kernel",
        help=argparse.SUPPRESS,
    )
    demo_e2e_parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Seed a demo-local LoopX goal queue and, by default, launch visible Codex TUI lanes "
            "that work the frontier from zero. Use --headless or --run-worker-loop for the "
            "non-interactive worker-loop proof path."
        ),
    )
    demo_e2e_parser.add_argument(
        "--run-worker-loop",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    demo_e2e_parser.add_argument(
        "--worker-loop-rounds",
        type=int,
        default=4,
        help="Maximum worker-loop rounds for --execute.",
    )
    demo_e2e_parser.add_argument(
        "--launch-visible",
        action="store_true",
        help=(
            "With --execute, explicitly launch the visible multi-lane supervisor. "
            "This is the default unless --headless is set. "
            "Visible panes alone do not make the multi-round kernel a live Codex E2E result."
        ),
    )
    demo_e2e_parser.add_argument(
        "--headless",
        action="store_true",
        help="With --execute, skip visible tmux/Codex panes and return JSON only.",
    )
    demo_e2e_parser.add_argument(
        "--live-evidence",
        help=(
            "Path to compact public-safe live Codex lane evidence. "
            "Raw transcripts are not read."
        ),
    )
    demo_e2e_parser.add_argument(
        "--visible-live-evidence-wait-seconds",
        type=float,
        default=30.0,
        help=argparse.SUPPRESS,
    )
    demo_e2e_parser.add_argument(
        "--configure-visible-worker-turn",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    demo_e2e_parser.add_argument(
        "--wake-visible-after-launch",
        action="store_true",
        help=(
            "After starting visible tmux panes, broadcast the fixed pane-local A2A "
            "wake prompt. Each pane still runs its own LoopX tick from state."
        ),
    )
    demo_e2e_parser.add_argument(
        "--auto-wake",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Run a session-scoped background wake loop for visible auto-research lanes. "
            "Use --no-auto-wake for full manual takeover."
        ),
    )
    demo_e2e_parser.add_argument(
        "--auto-wake-interval-seconds",
        type=float,
        default=45.0,
        help=argparse.SUPPRESS,
    )
    demo_e2e_parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Keep the temporary demo workspace after execution. The output payload still redacts its absolute path.",
    )
    demo_e2e_parser.add_argument(
        "--session-name",
        default="loopx-auto-research",
        help="Public-safe tmux session name when --launch-visible is set.",
    )
    demo_e2e_parser.add_argument("--cli-bin", default="loopx", help="LoopX CLI executable name.")
    demo_e2e_parser.add_argument("--codex-bin", default="codex", help="Codex CLI executable name.")
    demo_e2e_parser.add_argument("--tmux-bin", default="tmux", help="tmux executable name.")
    demo_e2e_parser.add_argument(
        "--reasoning-effort",
        default="high",
        help="Reasoning effort passed to visible Codex lanes through model_reasoning_effort.",
    )
    demo_e2e_parser.add_argument(
        "--launcher",
        choices=["auto", "tmux"],
        default="auto",
        help="Visible process launcher for --launch-visible. auto currently resolves to tmux.",
    )
    demo_e2e_parser.add_argument(
        "--attach",
        action="store_true",
        help="After visible launch with tmux, attach to the session. This is the default for --execute unless --no-attach is set.",
    )
    demo_e2e_parser.add_argument(
        "--no-attach",
        action="store_true",
        help="With the default visible --execute launch, start tmux in the background instead of attaching.",
    )
    demo_e2e_parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="With tmux launcher, kill an existing session with the same name before launching.",
    )
    demo_e2e_parser.add_argument(
        "--workspace",
        help=(
            "Directory where visible Codex lanes should start when --launch-visible is set. "
            "Omit to use a demo-owned clean workspace."
        ),
    )
    demo_e2e_parser.add_argument(
        "--create-workspace",
        action="store_true",
        help="Create --workspace when it does not already exist.",
    )
    demo_e2e_parser.add_argument(
        "--codex-trust-workspace",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Pass a per-invocation Codex trust config for the visible workspace. "
            "Defaults on only for the demo-owned clean workspace."
        ),
    )


def _execute_auto_research_demo_supervisor(
    payload: dict[str, object],
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    launcher: str,
    tmux_bin: str,
    cli_bin: str,
    codex_bin: str,
    attach: bool,
    replace_existing: bool,
    workspace: str | None,
    create_workspace: bool,
    codex_trust_workspace: bool,
    auto_wake: bool = False,
    auto_wake_interval_seconds: float = 45.0,
) -> dict[str, object]:
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_arg)
    result, chosen, workspace_mode = execute_visible_multi_agent_launcher(
        payload=payload,
        registry=registry_path,
        runtime_root=runtime_root,
        requested_launcher=launcher,
        tmux_bin=tmux_bin,
        cli_bin=cli_bin,
        codex_bin=codex_bin,
        attach=attach,
        replace_existing=replace_existing,
        workspace=workspace,
        create_workspace=create_workspace,
        cwd=Path.cwd(),
        codex_trust_workspace=codex_trust_workspace,
        auto_wake=auto_wake,
        auto_wake_interval_seconds=auto_wake_interval_seconds,
        launch_result_schema="auto_research_demo_launch_result_v0",
        lane_default="research-lane",
    )
    payload["mode"] = "executed_visible_launch"
    payload["launch_result"] = result
    boundary = payload.get("boundary")
    if isinstance(boundary, dict):
        boundary.update(
            {
                "dry_run_plan_only": False,
                "starts_tmux": chosen == "tmux",
                "opens_terminal": False,
                "runs_codex": True,
                "writes_loopx_state": False,
                "spends_loopx_quota": False,
                "external_service_call": False,
                "workspace_mode": workspace_mode,
                "workspace_write_scope": "selected_visible_workspace_only",
                "codex_trust_workspace": codex_trust_workspace,
                "codex_trust_scope": (
                    "persisted_selected_workspace_and_git_root"
                    if codex_trust_workspace
                    else "native_codex_trust_prompt"
                ),
                "shared_state_route": "LOOPX_REGISTRY_and_LOOPX_RUNTIME_ROOT",
                "shared_goal_surface": True,
                "all_lane_workspace_isolation": False,
                "mutation_isolation_policy": (
                    "only mutating research-executor attempts require a claimed git worktree "
                    "or equivalent execution boundary"
                ),
            }
        )
    return payload


def handle_auto_research_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int:
    try:
        if args.auto_research_command == "contract":
            payload = build_auto_research_user_contract(
                args.open_question,
                max_todos=args.max_todos,
                output_language=args.language,
                preset_id=args.preset,
            )
        elif args.auto_research_command == "start":
            preset_id = normalize_auto_research_preset_id(args.preset)
            launch_visible = bool(args.execute and not args.headless)
            visible_policy = resolve_visible_launch_policy(
                args,
            launch_visible=launch_visible,
            default_wake_allowed=False,
            default_attach_allowed=launch_visible,
                attach_wake_conflict_message=(
                    "--attach cannot be combined with --wake-visible-after-launch; "
                    "choose operator takeover (--attach) or visible role wake (--no-attach)"
                ),
            )
            goal_id, goal_surface_mode = _resolve_demo_goal_surface(
                goal_id=args.goal_id,
                demo_run_id=args.demo_run_id,
                inherit_default_goal=False,
            )
            output_language = infer_auto_research_output_language(
                args.open_question,
                output_language=args.language,
            )

            def append_start_evidence(packet_path: str) -> dict[str, object]:
                return _append_auto_research_rollout_events(
                    packet_path=packet_path,
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    dry_run=False,
                )

            visible_launcher: Callable[[dict[str, object], Path, str | None, Path], dict[str, object]] | None = None
            visible_wake: Callable[[str, list[str]], dict[str, object]] | None = None
            codex_trust_visible_workspace = resolve_codex_trust_workspace(
                args,
                launch_visible=visible_policy.launch_visible,
                default=True,
            )
            prepared_preset_workspace: dict[str, object] | None = None

            def start_workspace_policy(_default_workspace: Path) -> tuple[str | None, bool, bool]:
                nonlocal prepared_preset_workspace
                default_start_workspace = args.workspace is None
                workspace = (
                    _default_auto_research_start_workspace(goal_id)
                    if default_start_workspace
                    else args.workspace
                )
                create_workspace = True if default_start_workspace else args.create_workspace
                if preset_id == AUTO_RESEARCH_KNN_DEMO_PRESET_ID and workspace:
                    prepared_preset_workspace = materialize_knn_demo_workspace(
                        workspace,
                        goal_id=goal_id,
                    )
                    create_workspace = True
                return (
                    workspace,
                    create_workspace,
                    codex_trust_visible_workspace,
                )

            visible_launcher = make_visible_launcher_callback(
                launch_visible=visible_policy.launch_visible,
                launch_executor=_execute_auto_research_demo_supervisor,
                launcher=args.launcher,
                tmux_bin=args.tmux_bin,
                cli_bin=args.cli_bin,
                codex_bin=args.codex_bin,
                attach=visible_policy.attach,
                replace_existing=args.replace_existing,
                workspace_policy=start_workspace_policy,
                auto_wake=bool(visible_policy.launch_visible and args.auto_wake),
                auto_wake_interval_seconds=args.auto_wake_interval_seconds,
            )

            if visible_policy.launch_visible:
                visible_wake = make_visible_wake_callback(tmux_bin=args.tmux_bin)

            payload = run_auto_research_demo_e2e(
                agent_id=AUTO_RESEARCH_START_AGENT_ID,
                goal_id=goal_id,
                goal_surface_mode=goal_surface_mode,
                tracking_goal_id=args.tracking_goal_id,
                preset_id=preset_id,
                objective=args.open_question,
                output_dir="auto_research_lightweight_kernel",
                execute=args.execute,
                run_worker_loop=bool(args.execute and args.headless),
                worker_loop_rounds=args.worker_loop_rounds,
                configure_visible_worker_turn=args.configure_visible_worker_turn,
                launch_visible=visible_policy.launch_visible,
                keep_workspace=args.keep_workspace,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                session_name=args.session_name,
                cli_bin=args.cli_bin,
                codex_bin=args.codex_bin,
                tmux_bin=args.tmux_bin,
                reasoning_effort=args.reasoning_effort,
                output_language=output_language,
                live_evidence_path=None,
                append_evidence=append_start_evidence,
                visible_launcher=visible_launcher,
                visible_wake=visible_wake,
                wake_visible_after_launch=visible_policy.wake_visible_after_launch,
            )
            if prepared_preset_workspace:
                payload["preset_workspace"] = prepared_preset_workspace
        elif args.auto_research_command == "frontier":
            if bool(args.fixture) == bool(args.goal_id):
                raise ValueError(f"auto-research {args.auto_research_command} requires exactly one of --fixture or --goal-id")
            if args.fixture:
                fixture = load_auto_research_fixture(args.fixture)
                payload = build_auto_research_projection(
                    fixture,
                    agent_id=args.agent_id,
                )
            else:
                status_payload = collect_status(
                    registry_path=registry_path,
                    runtime_root_override=runtime_root_arg,
                    limit=5,
                    goal_id=args.goal_id,
                )
                quota_payload = build_quota_should_run(
                    status_payload,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                )
                registry = load_registry(registry_path)
                runtime_root = resolve_runtime_root(registry, runtime_root_arg)
                rollout_events = load_rollout_events(
                    rollout_event_log_path(runtime_root, args.goal_id)
                )
                payload = build_live_auto_research_projection(
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    quota_payload=quota_payload,
                    rollout_events=rollout_events,
                )
        elif args.auto_research_command == "evidence":
            payload = load_auto_research_evidence_packet_inputs(
                contract_path=args.contract,
                eval_result_paths=args.eval_result,
                hypothesis_id=args.hypothesis_id,
                todo_id=args.todo_id,
                agent_id=args.agent_id,
                claimed_by=args.claimed_by,
                mechanism_family=args.mechanism_family,
                hypothesis=args.hypothesis,
                parent_hypothesis_id=args.parent_hypothesis_id,
                grounding_refs=args.grounding_ref,
                novelty_audit_ref=args.novelty_audit_ref,
                branch_ref=args.branch_ref,
                attempt_start=args.attempt_start,
            )
        elif args.auto_research_command == "append-evidence":
            payload = _append_auto_research_rollout_events(
                packet_path=args.packet,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                dry_run=args.dry_run,
            )
            if args.output:
                output_path = Path(args.output).expanduser()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
        elif args.auto_research_command == "decide":
            payload = decide_terminal_result(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                hypothesis_id=args.hypothesis_id,
                outcome=args.outcome,
                reason=args.reason,
                decided_by=args.agent_id,
                evidence_refs=args.evidence_ref,
                dry_run=not args.execute,
            )
        elif args.auto_research_command == "review":
            payload = review_terminal_result(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                hypothesis_id=args.hypothesis_id,
                reviewer_agent_id=args.reviewer_agent_id,
                verdict=args.verdict,
                evidence_refs=args.evidence_ref,
                require_independent=args.require_independent,
                dry_run=not args.execute,
            )
        elif args.auto_research_command == "results":
            payload = query_terminal_results(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                hypothesis_id=args.hypothesis_id,
                include_history=args.include_history,
            )
        elif args.auto_research_command == "project-results":
            payload = project_terminal_results(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                dry_run=not args.execute,
            )
        elif args.auto_research_command == "artifact-receipt":
            payload = load_auto_research_artifact_receipt(
                contract_path=args.contract,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
            )
        elif args.auto_research_command == "capture-live-evidence":
            payload = capture_live_codex_e2e_evidence(
                packet_path=args.packet,
                append_result_path=args.append_result,
                agent_id=args.agent_id,
                lane_count=args.lane_count,
                visible_lanes_accepted=args.visible_lanes_accepted,
            )
            if args.execute:
                output_path = Path(args.output).expanduser()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
        elif args.auto_research_command == "worker-turn":
            def append_worker_evidence(packet_path: str) -> dict[str, object]:
                return _append_auto_research_rollout_events(
                    packet_path=packet_path,
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    dry_run=False,
                )

            payload = run_auto_research_worker_turn(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                objective=args.objective,
                workspace=Path.cwd(),
                output_dir=args.output_dir,
                evidence_dir=args.evidence_dir,
                execute=args.execute,
                append_evidence=append_worker_evidence if args.execute else None,
                lane_count=args.lane_count,
                visible_lanes_accepted=args.visible_lanes_accepted,
                live_evidence_output=args.live_evidence_output,
                complete_selected_todo=args.complete_selected_todo,
            )
        elif args.auto_research_command == "worker-loop":
            def append_loop_evidence(packet_path: str) -> dict[str, object]:
                return _append_auto_research_rollout_events(
                    packet_path=packet_path,
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    dry_run=False,
                )

            payload = run_auto_research_worker_loop(
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                agent_ids=args.agent_id,
                objective=args.objective,
                workspace=Path.cwd(),
                output_dir=args.output_dir,
                evidence_dir=args.evidence_dir,
                execute=args.execute,
                append_evidence=append_loop_evidence if args.execute else None,
                lane_count=args.lane_count,
                visible_lanes_accepted=args.visible_lanes_accepted,
                live_evidence_output=args.live_evidence_output,
                complete_selected_todo=args.complete_selected_todo,
                max_rounds=args.max_rounds,
            )
        elif args.auto_research_command == "demo-supervisor":
            goal_id, goal_surface_mode = _resolve_demo_goal_surface(
                goal_id=args.goal_id,
                demo_run_id=args.demo_run_id,
                inherit_default_goal=args.inherit_default_goal,
            )
            payload = build_auto_research_demo_supervisor_plan(
                goal_id=goal_id,
                agent_specs=args.agent,
                session_name=args.session_name,
                cli_bin=args.cli_bin,
                codex_bin=args.codex_bin,
                tmux_bin=args.tmux_bin,
                reasoning_effort=args.reasoning_effort,
                output_language=infer_auto_research_output_language(
                    "",
                    output_language=args.language,
                ),
            )
            payload["goal_surface_route"] = {
                "schema_version": "auto_research_demo_goal_surface_v0",
                "mode": goal_surface_mode,
                "goal_id": goal_id,
                "fresh_by_default": goal_surface_mode == "fresh_demo_goal",
                "reuses_default_internal_goal": goal_id == AUTO_RESEARCH_DEFAULT_GOAL_ID,
                "default_internal_goal_id": AUTO_RESEARCH_DEFAULT_GOAL_ID,
            }
            if args.execute:
                payload = _execute_auto_research_demo_supervisor(
                    payload,
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    launcher=args.launcher,
                    tmux_bin=args.tmux_bin,
                    cli_bin=args.cli_bin,
                    codex_bin=args.codex_bin,
                    attach=args.attach,
                    replace_existing=args.replace_existing,
                    workspace=args.workspace,
                    create_workspace=args.create_workspace,
                    codex_trust_workspace=args.codex_trust_workspace,
                )
        elif args.auto_research_command == "demo-e2e":
            if args.headless and args.launch_visible:
                raise ValueError("--headless cannot be combined with --launch-visible")
            goal_id, goal_surface_mode = _resolve_demo_goal_surface(
                goal_id=args.goal_id,
                demo_run_id=args.demo_run_id,
                inherit_default_goal=args.inherit_default_goal,
            )

            def append_demo_e2e_evidence(packet_path: str) -> dict[str, object]:
                return _append_auto_research_rollout_events(
                    packet_path=packet_path,
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    dry_run=False,
                )

            visible_launcher: Callable[[dict[str, object], Path, str | None, Path], dict[str, object]] | None = None
            visible_wake: Callable[[str, list[str]], dict[str, object]] | None = None
            auto_visible_launch = bool(args.execute and not args.headless)
            launch_visible = bool(args.launch_visible or auto_visible_launch)
            visible_policy = resolve_visible_launch_policy(
                args,
                launch_visible=launch_visible,
                default_wake_allowed=auto_visible_launch,
                default_attach_allowed=auto_visible_launch,
                attach_wake_conflict_message=(
                    "--wake-visible-after-launch cannot be combined with --attach; "
                    "wake evidence must be recorded before operator takeover"
                ),
            )
            def demo_workspace_policy(default_workspace: Path) -> tuple[str | None, bool, bool]:
                demo_owned_workspace = args.workspace is None
                return (
                    str(default_workspace / "visible-user-workspace")
                    if demo_owned_workspace
                    else args.workspace,
                    True if demo_owned_workspace else args.create_workspace,
                    resolve_codex_trust_workspace(
                        args,
                        launch_visible=visible_policy.launch_visible,
                        default=demo_owned_workspace,
                    ),
                )

            visible_launcher = make_visible_launcher_callback(
                launch_visible=visible_policy.launch_visible,
                launch_executor=_execute_auto_research_demo_supervisor,
                launcher=args.launcher,
                tmux_bin=args.tmux_bin,
                cli_bin=args.cli_bin,
                codex_bin=args.codex_bin,
                attach=visible_policy.attach,
                replace_existing=args.replace_existing,
                workspace_policy=demo_workspace_policy,
                auto_wake=bool(visible_policy.launch_visible and args.auto_wake),
                auto_wake_interval_seconds=args.auto_wake_interval_seconds,
            )

            if visible_policy.launch_visible:
                visible_wake = make_visible_wake_callback(tmux_bin=args.tmux_bin)

            run_hidden_worker_loop = bool(args.execute and (args.headless or args.run_worker_loop))
            payload = run_auto_research_demo_e2e(
                agent_id=args.agent_id,
                goal_id=goal_id,
                goal_surface_mode=goal_surface_mode,
                agent_specs=args.agent,
                tracking_goal_id=args.tracking_goal_id,
                objective=args.objective,
                output_dir=args.output_dir,
                execute=args.execute,
                run_worker_loop=run_hidden_worker_loop,
                worker_loop_rounds=args.worker_loop_rounds,
                configure_visible_worker_turn=args.configure_visible_worker_turn,
                launch_visible=visible_policy.launch_visible,
                keep_workspace=args.keep_workspace,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                session_name=args.session_name,
                cli_bin=args.cli_bin,
                codex_bin=args.codex_bin,
                tmux_bin=args.tmux_bin,
                reasoning_effort=args.reasoning_effort,
                output_language=infer_auto_research_output_language(
                    args.objective,
                    output_language=args.language,
                ),
                live_evidence_path=args.live_evidence,
                append_evidence=append_demo_e2e_evidence,
                visible_launcher=visible_launcher,
                visible_wake=visible_wake,
                wake_visible_after_launch=visible_policy.wake_visible_after_launch,
                visible_live_evidence_wait_seconds=args.visible_live_evidence_wait_seconds,
            )
        else:
            raise ValueError(
                "auto-research requires the `contract`, `frontier`, `evidence`, "
                "`append-evidence`, `decide`, `review`, `results`, "
                "`project-results`, `artifact-receipt`, `capture-live-evidence`, "
                "`worker-turn`, `worker-loop`, `demo-supervisor`, `demo-e2e`, "
                "or `start` subcommand"
            )
    except Exception as exc:
        error_code, public_error = _public_auto_research_error(exc)
        payload = {
            "ok": False,
            "mode": "auto-research",
            "error_code": error_code,
            "error": public_error,
        }
    print_payload(payload, output_format(args, "auto_research_format"), render_auto_research_markdown)
    return 0 if payload.get("ok") else 1
