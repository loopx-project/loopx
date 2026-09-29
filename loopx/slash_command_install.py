from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .agy_goal_mode import agy_home as _agy_home
from .kiro_cli_goal_mode import (
    KIRO_CLI_MCP_CONFIG_SUBPATH as _KIRO_MCP_CONFIG_SUBPATH,
    SKILLS_ROOT_LABEL as _KIRO_SKILLS_ROOT_LABEL,
    kiro_home as _kiro_home,
)
from .kiro_cli_goal_mode.gated_agent import (
    GATED_AGENT_LAUNCH,
    gated_agent_path,
    sync_gated_agent,
)
from .opencode_goal_mode import plugin_source, runtime_source
from .pi_goal_mode import extension_source as pi_extension_source
from .pi_goal_mode import runtime_source as pi_runtime_source
from .pi_goal_mode.installation import (
    _pi_agent_dir,
    _pi_extension_path,
    _pi_extension_root,
    _pi_runtime_path,
)
from .slash_command_files import (
    front_matter as _front_matter,
    install_skill_facade as _install_skill_facade,
    managed_marker as _managed_marker,
    retire_managed_file as _retire_managed_file,
    retire_status as _retire_status,
    skill_body as _skill_body,
    target_status as _target_status,
)
from .skill_install_readback import retire_duplicate_managed_skills
from .slash_commands import build_slash_command_catalog
from .zcode_goal_mode import zcode_home as _zcode_home

SCHEMA_VERSION = "loopx_slash_command_install_v0"
OPENCODE_GOAL_DEPENDENCIES = {
    "@opencode-ai/plugin": ">=1.17.15 <2",
    "opencode-goal-plugin": "0.7.0",
}


def _openai_skill_metadata(*, command: str, display_name: str, short_description: str) -> str:
    return "\n".join(
        [
            f"# {_managed_marker(command=command, surface='codex-skill-metadata')}",
            "interface:",
            f'  display_name: "{display_name}"',
            f'  short_description: "{short_description}"',
            "policy:",
            "  allow_implicit_invocation: false",
            "",
        ]
    )


def _opencode_command_body(spec: dict[str, Any]) -> str:
    return "\n\n".join(
        [
            _front_matter(
                fields={
                    "description": str(spec["description"]),
                    "agent": "build",
                }
            ),
            _managed_marker(command=str(spec["command"]), surface="opencode-command"),
            f"Treat this as the LoopX `{spec['command']}` OpenCode command.",
            (
                "The exact current host is OpenCode. On OpenCode 1, pass "
                "`--host-surface opencode` and use `loopx_goal_activate` from the "
                "returned host-loop activation packet. On OpenCode 2, pass "
                "`--host-surface opencode2` and run the returned "
                "`loopx opencode2-goal-worker` command with the heartbeat packet values."
            ),
            "\n".join(str(item) for item in spec["instructions"]),
            (
                "Keep public/private boundaries intact and do not perform external "
                "writes unless the active LoopX state or owner explicitly authorizes them."
            ),
        ]
    ) + "\n"


def _loopx_start_goal_arguments_instruction(
    *,
    cli_bin: str,
    host_surface: str | None,
) -> str:
    selected_host = host_surface or "<exact-current-host>"
    instruction = (
        "If arguments are present and the current host already has a verified "
        "active LoopX Goal/Agent binding, preserve that exact identity when the "
        "request continues, corrects, or refines the registered objective. Do "
        "not call `start-goal` for an ordinary phase, issue, PR, or Todo inside "
        "that Goal; follow its exact current `interaction_contract` or quota "
        "command first, then record the request through the typed Todo/writeback "
        "path for the bound agent. Start another Goal only for a materially "
        "different objective or an explicit new-Goal request. Otherwise pass "
        "the complete visible command arguments unchanged as one value to "
        f'`{cli_bin} start-goal --guided --project . --slash-command-arguments='
        f'"<complete visible $ARGUMENTS>" --host-surface {selected_host}`. '
        "The CLI, not the model, owns parsing supported leading switches and "
        "preserving the remaining goal text. Never split or recompose the "
        "arguments, and never infer a route from issue/PR wording or URLs."
    )
    if host_surface is None:
        instruction += (
            " If the host is unclear, omit the host flag once and follow the "
            "returned host-surface selection gate."
        )
    return instruction


def _dsh_native_loopx_instructions(*, cli_bin: str) -> list[str]:
    return [
        (
            "This entry skill is installed for the exact current host "
            "`deepseek-harness-native`; do not infer or substitute another host "
            "surface."
        ),
        (
            "Treat the complete original visible user task as `goalText`. It is "
            "the task wording, not a command token, routing envelope, or "
            "confirmation field. DSH does not substitute a `$ARGUMENTS` "
            "placeholder."
        ),
        (
            "Use DSH's shell tool to invoke the authoritative LoopX CLI. Never "
            "call plugin-provided LoopX model tools and never edit a LoopX "
            "registry directly."
        ),
        (
            "Require the exact non-empty `$DSH_SESSION_ID` supplied by DSH. "
            "Never synthesize, normalize, or reuse a Session id from prose."
        ),
        (
            "For a concrete task, preserve the complete task wording as one argv "
            "value. Encode it as one POSIX single-quoted shell word, replacing each "
            "embedded single quote with the exact sequence `'\"'\"'`, so no task "
            "text becomes shell syntax. Then run "
            f'`{cli_bin} start-goal --guided --project . '
            "--goal-text='<shell-escaped complete original visible user task>' "
            '--host-surface deepseek-harness-native '
            '--thread-id "$DSH_SESSION_ID"`. Do not summarize, classify, or '
            "rewrite the task before this call."
        ),
        (
            "For a status/continuation request with no new task, first run "
            f'`{cli_bin} --registry .loopx/registry.json --format json '
            'resolve-agent-thread --host-surface deepseek-harness-native '
            '--thread-id "$DSH_SESSION_ID"`; do not create a new Goal from an '
            "empty or inspection-only request."
        ),
        (
            "Treat returned `ordered_steps`, `goal_start_contract`, selection "
            "gates, identity commands, Todo commands, quota decisions, and "
            "writeback commands as authoritative. Execute only exact typed CLI "
            "commands, including the returned thread-binding step, and never "
            "guess or fuzzy-match a Goal or Agent id."
        ),
        (
            "Use the installed `loopx-project` Skill for advanced LoopX lifecycle "
            "operations. If no safe typed operation follows from the CLI packet, "
            "ask the user before mutating authority."
        ),
    ]


