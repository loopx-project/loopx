from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from loopx.control_plane.heartbeat.agent_input import (
    HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION,
)
from loopx.control_plane.testing.cli_output_semantics import (
    action_signature_semantic_sha256,
    json_shape_paths as collect_json_shape_paths,
    markdown_headings,
)


CLI_OUTPUT_BUDGET_SCHEMA_VERSION = "loopx_cli_output_budget_v0"
CLI_OUTPUT_MEASUREMENT_SCHEMA_VERSION = "loopx_cli_output_measurement_v0"

OutputFormat = Literal["json", "markdown"]
QualificationPolicy = Literal[
    "absolute_hot_path",
    "baseline_and_growth",
    "explicit_limit_cold_path",
]
CommandQualification = Literal[
    "qualified_default",
    "explicit_cold_path_exception",
]


@dataclass(frozen=True)
class CliOutputBudgetSpec:
    surface_id: str
    command: str
    owner: str
    consumer_action: str
    qualification_policy: QualificationPolicy
    cold_path: str
    semantic_json_keys: tuple[str, ...]
    markdown_anchor: str
    max_chars: dict[str, dict[OutputFormat, int]]
    max_lines: dict[str, dict[OutputFormat, int]]
    scale_axis: str | None = None
    max_json_growth_chars_per_unit: int | None = None
    max_json_fixed_semantic_growth_chars: int = 0
    output_contract_version: str | None = None


@dataclass(frozen=True)
class CliOutputModeVariantSpec:
    variant_id: str
    parent_surface_id: str
    command: str
    output_formats: tuple[OutputFormat, ...]
    semantic_json_keys: tuple[str, ...]
    markdown_anchor: str
    max_chars: dict[OutputFormat, int]
    max_lines: dict[OutputFormat, int]


@dataclass(frozen=True)
class CliOutputCommandClassification:
    command_id: str
    qualification: CommandQualification
    surface_id: str | None
    rationale: str


