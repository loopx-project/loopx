from __future__ import annotations

import os
from pathlib import Path
from typing import Any

KIRO_CLI_INSTALL_SURFACE = "kiro-cli"
KIRO_CLI_AGENT_TYPE = "kiro-cli"
DEFAULT_KIRO_CLI_HOME = ".kiro"
# The host's own override for its global root. Kiro CLI relocates global
# agents, prompts, skills, settings, and sessions when this is set, so it is
# authoritative over the default for anything LoopX installs or removes.
KIRO_CLI_HOME_ENV = "KIRO_HOME"
SKILLS_SUBDIR = "skills"
SKILLS_ROOT_LABEL = "KIRO_HOME/skills"
KIRO_CLI_BIN = "kiro-cli"
KIRO_CLI_ACCEPTED_INPUTS = (
    "kiro-cli",
    "kiro_cli",
    "kiro cli",
    "kirocli",
    "kiro",
    "kiro-cli-tui",
    "kiro tui",
)

# `kiro-cli acp` starts the host as an Agent Client Protocol agent over stdio.
# Probed on 2.21.1: it answers `initialize` with protocolVersion 1 and
# `loadSession: true`, and `session/new` returns a session id — exactly the
# shape `loopx.chat_acp.ACPStdioAdapter` already speaks, so LoopX Chat and the
# dashboard reach Kiro CLI through that adapter instead of a second transport.
#
# The argv carries no `--trust-all-tools`: LoopX Chat answers every ACP
# `session/request_permission` with `cancelled`, so a tool that needs approval
# is refused rather than auto-approved. Passing the trust flag here would move
# that decision out of the owner's hands.
KIRO_CLI_CHAT_AGENT_ID = "kiro-cli"
KIRO_CLI_CHAT_DISPLAY_NAME = "Kiro CLI"
KIRO_CLI_CHAT_ACP_SUBCOMMAND = "acp"
KIRO_CLI_CHAT_ADAPTER_KIND = "acp"

# Native goal primitive built into the kiro-cli binary. Only what the host
# itself contracts is recorded here.
#
# Two authoritative sources agree, and both were checked rather than copied
# from an earlier draft:
#   * the published command reference documents `/goal <description>`,
#     `/goal --max <N> <description>` and `/goal clear`, and states that the
#     agent derives its acceptance criteria from the goal statement;
#   * the installed host advertises its own command registry over ACP
#     (`_kiro.dev/commands/available`), where `/goal` carries exactly one
#     subcommand, `clear`.
#
# An earlier revision of this module also projected `--validate`, `--agent` and
# a `status` subcommand. Neither source contracts them, so LoopX no longer
# emits them: a flag the parser does not recognise would be absorbed into the
# description, silently polluting the objective and dropping the iteration
# budget instead of failing loudly. Acceptance criteria therefore travel inside
# the goal statement, which is the host's documented mechanism.
# The host owns the iteration loop: it re-dispatches the turn until the model
# proves completion or the iteration budget runs out.
KIRO_CLI_GOAL_COMMAND = "/goal"
KIRO_CLI_GOAL_CLEAR_COMMAND = "/goal clear"
KIRO_CLI_GOAL_MAX_FLAG = "--max"
# The documented mechanism for binding acceptance criteria: state them in the
# goal text so the host's own verification step has a concrete target.
KIRO_CLI_GOAL_CRITERIA_LEAD = "Done when:"
KIRO_CLI_GOAL_DEFAULT_MAX_ITERATIONS = 5
KIRO_CLI_GOAL_COMPLETION_TOOL = "goal"

# Kiro CLI stamps every session with a stable id, which is what a LoopX
# thread binding can key on instead of prose.
KIRO_CLI_SESSION_ID_ENV = "KIRO_SESSION_ID"

# Global MCP config the default agent loads (`kiro-cli mcp list` reports its
# servers under the default agent), relative to the resolved Kiro home. The
# LoopX stdio server registered there inherits the session's KIRO_SESSION_ID.
KIRO_CLI_MCP_CONFIG_SUBPATH = Path("settings") / "mcp.json"


def mcp_server_script() -> Path:
    """The Kiro CLI stdio MCP entrypoint registered by the kiro-cli surface."""
    return Path(__file__).resolve().with_name("mcp_server.py")