def _command_prompt_specs(*, cli_bin: str, include_legacy_aliases: bool) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        {
            "command": "/loopx",
            "name": "loopx",
            "description": "Inspect LoopX state, or start concrete project work when arguments are provided.",
            "argument_hint": "[--fine-grained] [--capability-route issue-fix] [task text]",
            "instructions": [
                "Visible command arguments: `$ARGUMENTS`.",
                "Identify the exact current host surface (codex-app, trae_app, codex-app-ssh, codex-ide-plugin, codex-cli-tui, opencode, opencode2, traex-cli, pi, gemini-cli, cursor-agent, zcode, agy, kiro-cli, deepseek-harness, or ark-managed-agent).",
                _loopx_start_goal_arguments_instruction(
                    cli_bin=cli_bin,
                    host_surface=None,
                ),
                "Treat the returned `ordered_steps` and `goal_start_contract` as authoritative. Follow their identity, capability-route, Todo, writeback, host-loop, quota, and stop/gate rules before substantive work; do not reconstruct those rules from skill memory.",
                "When the host explicitly supplies a `loopx_task_planning_v0` packet from `loopx todo plan` for a registered Goal/Agent, execute that bounded planning checkpoint instead of starting another Goal. Follow its shared planner and Todo delta, then return actual Todo ids for readback. Its caller-owned execution_handoff retains host activation and quota; do not create a planning Todo, execute task work, or claim delivery during the checkpoint.",
                "For a Codex App heartbeat, run the returned activation command, require ok=true, and save its `LoopX managed heartbeat bootstrap v2` task_body through automation_update. The saved loader fetches the current thin contract on every wake; do not persist a raw thin/compact/full execution body. Preserve the current goal, registered agent, task binding and existing schedule; read back the automation through the same App.",
                "If the packet exposes a goal-selection gate, rerun one exact choice before any mutation.",
                "When authoring task Todos, treat `--action-kind` as the documented extensible public-safe token: choose a short task-relevant value such as `implement`, `test`, or `review`; do not search the LoopX source for an allowlist.",
                "Consume the turn-start quota JSON packet exactly once: read the complete output directly or save it and query it with `jq`; never pipe it through `head` or `tail`, and never rerun the turn-start call to recover hidden fields. A host whose runtime mints Turn identity uses `--begin-turn`; every other host passes its own `--turn-instance-id`. When selection is required, choose the Todo and use `interaction_contract.cli_channel.selection_command` with the returned Turn identity before mutation.",
                "Runtime capability flags are host observations, not task requirements or grants; registered Agents reuse supported ones via `loopx agent-capabilities`. Before initial quota, include capabilities already established by this host context or successful task-facing use. Read capability_gate.repair_missing even when should_run is true: when runtime_capability_reentry is projected, verify its real callsite and follow the returned same-Turn command before choosing fallback work. Never infer credentials or production access from network availability, and do not claim a missing declaration proves a missing tool.",
                f"If arguments are empty and the host already identifies an active LoopX goal, follow its exact CLI `interaction_contract` or quota command first; otherwise inspect `{cli_bin} status` and `{cli_bin} bootstrap-command-pack --project .` before changing files.",
                "If this session cannot mutate the host loop surface, surface the exact pasteable gate instead of claiming autonomous setup.",
            ],
        },
        {
            "command": "/loopx-global-summary",
            "name": "loopx-global-summary",
            "description": "Read the compact global LoopX progress digest.",
            "argument_hint": "[optional focus]",
            "instructions": [
                "Visible command arguments: `$ARGUMENTS`.",
                f"Run `{cli_bin} global-summary` first and summarize visible projects, gates, monitor status, and next safe actions.",
                "This command is read-only unless the user explicitly asks for a state update.",
            ],
        },
        {
            "command": "/loopx-global-gates",
            "name": "loopx-global-gates",
            "description": "List open LoopX user/controller gates and what each blocks.",
            "argument_hint": "[optional focus]",
            "instructions": [
                "Visible command arguments: `$ARGUMENTS`.",
                f"Run `{cli_bin} global-gates` first and summarize formal open gates, "
                "blocked todo or goal scope, owner routing, and exact next questions.",
                "This command is read-only unless the user explicitly asks for a state update.",
            ],
        },
        {
            "command": "/loopx-global-todos",
            "name": "loopx-global-todos",
            "description": "List runnable, blocked, deferred-ready, and review LoopX todos across visible projects.",
            "argument_hint": "[optional focus]",
            "instructions": [
                "Visible command arguments: `$ARGUMENTS`.",
                f"Run `{cli_bin} global-todos` first and summarize prioritized ownership and structured readiness across visible projects without mutating state.",
                "This command is read-only unless the user explicitly asks for a state update.",
            ],
        },
        {
            "command": "/loopx-global-risks",
            "name": "loopx-global-risks",
            "description": "Show stale LoopX runs, boundary risks, failing checks, and rollback candidates.",
            "argument_hint": "[optional focus]",
            "instructions": [
                "Visible command arguments: `$ARGUMENTS`.",
                f"Run `{cli_bin} global-risks` first and summarize structured stale runs, "
                "boundary warnings, failing checks, and whether a formally evidenced "
                "rollback candidate source is available, without mutating state.",
                "This command is read-only unless the user explicitly asks for a state update.",
            ],
        },
        {
            "command": "/loopx-pr-review",
            "name": "loopx-pr-review",
            "description": "Run the LoopX PR-review packet first, then review selected PR groups with evidence.",
            "argument_hint": "[--repo owner/repo] [--state open|merged|all] [--review-priority other-developers-first|owner-first] [--since ISO]",
            "instructions": [
                "Visible command arguments: `$ARGUMENTS`.",
                "Use the installed `loopx-pr-review` skill when available.",
                f"Run `{cli_bin} --format json pr-review $ARGUMENTS` first and keep the full packet visible. The default review priority is `other-developers-first`; pass `--review-priority owner-first` only when the authenticated reviewer's own PRs should lead. Only non-null action rows carry review plans, templates, and evidence commands; null actions are readback-only. A fresh audit requires `--fresh-audit-exact-head NUMBER@HEAD_OID`.",
                "Do not reconstruct the PR queue manually from ad hoc GitHub calls before reading the LoopX packet.",
                "This command is read-only; do not comment, approve, merge, rerun CI, or spend quota unless separately authorized.",
            ],
        },
        {
            "command": "/loopx-deepresearch",
            "name": "loopx-deepresearch",
            "description": "Run a bounded LoopX deep-research loop: packet-driven expeditions, evidence ledgers, citation-auditable report.",
            "argument_hint": "<research question> [--max-sources N]",
            "instructions": [
                "Visible command arguments: `$ARGUMENTS`.",
                f"Run `{cli_bin} --format json deepresearch status --project .` first; if no research is active, treat `$ARGUMENTS` as the question and run `{cli_bin} --format json deepresearch start --project . --question $ARGUMENTS`.",
                "Keep `research_contract`, `stop_conditions`, `next_expedition`, and `evidence_commands` from the packet visible; the packet owns what to research next and when to stop.",
                "Record every finding through the typed subcommands (`add-source`, `add-subquestion`, `resolve-question`); never edit the state file directly, and never fabricate URLs or claims — a claim exists only if a tool you actually ran produced it.",
                "Resolve a question only with recorded evidence claim ids; an open contradiction blocks resolution until an explicit sides-with claim and rationale are recorded.",
                "Re-run `status` after every expedition; stop when `stop_conditions.stopped` is true, then run `deepresearch report` and present the report path.",
                "One active run per project: to research a new question, run `deepresearch close` (or `start --new-run` once stopped) — close marks the terminal state and the next start archives it, never by editing state files.",
            ],
        },
    ]
    if include_legacy_aliases:
        legacy_specs = []
        for canonical in specs:
            name = canonical["name"]
            if not str(name).startswith("loopx-global-"):
                continue
            legacy_name = str(name).replace("loopx-global-", "loop-global-", 1)
            legacy_specs.append(
                {
                    **canonical,
                    "command": "/" + legacy_name,
                    "name": legacy_name,
                    "description": canonical["description"] + " Legacy alias for the canonical /loopx-global-* command.",
                }
            )
        specs.extend(legacy_specs)
    return specs


def _command_skill_content(spec: dict[str, Any], *, surface: str) -> str:
    instructions = list(spec["instructions"])
    if surface == "codex-skills":
        instructions.insert(
            0,
            "On native Windows, run the installed PowerShell 7 entry as `loopx` "
            "from PowerShell; from another executor use `pwsh.exe -NoLogo "
            "-NoProfile -File \"$HOME/.local/bin/loopx.ps1\" <arguments>`.",
        )
    return _skill_body(
        command=str(spec["command"]),
        title=str(spec.get("title") or f"LoopX {spec['command']}"),
        description=str(spec["description"]),
        argument_hint=str(spec["argument_hint"]),
        instructions=instructions,
        surface=surface,
        front_matter_name=str(spec["name"]),
    )


