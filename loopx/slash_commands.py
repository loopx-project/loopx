from __future__ import annotations

from typing import Any

from .presentation.markdown import (
    markdown_code,
    markdown_table_row,
    markdown_table_separator,
)

SCHEMA_VERSION = "loopx_slash_command_catalog_v0"


def _command(
    *,
    command: str,
    scope: str,
    intent: str,
    mutation_policy: str,
    cli_reference: str,
    legacy_aliases: list[str] | None = None,
    implementation_status: str = "available",
    agent_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "command": command,
        "scope": scope,
        "intent": intent,
        "mutation_policy": mutation_policy,
        "cli_reference": cli_reference,
        "implementation_status": implementation_status,
    }
    if legacy_aliases:
        item["legacy_aliases"] = legacy_aliases
    if agent_contract:
        item["agent_contract"] = agent_contract
    return item


def build_slash_command_catalog(
    *,
    cli_bin: str = "loopx",
    include_legacy_aliases: bool = True,
) -> dict[str, Any]:
    legacy_summary = ["/loop-global-summary"] if include_legacy_aliases else []
    legacy_gates = ["/loop-global-gates"] if include_legacy_aliases else []
    legacy_todos = ["/loop-global-todos"] if include_legacy_aliases else []
    legacy_risks = ["/loop-global-risks"] if include_legacy_aliases else []
    commands = [
        _command(
            command="/loopx",
            scope="project",
            intent="Inspect or preview this project's LoopX connection, status, gates, and next safe action.",
            mutation_policy="read_first; ask before bootstrap/connect writes",
            cli_reference=f"{cli_bin} bootstrap-command-pack --project .",
        ),
        _command(
            command="/loopx <goal text>",
            scope="project",
            intent="Start a concrete project goal: plan ordered todos, write them in priority order, activate the host loop when needed, then enter the quota-gated loop.",
            mutation_policy="explicit goal-start intent may write project-local LoopX state after planning and must activate/report the host loop",
            cli_reference=f"{cli_bin} start-goal --guided --project . --goal-text '<goal text>'",
            agent_contract={
                "schema_version": "loopx_goal_start_agent_contract_v0",
                "planner_required_before_todo_write": True,
                "todo_write_order_defines_same_priority_rank": True,
                "host_loop_activation_required_after_todo_writeback": True,
                "host_loop_activation_catalog": f"{cli_bin} agent-onboard --list-agent-types",
                "host_loop_activation_by_agent_type": {
                    "codex-app": "create/update Codex App heartbeat automation from heartbeat-prompt task_body",
                    "trae_app": "create/update Trae App heartbeat automation from heartbeat-prompt task_body",
                    "codex-cli": "set visible Codex CLI TUI `/goal <task_body>`",
                    "claude-code": "arm LoopX with `/loopx <task>`, then run native `/loop`",
                    "opencode": "call `loopx_goal_activate`",
                    "pi": "call `loopx_goal_activate`",
                    "gemini-cli": "drive the loop from the agent's own turns; enter every turn through quota should-run",
                    "cursor-agent": "drive the loop from the agent's own turns; enter every turn through quota should-run",
                    "zcode": "drive the loop from the agent's turns; enter through quota should-run",
                    "agy": "native /goal + schedule wakes; quota entry advisory",
                    "antigravity-app": "schedule timer + sidecar heartbeat",
                    "kiro-cli": "native /goal loop; quota entry advisory",
                    "manual": "wire an external scheduler or run quota/status manually",
                    "other-agent": "use the custom host loop driver declared by `loopx agent-onboard`",
                },
                "setup_complete_requires": (
                    "registry/state plus ordered todos plus host_loop_activation current, "
                    "or a concrete host-tool gate; registry/quota identity alone is insufficient"
                ),
                "low_cost_recheck_policy": (
                    "Run `agent-onboard` only when activation is missing, unknown, stale, "
                    "or the agent type changed; normal ticks read quota/status/state directly."
                ),
            },
        ),
        _command(
            command="/loopx-global-summary",
            scope="global",
            intent="Read a progress digest across visible LoopX goals.",
            mutation_policy="read_only",
            cli_reference=f"{cli_bin} global-summary",
            legacy_aliases=legacy_summary,
        ),
        _command(
            command="/loopx-global-gates",
            scope="global",
            intent="List open user/controller gates and what each blocks.",
            mutation_policy="read_only",
            cli_reference=f"{cli_bin} global-gates",
            legacy_aliases=legacy_gates,
        ),
        _command(
            command="/loopx-global-todos",
            scope="global",
            intent="List top runnable, blocked, deferred-ready, and review todos across visible goals.",
            mutation_policy="read_only",
            cli_reference=f"{cli_bin} global-todos",
            legacy_aliases=legacy_todos,
        ),
        _command(
            command="/loopx-global-risks",
            scope="global",
            intent="Show stale runs, boundary risks, failing checks, and rollback candidates.",
            mutation_policy="read_only",
            cli_reference=f"{cli_bin} global-risks",
            legacy_aliases=legacy_risks,
        ),
        _command(
            command="/loopx-pr-review",
            scope="repo",
            intent="Run pr-review; execute actionable rows and read back null-action rows.",
            mutation_policy="read_only; does not comment, approve, merge, or spend quota",
            cli_reference=f"{cli_bin} pr-review [--repo owner/repo] [--state open|merged|all] [--since ISO]",
            agent_contract={
                "schema_version": "slash_command_agent_contract_v0",
                "must_run_cli_first": True,
                "primary_cli": f"{cli_bin} pr-review [--repo owner/repo] [--state open|merged|all] [--since ISO]",
                "visibility_cli": (
                    f"{cli_bin} --format json pr-review [--repo owner/repo] "
                    "[--state open|merged|all] [--since ISO]"
                ),
                "slash_prefix_dominates_intent": True,
                "stats_only_requires_explicit_opt_out": True,
                "authoritative_fields": [
                    "agent_response_contract",
                    "agent_response_contract.explanation_depth_contract",
                    "review_groups.unmerged",
                    "review_groups.merged",
                    "agent_response_contract.required_packet_fields_to_preserve",
                    "agent_response_contract.required_final_sections",
                ],
                "required_packet_fields_to_preserve": [
                    "agent_response_contract",
                    "result_completeness",
                    "review_groups",
                    "pull_requests",
                ],
                "final_answer_contract": {
                    "table_only_response_allowed": False,
                    "stats_only_opt_out_examples": [
                        "只统计",
                        "只列出",
                        "stats only",
                        "list only",
                        "不要 review",
                        "不用分析",
                    ],
                    "required_sections": [
                        "动机",
                        "改动思路",
                        "具体改动",
                        "对主干的风险",
                        "我的整体评价",
                    ],
                    "evidence_before_filling": "Execute each selected review_plan before filling the sections.",
                    "section_length_hint": "Use review_template ranges to render verified evidence without a competing host checklist.",
                    "reader_profile": "A technically curious reader who may not know the PR or subsystem.",
                    "freshness_policy": "Record the remote head before review, recheck it before the verdict, and restart if it changed.",
                },
                "manual_gh_policy": (
                    "Use gh only after the CLI packet selects a PR; do not reconstruct "
                    "the review window or state grouping from ad hoc gh calls."
                ),
                "json_projection_policy": (
                    "Do not pipe the first JSON packet to a summary-only projection. "
                    "Keep the response contract, review groups, plans, templates, and evidence commands "
                    "visible before planning the final answer."
                ),
            },
        ),
    ]
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "canonical_prefix": "/loopx",
        "commands": commands,
        "onboarding": {
            "tell_new_users": True,
            "suggested_user_note": render_onboarding_slash_command_note(commands, cli_bin=cli_bin),
        },
        "help": {
            "cli_command": f"{cli_bin} slash-commands",
            "legacy_alias_policy": "legacy /loop-global-* forms may be accepted during migration, but help should show /loopx-global-* as canonical",
        },
    }


