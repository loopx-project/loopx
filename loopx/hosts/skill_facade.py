"""Host-loop activation for CLI hosts LoopX reaches through a skill facade.

These hosts share one seam: LoopX cannot mutate their loop surface from the
CLI, so the installed `/loopx` skill facade is the entry and the host's own
turn loop is the driver. The differences between them are small and factual —
which skills root the installer writes to, whether the host owns a native goal
or wake primitive, and how honest the quota claim can be — which is why they
live together here instead of each growing `host_loop_activation.py`, whose
module budget is already spent on the agent-type catalog and identity rules.

Each host's facts stay in its own `*_goal_mode` package; this module only
binds them to the shared packet shape.
"""

from __future__ import annotations

from typing import Any

from ..agy_goal_mode import agy_activation_extras
from ..kiro_cli_goal_mode import (
    KIRO_CLI_INSTALL_SURFACE,
    SKILLS_ROOT_LABEL as KIRO_CLI_SKILLS_ROOT_LABEL,
    kiro_cli_activation_extras,
)
from ..zcode_goal_mode import (
    SKILLS_ROOT_LABEL as ZCODE_SKILLS_ROOT_LABEL,
    ZCODE_INSTALL_SURFACE,
)


def skill_facade_cli_activation(
    commands: dict[str, str],
    cli_bin: str,
    *,
    host_label: str,
    host_surface: str,
    install_surface: str,
    skills_root: str,
    extra_host_mutation: dict[str, Any] | None = None,
    extra_activation_steps: list[str] | None = None,
    host_scheduler_note: str | None = None,
    activation_method: str = "run_agent_cli_loop_gated_by_quota",
) -> dict[str, Any]:
    """Activation for a CLI host that LoopX reaches through a skill facade.

    For skill-facade CLI hosts where no direct host-native loop binding is
    integrated, the loop driver is the agent's own turn loop and LoopX gates it
    by requiring every continuation to enter through quota should-run. A host
    that does ship a native in-session scheduler passes ``host_scheduler_note``
    so the packet states that primitive instead of the default no-scheduler
    sentence. A host that also owns a native goal primitive overrides
    ``activation_method`` to name the goal binding. The weaker facade boundary
    remains explicit rather than claiming autonomous heartbeat support the host
    cannot deliver.
    """
    return {
        "host_surface": host_surface,
        "entry_command_hint": f"the LoopX skill installed in {skills_root}",
        "activation_method": activation_method,
        "activation_input_command": commands["heartbeat_prompt_json"],
        "setup_command": (
            f"{cli_bin} slash-commands --install --surface {install_surface}"
        ),
        "host_mutation": {
            "owner": f"{host_label} session",
            "host_loop_primitive": None,
            "cli_can_mutate_directly": False,
            "loop_driver": "agent_cli_turn_loop",
            "missing_host_tool_gate": (
                f"{host_label} exposes no goal or automation primitive for LoopX to "
                "bind. If the session cannot keep entering through quota should-run, "
                "show the exact heartbeat-prompt command for the user to run and do "
                "not claim autonomous heartbeat support."
            ),
            **(extra_host_mutation or {}),
        },
        "activation_steps": [
            f"Install or refresh the LoopX {host_label} surface when needed.",
            "Run the heartbeat-prompt JSON command after project state and todos are written.",
            "Read task_body from the JSON payload and carry it as the session objective.",
            *(extra_activation_steps or []),
            "Start every following turn with quota should-run and stop when it says stop; "
            + (
                host_scheduler_note
                or "there is no host scheduler to fall back on."
            ),
        ],
        "success_criteria": [
            f"The {host_label} session has the LoopX skill facade installed and the "
            "generated task_body as its objective.",
            "Each continuation enters through LoopX quota/status/state, and a stop "
            "decision ends the session loop instead of free-running.",
        ],
    }


def gemini_cli_activation(commands: dict[str, str], cli_bin: str) -> dict[str, Any]:
    return skill_facade_cli_activation(
        commands,
        cli_bin,
        host_label="Gemini CLI",
        host_surface="gemini_cli_agent_loop",
        install_surface="gemini",
        skills_root="GEMINI_HOME/skills",
    )


def cursor_agent_activation(commands: dict[str, str], cli_bin: str) -> dict[str, Any]:
    return skill_facade_cli_activation(
        commands,
        cli_bin,
        host_label="Cursor Agent CLI",
        host_surface="cursor_agent_loop",
        install_surface="cursor",
        skills_root="CURSOR_HOME/skills",
        extra_host_mutation={
            # The MCP server is how a cursor-agent session reads LoopX state
            # without shelling out; the loop is still the agent's own turns.
            "host_mcp_server": "loopx",
            "host_mcp_config": "CURSOR_HOME/mcp.json",
        },
        extra_activation_steps=[
            "Confirm the `loopx` MCP server is enabled in this session "
            "(`cursor-agent mcp`); it is registered by the surface installer.",
        ],
    )


def zcode_activation(commands: dict[str, str], cli_bin: str) -> dict[str, Any]:
    return skill_facade_cli_activation(
        commands,
        cli_bin,
        host_label="ZCode",
        host_surface="zcode_agent_loop",
        install_surface=ZCODE_INSTALL_SURFACE,
        skills_root=ZCODE_SKILLS_ROOT_LABEL,
        extra_host_mutation={
            "missing_host_tool_gate": (
                "The default ZCode entry remains the LoopX skill facade. Explicit "
                "zcode-goal bind selects a separate managed native CLI session; "
                "it does not attach this conversation or enable Automations. "
                "If the skill session cannot keep entering through quota "
                "should-run, show the exact heartbeat-prompt command for the user "
                "to run and do not claim autonomous heartbeat support."
            ),
        },
    )


def agy_cli_activation(commands: dict[str, str], cli_bin: str) -> dict[str, Any]:
    return skill_facade_cli_activation(
        commands,
        cli_bin,
        host_label="Antigravity CLI",
        host_surface="agy_agent_loop",
        install_surface="agy",
        skills_root="~/.gemini/antigravity-cli/skills",
        **agy_activation_extras(),
    )


def kiro_cli_activation(commands: dict[str, str], cli_bin: str) -> dict[str, Any]:
    return skill_facade_cli_activation(
        commands,
        cli_bin,
        host_label="Kiro CLI",
        host_surface="kiro_cli_agent_loop",
        install_surface=KIRO_CLI_INSTALL_SURFACE,
        skills_root=KIRO_CLI_SKILLS_ROOT_LABEL,
        **kiro_cli_activation_extras(),
    )