# These ceilings characterize the emitted CLI text on public fixtures. They are
# deliberately separate from compact in-memory payload budgets. Baseline-only
# ceilings freeze current debt so later optimization can lower them safely.
CLI_OUTPUT_BUDGET_SPECS: tuple[CliOutputBudgetSpec, ...] = (
    CliOutputBudgetSpec(
        surface_id="start_goal_guided",
        command="start-goal --guided",
        owner="goal bootstrap",
        consumer_action="plan a goal start before any mutation",
        qualification_policy="baseline_and_growth",
        cold_path="--include-command-pack-detail and bootstrap-command-pack",
        semantic_json_keys=(
            "guided_transaction",
            "command_pack",
            "safety_contract",
            "packet_summary",
        ),
        markdown_anchor="# Guided Start Goal",
        max_chars={
            "small": {"json": 40_000, "markdown": 3_200},
            "crowded": {"json": 40_000, "markdown": 3_200},
            "multi_agent": {"json": 40_000, "markdown": 3_200},
        },
        max_lines={
            "small": {"json": 450, "markdown": 55},
            "crowded": {"json": 450, "markdown": 55},
            "multi_agent": {"json": 450, "markdown": 55},
        },
        scale_axis="todo_count",
        max_json_growth_chars_per_unit=8,
    ),
    CliOutputBudgetSpec(
        surface_id="bootstrap_command_pack",
        command="bootstrap-command-pack",
        owner="slash-command bootstrap",
        consumer_action="connect and activate one host loop",
        qualification_policy="baseline_and_growth",
        cold_path="message-only projection and packet_summary detail_refs",
        semantic_json_keys=(
            "commands",
            "goal_start_contract",
            "host_loop_activation",
            "packet_summary",
        ),
        markdown_anchor="# /loopx Bootstrap Command Pack",
        max_chars={
            "small": {"json": 45_000, "markdown": 14_500},
            "crowded": {"json": 45_000, "markdown": 14_500},
            "multi_agent": {"json": 45_000, "markdown": 14_500},
        },
        max_lines={
            "small": {"json": 470, "markdown": 240},
            "crowded": {"json": 470, "markdown": 240},
            "multi_agent": {"json": 470, "markdown": 240},
        },
        scale_axis="todo_count",
        max_json_growth_chars_per_unit=8,
    ),
    CliOutputBudgetSpec(
        surface_id="quota_should_run",
        command="quota should-run",
        owner="quota guard",
        consumer_action="decide whether one agent may run",
        qualification_policy="absolute_hot_path",
        cold_path=(
            "status, history, active state, and repeatable --include-detail "
            "selectors for scheduler, agent-todos, user-todos, goal-boundary, "
            "or all"
        ),
        semantic_json_keys=("interaction_contract", "scheduler_hint", "selected_todo"),
        markdown_anchor="# LoopX Quota Should Run",
        max_chars={
            "small": {"json": 20_000, "markdown": 6_700},
            "crowded": {"json": 30_000, "markdown": 7_800},
            "multi_agent": {"json": 23_000, "markdown": 7_000},
        },
        max_lines={
            "small": {"json": 520, "markdown": 72},
            "crowded": {"json": 750, "markdown": 78},
            "multi_agent": {"json": 650, "markdown": 75},
        },
        scale_axis="todo_count",
        max_json_growth_chars_per_unit=300,
    ),
    CliOutputBudgetSpec(
        surface_id="loopx_turn_plan",
        command="turn plan",
        owner="LoopX Turn",
        consumer_action="route one live LoopX decision without host or state side effects",
        qualification_policy="absolute_hot_path",
        cold_path=(
            "--include-transaction-detail, turn run-once, TurnEnvelope detail_ref "
            "commands, and full quota should-run decision"
        ),
        semantic_json_keys=("route", "turn_envelope", "effects", "boundary"),
        markdown_anchor="# LoopX Turn Plan",
        max_chars={
            "small": {"json": 12_000, "markdown": 300},
            # The crowded fixture exercises the required-vision route. Its
            # TurnEnvelope intentionally carries the complete authoring schema
            # that the validator accepts, plus the typed executor and selection
            # facts needed to decide whether execution is authorized. The
            # latest-main fixture measures 14,159 chars, so 14,500 retains a
            # narrow 341-char regression margin without relaxing Todo growth.
            # The over-target TurnEnvelope diagnostic remains visible instead
            # of hiding authority overflow; latest main renders it in 542
            # characters, leaving a narrow 58-character presentation margin.
            "crowded": {"json": 14_500, "markdown": 600},
            "multi_agent": {"json": 12_000, "markdown": 300},
        },
        max_lines={
            "small": {"json": 320, "markdown": 12},
            # The same latest-main fixture measures 389 lines. Keep a bounded
            # 11-line formatting margin while the semantic character budget
            # above remains the primary cost guard.
            "crowded": {"json": 400, "markdown": 12},
            "multi_agent": {"json": 320, "markdown": 12},
        },
        scale_axis="todo_count",
        max_json_growth_chars_per_unit=60,
        # The complete validator-owned vision-authoring schema appears only on
        # the required-vision route. Account for that fixed semantic packet
        # separately so it does not relax the per-Todo growth budget.
        max_json_fixed_semantic_growth_chars=3_800,
    ),
    CliOutputBudgetSpec(
        surface_id="status",
        command="status --goal-id",
        owner="operator and agent status",
        consumer_action="locate the current goal and attention state",
        qualification_policy="absolute_hot_path",
        cold_path=(
            "status without --agent-id or todo list for whole-goal todo index "
            "detail; --include-task-graph, history, and run artifacts"
        ),
        semantic_json_keys=("status_contract", "attention_queue", "todo_index"),
        markdown_anchor="# LoopX Status",
        max_chars={
            "small": {"json": 42_000, "markdown": 7_000},
            "crowded": {"json": 58_000, "markdown": 7_800},
            "multi_agent": {"json": 58_000, "markdown": 7_800},
        },
        max_lines={
            "small": {"json": 1_120, "markdown": 85},
            "crowded": {"json": 1_550, "markdown": 90},
            "multi_agent": {"json": 1_550, "markdown": 90},
        },
        scale_axis="todo_count",
        max_json_growth_chars_per_unit=550,
    ),
    CliOutputBudgetSpec(
        surface_id="diagnose",
        command="diagnose --goal-id",
        owner="agent self-repair",
        consumer_action="explain whether and why one goal can advance",
        qualification_policy="explicit_limit_cold_path",
        cold_path="status plus goal-specific quota and todo reads",
        semantic_json_keys=("agent_must_reason", "selected", "goals", "status_summary"),
        markdown_anchor="# LoopX Diagnosis Packet",
        max_chars={
            "small": {"json": 21_000, "markdown": 4_300},
            "crowded": {"json": 34_000, "markdown": 4_500},
            "multi_agent": {"json": 21_000, "markdown": 4_300},
        },
        max_lines={
            "small": {"json": 470, "markdown": 72},
            "crowded": {"json": 720, "markdown": 72},
            "multi_agent": {"json": 480, "markdown": 72},
        },
        scale_axis="todo_count",
        max_json_growth_chars_per_unit=520,
    ),
    CliOutputBudgetSpec(
        surface_id="review_packet_handoff_only",
        command="review-packet --handoff-only",
        owner="project-agent handoff",
        consumer_action="forward the smallest sufficient work packet",
        qualification_policy="absolute_hot_path",
        cold_path="full review-packet and run-history artifacts",
        semantic_json_keys=(
            "project_agent_handoff",
            "handoff_interface_budget",
            "within_budget",
        ),
        markdown_anchor="quota should-run",
        max_chars={
            "small": {"json": 4_500, "markdown": 1_400},
            "crowded": {"json": 5_100, "markdown": 1_700},
            "multi_agent": {"json": 5_100, "markdown": 1_700},
        },
        max_lines={
            "small": {"json": 48, "markdown": 18},
            "crowded": {"json": 48, "markdown": 20},
            "multi_agent": {"json": 48, "markdown": 20},
        },
        scale_axis="todo_count",
        max_json_growth_chars_per_unit=20,
    ),
    CliOutputBudgetSpec(
        surface_id="heartbeat_prompt_thin",
        command="heartbeat-prompt --thin",
        owner="heartbeat automation",
        consumer_action="wake and route one bounded turn",
        qualification_policy="absolute_hot_path",
        cold_path=(
            "heartbeat-prompt Markdown diagnostics, --compact, or --full"
        ),
        semantic_json_keys=(
            "schema_version",
            "task_body",
            "interface_budget",
        ),
        markdown_anchor="# Heartbeat Automation Prompt",
        max_chars={
            "small": {"json": 3_400, "markdown": 5_100},
            "crowded": {"json": 3_400, "markdown": 5_100},
            "multi_agent": {"json": 3_400, "markdown": 5_100},
        },
        max_lines={
            "small": {"json": 18, "markdown": 72},
            "crowded": {"json": 18, "markdown": 72},
            "multi_agent": {"json": 18, "markdown": 72},
        },
        scale_axis="todo_count",
        max_json_growth_chars_per_unit=4,
        output_contract_version=HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION,
    ),
    CliOutputBudgetSpec(
        surface_id="todo_list",
        command="todo list",
        owner="todo control plane",
        consumer_action="select and inspect current work items",
        qualification_policy="baseline_and_growth",
        cold_path=(
            "role/status filters, full per-role sections without --limit, and "
            "direct todo-id lifecycle commands"
        ),
        semantic_json_keys=("todos", "agent_todos", "filter_semantics"),
        markdown_anchor="# LoopX Todo List",
        max_chars={
            "small": {"json": 7_500, "markdown": 1_000},
            "crowded": {"json": 82_000, "markdown": 8_500},
            "multi_agent": {"json": 30_000, "markdown": 2_200},
        },
        max_lines={
            "small": {"json": 220, "markdown": 28},
            "crowded": {"json": 2_300, "markdown": 70},
            "multi_agent": {"json": 850, "markdown": 38},
        },
        scale_axis="todo_count",
        max_json_growth_chars_per_unit=2_050,
    ),
    CliOutputBudgetSpec(
        surface_id="history_limited",
        command="history --limit 5",
        owner="run-history read model",
        consumer_action="inspect bounded recent progress",
        qualification_policy="explicit_limit_cold_path",
        cold_path="individual run JSON/Markdown artifacts",
        semantic_json_keys=("goals", "runs", "run_count"),
        markdown_anchor="# LoopX Run History",
        max_chars={
            "small": {"json": 5_800, "markdown": 900},
            "crowded": {"json": 12_500, "markdown": 1_500},
            "multi_agent": {"json": 12_500, "markdown": 1_500},
        },
        max_lines={
            "small": {"json": 160, "markdown": 22},
            "crowded": {"json": 280, "markdown": 28},
            "multi_agent": {"json": 290, "markdown": 28},
        },
        scale_axis="returned_run_count",
        max_json_growth_chars_per_unit=1_600,
    ),
    CliOutputBudgetSpec(
        surface_id="evidence_log_thin",
        command="evidence-log --thin --limit 5",
        owner="agent-scoped evidence ledger",
        consumer_action="read bounded public-safe evidence for replan",
        qualification_policy="explicit_limit_cold_path",
        cold_path="referenced run-history and rollout-event artifacts",
        semantic_json_keys=("ledger", "truncated", "other_agent_frontier"),
        markdown_anchor="# LoopX Evidence Log",
        max_chars={
            "small": {"json": 2_900, "markdown": 800},
            "crowded": {"json": 3_500, "markdown": 1_100},
            "multi_agent": {"json": 4_300, "markdown": 1_200},
        },
        max_lines={
            "small": {"json": 100, "markdown": 26},
            "crowded": {"json": 120, "markdown": 30},
            "multi_agent": {"json": 140, "markdown": 34},
        },
        scale_axis="returned_evidence_count",
        max_json_growth_chars_per_unit=800,
    ),
)