# The host ships a real hook seam, and `preToolUse` can block a tool call by
# exiting 2. LoopX installs no hook today, so quota pacing stays advisory; the
# seam is recorded here so the honest claim and the future binding point stay
# in one place instead of being rediscovered from host docs.
KIRO_CLI_HOOK_TRIGGERS = (
    "agentSpawn",
    "userPromptSubmit",
    "preToolUse",
    "postToolUse",
    "stop",
)

KIRO_CLI_NATIVE_GOAL_FACTS = (
    "native `/goal [--max N] <description> | clear` command: the host "
    "re-dispatches turns toward one objective, verifies each iteration against "
    "the acceptance criteria it derives from the goal statement, and stops when "
    f"the work is confirmed or the iteration budget is spent (default "
    f"{KIRO_CLI_GOAL_DEFAULT_MAX_ITERATIONS})",
    "the built-in `goal` tool's `complete` command enforces a completion "
    "contract: every success criterion needs cited tool output, not narrative "
    "confidence",
    "the host derives acceptance criteria from the goal statement, so LoopX "
    "states the todo's criteria inside the description instead of passing a "
    "separate flag",
    "`/goal clear` cancels the active goal; a running goal is steered in place "
    "rather than read back through a status subcommand",
    "`hooks` in the agent config expose agentSpawn/userPromptSubmit/"
    "preToolUse/postToolUse/stop, and preToolUse can block a tool call with "
    "exit code 2",
)

KIRO_CLI_AGENT_TYPE_CATALOG_ENTRY: dict[str, Any] = {
    "display_name": "Kiro CLI",
    "host_loop": (
        "Kiro CLI native /goal iteration loop bounded by the host's own "
        "iteration budget (LoopX quota pacing is advisory)"
    ),
    "entry": f"/loopx <task> from the LoopX skill installed in {SKILLS_ROOT_LABEL}",
    "accepted_inputs": list(KIRO_CLI_ACCEPTED_INPUTS),
}


def kiro_cli_goal_invocation(
    *,
    task: str = "<task_body>",
    criteria: str | None = "<criteria>",
    max_iterations: str = "<N>",
) -> str:
    """The canonical native goal invocation, in the host's documented order.

    Every public projection — onboarding instructions, activation packets,
    docs — must render the command from here.

    The host documents ``/goal --max <N> <description>``: the iteration flag
    precedes the description, and everything after it is the goal statement.
    Acceptance criteria therefore belong *inside* that statement, because the
    host derives them from it; there is no criteria flag to pass. Appending an
    unrecognised flag after the description would not fail loudly — it would be
    read as more goal text, polluting the objective and silently dropping the
    iteration budget.
    """

    description = (
        task
        if not criteria
        else f"{task} {KIRO_CLI_GOAL_CRITERIA_LEAD} {criteria}"
    )
    return (
        f"{KIRO_CLI_GOAL_COMMAND} {KIRO_CLI_GOAL_MAX_FLAG} {max_iterations} "
        f"{description}"
    )