def render_onboarding_slash_command_note(commands: list[dict[str, Any]], *, cli_bin: str = "loopx") -> str:
    command_by_name = {str(item.get("command")): item for item in commands}
    project = command_by_name.get("/loopx", {})
    goal = command_by_name.get("/loopx <goal text>", {})
    return "\n".join(
        [
            "LoopX command surface is available. Useful commands:",
            f"- `/loopx`: {project.get('intent', 'inspect this project')}",
            f"- `/loopx <goal text>`: {goal.get('intent', 'start a concrete project goal')}",
            f"  New hosts should choose an exact agent type with `{cli_bin} agent-onboard --list-agent-types`; do not pass ambiguous values such as `codex`.",
            "- `/loopx-global-summary`: read the global progress digest.",
            "- `/loopx-global-gates`, `/loopx-global-todos`, `/loopx-global-risks`: inspect manager-level gates, work, and risks.",
            "- `/loopx-pr-review`: run `loopx pr-review` first, then review its unmerged and merged PR groups one by one.",
            f"CLI help: `{cli_bin} slash-commands`.",
        ]
    )


def render_slash_command_catalog_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return "# LoopX Slash Commands\n\n- ok: `False`"
    lines = [
        "# LoopX Slash Commands",
        "",
        str(payload.get("onboarding", {}).get("suggested_user_note") or ""),
        "",
        markdown_table_row(["Command", "Scope", "Intent", "Mutation policy", "CLI reference"]),
        markdown_table_separator(5),
    ]
    for item in payload.get("commands") or []:
        if not isinstance(item, dict):
            continue
        legacy = item.get("legacy_aliases") or []
        intent = str(item.get("intent") or "")
        if legacy:
            intent += " Legacy aliases: " + ", ".join(f"`{alias}`" for alias in legacy) + "."
        agent_contract = item.get("agent_contract") if isinstance(item.get("agent_contract"), dict) else {}
        if agent_contract.get("must_run_cli_first"):
            intent += " Agent contract: run the CLI reference first; do not rebuild the queue manually."
        if agent_contract.get("host_loop_activation_required_after_todo_writeback"):
            intent += " Agent contract: after todo writeback, activate the host loop or report the concrete host-tool gate."
        lines.append(
            markdown_table_row(
                [
                    markdown_code(item.get("command")),
                    item.get("scope"),
                    intent,
                    item.get("mutation_policy"),
                    markdown_code(item.get("cli_reference")),
                ]
            )
        )
    lines.extend(
        [
            "",
            "Global manager commands are read-only by default. Project-local `/loopx <goal text>` is the only slash form here that can authorize project-local state writes, and it must plan before writing todos.",
        ]
    )
    return "\n".join(lines)