CLI_OUTPUT_BUDGET_BY_ID = {spec.surface_id: spec for spec in CLI_OUTPUT_BUDGET_SPECS}


# Explicit variants are opt-in cold paths, but they still need real stdout
# characterization. This prevents a compact default from hiding unbounded
# growth in the exact diagnostic mode an agent uses during repair.
CLI_OUTPUT_MODE_VARIANT_SPECS: tuple[CliOutputModeVariantSpec, ...] = (
    CliOutputModeVariantSpec(
        variant_id="start_goal_guided_command_pack_detail",
        parent_surface_id="start_goal_guided",
        command="start-goal --guided --include-command-pack-detail",
        output_formats=("json", "markdown"),
        semantic_json_keys=(
            "guided_transaction",
            "command_pack",
            "safety_contract",
            "packet_summary",
        ),
        markdown_anchor="# Guided Start Goal",
        max_chars={"json": 64_000, "markdown": 3_200},
        max_lines={"json": 650, "markdown": 55},
    ),
    CliOutputModeVariantSpec(
        variant_id="bootstrap_command_pack_message_only",
        parent_surface_id="bootstrap_command_pack",
        command="bootstrap-command-pack --message-only",
        output_formats=("markdown",),
        semantic_json_keys=(),
        markdown_anchor="Handle `/loopx`",
        max_chars={"markdown": 9_500},
        max_lines={"markdown": 135},
    ),
    CliOutputModeVariantSpec(
        variant_id="quota_should_run_scheduler_detail",
        parent_surface_id="quota_should_run",
        command="quota should-run --include-detail scheduler",
        output_formats=("json", "markdown"),
        semantic_json_keys=("interaction_contract", "scheduler_hint", "selected_todo"),
        markdown_anchor="# LoopX Quota Should Run",
        max_chars={"json": 31_000, "markdown": 6_700},
        max_lines={"json": 820, "markdown": 72},
    ),
    CliOutputModeVariantSpec(
        variant_id="quota_should_run_todo_summary_detail",
        parent_surface_id="quota_should_run",
        command="quota should-run --include-detail agent-todos",
        output_formats=("json", "markdown"),
        semantic_json_keys=("interaction_contract", "scheduler_hint", "selected_todo"),
        markdown_anchor="# LoopX Quota Should Run",
        max_chars={"json": 55_000, "markdown": 7_800},
        max_lines={"json": 1_350, "markdown": 78},
    ),
    CliOutputModeVariantSpec(
        variant_id="quota_should_run_user_todo_summary_detail",
        parent_surface_id="quota_should_run",
        command="quota should-run --include-detail user-todos",
        output_formats=("json", "markdown"),
        semantic_json_keys=("interaction_contract", "scheduler_hint", "selected_todo"),
        markdown_anchor="# LoopX Quota Should Run",
        max_chars={"json": 35_000, "markdown": 7_800},
        max_lines={"json": 900, "markdown": 78},
    ),
    CliOutputModeVariantSpec(
        variant_id="quota_should_run_goal_boundary_detail",
        parent_surface_id="quota_should_run",
        command="quota should-run --include-detail goal-boundary",
        output_formats=("json", "markdown"),
        semantic_json_keys=("interaction_contract", "scheduler_hint", "selected_todo"),
        markdown_anchor="# LoopX Quota Should Run",
        max_chars={"json": 35_000, "markdown": 7_800},
        max_lines={"json": 900, "markdown": 78},
    ),
    CliOutputModeVariantSpec(
        variant_id="quota_should_run_vision_detail",
        parent_surface_id="quota_should_run",
        command="quota should-run --include-detail vision",
        output_formats=("json", "markdown"),
        semantic_json_keys=("interaction_contract", "scheduler_hint", "selected_todo"),
        markdown_anchor="# LoopX Quota Should Run",
        max_chars={"json": 55_000, "markdown": 7_800},
        max_lines={"json": 1_350, "markdown": 78},
    ),
    CliOutputModeVariantSpec(
        variant_id="quota_should_run_all_detail",
        parent_surface_id="quota_should_run",
        command="quota should-run --include-detail all",
        output_formats=("json", "markdown"),
        semantic_json_keys=("interaction_contract", "scheduler_hint", "selected_todo"),
        markdown_anchor="# LoopX Quota Should Run",
        max_chars={"json": 85_000, "markdown": 7_800},
        max_lines={"json": 2_100, "markdown": 78},
    ),
    CliOutputModeVariantSpec(
        variant_id="quota_should_run_turn_envelope",
        parent_surface_id="quota_should_run",
        command="quota should-run --turn-envelope",
        output_formats=("json", "markdown"),
        semantic_json_keys=("schema_version", "contract_capsule", "action_signature"),
        markdown_anchor="# LoopX Turn Envelope",
        max_chars={"json": 9_000, "markdown": 650},
        max_lines={"json": 250, "markdown": 20},
    ),
    CliOutputModeVariantSpec(
        variant_id="loopx_turn_plan_transaction_detail",
        parent_surface_id="loopx_turn_plan",
        command="turn plan --include-transaction-detail",
        output_formats=("json",),
        semantic_json_keys=(
            "route",
            "session",
            "transaction",
            "turn_envelope",
            "effects",
            "boundary",
        ),
        markdown_anchor=None,
        max_chars={"json": 13_000},
        max_lines={"json": 360},
    ),
    CliOutputModeVariantSpec(
        variant_id="loopx_turn_run_once_preview",
        parent_surface_id="loopx_turn_plan",
        command="turn run-once",
        output_formats=("json", "markdown"),
        semantic_json_keys=("status", "host", "effects", "resume_turn_key"),
        markdown_anchor="# LoopX Turn Run Once",
        max_chars={"json": 3_000, "markdown": 350},
        max_lines={"json": 90, "markdown": 12},
    ),
    CliOutputModeVariantSpec(
        variant_id="status_task_graph_detail",
        parent_surface_id="status",
        command="status --include-task-graph",
        output_formats=("json", "markdown"),
        semantic_json_keys=("status_contract", "attention_queue", "todo_index"),
        markdown_anchor="# LoopX Status",
        max_chars={"json": 47_000, "markdown": 7_000},
        max_lines={"json": 1_250, "markdown": 85},
    ),
    CliOutputModeVariantSpec(
        variant_id="review_packet_full",
        parent_surface_id="review_packet_handoff_only",
        command="review-packet",
        output_formats=("json", "markdown"),
        semantic_json_keys=(
            "goal_id",
            "project_agent_handoff",
            "handoff_interface_budget",
        ),
        markdown_anchor="LoopX Review Packet",
        max_chars={"json": 11_000, "markdown": 2_000},
        max_lines={"json": 220, "markdown": 35},
    ),
    CliOutputModeVariantSpec(
        variant_id="heartbeat_prompt_brief",
        parent_surface_id="heartbeat_prompt_thin",
        command="heartbeat-prompt --brief",
        output_formats=("json", "markdown"),
        semantic_json_keys=("task_body", "quota_guard_command", "interface_budget"),
        markdown_anchor="# Heartbeat Automation Prompt",
        max_chars={"json": 10_500, "markdown": 9_000},
        max_lines={"json": 58, "markdown": 115},
    ),
    CliOutputModeVariantSpec(
        variant_id="heartbeat_prompt_compact",
        parent_surface_id="heartbeat_prompt_thin",
        command="heartbeat-prompt --compact",
        output_formats=("json", "markdown"),
        semantic_json_keys=("task_body", "quota_guard_command", "interface_budget"),
        markdown_anchor="# Heartbeat Automation Prompt",
        max_chars={"json": 13_000, "markdown": 11_500},
        max_lines={"json": 58, "markdown": 155},
    ),
    CliOutputModeVariantSpec(
        variant_id="heartbeat_prompt_full",
        parent_surface_id="heartbeat_prompt_thin",
        command="heartbeat-prompt --full",
        output_formats=("json", "markdown"),
        semantic_json_keys=("task_body", "quota_guard_command", "interface_budget"),
        markdown_anchor="# Heartbeat Automation Prompt",
        max_chars={"json": 20_000, "markdown": 18_000},
        max_lines={"json": 58, "markdown": 280},
    ),
    CliOutputModeVariantSpec(
        variant_id="todo_list_limited",
        parent_surface_id="todo_list",
        command="todo list --limit 3",
        output_formats=("json", "markdown"),
        semantic_json_keys=(
            "todos",
            "agent_todos",
            "explicit_limit",
            "todo_list_projection",
        ),
        markdown_anchor="# LoopX Todo List",
        max_chars={"json": 40_000, "markdown": 1_200},
        max_lines={"json": 1_100, "markdown": 24},
    ),
    CliOutputModeVariantSpec(
        variant_id="todo_list_thin",
        parent_surface_id="todo_list",
        command="todo list --thin",
        output_formats=("json", "markdown"),
        semantic_json_keys=(
            "todos",
            "agent_todos",
            "thin",
            "matched_todo_count",
            "returned_todo_count",
            "omitted_todo_count",
            "todo_list_field_projection",
        ),
        markdown_anchor="# LoopX Todo List",
        max_chars={"json": 20_000, "markdown": 6_000},
        max_lines={"json": 600, "markdown": 60},
    ),
)