def materialize_loopx_entry_skill(
    *,
    skills_dir: Path,
    execute: bool,
    cli_bin: str = "loopx",
    host_surface: str | None = None,
) -> dict[str, Any]:
    """Materialize the generated ``$loopx`` entry skill into a host skill root."""

    if host_surface not in {
        None,
        "ark-managed-agent",
        "deepseek-harness-native",
    }:
        raise ValueError(f"unsupported fixed LoopX entry host surface: {host_surface}")
    spec = next(
        item
        for item in _command_prompt_specs(
            cli_bin=cli_bin,
            include_legacy_aliases=False,
        )
        if item["name"] == "loopx"
    )
    if host_surface == "deepseek-harness-native":
        spec = {
            **spec,
            "command": "loopx",
            "title": "LoopX CLI Workflow",
            "description": (
                "Use the authoritative LoopX CLI for the current DSH task or "
                "continuation."
            ),
            "argument_hint": "[task text]",
            "instructions": _dsh_native_loopx_instructions(cli_bin=cli_bin),
        }
    elif host_surface:
        instructions = list(spec["instructions"])
        instructions[1] = (
            "This entry skill is installed for the exact current host "
            f"`{host_surface}`; do not infer or substitute another host surface."
        )
        instructions[2] = _loopx_start_goal_arguments_instruction(
            cli_bin=cli_bin,
            host_surface=host_surface,
        )
        spec = {**spec, "instructions": instructions}
    skill_path = skills_dir / "loopx" / "SKILL.md"
    content = _command_skill_content(
        spec,
        surface=(
            "dsh-skills"
            if host_surface == "deepseek-harness-native"
            else "codex-skills"
        ),
    )
    status = _target_status(skill_path, content, execute=execute)
    result = {"skill_id": "loopx", "path": str(skill_path), "status": status}
    # Workflow installs can generate this entry without slash-commands. Keep
    # the Codex presentation identical, without changing exact managed hosts.
    if host_surface is None and status not in {
        "skipped_user_file", "preserved_existing_loopx_skill",
    }:
        result["metadata_status"] = _target_status(
            skill_path.parent / "agents" / "openai.yaml",
            _openai_skill_metadata(
                command=str(spec["command"]),
                display_name="LoopX",
                short_description=str(spec["description"]),
            ),
            execute=execute,
        )
    return result


def _codex_home(value: str | None = None) -> Path:
    raw = value or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    return Path(raw).expanduser()


def _claude_home(value: str | None = None) -> Path:
    raw = value or os.environ.get("CLAUDE_HOME") or str(Path.home() / ".claude")
    return Path(raw).expanduser()


def _gemini_home(value: str | None = None) -> Path:
    """Gemini CLI reads user skills from GEMINI_HOME/skills (default ~/.gemini)."""
    raw = value or os.environ.get("GEMINI_HOME") or str(Path.home() / ".gemini")
    return Path(raw).expanduser()


def _cursor_home(value: str | None = None) -> Path:
    """Cursor CLI reads MCP servers from CURSOR_HOME/mcp.json (default ~/.cursor)."""
    raw = value or os.environ.get("CURSOR_HOME") or str(Path.home() / ".cursor")
    return Path(raw).expanduser()


def _opencode_home(value: str | None = None) -> Path:
    raw = value or os.environ.get("OPENCODE_CONFIG_DIR")
    if not raw:
        config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        raw = str(Path(config_home) / "opencode")
    return Path(raw).expanduser()