def kiro_cli_activation_extras() -> dict[str, Any]:
    """Keyword overrides for ``_kiro_cli_activation``'s facade call.

    Keeps the Kiro CLI host facts (goal command, iteration budget, completion
    tool, gate text, activation steps) in this package instead of growing
    ``host_loop_activation.py`` past its module metric budget. Kiro CLI ships
    both halves of a goal-mode host: a native goal primitive — ``/goal
    [--max N] <description> | clear`` whose host-side loop re-dispatches turns
    until the ``goal`` tool proves completion — and a bounded iteration budget
    the host itself enforces (default 5). What it does not ship is a
    cross-session daemon, and LoopX installs no hook, so quota pacing is
    instructed rather than enforced.
    """
    return {
        "activation_method": "bind_native_goal_with_advisory_quota_entry",
        "extra_host_mutation": {
            "host_loop_primitive": "kiro-cli-/goal-iteration-loop",
            "loop_driver": "kiro_cli_native_goal_loop",
            "quota_gate_enforcement": "advisory_only",
            "native_goal_command": KIRO_CLI_GOAL_COMMAND,
            "native_goal_cancel_command": KIRO_CLI_GOAL_CLEAR_COMMAND,
            "native_goal_max_flag": KIRO_CLI_GOAL_MAX_FLAG,
            "native_goal_criteria_placement": "inside_goal_statement",
            "native_goal_default_max_iterations": (
                KIRO_CLI_GOAL_DEFAULT_MAX_ITERATIONS
            ),
            "native_goal_completion_tool": KIRO_CLI_GOAL_COMPLETION_TOOL,
            "host_session_id_env": KIRO_CLI_SESSION_ID_ENV,
            # The typed control plane the same session reaches without
            # shelling out; its identity is the session's thread binding.
            "host_mcp_server": "loopx",
            "host_mcp_config": f"KIRO_HOME/{KIRO_CLI_MCP_CONFIG_SUBPATH.as_posix()}",
            "host_hook_triggers": list(KIRO_CLI_HOOK_TRIGGERS),
            "missing_host_tool_gate": (
                "Kiro CLI's /goal loop runs only while the CLI session is "
                "alive; there is no cross-session daemon, and the host stops "
                "the loop on its own turn and context limits rather than a "
                "LoopX-declared iteration ceiling. LoopX "
                "installs no Kiro hook, so no host hook intercepts a native "
                "iteration and quota pacing is advisory: the facade instructs, "
                "it cannot enforce. If the loop stops with work remaining, "
                "show the exact "
                "heartbeat-prompt command for the user to run and do not "
                "claim unattended heartbeat support."
            ),
        },
        "extra_activation_steps": [
            "Bind the objective with the native goal command: "
            f"`{kiro_cli_goal_invocation()}` — the host derives its acceptance "
            "criteria from the goal statement, so state the criteria the LoopX "
            f"todo already names after `{KIRO_CLI_GOAL_CRITERIA_LEAD}` rather "
            "than passing a separate flag, and choose N from the remaining "
            "quota slots, not from ambition "
            f"(host default is {KIRO_CLI_GOAL_DEFAULT_MAX_ITERATIONS}); "
            f"`{KIRO_CLI_GOAL_CLEAR_COMMAND}` cancels it.",
            "When the `loopx` MCP tools are loaded (`/mcp`), use "
            "`should_run`, `claim_task` and `complete_task` instead of "
            "hand-typed CLI: they act only for the agent this session's "
            f"`{KIRO_CLI_SESSION_ID_ENV}` is bound to and settle through the "
            "shared typed completion path.",
            "Steer a running goal in place instead of expecting a status "
            "readback: the host exposes cancellation and mid-loop steering, "
            "not a goal status subcommand.",
            "Start every turn and native goal iteration with `quota "
            "should-run` and honor a stop/throttle decision before any "
            "delivery work — instructed pacing, not a host-enforced gate.",
            f"Settle the goal through the built-in `{KIRO_CLI_GOAL_COMPLETION_TOOL}` "
            "tool only after LoopX writeback: its completion contract wants "
            "the same cited evidence LoopX records, so cite the validated "
            "commands rather than a narrative summary.",
            "When the loop stops with work remaining and quota still allows "
            "more, re-arm one further bounded goal instead of free-running, "
            "and re-enter through `quota should-run` on the same advisory "
            "basis.",
        ],
        "host_scheduler_note": (
            "the native `/goal` iteration loop drives this session within the "
            "host's own iteration budget; quota should-run entry is advisory "
            "guidance in the facade, not a host-enforced gate."
        ),
    }


def kiro_cli_chat_command(bin_name: str | None = None) -> tuple[str, ...]:
    """Argv that starts Kiro CLI as an ACP stdio agent for LoopX Chat.

    ``bin_name`` is an injection point for tests and for an owner whose host
    binary is not on ``PATH`` under the documented name; the default is the
    documented executable. No trust flag is added: LoopX Chat cancels every ACP
    permission request, and auto-approving tools from a dashboard-launched
    session is the owner's decision, not this adapter's.
    """
    return (bin_name or KIRO_CLI_BIN, KIRO_CLI_CHAT_ACP_SUBCOMMAND)


def kiro_home(value: str | None = None) -> Path:
    """The Kiro CLI home: ``KIRO_HOME`` when set, else ``~/.kiro``.

    Kiro CLI discovers global skills from ``<home>/skills/<name>/SKILL.md`` and
    workspace skills from ``.kiro/skills/<name>/SKILL.md``; the default agent
    carries both as ``skill://`` resources. ``KIRO_HOME`` is the host's own
    override for that global root, so LoopX must resolve it: writing to
    ``~/.kiro`` while the active profile lives elsewhere would report a
    successful install that the running host never discovers. Resolution order
    matches every other host in this repository: explicit injected ``value``,
    then the host environment override, then the default. Install and uninstall
    share this one resolver, so they cannot target different roots.
    """
    raw = (
        value
        or os.environ.get(KIRO_CLI_HOME_ENV)
        or str(Path.home() / DEFAULT_KIRO_CLI_HOME)
    )
    return Path(raw).expanduser()