CLI_OUTPUT_MODE_VARIANT_BY_ID = {
    spec.variant_id: spec for spec in CLI_OUTPUT_MODE_VARIANT_SPECS
}


# The public help surface is the authority for recurring agent-facing commands.
# Every command in its start/daily groups, plus heartbeat-prompt, must resolve
# here. Tests fail closed when help adds a command without a budget or a named
# cold-path exception.
CLI_OUTPUT_COMMAND_CLASSIFICATIONS: tuple[CliOutputCommandClassification, ...] = (
    CliOutputCommandClassification(
        command_id="doctor",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="installation diagnostic invoked on demand, not a recurring decision payload",
    ),
    CliOutputCommandClassification(
        command_id="first-run-report",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="explicit local feedback receipt invoked on demand and sends nothing",
    ),
    CliOutputCommandClassification(
        command_id="usage-ping",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="operator-invoked consent toggle and payload preview; never sends from this command",
    ),
    CliOutputCommandClassification(
        command_id="slash-commands",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="installation and discovery command invoked explicitly",
    ),
    CliOutputCommandClassification(
        command_id="preset",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="configuration discovery command invoked explicitly",
    ),
    CliOutputCommandClassification(
        command_id="machine-config",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="explicit machine-policy inspection and revision-bound configuration, not a recurring decision payload",
    ),
    CliOutputCommandClassification(
        command_id="ready-score",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="operator readiness diagnostic invoked explicitly",
    ),
    CliOutputCommandClassification(
        command_id="start-goal",
        qualification="qualified_default",
        surface_id="start_goal_guided",
        rationale="guided goal-start packet is a recurring agent bootstrap surface",
    ),
    CliOutputCommandClassification(
        command_id="agent-onboard",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="one-time host activation packet with its own activation smokes",
    ),
    CliOutputCommandClassification(
        command_id="bootstrap-command-pack",
        qualification="qualified_default",
        surface_id="bootstrap_command_pack",
        rationale="lower-level host handoff packet is consumed by agent bootstrap",
    ),
    CliOutputCommandClassification(
        command_id="status",
        qualification="qualified_default",
        surface_id="status",
        rationale="first-screen agent and operator status read",
    ),
    CliOutputCommandClassification(
        command_id="chat",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="interactive local workspace launched explicitly by the operator",
    ),
    CliOutputCommandClassification(
        command_id="dashboard",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="interactive local dashboard launched explicitly by the operator",
    ),
    CliOutputCommandClassification(
        command_id="diagnose",
        qualification="qualified_default",
        surface_id="diagnose",
        rationale="agent self-repair packet",
    ),
    CliOutputCommandClassification(
        command_id="review-packet",
        qualification="qualified_default",
        surface_id="review_packet_handoff_only",
        rationale="agent handoff and review packet",
    ),
    CliOutputCommandClassification(
        command_id="goal-channel",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="explicit provider setup, sync, doctor, and gate-notification command family",
    ),
    CliOutputCommandClassification(
        command_id="goal-portfolio",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="explicit manager-scoped evidence read with caller-provided goal bounds",
    ),
    CliOutputCommandClassification(
        command_id="manager-inbox",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="explicit manager-context read and acknowledgement command family",
    ),
    CliOutputCommandClassification(
        command_id="goal-lifecycle",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="explicit operator preview, stop, and resume command family",
    ),
    CliOutputCommandClassification(
        command_id="evidence-log",
        qualification="qualified_default",
        surface_id="evidence_log_thin",
        rationale="bounded evidence read before replan or handoff",
    ),
    CliOutputCommandClassification(
        command_id="agent-capabilities",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="explicit inspection or correction of one registered Agent's runtime observations",
    ),
    CliOutputCommandClassification(
        command_id="handoff",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="explicit cross-agent Todo handoff preparation or adoption",
    ),
    CliOutputCommandClassification(
        command_id="todo",
        qualification="qualified_default",
        surface_id="todo_list",
        rationale="agent work-queue read path",
    ),
    CliOutputCommandClassification(
        command_id="task-lease",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="explicit per-todo lease lifecycle command family",
    ),
    CliOutputCommandClassification(
        command_id="coordination-shadow",
        qualification="explicit_cold_path_exception",
        surface_id=None,
        rationale="explicit Stage 2C administrative inspection and bootstrap command family",
    ),
    CliOutputCommandClassification(
        command_id="quota",
        qualification="qualified_default",
        surface_id="quota_should_run",
        rationale="recurring next-turn execution guard",
    ),
    CliOutputCommandClassification(
        command_id="history",
        qualification="qualified_default",
        surface_id="history_limited",
        rationale="bounded run-history read path",
    ),
    CliOutputCommandClassification(
        command_id="heartbeat-prompt",
        qualification="qualified_default",
        surface_id="heartbeat_prompt_thin",
        rationale="recurring host automation body",
    ),
)


