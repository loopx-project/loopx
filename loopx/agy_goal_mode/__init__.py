"""Antigravity CLI (``agy``) & Antigravity App (``antigravity-app``) host-surface constants and helpers.

Supports both:
- Legacy Antigravity CLI (``agy`` 1.x) flat skills in ``~/.gemini/antigravity-cli/skills/<name>.md`` with native ``/goal`` and ``schedule`` tools.
- Antigravity 2.0 / Antigravity App (``antigravity-app``) directory skills in ``~/.gemini/config/skills/<name>/SKILL.md``, in-session ``schedule(DurationSeconds=..., Prompt=...)`` closed-loop timer suspension (for ``waiting_external`` / CI waits), background ``sidecar.json`` periodic heartbeat template, and optional ``mcp_config.json`` + ``hooks.json`` continuation gate registration.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

AGY_INSTALL_SURFACE = "agy"
ANTIGRAVITY_APP_INSTALL_SURFACE = "antigravity-app"
ANTIGRAVITY_APP_AGENT_TYPE = "antigravity-app"

# Every token normalize_agent_type and the slash-commands installer accept for
# the Antigravity CLI surface.
AGY_ACCEPTED_INPUTS: tuple[str, ...] = (
    "agy",
    "antigravity",
    "antigravity-cli",
    "antigravity_cli",
    "antigravity cli",
    "google-antigravity",
    "google antigravity",
)

ANTIGRAVITY_APP_ACCEPTED_INPUTS: tuple[str, ...] = (
    "antigravity-app",
    "antigravity_app",
    "antigravity app",
    "agy-app",
    "agy_app",
    "agy app",
    "antigravity-2",
    "antigravity 2.0",
)

# Native in-session automation primitives agy and Antigravity App ship.
AGY_NATIVE_WAKE_TOOLS: tuple[str, ...] = (
    "schedule",
    "manage_task",
    "invoke_subagent",
    "send_message",
    "manage_inbox",
)

AGY_NATIVE_WAKE_FACTS: tuple[str, ...] = (
    "The built-in `schedule` tool arms a one-shot timer (`DurationSeconds` + "
    "wake `Prompt`; optional `TimerCondition`: `never`, `any`, or a sender ID) "
    "or a recurring cron (`CronExpression` + `Prompt`; optional `MaxIterations`, "
    "`IsDaemon`, `ExactTiming`) that delivers its `Prompt` as a high-priority "
    "wakeup notification when it fires; `manage_task` lists or kills active "
    "schedules.",
    "When a LoopX turn enters `waiting_external` (for example CI checks or PR "
    "review waits), call `schedule(DurationSeconds=<poll_after_seconds>, "
    "Prompt=...)` and end the turn immediately so the host wakes the session "
    "reactively instead of running shell sleep polling loops.",
    "Background `run_command` tasks and `invoke_subagent` runs also resume the "
    "session reactively on completion or message arrival, so the agent does "
    "not need a polling loop inside a turn.",
    "All native wakes live inside the running Antigravity process; for "
    "background periodic heartbeats in Antigravity 2.0 (`antigravity-app`), "
    "configure `sidecar.json` and optional `hooks.json` continuation gates in "
    "`~/.gemini/config`.",
)

# Native goal primitive (built into the agy binary; live-verified on 1.1.18 in
# both the TUI and headless `agy -p`).
AGY_GOAL_COMMAND = "/goal"
AGY_GOAL_COMMAND_DESCRIPTION = (
    "Pursue a goal relentlessly until it is completely finished"
)
AGY_GOAL_COMPLETE_TOKEN = "<!-- GOAL_COMPLETE -->"
AGY_GOAL_CANCELLED_TOKEN = "<!-- GOAL_CANCELLED -->"

DEFAULT_AGY_HOME = ".gemini/antigravity-cli"
DEFAULT_ANTIGRAVITY_CONFIG_HOME = ".gemini/config"
ANTIGRAVITY_SKILLS_ROOT_LABEL = "~/.gemini/config/skills"
ANTIGRAVITY_LEGACY_SKILLS_ROOT_LABEL = "~/.gemini/antigravity-cli/skills"
ANTIGRAVITY_MCP_CONFIG_FILENAME = "mcp_config.json"
ANTIGRAVITY_HOOKS_CONFIG_FILENAME = "hooks.json"
ANTIGRAVITY_SIDECAR_FILENAME = "sidecar.json"
ANTIGRAVITY_MCP_CONFIG_LABEL = "~/.gemini/config/mcp_config.json"
ANTIGRAVITY_HOOKS_CONFIG_LABEL = "~/.gemini/config/hooks.json"
ANTIGRAVITY_SIDECAR_CONFIG_LABEL = "~/.gemini/config/sidecar.json"

ANTIGRAVITY_APP_AGENT_TYPE_CATALOG_ENTRY: dict[str, Any] = {
    "display_name": "Antigravity App",
    "host_loop": (
        "Antigravity App native schedule timer closed loop + optional background "
        "sidecar.json heartbeat and hooks.json continuation gate"
    ),
    "entry": (
        f"the LoopX skill installed in {ANTIGRAVITY_SKILLS_ROOT_LABEL} "
        f"(compat: {ANTIGRAVITY_LEGACY_SKILLS_ROOT_LABEL})"
    ),
    "accepted_inputs": list(ANTIGRAVITY_APP_ACCEPTED_INPUTS),
}


def build_antigravity_sidecar_template(
    *,
    goal_id: str = "<goal-id>",
    agent_id: str | None = None,
    cli_bin: str = "loopx",
    interval_seconds: int = 300,
) -> dict[str, Any]:
    """Build a public-safe Antigravity 2.0 ``sidecar.json`` periodic heartbeat template."""
    quota_args = [
        cli_bin,
        "--format",
        "json",
        "quota",
        "should-run",
        "--goal-id",
        goal_id,
        "--runtime-profile",
        "antigravity_app",
    ]
    if agent_id:
        quota_args.extend(["--agent-id", agent_id])
    heartbeat_args = [
        cli_bin,
        "--format",
        "json",
        "heartbeat-prompt",
        "--goal-id",
        goal_id,
        "--thin",
        "--runtime-profile",
        "antigravity_app",
    ]
    if agent_id:
        heartbeat_args.extend(["--agent-id", agent_id])
    return {
        "schema_version": "loopx_antigravity_sidecar_template_v0",
        "config_path": ANTIGRAVITY_SIDECAR_CONFIG_LABEL,
        "sidecar": {
            "loopx-heartbeat": {
                "description": (
                    f"LoopX periodic quota-gated heartbeat for goal {goal_id}"
                ),
                "interval_seconds": int(interval_seconds),
                "gate_command": shlex.join(quota_args),
                "wake_when_json_field_true": "should_run",
                "prompt_command": shlex.join(heartbeat_args),
                "prompt_json_field": "task_body",
                "suspend_on_waiting_external": {
                    "tool": "schedule",
                    "duration_field": "scheduler_hint.poll_after_seconds",
                    "default_duration_seconds": int(interval_seconds),
                },
            }
        },
    }


def build_antigravity_hooks_template(
    *,
    cli_bin: str = "loopx",
) -> dict[str, Any]:
    """Build a public-safe ``hooks.json`` continuation gate entry for Antigravity 2.0."""
    return {
        "loopx": {
            "schema_version": "loopx_antigravity_hooks_gate_v0",
            "event": "before_continuation",
            "command": cli_bin,
            "args": [
                "--format",
                "json",
                "quota",
                "should-run",
                "--runtime-profile",
                "antigravity_app",
            ],
            "require_json_field_true": "should_run",
            "on_waiting_external": "suspend_via_schedule_timer",
        }
    }


def agy_activation_extras() -> dict[str, Any]:
    """Keyword overrides for ``agy_cli_activation``'s facade call.

    Keeps the agy host facts (goal command, tokens, wake tools, gate text,
    activation steps) in the agy package instead of growing
    ``host_loop_activation.py`` past its module metric budget.
    """
    return {
        "activation_method": "bind_native_goal_with_advisory_quota_entry",
        "extra_host_mutation": {
            "host_loop_primitive": "agy-/goal-and-schedule-tool",
            "loop_driver": "agy_native_goal_loop_with_schedule_wakes",
            "quota_gate_enforcement": "advisory_only",
            "native_goal_command": AGY_GOAL_COMMAND,
            "goal_complete_token": AGY_GOAL_COMPLETE_TOKEN,
            "goal_cancelled_token": AGY_GOAL_CANCELLED_TOKEN,
            "native_wake_tools": list(AGY_NATIVE_WAKE_TOOLS),
            "native_wake_facts": list(AGY_NATIVE_WAKE_FACTS),
            "skills_roots": [
                ANTIGRAVITY_SKILLS_ROOT_LABEL,
                ANTIGRAVITY_LEGACY_SKILLS_ROOT_LABEL,
            ],
            "missing_host_tool_gate": (
                "agy's /goal loop and native wakes fire only while the CLI "
                "session stays alive (there is no background daemon), and "
                "agy exposes no hook to intercept `/goal`'s forced-"
                "continuation audit, so `quota should-run` is advisory "
                "guidance rather than a host-enforced gate. If the session "
                "cannot keep entering through `quota should-run`, show the "
                "exact heartbeat-prompt command for the user to run and do "
                "not claim unattended heartbeat support."
            ),
        },
        "extra_activation_steps": [
            "Bind the objective to agy's native goal loop by running "
            "`/goal <task_body>` — agy's forced continuation audits the work "
            "at the end of each turn and keeps the agent going until every "
            f"aspect is verified and `{AGY_GOAL_COMPLETE_TOKEN}` is emitted "
            f"(or `{AGY_GOAL_CANCELLED_TOKEN}` to abort).",
            "At the start of every turn, wake and `/goal` audit-continuation, "
            "run `quota should-run` first; when it says stop, emit "
            f"`{AGY_GOAL_CANCELLED_TOKEN}` (or `{AGY_GOAL_COMPLETE_TOKEN}` "
            "when the LoopX goal is genuinely complete) so the native goal "
            "loop terminates instead of free-running past the quota.",
            "When `quota should-run` allows continuation and more work or a "
            "deferred check remains (such as `waiting_external` or CI checks), "
            "arm the next wakeup in-session with agy's built-in `schedule` tool "
            "(`DurationSeconds` + wake `Prompt`, or `CronExpression` with "
            "`MaxIterations` for bounded recurring checks) instead of blind "
            "polling; cancel obsolete timers via `manage_task` before stopping.",
        ],
        "host_scheduler_note": (
            "use the built-in `schedule` tool (`DurationSeconds` or "
            "`CronExpression` with `MaxIterations`) for in-session wakes, and "
            f"emit `{AGY_GOAL_COMPLETE_TOKEN}` / `{AGY_GOAL_CANCELLED_TOKEN}` "
            "to end the native `/goal` loop (neither survives after the agy "
            "process exits)."
        ),
    }


def antigravity_app_activation_extras(
    *,
    goal_id: str = "<goal-id>",
    agent_id: str | None = None,
    cli_bin: str = "loopx",
) -> dict[str, Any]:
    """Keyword overrides for ``antigravity_app_activation``'s facade call."""
    sidecar_template = build_antigravity_sidecar_template(
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
    )
    hooks_template = build_antigravity_hooks_template(cli_bin=cli_bin)
    wake_prompt = (
        f"Run `{cli_bin} --format json quota should-run --goal-id {goal_id}"
        + (f" --agent-id {agent_id}" if agent_id else "")
        + " --runtime-profile antigravity_app` and continue the active LoopX Todo when allowed."
    )
    return {
        "activation_method": "bind_antigravity_schedule_loop_and_sidecar",
        "extra_host_mutation": {
            "host_loop_primitive": "antigravity-schedule-tool-and-sidecar",
            "loop_driver": "antigravity_app_schedule_timer_and_sidecar_loop",
            "quota_gate_enforcement": "hooks_or_advisory_quota_entry",
            "native_goal_command": AGY_GOAL_COMMAND,
            "goal_complete_token": AGY_GOAL_COMPLETE_TOKEN,
            "goal_cancelled_token": AGY_GOAL_CANCELLED_TOKEN,
            "native_wake_tools": list(AGY_NATIVE_WAKE_TOOLS),
            "native_wake_facts": list(AGY_NATIVE_WAKE_FACTS),
            "skills_roots": [
                ANTIGRAVITY_SKILLS_ROOT_LABEL,
                ANTIGRAVITY_LEGACY_SKILLS_ROOT_LABEL,
            ],
            "host_mcp_server": "loopx",
            "host_mcp_config": ANTIGRAVITY_MCP_CONFIG_LABEL,
            "host_hooks_config": ANTIGRAVITY_HOOKS_CONFIG_LABEL,
            "host_sidecar_config": ANTIGRAVITY_SIDECAR_CONFIG_LABEL,
            "in_session_schedule_call": {
                "tool": "schedule",
                "arguments": {
                    "DurationSeconds": 180,
                    "Prompt": wake_prompt,
                    "TimerCondition": "any",
                },
                "when_to_use": (
                    "Call `schedule(DurationSeconds=..., Prompt=...)` and end the turn "
                    "whenever `waiting_external`, CI checks, or PR review gates block "
                    "immediate completion; never use shell sleep loops."
                ),
            },
            "sidecar_template": sidecar_template,
            "hooks_template": hooks_template,
            "missing_host_tool_gate": (
                "Antigravity App in-session `schedule(DurationSeconds=...)` timers fire "
                "while the app session is active; for unattended background ticks, "
                f"configure `sidecar.json` in `{ANTIGRAVITY_SIDECAR_CONFIG_LABEL}` "
                f"and optional `hooks.json` in `{ANTIGRAVITY_HOOKS_CONFIG_LABEL}`."
            ),
        },
        "extra_activation_steps": [
            f"Install or refresh the Antigravity skill and optional MCP/hooks surface (`{cli_bin} slash-commands --install --surface antigravity-app --with-agy-mcp --with-agy-hooks` and `{cli_bin} workflow-skills --install --host-surface antigravity-app`).",
            "When a turn enters `waiting_external` or waits on CI/review checks, suspend directly with the native `schedule(DurationSeconds=<poll_after_seconds>, Prompt=...)` tool and end the turn instead of polling in a shell loop.",
            f"For background periodic heartbeats, apply the generated `sidecar_template` in `{ANTIGRAVITY_SIDECAR_CONFIG_LABEL}` so each tick gates on `quota should-run` before waking the agent.",
        ],
        "host_scheduler_note": (
            "use the native `schedule(DurationSeconds=..., Prompt=...)` tool to suspend "
            "and wake on `waiting_external` / CI waits, or configure `sidecar.json` for "
            "background periodic heartbeats."
        ),
    }