def _strip_jsonc_comments(content: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(content):
        char = content[index]
        next_char = content[index + 1] if index + 1 < len(content) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            output.extend((" ", " "))
            index += 2
            while index < len(content) and content[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue
        if char == "/" and next_char == "*":
            output.extend((" ", " "))
            index += 2
            while index < len(content):
                if index + 1 < len(content) and content[index : index + 2] == "*/":
                    output.extend((" ", " "))
                    index += 2
                    break
                output.append("\n" if content[index] == "\n" else " ")
                index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _strip_jsonc_trailing_commas(content: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(content):
        char = content[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(content) and content[lookahead].isspace():
                lookahead += 1
            if lookahead < len(content) and content[lookahead] in "]}":
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def _opencode_plugin_name(plugin: Any) -> str | None:
    if isinstance(plugin, str):
        return plugin
    if (
        isinstance(plugin, list)
        and plugin
        and isinstance(plugin[0], str)
    ):
        return plugin[0]
    return None


def _opencode_direct_goal_plugin_conflicts(root: Path) -> tuple[list[str], list[str]]:
    conflicts: list[str] = []
    invalid: list[str] = []
    goal_plugins = {
        "opencode-goal-plugin",
        "@heimoshuiyu/opencode-goal-plugin",
        "@prevalentware/opencode-goal-plugin",
    }
    for name in ("opencode.json", "opencode.jsonc"):
        path = root / name
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
            if path.suffix == ".jsonc":
                content = _strip_jsonc_trailing_commas(_strip_jsonc_comments(content))
            payload = json.loads(content)
        except (json.JSONDecodeError, OSError):
            invalid.append(str(path))
            continue
        if not isinstance(payload, dict):
            invalid.append(str(path))
            continue
        plugins = payload.get("plugin") or []
        if isinstance(plugins, str):
            plugins = [plugins]
        if not isinstance(plugins, list):
            invalid.append(str(path))
            continue
        plugin_names = [
            name
            for plugin in plugins
            if (name := _opencode_plugin_name(plugin)) is not None
        ]
        if any(
            plugin == package or plugin.startswith(f"{package}@")
            for plugin in plugin_names
            for package in goal_plugins
        ):
            conflicts.append(str(path))
    return conflicts, invalid


def _target_package_dependencies(
    path: Path,
    dependencies: dict[str, str],
    *,
    execute: bool,
) -> str:
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return "blocked_invalid_user_package_json"
        if not isinstance(payload, dict):
            return "blocked_invalid_user_package_json"
        current = payload.get("dependencies")
        if current is None:
            current = {}
        if not isinstance(current, dict):
            return "blocked_invalid_user_package_json"
        wanted = {**current, **dependencies}
        if wanted == current:
            return "unchanged"
        payload["dependencies"] = wanted
        status = "updated" if execute else "would_update"
    else:
        payload = {"private": True, "dependencies": dependencies}
        status = "created" if execute else "would_create"
    if execute:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    return status


def _normalize_surfaces(surfaces: list[str] | None) -> list[str]:
    requested = surfaces or ["all"]
    normalized: list[str] = []
    for surface in requested:
        if surface == "all":
            candidates = ["codex", "claude-code", "opencode"]
        elif surface == "codex":
            candidates = ["codex"]
        elif surface in {"codex-app", "codex-app-ssh", "codex-ide-plugin", "codex-ide", "codex-cli"}:
            candidates = ["codex"]
        elif surface in {"gemini-cli", "gemini-code"}:
            candidates = ["gemini"]
        elif surface in {"cursor-agent", "cursor-cli"}:
            candidates = ["cursor"]
        elif surface in {"zcode", "z-code"}:
            candidates = ["zcode"]
        elif surface in {"agy", "antigravity", "antigravity-cli"}:
            candidates = ["agy"]
        elif surface in {"kiro", "kiro-cli", "kirocli"}:
            candidates = ["kiro-cli"]
        else:
            candidates = [surface]
        for candidate in candidates:
            if candidate not in normalized:
                normalized.append(candidate)
    return normalized


# Every surface that registers the LoopX stdio server shares one key, one
# ownership marker file name and one merge rule; only the config location, the
# host-specific server script and the reported status names differ.
MANAGED_MCP_KEY = "loopx"
CURSOR_MCP_MARKER_NAME = ".loopx-managed-mcp.json"
CURSOR_MCP_MARKER_SCHEMA_VERSION = "loopx_managed_cursor_mcp_v0"
KIRO_CLI_MCP_MARKER_SCHEMA_VERSION = "loopx_managed_kiro_cli_mcp_v0"

McpCommandFactory = Callable[[], tuple[str, str] | None]


def _provisioned_mcp_interpreter() -> str | None:
    """An interpreter that can import `mcp.server.fastmcp`, or None.

    Reuses the Claude adapter's provisioning so every surface shares one
    dedicated venv and one `mcp` dependency.
    """
    try:
        from loopx.claude_goal_mode.scripts import install as claude_install
    except ImportError:
        return None
    try:
        # Provisioning narrates to stdout ("[deps] creating mcp venv …"), which
        # would land in the middle of `--format json` and make the whole install
        # result unparseable. The narration is still worth keeping — on stderr.
        with contextlib.redirect_stdout(sys.stderr):
            interpreter = claude_install.provision_mcp_python(dry=False)
    except (OSError, ValueError):
        # Provisioning shells out to venv and pip; a failure there means the
        # surface is skipped, never that the whole install run dies.
        return None
    if not interpreter or not claude_install._has_mcp(interpreter):
        return None
    return str(interpreter)


def _mcp_command_for_script(script: Path) -> tuple[str, str] | None:
    """Interpreter and script for one LoopX stdio MCP entrypoint, or None.

    Returns None when `mcp` cannot be provisioned — the caller records that
    instead of writing a config that would fail to start.
    """
    if not script.exists():
        return None
    interpreter = _provisioned_mcp_interpreter()
    if interpreter is None:
        return None
    return interpreter, str(script)


def _loopx_mcp_command() -> tuple[str, str] | None:
    """The shared Claude-profile server Cursor registers."""
    try:
        from loopx.claude_goal_mode.scripts import install as claude_install
    except ImportError:
        return None
    return _mcp_command_for_script(Path(claude_install._p("mcp", "loopx_mcp.py")))


def _kiro_cli_mcp_command() -> tuple[str, str] | None:
    """The Kiro CLI server: same control plane, Kiro runtime profile and
    session-bound identity."""
    from .kiro_cli_goal_mode import mcp_server_script

    return _mcp_command_for_script(mcp_server_script())


def _read_mcp_marker(marker_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    servers = payload.get("servers")
    return servers if isinstance(servers, dict) else {}


def _write_json_atomic(path: Path, payload: Any) -> None:
    """Replace `path` in one step, so an interrupted run cannot truncate it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode if path.exists() else None
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2) + "\n")
        if mode is not None:
            os.chmod(tmp, mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _merge_managed_mcp_json(
    *,
    config_path: Path,
    marker_schema_version: str,
    invalid_status: str,
    command_factory: McpCommandFactory,
    uninstall: bool,
    execute: bool,
) -> str:
    """Add or remove the `loopx` entry in a host's `mcpServers` JSON — and nothing else.

    The config is a user-owned file that LoopX did not create, so every path
    here fails closed: an unexpected shape is reported instead of normalized, a
    same-name entry LoopX cannot prove it wrote is left alone, and uninstall
    removes only an entry that still matches what LoopX recorded. Sharing the
    `loopx` name with a user's own server is unlikely but cheap to survive;
    silently replacing that server, or dropping their `mcpServers` list while
    "merging", is not.

    Provenance lives in a sidecar beside the config, because the config belongs
    to the host's schema and must not carry an entry the host has to parse.
    """
    marker_path = config_path.parent / CURSOR_MCP_MARKER_NAME
    try:
        raw = (
            json.loads(config_path.read_text(encoding="utf-8"))
            if config_path.exists()
            else {}
        )
    except (OSError, json.JSONDecodeError):
        return invalid_status
    if not isinstance(raw, dict):
        return invalid_status
    servers = raw.get("mcpServers")
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        # A list-valued or scalar `mcpServers` is not something to overwrite:
        # either the file follows a shape LoopX does not understand, or it is
        # damaged. Both are the user's to resolve.
        return invalid_status

    existing = servers.get(MANAGED_MCP_KEY)
    recorded = _read_mcp_marker(marker_path).get(MANAGED_MCP_KEY)
    # Ownership is only demonstrable when the entry still matches what LoopX
    # recorded writing. A hand-edited entry counts as the user's from then on.
    loopx_owned = existing is not None and existing == recorded

    # A marker that no longer describes reality is not just useless, it is
    # dangerous: once the user deletes or edits the entry, the stale marker
    # would still vouch for a future same-name entry and authorize deleting
    # something LoopX never wrote. Ownership has to expire the moment it stops
    # being demonstrable — and only in execute mode, so a dry run stays a
    # read-only report.
    if execute and recorded is not None and not loopx_owned:
        marker_path.unlink(missing_ok=True)

    if uninstall:
        if existing is None:
            return "absent"
        if not loopx_owned:
            return "skipped_user_owned_mcp_entry"
        if not execute:
            return "would_retire"
        servers.pop(MANAGED_MCP_KEY, None)
        raw["mcpServers"] = servers
        _write_json_atomic(config_path, raw)
        marker_path.unlink(missing_ok=True)
        return "retired"

    if existing is not None and not loopx_owned:
        return "skipped_user_owned_mcp_entry"
    if not execute:
        # A dry run must not touch anything: provisioning `mcp` would create a
        # venv on disk during a run whose only job is to report what would happen.
        return "unchanged" if existing is not None else "would_write"
    command = command_factory()
    if command is None:
        return "skipped_mcp_dependency_missing"
    entry = {"command": command[0], "args": [command[1]]}
    if existing == entry:
        return "unchanged"
    servers[MANAGED_MCP_KEY] = entry
    raw["mcpServers"] = servers
    _write_json_atomic(config_path, raw)
    _write_json_atomic(
        marker_path,
        {
            "schema_version": marker_schema_version,
            "servers": {MANAGED_MCP_KEY: entry},
        },
    )
    return "written"


def install_slash_commands(
    *,
    execute: bool,
    uninstall: bool = False,
    with_goal_bridge: bool = False,
    with_gated_agent: bool = False,
    surfaces: list[str] | None = None,
    cli_bin: str = "loopx",
    include_legacy_aliases: bool = True,
    codex_home: str | None = None,
    claude_home: str | None = None,
    opencode_home: str | None = None,
    gemini_home: str | None = None,
    cursor_home: str | None = None,
    zcode_home: str | None = None,
    zcode_agents_home: str | None = None,
    agy_home: str | None = None,
    kiro_home: str | None = None,
    pi_project: str | None = None,
    pi_scope: str = "project",
    pi_user_home: str | None = None,
) -> dict[str, Any]:
    specs = _command_prompt_specs(cli_bin=cli_bin, include_legacy_aliases=include_legacy_aliases)
    effective_surfaces = _normalize_surfaces(surfaces)
    codex_root = _codex_home(codex_home)
    claude_root = _claude_home(claude_home)
    opencode_root = _opencode_home(opencode_home)
    gemini_root = _gemini_home(gemini_home)
    cursor_root = _cursor_home(cursor_home)
    zcode_root = _zcode_home(zcode_home or zcode_agents_home)
    agy_root = _agy_home(agy_home)
    kiro_root = _kiro_home(kiro_home)
    if pi_scope not in {"project", "user"}:
        raise ValueError("pi_scope must be 'project' or 'user'")
    pi_project_root = Path(pi_project or ".").expanduser().resolve()
    pi_agent_dir = _pi_agent_dir(pi_user_home)
    pi_extension_root = _pi_extension_root(
        pi_project_root, scope=pi_scope, agent_dir=pi_agent_dir
    )
    installed: list[dict[str, Any]] = []

    if with_goal_bridge and "opencode" not in effective_surfaces:
        installed.append(
            {
                "surface": "opencode",
                "host_surfaces": ["opencode"],
                "mechanism": "opencode_goal_bridge",
                "command": "/goal",
                "path": None,
                "status": "blocked_goal_bridge_requires_opencode_surface",
                "invoke_as": [],
                "reason": "Select --surface opencode when using --with-goal-bridge.",
            }
        )

    if with_gated_agent and "kiro-cli" not in effective_surfaces:
        installed.append(
            {
                "surface": "kiro-cli",
                "host_surfaces": ["kiro-cli"],
                "mechanism": "kiro_cli_gated_agent",
                "command": GATED_AGENT_LAUNCH,
                "path": None,
                "status": "blocked_gated_agent_requires_kiro_cli_surface",
                "invoke_as": [],
                "reason": "Select --surface kiro-cli when using --with-gated-agent.",
            }
        )

    codex_reconciliation = None
    if "codex" in effective_surfaces:
        # Keep aliases in the catalog and native slash hosts, but expose one
        # canonical skill per outcome in Codex's skill picker.
        codex_specs = _command_prompt_specs(cli_bin=cli_bin, include_legacy_aliases=False)
        legacy_specs = [s for s in _command_prompt_specs(cli_bin=cli_bin, include_legacy_aliases=True)
                        if str(s["name"]).startswith("loop-global-")]
        for spec in legacy_specs:
            for path in (codex_root / "skills" / spec["name"] / "SKILL.md",
                         codex_root / "skills" / spec["name"] / "agents" / "openai.yaml",
                         codex_root / "prompts" / f"{spec['name']}.md"):
                status = _retire_managed_file(path, execute=execute)
                if status:
                    installed.append({"surface": "codex", "mechanism": "retired_codex_legacy_alias",
                                      "command": spec["command"], "path": str(path),
                                      "status": status, "invoke_as": []})
        prompt_dir = codex_root / "prompts"
        for spec in codex_specs:
            prompt_path = prompt_dir / f"{spec['name']}.md"
            if uninstall:
                retire_status = _retire_status(prompt_path, execute=execute)
                installed.append(
                    {
                        "surface": "codex",
                        "host_surfaces": ["codex-cli", "codex-ide-plugin", "codex-app", "codex-app-ssh"],
                        "mechanism": "retired_codex_custom_prompt",
                        "command": spec["command"],
                        "path": str(prompt_path),
                        "status": retire_status,
                        "invoke_as": [],
                    }
                )
                continue
            retire_status = _retire_managed_file(prompt_path, execute=execute)
            if retire_status:
                installed.append(
                    {
                        "surface": "codex",
                        "host_surfaces": ["codex-cli", "codex-ide-plugin", "codex-app", "codex-app-ssh"],
                        "mechanism": "retired_codex_custom_prompt",
                        "command": spec["command"],
                        "path": str(prompt_path),
                        "status": retire_status,
                        "invoke_as": [],
                    }
                )

        skill_dir = codex_root / "skills"
        for spec in codex_specs:
            skill_path = skill_dir / str(spec["name"]) / "SKILL.md"
            metadata_path = skill_path.parent / "agents" / "openai.yaml"
            if uninstall:
                skill_status = _retire_status(skill_path, execute=execute)
                installed.append(
                    {
                        "surface": "codex",
                        "host_surfaces": ["codex-cli", "codex-ide-plugin", "codex-app", "codex-app-ssh"],
                        "mechanism": "codex_explicit_skills",
                        "command": spec["command"],
                        "path": str(skill_path),
                        "status": skill_status,
                        "invoke_as": [f"${spec['name']}", "/skills"],
                    }
                )
                metadata_status = _retire_status(metadata_path, execute=execute)
                installed.append(
                    {
                        "surface": "codex",
                        "host_surfaces": ["codex-cli", "codex-ide-plugin", "codex-app", "codex-app-ssh"],
                        "mechanism": "codex_skill_openai_metadata",
                        "command": spec["command"],
                        "path": str(metadata_path),
                        "status": metadata_status,
                        "invoke_as": [f"${spec['name']}", "/skills"],
                    }
                )
                continue
            skill_content = _command_skill_content(spec, surface="codex-skills")
            skill_status = _target_status(skill_path, skill_content, execute=execute)
            installed.append(
                {
                    "surface": "codex",
                    "host_surfaces": ["codex-cli", "codex-ide-plugin", "codex-app", "codex-app-ssh"],
                    "mechanism": "codex_explicit_skills",
                    "command": spec["command"],
                    "path": str(skill_path),
                    "status": skill_status,
                    "invoke_as": [f"${spec['name']}", "/skills"],
                }
            )
            if skill_status not in {"skipped_user_file", "preserved_existing_loopx_skill"}:
                display_name = (
                    "LoopX" if spec["name"] == "loopx" else "LoopX " + str(spec["name"])[6:].replace("-", " ").title().replace("Pr ", "PR ")
                )
                metadata = _openai_skill_metadata(
                    command=str(spec["command"]),
                    display_name=display_name,
                    short_description=str(spec["description"]),
                )
                metadata_status = _target_status(metadata_path, metadata, execute=execute)
                installed.append(
                    {
                        "surface": "codex",
                        "host_surfaces": ["codex-cli", "codex-ide-plugin", "codex-app", "codex-app-ssh"],
                        "mechanism": "codex_skill_openai_metadata",
                        "command": spec["command"],
                        "path": str(metadata_path),
                        "status": metadata_status,
                        "invoke_as": [f"${spec['name']}", "/skills"],
                    }
                )
            elif skill_status in {"skipped_user_file", "preserved_existing_loopx_skill"}:
                retire_status = _retire_managed_file(metadata_path, execute=execute)
                if retire_status:
                    installed.append(
                        {
                            "surface": "codex",
                            "host_surfaces": ["codex-cli", "codex-ide-plugin", "codex-app", "codex-app-ssh"],
                            "mechanism": "retired_codex_command_metadata",
                            "command": spec["command"],
                            "path": str(metadata_path),
                            "status": retire_status,
                            "invoke_as": [],
                        }
                    )
        for spec in codex_specs:
            installed.append(
                {
                    "surface": "codex",
                    "host_surfaces": ["codex-cli"],
                    "mechanism": "unsupported_native_slash_registry",
                    "command": spec["command"],
                    "path": None,
                    "status": "unsupported_host_surface",
                    "invoke_as": [],
                    "reason": (
                        "Current Codex does not support user-defined native top-level slash "
                        "commands. Use explicit skills instead."
                    ),
                    "native_registry_supported": False,
                    "failure_policy": "fail_closed_to_explicit_skill",
                    "fallback": (
                        f"Use `${spec['name']}` or `/skills` to explicitly invoke the LoopX "
                        "command skill; for the visible TUI loop, run "
                        "`loopx codex-cli-bootstrap-message --project .`, paste the setup "
                        "message, then set `/goal <thin task_body>`."
                    ),
                }
            )

        if not uninstall:
            codex_reconciliation = retire_duplicate_managed_skills(
                skill_dir, execute=execute, retire_legacy_aliases=True,
            )

    if "claude-code" in effective_surfaces:
        skills_dir = claude_root / "skills"
        for spec in specs:
            path = skills_dir / str(spec["name"]) / "SKILL.md"
            if uninstall:
                status = _retire_status(path, execute=execute)
                installed.append(
                    {
                        "surface": "claude-code",
                        "mechanism": "claude_code_skills",
                        "command": spec["command"],
                        "path": str(path),
                        "status": status,
                        "invoke_as": [str(spec["command"])],
                    }
                )
                continue
            content = _skill_body(
                command=str(spec["command"]),
                title=f"LoopX {spec['command']}",
                description=str(spec["description"]),
                argument_hint=str(spec["argument_hint"]),
                instructions=list(spec["instructions"]),
                surface="claude-skills",
                front_matter_name=str(spec["name"]),
            )
            status = _target_status(path, content, execute=execute)
            installed.append(
                {
                    "surface": "claude-code",
                    "mechanism": "claude_code_skills",
                    "command": spec["command"],
                    "path": str(path),
                    "status": status,
                    "invoke_as": [str(spec["command"])],
                }
            )

    if "gemini" in effective_surfaces:
        # Gemini CLI discovers user skills from GEMINI_HOME/skills. Files are
        # written directly, not through `gemini skills install --consent`: that
        # command copies from a git URL or an existing local path and the host
        # owns the copy, so LoopX would lose the managed marker, the per-file
        # status and the dry run that every other surface reports — and it would
        # need the `gemini` binary on PATH to install a file it already has.
        _install_skill_facade(
            specs=specs,
            installed=installed,
            skills_dir=gemini_root / "skills",
            surface="gemini",
            host_surfaces=["gemini-cli"],
            mechanism="gemini_cli_skills",
            execute=execute,
            uninstall=uninstall,
        )

    if "agy" in effective_surfaces:
        # Antigravity CLI discovers global skills from the fixed
        # ~/.gemini/antigravity-cli/skills root using the documented flat
        # layout (one <name>.md per skill). The official docs describe no home
        # override, so LoopX offers none: installs target exactly that path,
        # and the root belongs to agy alone (Gemini CLI reads ~/.gemini/skills),
        # so the managed skill surfaces never collide across different hosts.
        _install_skill_facade(
            specs=specs,
            installed=installed,
            skills_dir=agy_root / "skills",
            surface="agy",
            host_surfaces=["agy"],
            mechanism="agy_cli_skills",
            execute=execute,
            uninstall=uninstall,
            flat=True,
        )

    if "kiro-cli" in effective_surfaces:
        # Kiro CLI discovers global skills from <KIRO_HOME>/skills/<name>/SKILL.md
        # (workspace skills live in .kiro/skills), and exposes every discovered
        # skill as a `/<skill-name>` slash command. KIRO_HOME is the host's own
        # override for that global root, so the resolver honours it: installing
        # into ~/.kiro while the active profile lives elsewhere would report a
        # success the running host never discovers. A same-named user prompt in
        # .kiro/prompts wins over a skill by Kiro's own resolution order; the
        # installer never touches the prompt directories.
        _install_skill_facade(
            specs=specs,
            installed=installed,
            skills_dir=kiro_root / "skills",
            surface="kiro-cli",
            host_surfaces=["kiro-cli"],
            mechanism="kiro_cli_skills",
            execute=execute,
            uninstall=uninstall,
            invoke_prefix="/",
        )
        # Kiro's default agent loads KIRO_HOME/settings/mcp.json, so the same
        # session that runs `/loopx` gets the typed should_run/claim/complete
        # tools without switching agents. Only the `loopx` key is touched.
        kiro_mcp_path = kiro_root / _KIRO_MCP_CONFIG_SUBPATH
        status = _merge_managed_mcp_json(
            config_path=kiro_mcp_path,
            marker_schema_version=KIRO_CLI_MCP_MARKER_SCHEMA_VERSION,
            invalid_status="blocked_invalid_kiro_cli_mcp_json",
            command_factory=lambda: _kiro_cli_mcp_command(),
            uninstall=uninstall,
            execute=execute,
        )
        installed.append(
            {
                "surface": "kiro-cli",
                "host_surfaces": ["kiro-cli"],
                "mechanism": "kiro_cli_mcp_server",
                "command": "loopx",
                "path": str(kiro_mcp_path),
                "status": status,
                "invoke_as": [],
            }
        )
        # The enforcing agent is opt-in, but uninstalling the surface always
        # retires it: a managed agent left pointing at a removed hook would run
        # every tool ungated, since a failed hook does not block in Kiro.
        if with_gated_agent or uninstall:
            gated_status = sync_gated_agent(
                kiro_root, uninstall=uninstall, execute=execute
            )
            gated_row = {
                "surface": "kiro-cli",
                "host_surfaces": ["kiro-cli"],
                "mechanism": "kiro_cli_gated_agent",
                "command": GATED_AGENT_LAUNCH,
                "path": str(gated_agent_path(kiro_root)),
                "status": gated_status,
                "invoke_as": [GATED_AGENT_LAUNCH],
            }
            if gated_status == "blocked_unverified_kiro_cli_version":
                gated_row["reason"] = (
                    "The gate's embedded preToolUse hook is verified on Kiro CLI "
                    "2.x only; this host would load the agent without the hook "
                    "and run every tool ungated."
                )
            installed.append(gated_row)

    if "cursor" in effective_surfaces:
        # Cursor reads SKILL.md from CURSOR_HOME/skills (its skill roots also
        # include .claude/skills and .codex/skills, but relying on another
        # host's directory would break the moment that host is uninstalled).
        _install_skill_facade(
            specs=specs,
            installed=installed,
            skills_dir=cursor_root / "skills",
            surface="cursor",
            host_surfaces=["cursor-agent"],
            mechanism="cursor_skills",
            execute=execute,
            uninstall=uninstall,
        )
        # `cursor-agent mcp` can list and enable servers but not add them, so
        # the entry is merged into CURSOR_HOME/mcp.json. Only our own `loopx`
        # key is touched — a user's other servers are left exactly as they are.
        status = _merge_managed_mcp_json(
            config_path=cursor_root / "mcp.json",
            marker_schema_version=CURSOR_MCP_MARKER_SCHEMA_VERSION,
            invalid_status="blocked_invalid_cursor_mcp_json",
            # Late-bound so tests can stub provisioning on the module.
            command_factory=lambda: _loopx_mcp_command(),
            uninstall=uninstall,
            execute=execute,
        )
        installed.append(
            {
                "surface": "cursor",
                "host_surfaces": ["cursor-agent"],
                "mechanism": "cursor_mcp_server",
                "command": "loopx",
                "path": str(cursor_root / "mcp.json"),
                "status": status,
                "invoke_as": [],
            }
        )

    if "zcode" in effective_surfaces:
        # ZCode discovers user skills from ZCODE_HOME/skills (default ~/.zcode/skills).
        _install_skill_facade(
            specs=specs,
            installed=installed,
            skills_dir=zcode_root / "skills",
            surface="zcode",
            host_surfaces=["zcode"],
            mechanism="zcode_skills",
            execute=execute,
            uninstall=uninstall,
            invoke_prefix="$",
        )

    if "opencode" in effective_surfaces:
        # OpenCode reads global skills from OPENCODE_CONFIG_DIR/skills. The
        # static command facade below stays as it is — a command is something
        # the user types, a skill is something the model can reach for itself.
        _install_skill_facade(
            specs=specs,
            installed=installed,
            skills_dir=opencode_root / "skills",
            surface="opencode",
            host_surfaces=["opencode"],
            mechanism="opencode_skills",
            execute=execute,
            uninstall=uninstall,
        )
        commands_dir = opencode_root / "commands"
        plugin_path = opencode_root / "plugins" / "loopx-goal.js"
        runtime_path = opencode_root / "loopx" / "goal-bridge-runtime.mjs"
        package_path = opencode_root / "package.json"
        plugin_content = plugin_source()
        runtime_content = runtime_source()

        bridge_preflight_blocked = False
        if with_goal_bridge and not uninstall:
            conflicts, invalid_configs = _opencode_direct_goal_plugin_conflicts(opencode_root)
            if invalid_configs:
                installed.append(
                    {
                        "surface": "opencode",
                        "host_surfaces": ["opencode"],
                        "mechanism": "opencode_goal_bridge",
                        "command": "/goal",
                        "path": str(plugin_path),
                        "status": "blocked_invalid_opencode_config",
                        "invoke_as": [],
                        "reason": (
                            "Repair the listed OpenCode JSON/JSONC config before installing "
                            "the bridge so direct plugin conflicts can be checked safely."
                        ),
                        "invalid_configs": invalid_configs,
                    }
                )
                bridge_preflight_blocked = True
            elif conflicts:
                installed.append(
                    {
                        "surface": "opencode",
                        "host_surfaces": ["opencode"],
                        "mechanism": "opencode_goal_bridge",
                        "command": "/goal",
                        "path": str(plugin_path),
                        "status": "blocked_conflicting_direct_plugin",
                        "invoke_as": [],
                        "reason": (
                            "Remove direct goal-plugin registration from the listed "
                            "OpenCode config, then rerun installation. The bridge imports "
                            "the pinned plugin and both must not be loaded independently."
                        ),
                        "conflicts": conflicts,
                    }
                )
                bridge_preflight_blocked = True
            else:
                user_owned_bridge_paths = [
                    str(path)
                    for path, content in (
                        (plugin_path, plugin_content),
                        (runtime_path, runtime_content),
                    )
                    if _target_status(path, content, execute=False)
                    == "skipped_user_file"
                ]
                if user_owned_bridge_paths:
                    installed.append(
                        {
                            "surface": "opencode",
                            "host_surfaces": ["opencode"],
                            "mechanism": "opencode_goal_bridge",
                            "command": "/goal",
                            "path": str(plugin_path),
                            "status": "blocked_user_owned_bridge_file",
                            "invoke_as": [],
                            "reason": (
                                "Move or rename the listed user-owned OpenCode bridge "
                                "files before installing LoopX so no partial bridge or "
                                "dependency update is applied."
                            ),
                            "conflicts": user_owned_bridge_paths,
                        }
                    )
                    bridge_preflight_blocked = True

            if not bridge_preflight_blocked:
                package_status = _target_package_dependencies(
                    package_path,
                    OPENCODE_GOAL_DEPENDENCIES,
                    execute=False,
                )
                if package_status == "blocked_invalid_user_package_json":
                    installed.append(
                        {
                            "surface": "opencode",
                            "host_surfaces": ["opencode"],
                            "mechanism": "opencode_goal_dependencies",
                            "command": "/goal",
                            "path": str(package_path),
                            "status": package_status,
                            "invoke_as": [],
                        }
                    )
                    bridge_preflight_blocked = True

        if with_goal_bridge and not bridge_preflight_blocked:
            if uninstall:
                for mechanism, path in (
                    ("opencode_goal_bridge", plugin_path),
                    ("opencode_goal_bridge_runtime", runtime_path),
                ):
                    installed.append(
                        {
                            "surface": "opencode",
                            "host_surfaces": ["opencode"],
                            "mechanism": mechanism,
                            "command": "/goal",
                            "path": str(path),
                            "status": _retire_status(path, execute=execute),
                            "invoke_as": ["/goal", "loopx_goal_activate"],
                        }
                    )
                installed.append(
                    {
                        "surface": "opencode",
                        "host_surfaces": ["opencode"],
                        "mechanism": "opencode_goal_dependencies",
                        "command": "/goal",
                        "path": str(package_path),
                        "status": "preserved_shared_dependencies",
                        "invoke_as": [],
                    }
                )
            else:
                package_status = _target_package_dependencies(
                    package_path,
                    OPENCODE_GOAL_DEPENDENCIES,
                    execute=execute,
                )
                installed.append(
                    {
                        "surface": "opencode",
                        "host_surfaces": ["opencode"],
                        "mechanism": "opencode_goal_dependencies",
                        "command": "/goal",
                        "path": str(package_path),
                        "status": package_status,
                        "invoke_as": [],
                    }
                )
                if package_status == "blocked_invalid_user_package_json":
                    bridge_preflight_blocked = True
                else:
                    for mechanism, path, content in (
                        ("opencode_goal_bridge_runtime", runtime_path, runtime_content),
                        ("opencode_goal_bridge", plugin_path, plugin_content),
                    ):
                        installed.append(
                            {
                                "surface": "opencode",
                                "host_surfaces": ["opencode"],
                                "mechanism": mechanism,
                                "command": "/goal",
                                "path": str(path),
                                "status": _target_status(path, content, execute=execute),
                                "invoke_as": ["/goal", "loopx_goal_activate"],
                            }
                        )

        if not bridge_preflight_blocked:
            for spec in specs:
                path = commands_dir / f"{spec['name']}.md"
                status = (
                    _retire_status(path, execute=execute)
                    if uninstall
                    else _target_status(
                        path,
                        _opencode_command_body(spec),
                        execute=execute,
                    )
                )
                installed.append(
                    {
                        "surface": "opencode",
                        "host_surfaces": ["opencode"],
                        "mechanism": "opencode_commands",
                        "command": spec["command"],
                        "path": str(path),
                        "status": status,
                        "invoke_as": [str(spec["command"])],
                    }
                )

    if "pi" in effective_surfaces:
        extension_path = _pi_extension_path(pi_extension_root, scope=pi_scope)
        runtime_path = _pi_runtime_path(pi_extension_root)
        extension_content = pi_extension_source()
        runtime_content = pi_runtime_source()
        if uninstall:
            # Uninstall stays per-file like every other surface: LoopX-managed
            # files are removed and a user-owned file is reported as skipped.
            # Aborting the whole scope instead would leave the managed adapter
            # loaded and remove the only supported way to uninstall it.
            for mechanism, path in (
                ("pi_goal_extension", extension_path),
                ("pi_goal_extension_runtime", runtime_path),
            ):
                installed.append(
                    {
                        "surface": "pi",
                        "host_surfaces": ["pi"],
                        "mechanism": mechanism,
                        "command": "/loopx",
                        "path": str(path),
                        "status": _retire_status(path, execute=execute),
                        "invoke_as": ["/loopx", "loopx_goal_activate"],
                    }
                )
        else:
            # The adapter and its loop runtime are one atomic delivery unit:
            # preflight both targets and fail closed with zero writes when any
            # target is a user-owned file, so a newly created managed adapter
            # can never import an unmanaged runtime that may lack the exports
            # it needs.
            user_owned_pi_paths = [
                str(path)
                for path, content in (
                    (extension_path, extension_content),
                    (runtime_path, runtime_content),
                )
                if _target_status(path, content, execute=False) == "skipped_user_file"
            ]
            if user_owned_pi_paths:
                installed.append(
                    {
                        "surface": "pi",
                        "host_surfaces": ["pi"],
                        "mechanism": "pi_goal_extension",
                        "command": "/loopx",
                        "path": str(extension_path),
                        "status": "blocked_user_owned_pi_file",
                        "invoke_as": ["/loopx", "loopx_goal_activate"],
                        "reason": (
                            "Move or rename the listed user-owned Pi files before "
                            "installing LoopX so the adapter and its loop runtime "
                            "are installed as one atomic unit; no Pi file was written."
                        ),
                        "conflicts": user_owned_pi_paths,
                    }
                )
            else:
                for mechanism, path, content in (
                    ("pi_goal_extension", extension_path, extension_content),
                    ("pi_goal_extension_runtime", runtime_path, runtime_content),
                ):
                    installed.append(
                        {
                            "surface": "pi",
                            "host_surfaces": ["pi"],
                            "mechanism": mechanism,
                            "command": "/loopx",
                            "path": str(path),
                            "status": _target_status(path, content, execute=execute),
                            "invoke_as": ["/loopx", "loopx_goal_activate"],
                        }
                    )

    status_counts: dict[str, int] = {}
    for item in installed:
        status = str(item["status"])
        status_counts[status] = status_counts.get(status, 0) + 1

    pi_target_note = (
        "the user agent dir <agent-dir>/extensions/loopx/ (scope=user)"
        if pi_scope == "user"
        else "the project's .pi/extensions/ (scope=project)"
    )
    return {
        "ok": not any(status.startswith("blocked_") for status in status_counts),
        "schema_version": SCHEMA_VERSION,
        "operation": "uninstall" if uninstall else "install",
        "execute": execute,
        "with_goal_bridge": with_goal_bridge,
        "with_gated_agent": with_gated_agent,
        "requested_surfaces": surfaces or ["all"],
        "effective_surfaces": effective_surfaces,
        "catalog_schema_version": build_slash_command_catalog(
            cli_bin=cli_bin,
            include_legacy_aliases=include_legacy_aliases,
        )["schema_version"],
        "summary": {
            "codex_prompt_dir": None,
            "codex_skill_dir": str(codex_root / "skills") if "codex" in effective_surfaces else None,
            "claude_skill_dir": str(claude_root / "skills") if "claude-code" in effective_surfaces else None,
            "gemini_skill_dir": str(gemini_root / "skills") if "gemini" in effective_surfaces else None,
            "cursor_skill_dir": str(cursor_root / "skills") if "cursor" in effective_surfaces else None,
            "cursor_mcp_path": str(cursor_root / "mcp.json") if "cursor" in effective_surfaces else None,
            "zcode_skill_dir": str(zcode_root / "skills") if "zcode" in effective_surfaces else None,
            "agy_skill_dir": str(agy_root / "skills") if "agy" in effective_surfaces else None,
            "kiro_cli_skill_dir": str(kiro_root / "skills") if "kiro-cli" in effective_surfaces else None,
            "kiro_cli_mcp_path": str(kiro_root / _KIRO_MCP_CONFIG_SUBPATH) if "kiro-cli" in effective_surfaces else None,
            "kiro_cli_gated_agent_path": str(gated_agent_path(kiro_root)) if "kiro-cli" in effective_surfaces and (with_gated_agent or uninstall) else None,
            "opencode_skill_dir": str(opencode_root / "skills") if "opencode" in effective_surfaces else None,
            "opencode_command_dir": str(opencode_root / "commands") if "opencode" in effective_surfaces else None,
            "opencode_plugin_path": str(opencode_root / "plugins" / "loopx-goal.js") if "opencode" in effective_surfaces and with_goal_bridge else None,
            "opencode_package_path": str(opencode_root / "package.json") if "opencode" in effective_surfaces and with_goal_bridge else None,
            "pi_scope": pi_scope if "pi" in effective_surfaces else None,
            "pi_extension_path": str(_pi_extension_path(pi_extension_root, scope=pi_scope)) if "pi" in effective_surfaces else None,
            "pi_runtime_path": str(_pi_runtime_path(pi_extension_root)) if "pi" in effective_surfaces else None,
            "status_counts": status_counts,
            "skip_policy": (
                "Uninstall removes only LoopX-managed files; user files without a LoopX managed marker are preserved"
                if uninstall
                else "LoopX-managed files are upgraded; same-name user files without a LoopX managed marker or legacy signature are never overwritten"
            ),
        },
        "installed": installed,
        "codex_skill_reconciliation": codex_reconciliation,
        "notes": [
            "Codex does not currently support user-defined native top-level slash commands; use explicit skill invocation through `$loopx` or `/skills`.",
            "Explicit LoopX command-facade skills use agents/openai.yaml policy allow_implicit_invocation=false and remain distinct from richer workflow skills such as loopx-project.",
            "Claude Code discovers user skills from CLAUDE_HOME/skills and exposes each skill name as a slash command.",
            "Gemini CLI discovers user skills from GEMINI_HOME/skills with the same SKILL.md front matter; files are written directly because `gemini skills install` copies from a git URL or an existing local path and hands the copy to the host, which would lose the managed marker, per-file status and dry-run reporting every other surface has.",
            "Cursor discovers skills from CURSOR_HOME/skills and has no user-defined slash commands, so the cursor surface installs the skill facade and registers the LoopX MCP server in CURSOR_HOME/mcp.json; run `cursor-agent mcp enable loopx` once to approve it.",
            "ZCode discovers user skills from ZCODE_HOME/skills (default ~/.zcode/skills) and exposes each skill for invocation via `$skill-name` or Settings -> Skills.",
            "Antigravity CLI discovers global skills from the fixed ~/.gemini/antigravity-cli/skills root using the documented flat layout (one <name>.md per skill); the agy surface is opt-in and offers no home override because the host documents none.",
            f"Kiro CLI discovers global skills from {_KIRO_SKILLS_ROOT_LABEL}/<name>/SKILL.md (default ~/.kiro/skills) and exposes each as a `/<skill-name>` slash command; the kiro-cli surface is opt-in and resolves KIRO_HOME so install and uninstall target the profile the running host reads. Kiro resolves .kiro/prompts and KIRO_HOME/prompts before skills, so a same-named user prompt shadows the managed skill. The kiro-cli surface also registers the LoopX MCP server in KIRO_HOME/settings/mcp.json; it acts only for a session whose KIRO_SESSION_ID start-goal bound to a registered agent. --with-gated-agent also installs the `loopx` agent (`kiro-cli chat --agent loopx`), whose preToolUse hook denies state-changing tool calls unless `quota should-run` allows them; uninstalling the surface retires it.",
            "OpenCode discovers global skills from OPENCODE_CONFIG_DIR/skills in addition to the static command facade; a command is typed by the user, a skill can be reached by the model itself.",
            "The default all surface installs only OpenCode's static command facade; the executable goal bridge requires --with-goal-bridge.",
            f"The Pi surface is opt-in and installs the self-contained goal extension and its loop runtime into {pi_target_note}; it is not part of the default all surface.",
            "The OpenCode goal bridge uses Bun-managed config-directory dependencies and must replace any direct goal-plugin registration.",
            "OpenCode bridge uninstall preserves package.json dependencies because they may be shared by user-owned local plugins.",
            "Uninstall is fail-closed: it retires only files carrying the LoopX managed marker and leaves user-owned files in place.",
        ],
    }


def render_slash_command_install_markdown(payload: dict[str, Any]) -> str:
    operation = str(payload.get("operation") or "install")
    lines = [
        "# LoopX Slash Command Uninstall" if operation == "uninstall" else "# LoopX Slash Command Install",
        "",
        f"- operation: `{operation}`",
        f"- execute: `{payload.get('execute')}`",
        f"- surfaces: `{','.join(payload.get('effective_surfaces') or [])}`",
        f"- skip policy: `{payload.get('summary', {}).get('skip_policy')}`",
    ]
    codex_prompt_dir = payload.get("summary", {}).get("codex_prompt_dir")
    codex_skill_dir = payload.get("summary", {}).get("codex_skill_dir")
    claude_skill_dir = payload.get("summary", {}).get("claude_skill_dir")
    gemini_skill_dir = payload.get("summary", {}).get("gemini_skill_dir")
    cursor_skill_dir = payload.get("summary", {}).get("cursor_skill_dir")
    zcode_skill_dir = payload.get("summary", {}).get("zcode_skill_dir")
    kiro_cli_skill_dir = payload.get("summary", {}).get("kiro_cli_skill_dir")
    opencode_command_dir = payload.get("summary", {}).get("opencode_command_dir")
    opencode_plugin_path = payload.get("summary", {}).get("opencode_plugin_path")
    if codex_prompt_dir:
        lines.append(f"- codex prompts: `{codex_prompt_dir}`")
    if codex_skill_dir:
        lines.append(f"- codex skills: `{codex_skill_dir}`")
    if claude_skill_dir:
        lines.append(f"- claude skills: `{claude_skill_dir}`")
    if gemini_skill_dir:
        lines.append(f"- gemini skills: `{gemini_skill_dir}`")
    if cursor_skill_dir:
        lines.append(f"- cursor skills: `{cursor_skill_dir}`")
    if zcode_skill_dir:
        lines.append(f"- zcode skills: `{zcode_skill_dir}`")
    if kiro_cli_skill_dir:
        lines.append(f"- kiro-cli skills: `{kiro_cli_skill_dir}`")
    if opencode_command_dir:
        lines.append(f"- opencode commands: `{opencode_command_dir}`")
    if opencode_plugin_path:
        lines.append(f"- opencode bridge: `{opencode_plugin_path}`")
    pi_extension_path = payload.get("summary", {}).get("pi_extension_path")
    if pi_extension_path:
        lines.append(f"- pi extension: `{pi_extension_path}`")
    pi_runtime_path = payload.get("summary", {}).get("pi_runtime_path")
    if pi_runtime_path:
        lines.append(f"- pi loop runtime: `{pi_runtime_path}`")
    counts = payload.get("summary", {}).get("status_counts") or {}
    if isinstance(counts, dict) and counts:
        count_text = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        lines.append(f"- statuses: `{count_text}`")
    skipped = [
        item for item in payload.get("installed") or []
        if isinstance(item, dict) and item.get("status") == "skipped_user_file"
    ]
    if skipped:
        lines.append("")
        lines.append("Skipped user-owned files:")
        for item in skipped:
            lines.append(f"- `{item.get('command')}` at `{item.get('path')}`")
    notes = [note for note in payload.get("notes") or [] if isinstance(note, str)]
    if notes:
        lines.append("")
        lines.append("Notes:")
        for note in notes:
            lines.append(f"- {note}")
    lines.append("")
    lines.append("Restart the host if its slash-command menu was already open.")
    return "\n".join(lines)