CLI_OUTPUT_COMMAND_CLASSIFICATION_BY_ID = {
    spec.command_id: spec for spec in CLI_OUTPUT_COMMAND_CLASSIFICATIONS
}


def measure_cli_output(
    text: str,
    *,
    output_format: OutputFormat,
) -> dict[str, Any]:
    payload: dict[str, Any] | None = None
    compact_payload_chars: int | None = None
    json_shape_paths: list[str] = []
    action_signature_sha256: str | None = None
    if output_format == "json":
        decoded = json.loads(text)
        if not isinstance(decoded, dict):
            raise ValueError("agent-facing CLI JSON output must be an object")
        payload = decoded
        compact_payload_chars = len(
            json.dumps(
                decoded,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        json_shape_paths = collect_json_shape_paths(decoded)
        action_signature_sha256 = action_signature_semantic_sha256(decoded)
    return {
        "schema_version": CLI_OUTPUT_MEASUREMENT_SCHEMA_VERSION,
        "format": output_format,
        "chars": len(text),
        "utf8_bytes": len(text.encode("utf-8")),
        "lines": len(text.splitlines()),
        "json_parseable": payload is not None,
        "top_level_keys": len(payload or {}),
        "compact_payload_chars": compact_payload_chars,
        "pretty_print_overhead_chars": (
            len(text) - compact_payload_chars
            if compact_payload_chars is not None
            else None
        ),
        "json_shape_paths": json_shape_paths,
        "markdown_headings": markdown_headings(text),
        "action_signature_sha256": action_signature_sha256,
        "payload": payload,
    }


def assert_cli_output_baseline(
    spec: CliOutputBudgetSpec,
    *,
    scenario: str,
    output_format: OutputFormat,
    text: str,
    measurement: dict[str, Any],
) -> None:
    max_chars = spec.max_chars[scenario][output_format]
    max_lines = spec.max_lines[scenario][output_format]
    if int(measurement["chars"]) > max_chars:
        raise AssertionError(
            f"{spec.surface_id}/{scenario}/{output_format} emitted "
            f"{measurement['chars']} chars; baseline ceiling is {max_chars}"
        )
    if int(measurement["lines"]) > max_lines:
        raise AssertionError(
            f"{spec.surface_id}/{scenario}/{output_format} emitted "
            f"{measurement['lines']} lines; baseline ceiling is {max_lines}"
        )
    if output_format == "markdown" and spec.markdown_anchor not in text:
        raise AssertionError(
            f"{spec.surface_id} markdown lost semantic anchor {spec.markdown_anchor!r}"
        )
    if output_format == "json":
        payload = measurement.get("payload")
        if not isinstance(payload, dict):
            raise AssertionError(f"{spec.surface_id} did not emit a JSON object")
        missing = [key for key in spec.semantic_json_keys if key not in payload]
        if missing:
            raise AssertionError(
                f"{spec.surface_id} JSON lost semantic key(s): {', '.join(missing)}"
            )


def assert_cli_output_mode_variant(
    spec: CliOutputModeVariantSpec,
    *,
    output_format: OutputFormat,
    text: str,
    measurement: dict[str, Any],
) -> None:
    if output_format not in spec.output_formats:
        raise AssertionError(
            f"{spec.variant_id} does not declare {output_format} qualification"
        )
    if int(measurement["chars"]) > spec.max_chars[output_format]:
        raise AssertionError(
            f"{spec.variant_id}/{output_format} emitted {measurement['chars']} chars; "
            f"variant ceiling is {spec.max_chars[output_format]}"
        )
    if int(measurement["lines"]) > spec.max_lines[output_format]:
        raise AssertionError(
            f"{spec.variant_id}/{output_format} emitted {measurement['lines']} lines; "
            f"variant ceiling is {spec.max_lines[output_format]}"
        )
    if output_format == "markdown":
        if spec.markdown_anchor not in text:
            raise AssertionError(
                f"{spec.variant_id} markdown lost semantic anchor "
                f"{spec.markdown_anchor!r}"
            )
        return
    payload = measurement.get("payload")
    if not isinstance(payload, dict):
        raise AssertionError(f"{spec.variant_id} did not emit a JSON object")
    missing = [key for key in spec.semantic_json_keys if key not in payload]
    if missing:
        raise AssertionError(
            f"{spec.variant_id} JSON lost semantic key(s): {', '.join(missing)}"
        )


def public_manifest() -> dict[str, Any]:
    return {
        "schema_version": CLI_OUTPUT_BUDGET_SCHEMA_VERSION,
        "surface_count": len(CLI_OUTPUT_BUDGET_SPECS),
        "mode_variant_count": len(CLI_OUTPUT_MODE_VARIANT_SPECS),
        "command_classification_count": len(CLI_OUTPUT_COMMAND_CLASSIFICATIONS),
        "surfaces": [
            {
                "surface_id": spec.surface_id,
                "command": spec.command,
                "owner": spec.owner,
                "consumer_action": spec.consumer_action,
                "qualification_policy": spec.qualification_policy,
                "cold_path": spec.cold_path,
                "formats": ["json", "markdown"],
                "scenarios": sorted(spec.max_chars),
                "scale_axis": spec.scale_axis,
            }
            for spec in CLI_OUTPUT_BUDGET_SPECS
        ],
        "mode_variants": [
            {
                "variant_id": spec.variant_id,
                "parent_surface_id": spec.parent_surface_id,
                "command": spec.command,
                "qualification_policy": "explicit_opt_in_cold_path",
                "formats": list(spec.output_formats),
            }
            for spec in CLI_OUTPUT_MODE_VARIANT_SPECS
        ],
        "command_classifications": [
            {
                "command_id": spec.command_id,
                "qualification": spec.qualification,
                "surface_id": spec.surface_id,
                "rationale": spec.rationale,
            }
            for spec in CLI_OUTPUT_COMMAND_CLASSIFICATIONS
        ],
    }