def agy_home(value: str | None = None) -> Path:
    """Resolve the legacy Antigravity CLI user home directory (``~/.gemini/antigravity-cli``).

    Honours an explicit ``--agy-home`` override or ``AGY_CLI_HOME`` environment
    variable while defaulting to ``~/.gemini/antigravity-cli`` for flat
    ``skills/<name>.md`` compatibility.
    """
    raw = value or os.environ.get("AGY_CLI_HOME")
    if raw:
        return Path(raw).expanduser()
    return (Path.home() / DEFAULT_AGY_HOME).expanduser()


def antigravity_config_home(value: str | None = None) -> Path:
    """Resolve the Antigravity 2.0 / Antigravity App config home (``~/.gemini/config``).

    Honours an explicit ``--agy-home`` override or ``AGY_HOME`` / ``ANTIGRAVITY_HOME``
    environment variables, defaulting to ``~/.gemini/config`` where
    ``skills/<name>/SKILL.md``, ``mcp_config.json``, ``hooks.json``, and
    ``sidecar.json`` live.
    """
    raw = (
        value
        or os.environ.get("AGY_HOME")
        or os.environ.get("ANTIGRAVITY_HOME")
        or str(Path.home() / DEFAULT_ANTIGRAVITY_CONFIG_HOME)
    )
    return Path(raw).expanduser()


def antigravity_skill_roots(value: str | None = None) -> tuple[Path, ...]:
    """Return Antigravity skill roots in discovery order (2.0 config first, then legacy CLI)."""
    if value:
        base = Path(value).expanduser()
        return (base / "skills",)
    return (
        antigravity_config_home() / "skills",
        agy_home() / "skills",
    )
