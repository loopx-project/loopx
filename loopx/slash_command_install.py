from __future__ import annotations

import contextlib
import os
import re
import sys
from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path

from .agy_goal_mode import (
    ANTIGRAVITY_HOOKS_CONFIG_FILENAME,
    ANTIGRAVITY_MCP_CONFIG_FILENAME,
    agy_home as _agy_home,
    antigravity_config_home as _antigravity_config_home,
    build_antigravity_hooks_template,
)
from .hosts.slash_command_config_io import (
    merge_managed_json_entry as _merge_managed_json_entry,
    read_managed_json_entry as _read_managed_json_entry,
)
from .hosts.slash_command_surfaces import (
    install_codex_surface as _install_codex_surface,
    install_opencode_surface as _install_opencode_surface,
    install_pi_surface as _install_pi_surface,
    installed_entry as _installed_entry,
)
from .hosts.kiro_cli import (
    KIRO_CLI_MCP_CONFIG_SUBPATH as _KIRO_MCP_CONFIG_SUBPATH,
    SKILLS_ROOT_LABEL as _KIRO_SKILLS_ROOT_LABEL,
    kiro_home as _kiro_home,
    mcp_server_script as _kiro_mcp_server_script,
)
from .hosts.pi import extension_source as pi_extension_source
from .hosts.pi import runtime_source as pi_runtime_source
from .hosts.pi.installation import (
    _pi_agent_dir,
    _pi_extension_path,
    _pi_extension_root,
    _pi_runtime_path,
)
from .slash_command_files import (
    CommandFacadeSpec,
    MANAGED_MARKER_PREFIX,
    front_matter as _front_matter,
    install_skill_facade as _install_skill_facade,
    managed_marker as _managed_marker,
    skill_body as _skill_body,
    skill_facade_content,
    target_status as _target_status,
)
from .slash_commands import build_slash_command_catalog
from .zcode_goal_mode import zcode_home as _zcode_home

SCHEMA_VERSION = "loopx_slash_command_install_v0"
OPENCODE_GOAL_DEPENDENCIES = {
    "@opencode-ai/plugin": ">=1.17.15 <2",
    "opencode-goal-plugin": "0.7.0",
}
_OPENCODE_DIRECT_GOAL_PLUGINS = frozenset(
    {
        "opencode-goal-plugin",
        "@heimoshuiyu/opencode-goal-plugin",
        "@prevalentware/opencode-goal-plugin",
    }
)


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


def _opencode_command_body(spec: CommandFacadeSpec) -> str:
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
        "arguments, and never infer a route from issue/PR wording or URLs. "
        "Use `--new-peer` only when the user explicitly asks for a fresh, new, "
        "second, or additional Agent identity rather than continuing or taking "
        "over an existing lane. A stable unbound host thread already defaults to "
        "fresh registration; on a host without a stable thread id, pass "
        "`--new-peer` as a separate argument before `--slash-command-arguments`, "
        "or preserve it when supplied as a leading visible switch. Do not infer "
        "a fresh identity from ordinary task wording, registry order, or the "
        "availability of another lane."
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


def _command_prompt_specs(
    *,
    cli_bin: str,
    include_legacy_aliases: bool,
    host_surface: str | None = None,
) -> list[CommandFacadeSpec]:
    codex_heartbeat_instruction = (
        "For a Codex App heartbeat, run the returned activation command, require ok=true, and save its `LoopX managed heartbeat bootstrap v2` task_body through automation_update. The saved loader fetches the current thin contract on every wake; do not persist a raw thin/compact/full execution body. Preserve the current goal, registered agent, task binding and existing schedule; read back the automation through the same App."
        if host_surface in {None, "codex", "codex-cli", "codex-ide-plugin", "codex-app", "codex-app-ssh"}
        else None
    )
    antigravity_resident_instruction = (
        f"For an Antigravity App fixed resident session (`antigravity-app` / `agy`), read `<user_information>` `Conversation ID` (or `$ANTIGRAVITY_CONVERSATION_ID`), bind it once via `{cli_bin} bind-agent-thread --goal-id <goal-id> --thread-id <conversation-id> --host-surface agy --agent-id <agent-id> --with-agy-hooks --execute` and `{cli_bin} sync-global` (updating `~/.gemini/config/hooks.json` `Stop` recovery with the active `{cli_bin}` path and `--conversation-id`; do not create a `new-conversation` `sidecar.json` for a fixed session). On every wake, run `{cli_bin} --format json heartbeat-prompt --thin --runtime-profile antigravity_app --goal-id <goal-id> --agent-id <agent-id>`, follow `task_body` only when `ok=true`, perform peer code/doc edits and `quota should-run` inside an isolated `.worktrees/` git worktree (keeping the main checkout clean for `workspace_guard`), and before ending every turn (settled, `waiting_external`, or idle) unless stopped, call `schedule(DurationSeconds=max(180, recommended_interval_minutes * 60), TimerCondition=\"any\", Prompt=...)` (`180`s floor; `3 -> 6 -> 10`m backoff) without shell `sleep`."
        if host_surface in {"agy", "antigravity-app"}
        else None
    )
    specs: list[CommandFacadeSpec] = [
        {
            "command": "/loopx",
            "name": "loopx",
            "description": "Inspect LoopX state, or start concrete project work when arguments are provided.",
            "argument_hint": "[--fine-grained] [--new-peer] [--capability-route issue-fix] [task text]",
            "instructions": [
                line
                for line in [
                    "Visible command arguments: `$ARGUMENTS`.",
                    "Identify the exact current host surface (codex-app, trae_app, codex-app-ssh, codex-ide-plugin, codex-cli-tui, opencode, opencode2, traex-cli, pi, gemini-cli, cursor-agent, zcode, antigravity-app, agy, kiro-cli, deepseek-harness, or ark-managed-agent).",
                    _loopx_start_goal_arguments_instruction(
                        cli_bin=cli_bin,
                        host_surface=None,
                    ),
                    "Treat the returned `ordered_steps` and `goal_start_contract` as authoritative. Follow their identity, capability-route, Todo, writeback, host-loop, quota, and stop/gate rules before substantive work; do not reconstruct those rules from skill memory.",
                    "During first planning, follow the shared goal_requirements_guidance: reuse existing owner-approved requirements and declare only missing source-grounded overall requirements through the existing outcome owner, link actual Todos and read back the canonical declaration. Preserve existing requirements on resume; maximum timeout is not a minimum work commitment and independent executable checks stay optional.",
                    "When the host explicitly supplies a `loopx_task_planning_v0` packet from `loopx todo plan` for a registered Goal/Agent, execute that bounded planning checkpoint instead of starting another Goal. Follow its shared planner, requirement declaration/readback and Todo delta in the same invocation, then return actual Todo ids for readback. Its caller-owned execution_handoff retains host activation and quota; do not create a planning Todo, execute task work, or claim delivery during the checkpoint.",
                    codex_heartbeat_instruction,
                    antigravity_resident_instruction,
                    "If the packet exposes a goal-selection gate, rerun one exact choice before any mutation.",
                    "When authoring task Todos, treat `--action-kind` as the documented extensible public-safe token: choose a short task-relevant value such as `implement`, `test`, or `review`; do not search the LoopX source for an allowlist.",
                    "Consume the turn-start quota JSON packet exactly once: read the complete output directly or save it and query it with `jq`; never pipe it through `head` or `tail`, and never rerun the turn-start call to recover hidden fields. A host whose runtime mints Turn identity uses `--begin-turn`; every other host passes its own `--turn-instance-id`. When selection is required, choose the Todo and use `interaction_contract.cli_channel.selection_command` with the returned Turn identity before mutation.",
                    "Runtime capability flags are host observations, not task requirements or grants; registered Agents reuse supported ones via `loopx agent-capabilities`. Before initial quota, include capabilities already established by this host context or successful task-facing use. Read capability_gate.repair_missing even when should_run is true: when runtime_capability_reentry is projected, verify its real callsite and follow the returned same-Turn command before choosing fallback work. Never infer credentials or production access from network availability, and do not claim a missing declaration proves a missing tool.",
                    f"If arguments are empty and the host already identifies an active LoopX goal, follow its exact CLI `interaction_contract` or quota command first; otherwise inspect `{cli_bin} status` and `{cli_bin} bootstrap-command-pack --project .` before changing files.",
                    "If this session cannot mutate the host loop surface, surface the exact pasteable gate instead of claiming autonomous setup.",
                ]
                if line is not None
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
        catalog = build_slash_command_catalog(cli_bin=cli_bin, include_legacy_aliases=True)
        aliases = {row["command"]: row.get("legacy_aliases", []) for row in catalog["commands"]}
        legacy_specs: list[CommandFacadeSpec] = []
        for canonical in specs:
            for alias in aliases.get(canonical["command"], []):
                legacy_specs.append({
                    **canonical,
                    "command": alias,
                    "name": alias.removeprefix("/"),
                    "description": canonical["description"] + f" Legacy alias for {canonical['command']}.",
                    "alias_for": canonical["command"],
                })
        specs.extend(legacy_specs)
    return specs


def _command_skill_content(spec: CommandFacadeSpec, *, surface: str) -> str:
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


class SkillFacadeReadbackStatus(str, Enum):
    """Local filesystem diagnostics; these do not classify host readiness."""

    READY = "ready"
    MISSING = "missing"
    STALE = "stale"
    USER_OWNED = "user_owned"
    UNREADABLE = "unreadable"


_MAX_SKILL_FACADE_READ_BYTES = 1024 * 1024


def inspect_skill_facades(
    skills_dir: Path,
    *,
    host_surface: str | None = None,
) -> dict[str, dict[str, object]]:
    """Read the canonical facades without changing files or running a host.

    Compare the installer's current rendering, allowing its existing cli_bin
    parameter to vary consistently. No independent phrase list defines freshness.
    """
    cli_placeholder = "__LOOPX_FACADE_CLI_PARAMETER__"
    summaries: dict[str, dict[str, object]] = {}
    for spec in _command_prompt_specs(
        cli_bin=cli_placeholder,
        include_legacy_aliases=False,
        host_surface=host_surface,
    ):
        name = str(spec["name"])
        path = skills_dir / name / "SKILL.md"
        status = SkillFacadeReadbackStatus.MISSING
        reason = "The managed command facade is missing; refresh this host's surface."
        exists = False
        configured_cli_bin = None
        try:
            with path.open("rb") as handle:
                content = handle.read(_MAX_SKILL_FACADE_READ_BYTES + 1)
            exists = True
            if len(content) > _MAX_SKILL_FACADE_READ_BYTES:
                status = SkillFacadeReadbackStatus.UNREADABLE
                reason = (
                    "The command facade exceeds the 1 MiB diagnostic read limit; "
                    "reduce its size or resolve the command-name collision. The file is preserved."
                )
                text = None
            else:
                text = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            if text is None:
                pass
            elif MANAGED_MARKER_PREFIX not in text:
                status = SkillFacadeReadbackStatus.USER_OWNED
                reason = (
                    "A user-owned file occupies this command; the installer preserves it. "
                    "Resolve the name collision before installing the managed facade."
                )
            else:
                template = skill_facade_content(spec)
                parts = template.split(cli_placeholder)
                pattern = re.escape(parts[0])
                for index, part in enumerate(parts[1:]):
                    pattern += (
                        r"(?P<cli_bin>[^\r\n]+?)" if index == 0 else r"(?P=cli_bin)"
                    ) + re.escape(part)
                match = re.fullmatch(pattern, text)
                if match is not None:
                    status = SkillFacadeReadbackStatus.READY
                    configured_cli_bin = match.groupdict().get("cli_bin")
                    reason = "The file matches the current managed facade; runtime skill loading is unverified."
                else:
                    status = SkillFacadeReadbackStatus.STALE
                    reason = "The managed facade differs from the current installer; refresh this host's surface."
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            exists = True
            status = SkillFacadeReadbackStatus.UNREADABLE
            reason = "The command facade cannot be read as UTF-8; check its file type and read access."
        ready = status is SkillFacadeReadbackStatus.READY
        summaries[name] = {
            "path": str(path),
            "candidate_paths": [str(path)] if exists else [],
            "route_count": int(exists),
            "route_conflict": False,
            "source_root": str(skills_dir) if exists else None,
            "managed_externally": False,
            "exists": exists,
            "required_phrases": ready,
            "readback_status": status.value,
            "reason": reason,
            "cli_bin": configured_cli_bin,
        }
    return summaries


def materialize_loopx_entry_skill(
    *,
    skills_dir: Path,
    execute: bool,
    cli_bin: str = "loopx",
    host_surface: str | None = None,
) -> dict[str, str]:
    """Materialize the generated ``$loopx`` entry skill into a host skill root."""

    if host_surface not in {
        None,
        "ark-managed-agent",
        "deepseek-harness-native",
        "agy",
        "antigravity-app",
    }:
        raise ValueError(f"unsupported fixed LoopX entry host surface: {host_surface}")
    spec = next(
        item
        for item in _command_prompt_specs(
            cli_bin=cli_bin,
            include_legacy_aliases=False,
            host_surface=host_surface,
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


def _env_home(env_var: str, default_dir: str, value: str | None = None) -> Path:
    raw = value or os.environ.get(env_var) or str(Path.home() / default_dir)
    return Path(raw).expanduser()


def _opencode_home(value: str | None = None) -> Path:
    raw = value or os.environ.get("OPENCODE_CONFIG_DIR")
    if not raw:
        config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        raw = str(Path(config_home) / "opencode")
    return Path(raw).expanduser()


_SURFACE_ALIASES: dict[str, tuple[str, ...]] = {
    "all": ("codex", "claude-code", "opencode"),
    "codex": ("codex",),
    "codex-app": ("codex",),
    "codex-app-ssh": ("codex",),
    "codex-ide-plugin": ("codex",),
    "codex-ide": ("codex",),
    "codex-cli": ("codex",),
    "gemini-cli": ("gemini",),
    "gemini-code": ("gemini",),
    "cursor-agent": ("cursor",),
    "cursor-cli": ("cursor",),
    "zcode": ("zcode",),
    "z-code": ("zcode",),
    "agy": ("agy",),
    "antigravity": ("agy",),
    "antigravity-cli": ("agy",),
    "antigravity-app": ("antigravity-app",),
    "antigravity_app": ("antigravity-app",),
    "agy-app": ("antigravity-app",),
    "agy_app": ("antigravity-app",),
    "kiro": ("kiro-cli",),
    "kiro-cli": ("kiro-cli",),
    "kirocli": ("kiro-cli",),
}


def _normalize_surfaces(surfaces: list[str] | None) -> list[str]:
    requested = surfaces or ["all"]
    normalized: list[str] = []
    for surface in requested:
        for candidate in _SURFACE_ALIASES.get(surface, (surface,)):
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
ANTIGRAVITY_MCP_MARKER_SCHEMA_VERSION = "loopx_managed_antigravity_mcp_v0"
ANTIGRAVITY_HOOKS_MARKER_NAME = ".loopx-managed-hooks.json"
ANTIGRAVITY_HOOKS_MARKER_SCHEMA_VERSION = "loopx_managed_antigravity_hooks_v0"

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
    return _mcp_command_for_script(_kiro_mcp_server_script())


def _managed_kiro_mcp_needs_migration(config_path: Path) -> bool:
    """Return True when a sidecar-verified LoopX entry points away from the current Kiro script."""
    existing = _read_managed_json_entry(
        config_path=config_path,
        marker_path=config_path.parent / CURSOR_MCP_MARKER_NAME,
        marker_section="servers",
        marker_schema_version=KIRO_CLI_MCP_MARKER_SCHEMA_VERSION,
        entry_key=MANAGED_MCP_KEY,
        container_key="mcpServers",
    )
    if existing is None:
        return False
    return existing.get("args") != [str(_kiro_mcp_server_script())]


def _merge_managed_mcp_json(
    *,
    config_path: Path,
    marker_schema_version: str,
    invalid_status: str,
    command_factory: McpCommandFactory,
    uninstall: bool,
    execute: bool,
) -> str:
    """Add or remove the ``loopx`` entry in a host's ``mcpServers`` JSON — and nothing else.

    The config is a user-owned file that LoopX did not create, so every path
    here fails closed: an unexpected shape is reported instead of normalized, a
    same-name entry LoopX cannot prove it wrote is left alone, and uninstall
    removes only an entry that still matches what LoopX recorded. Sharing the
    ``loopx`` name with a user's own server is unlikely but cheap to survive;
    silently replacing that server, or dropping their ``mcpServers`` list while
    "merging", is not.
    """

    def entry_factory() -> object | None:
        command = command_factory()
        return None if command is None else {"command": command[0], "args": [command[1]]}

    return _merge_managed_json_entry(
        config_path=config_path,
        marker_path=config_path.parent / CURSOR_MCP_MARKER_NAME,
        marker_section="servers",
        marker_schema_version=marker_schema_version,
        entry_key=MANAGED_MCP_KEY,
        container_key="mcpServers",
        legacy_container_key=None,
        invalid_status=invalid_status,
        user_owned_status="skipped_user_owned_mcp_entry",
        missing_dependency_status="skipped_mcp_dependency_missing",
        entry_factory=entry_factory,
        resolve_before_dry_run=False,
        uninstall=uninstall,
        execute=execute,
    )


def _merge_managed_hooks_json(
    *,
    config_path: Path,
    cli_bin: str,
    uninstall: bool,
    execute: bool,
    goal_id: str | None = None,
    agent_id: str | None = None,
    runtime_root: str | None = None,
    conversation_id: str | None = None,
    registry: str | None = None,
    resolve_local_executable: bool = True,
) -> str:
    """Add or remove the managed ``loopx`` Stop recovery hook in Antigravity ``hooks.json``."""
    return _merge_managed_json_entry(
        config_path=config_path,
        marker_path=config_path.parent / ANTIGRAVITY_HOOKS_MARKER_NAME,
        marker_section="hooks",
        marker_schema_version=ANTIGRAVITY_HOOKS_MARKER_SCHEMA_VERSION,
        entry_key=MANAGED_MCP_KEY,
        container_key=None,
        legacy_container_key="hooks",
        invalid_status="blocked_invalid_antigravity_hooks_json",
        user_owned_status="skipped_user_owned_hooks_entry",
        missing_dependency_status="skipped_mcp_dependency_missing",
        entry_factory=lambda: build_antigravity_hooks_template(
            cli_bin=cli_bin,
            goal_id=goal_id,
            agent_id=agent_id,
            runtime_root=runtime_root,
            conversation_id=conversation_id,
            registry=registry,
            resolve_local_executable=resolve_local_executable,
        )[MANAGED_MCP_KEY],
        resolve_before_dry_run=True,
        uninstall=uninstall,
        execute=execute,
    )


def sync_antigravity_hooks(
    *,
    cli_bin: str = "loopx",
    agy_home: str | None = None,
    uninstall: bool = False,
    execute: bool,
    goal_id: str | None = None,
    agent_id: str | None = None,
    runtime_root: str | None = None,
    conversation_id: str | None = None,
    registry: str | None = None,
    resolve_local_executable: bool = True,
) -> dict[str, str]:
    """Preview or update Antigravity ``hooks.json`` with the managed Stop recovery hook."""
    config_path = _antigravity_config_home(agy_home) / ANTIGRAVITY_HOOKS_CONFIG_FILENAME
    status = _merge_managed_hooks_json(
        config_path=config_path,
        cli_bin=cli_bin,
        uninstall=uninstall,
        execute=execute,
        goal_id=goal_id,
        agent_id=agent_id,
        runtime_root=runtime_root,
        conversation_id=conversation_id,
        registry=registry,
        resolve_local_executable=resolve_local_executable,
    )
    return {"path": str(config_path), "status": status}


_MARKDOWN_SUMMARY_FIELDS: tuple[tuple[str, str], ...] = (
    ("codex_prompt_dir", "codex prompts"),
    ("codex_skill_dir", "codex skills"),
    ("claude_skill_dir", "claude skills"),
    ("gemini_skill_dir", "gemini skills"),
    ("cursor_skill_dir", "cursor skills"),
    ("zcode_skill_dir", "zcode skills"),
    ("kiro_cli_skill_dir", "kiro-cli skills"),
    ("opencode_command_dir", "opencode commands"),
    ("opencode_plugin_path", "opencode bridge"),
    ("pi_extension_path", "pi extension"),
    ("pi_runtime_path", "pi loop runtime"),
)


def _install_managed_mcp_surface(
    *,
    installed: list[dict[str, object]],
    surface: str,
    host_surfaces: list[str],
    mechanism: str,
    config_path: Path,
    marker_schema_version: str,
    invalid_status: str,
    command_factory: McpCommandFactory,
    uninstall: bool,
    execute: bool,
) -> None:
    status = _merge_managed_mcp_json(
        config_path=config_path,
        marker_schema_version=marker_schema_version,
        invalid_status=invalid_status,
        command_factory=command_factory,
        uninstall=uninstall,
        execute=execute,
    )
    installed.append(
        _installed_entry(
            surface=surface,
            host_surfaces=host_surfaces,
            mechanism=mechanism,
            command="loopx",
            path=config_path,
            status=status,
        )
    )


def install_slash_commands(
    *,
    execute: bool,
    uninstall: bool = False,
    with_goal_bridge: bool = False,
    with_agy_mcp: bool = False,
    with_agy_hooks: bool = False,
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
    agy_goal_id: str | None = None,
    agy_agent_id: str | None = None,
    agy_conversation_id: str | None = None,
    agy_runtime_root: str | None = None,
    agy_registry: str | None = None,
    kiro_home: str | None = None,
    pi_project: str | None = None,
    pi_scope: str = "project",
    pi_user_home: str | None = None,
) -> dict[str, object]:
    facade_specs = _command_prompt_specs(cli_bin=cli_bin, include_legacy_aliases=True)
    agy_facade_specs = _command_prompt_specs(
        cli_bin=cli_bin,
        include_legacy_aliases=True,
        host_surface="antigravity-app",
    )
    canonical_specs = [spec for spec in facade_specs if "alias_for" not in spec]
    legacy_alias_specs = [spec for spec in facade_specs if "alias_for" in spec]
    specs = facade_specs if include_legacy_aliases else canonical_specs
    effective_surfaces = _normalize_surfaces(surfaces)
    codex_root = _env_home("CODEX_HOME", ".codex", codex_home)
    claude_root = _env_home("CLAUDE_HOME", ".claude", claude_home)
    opencode_root = _opencode_home(opencode_home)
    gemini_root = _env_home("GEMINI_HOME", ".gemini", gemini_home)
    cursor_root = _env_home("CURSOR_HOME", ".cursor", cursor_home)
    zcode_root = _zcode_home(zcode_home or zcode_agents_home)
    agy_root = _agy_home(agy_home)
    agy_config_root = _antigravity_config_home(agy_home)
    kiro_root = _kiro_home(kiro_home)
    if pi_scope not in {"project", "user"}:
        raise ValueError("pi_scope must be 'project' or 'user'")
    pi_project_root = Path(pi_project or ".").expanduser().resolve()
    pi_extension_root = _pi_extension_root(
        pi_project_root, scope=pi_scope, agent_dir=_pi_agent_dir(pi_user_home)
    )
    installed: list[dict[str, object]] = []

    if with_goal_bridge and "opencode" not in effective_surfaces:
        installed.append(
            _installed_entry(
                surface="opencode",
                host_surfaces=["opencode"],
                mechanism="opencode_goal_bridge",
                command="/goal",
                path=None,
                status="blocked_goal_bridge_requires_opencode_surface",
                reason="Select --surface opencode when using --with-goal-bridge.",
            )
        )

    codex_reconciliation = (
        _install_codex_surface(
            installed=installed,
            codex_root=codex_root,
            canonical_specs=canonical_specs,
            legacy_alias_specs=legacy_alias_specs,
            execute=execute,
            uninstall=uninstall,
            render_skill_content=lambda spec: _command_skill_content(
                spec, surface="codex-skills"
            ),
            render_openai_metadata=_openai_skill_metadata,
        )
        if "codex" in effective_surfaces
        else None
    )

    if "claude-code" in effective_surfaces:
        _install_skill_facade(
            specs=facade_specs,
            installed=installed,
            skills_dir=claude_root / "skills",
            surface="claude-code",
            host_surfaces=["claude-code"],
            mechanism="claude_code_skills",
            execute=execute,
            uninstall=uninstall,
            invoke_prefix="/",
        )

    if "gemini" in effective_surfaces:
        # Gemini CLI discovers user skills from GEMINI_HOME/skills. Files are
        # written directly, not through `gemini skills install --consent`: that
        # command copies from a git URL or an existing local path and the host
        # owns the copy, so LoopX would lose the managed marker, the per-file
        # status and the dry run that every other surface reports — and it would
        # need the `gemini` binary on PATH to install a file it already has.
        _install_skill_facade(
            specs=facade_specs,
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
        # layout (one <name>.md per skill). When --agy-home is not explicitly
        # overriding the root, also populate Antigravity 2.0's directory-based
        # ~/.gemini/config/skills/<name>/SKILL.md root so both Antigravity App
        # and legacy CLI work out of the box.
        _install_skill_facade(
            specs=agy_facade_specs,
            installed=installed,
            skills_dir=agy_root / "skills",
            surface="agy",
            host_surfaces=["agy"],
            mechanism="agy_cli_skills",
            execute=execute,
            uninstall=uninstall,
            flat=True,
        )
        if agy_home is None and "antigravity-app" not in effective_surfaces:
            _install_skill_facade(
                specs=agy_facade_specs,
                installed=installed,
                skills_dir=agy_config_root / "skills",
                surface="antigravity-app",
                host_surfaces=["antigravity-app", "agy"],
                mechanism="antigravity_app_skills",
                execute=execute,
                uninstall=uninstall,
                invoke_prefix="/",
            )

    if "antigravity-app" in effective_surfaces:
        _install_skill_facade(
            specs=agy_facade_specs,
            installed=installed,
            skills_dir=agy_config_root / "skills",
            surface="antigravity-app",
            host_surfaces=["antigravity-app", "agy"],
            mechanism="antigravity_app_skills",
            execute=execute,
            uninstall=uninstall,
            invoke_prefix="/",
        )

    agy_selected = "antigravity-app" in effective_surfaces or "agy" in effective_surfaces
    if agy_selected and with_agy_mcp:
        _install_managed_mcp_surface(
            installed=installed,
            surface="antigravity-app",
            host_surfaces=["antigravity-app", "agy"],
            mechanism="antigravity_mcp_server",
            config_path=agy_config_root / ANTIGRAVITY_MCP_CONFIG_FILENAME,
            marker_schema_version=ANTIGRAVITY_MCP_MARKER_SCHEMA_VERSION,
            invalid_status="blocked_invalid_antigravity_mcp_json",
            command_factory=lambda: _loopx_mcp_command(),
            uninstall=uninstall,
            execute=execute,
        )

    if agy_selected and with_agy_hooks:
        agy_hooks_path = agy_config_root / ANTIGRAVITY_HOOKS_CONFIG_FILENAME
        hooks_status = _merge_managed_hooks_json(
            config_path=agy_hooks_path,
            cli_bin=cli_bin,
            uninstall=uninstall,
            execute=execute,
            goal_id=agy_goal_id,
            agent_id=agy_agent_id,
            runtime_root=agy_runtime_root,
            conversation_id=agy_conversation_id,
            registry=agy_registry,
        )
        installed.append(
            _installed_entry(
                surface="antigravity-app",
                host_surfaces=["antigravity-app", "agy"],
                mechanism="antigravity_stop_recovery_hook",
                command="loopx",
                path=agy_hooks_path,
                status=hooks_status,
            )
        )

    kiro_mcp_config_path = kiro_root / _KIRO_MCP_CONFIG_SUBPATH
    kiro_mcp_migrated = False
    if "kiro-cli" in effective_surfaces:
        _install_skill_facade(
            specs=facade_specs,
            installed=installed,
            skills_dir=kiro_root / "skills",
            surface="kiro-cli",
            host_surfaces=["kiro-cli"],
            mechanism="kiro_cli_skills",
            execute=execute,
            uninstall=uninstall,
            invoke_prefix="/",
        )
        _install_managed_mcp_surface(
            installed=installed,
            surface="kiro-cli",
            host_surfaces=["kiro-cli"],
            mechanism="kiro_cli_mcp_server",
            config_path=kiro_mcp_config_path,
            marker_schema_version=KIRO_CLI_MCP_MARKER_SCHEMA_VERSION,
            invalid_status="blocked_invalid_kiro_cli_mcp_json",
            command_factory=lambda: _kiro_cli_mcp_command(),
            uninstall=uninstall,
            execute=execute,
        )
    elif not uninstall and _managed_kiro_mcp_needs_migration(kiro_mcp_config_path):
        # Updater runs default `--install` (`surfaces=all`, without `kiro-cli`);
        # migrate proven managed Kiro MCP entries whose server path moved.
        kiro_mcp_migrated = True
        if execute:
            _install_managed_mcp_surface(
                installed=installed,
                surface="kiro-cli",
                host_surfaces=["kiro-cli"],
                mechanism="kiro_cli_mcp_server",
                config_path=kiro_mcp_config_path,
                marker_schema_version=KIRO_CLI_MCP_MARKER_SCHEMA_VERSION,
                invalid_status="blocked_invalid_kiro_cli_mcp_json",
                command_factory=lambda: _kiro_cli_mcp_command(),
                uninstall=False,
                execute=True,
            )
        else:
            installed.append(
                _installed_entry(
                    surface="kiro-cli",
                    host_surfaces=["kiro-cli"],
                    mechanism="kiro_cli_mcp_server",
                    command="loopx",
                    path=kiro_mcp_config_path,
                    status="would_write",
                )
            )

    if "cursor" in effective_surfaces:
        _install_skill_facade(
            specs=facade_specs,
            installed=installed,
            skills_dir=cursor_root / "skills",
            surface="cursor",
            host_surfaces=["cursor-agent"],
            mechanism="cursor_skills",
            execute=execute,
            uninstall=uninstall,
        )
        _install_managed_mcp_surface(
            installed=installed,
            surface="cursor",
            host_surfaces=["cursor-agent"],
            mechanism="cursor_mcp_server",
            config_path=cursor_root / "mcp.json",
            marker_schema_version=CURSOR_MCP_MARKER_SCHEMA_VERSION,
            invalid_status="blocked_invalid_cursor_mcp_json",
            # Late-bound so tests can stub provisioning on the module.
            command_factory=lambda: _loopx_mcp_command(),
            uninstall=uninstall,
            execute=execute,
        )

    if "zcode" in effective_surfaces:
        _install_skill_facade(
            specs=facade_specs,
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
        _install_opencode_surface(
            installed=installed,
            opencode_root=opencode_root,
            facade_specs=facade_specs,
            specs=specs,
            with_goal_bridge=with_goal_bridge,
            execute=execute,
            uninstall=uninstall,
            dependencies=OPENCODE_GOAL_DEPENDENCIES,
            direct_goal_plugins=_OPENCODE_DIRECT_GOAL_PLUGINS,
            render_command_body=_opencode_command_body,
        )

    if "pi" in effective_surfaces:
        _install_pi_surface(
            installed=installed,
            extension_path=_pi_extension_path(pi_extension_root, scope=pi_scope),
            runtime_path=_pi_runtime_path(pi_extension_root),
            extension_content=pi_extension_source(),
            runtime_content=pi_runtime_source(),
            execute=execute,
            uninstall=uninstall,
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
            "antigravity_app_skill_dir": (
                str(agy_config_root / "skills")
                if (
                    "antigravity-app" in effective_surfaces
                    or ("agy" in effective_surfaces and agy_home is None)
                )
                else None
            ),
            "antigravity_mcp_path": (
                str(agy_config_root / ANTIGRAVITY_MCP_CONFIG_FILENAME)
                if with_agy_mcp and agy_selected
                else None
            ),
            "antigravity_hooks_path": (
                str(agy_config_root / ANTIGRAVITY_HOOKS_CONFIG_FILENAME)
                if with_agy_hooks and agy_selected
                else None
            ),
            "kiro_cli_skill_dir": str(kiro_root / "skills") if "kiro-cli" in effective_surfaces else None,
            "kiro_cli_mcp_path": (
                str(kiro_mcp_config_path)
                if ("kiro-cli" in effective_surfaces or kiro_mcp_migrated)
                else None
            ),
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
            "Every host skill root installs canonical facades only and retires managed /loop-global-* alias skills. Use /loopx-global-* instead on skill-backed slash hosts, including Claude Code and Kiro. Only OpenCode retains independently installed native alias command files when legacy aliases are enabled; catalog aliases remain available.",
            "Explicit LoopX command-facade skills use agents/openai.yaml policy allow_implicit_invocation=false and remain distinct from richer workflow skills such as loopx-project.",
            "Claude Code discovers user skills from CLAUDE_HOME/skills and exposes each skill name as a slash command.",
            "Gemini CLI discovers user skills from GEMINI_HOME/skills with the same SKILL.md front matter; files are written directly because `gemini skills install` copies from a git URL or an existing local path and hands the copy to the host, which would lose the managed marker, per-file status and dry-run reporting every other surface has.",
            "Cursor discovers skills from CURSOR_HOME/skills and has no user-defined slash commands, so the cursor surface installs the skill facade and registers the LoopX MCP server in CURSOR_HOME/mcp.json; run `cursor-agent mcp enable loopx` once to approve it.",
            "ZCode discovers user skills from ZCODE_HOME/skills (default ~/.zcode/skills) and exposes each skill for invocation via `$skill-name` or Settings -> Skills.",
            "Antigravity CLI discovers global skills from the fixed ~/.gemini/antigravity-cli/skills root using the documented flat layout (one <name>.md per skill); the agy surface is opt-in and offers no home override because the host documents none.",
            f"Kiro CLI discovers global skills from {_KIRO_SKILLS_ROOT_LABEL}/<name>/SKILL.md (default ~/.kiro/skills) and exposes each as a `/<skill-name>` slash command; the kiro-cli surface is opt-in and resolves KIRO_HOME so install and uninstall target the profile the running host reads. Kiro resolves .kiro/prompts and KIRO_HOME/prompts before skills, so a same-named user prompt shadows the managed skill. The kiro-cli surface also registers the LoopX MCP server in KIRO_HOME/settings/mcp.json; it acts only for a session whose KIRO_SESSION_ID start-goal bound to a registered agent.",
            "OpenCode discovers global skills from OPENCODE_CONFIG_DIR/skills in addition to the static command facade; a command is typed by the user, a skill can be reached by the model itself.",
            "The default all surface installs only OpenCode's static command facade; the executable goal bridge requires --with-goal-bridge.",
            f"The Pi surface is opt-in and installs the self-contained goal extension and its loop runtime into {pi_target_note}; it is not part of the default all surface.",
            "The OpenCode goal bridge uses Bun-managed config-directory dependencies and must replace any direct goal-plugin registration.",
            "OpenCode bridge uninstall preserves package.json dependencies because they may be shared by user-owned local plugins.",
            "Uninstall is fail-closed: it retires only files carrying the LoopX managed marker and leaves user-owned files in place.",
        ],
    }


def render_slash_command_install_markdown(payload: Mapping[str, object]) -> str:
    operation = str(payload.get("operation") or "install")
    raw_surfaces = payload.get("effective_surfaces")
    surfaces = [str(item) for item in raw_surfaces] if isinstance(raw_surfaces, list) else []
    raw_summary = payload.get("summary")
    summary: Mapping[str, object] = raw_summary if isinstance(raw_summary, Mapping) else {}
    lines = [
        "# LoopX Slash Command Uninstall" if operation == "uninstall" else "# LoopX Slash Command Install",
        "",
        f"- operation: `{operation}`",
        f"- execute: `{payload.get('execute')}`",
        f"- surfaces: `{','.join(surfaces)}`",
        f"- skip policy: `{summary.get('skip_policy')}`",
    ]
    for key, label in _MARKDOWN_SUMMARY_FIELDS:
        value = summary.get(key)
        if value:
            lines.append(f"- {label}: `{value}`")
    counts = summary.get("status_counts")
    if isinstance(counts, Mapping) and counts:
        count_text = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        lines.append(f"- statuses: `{count_text}`")
    raw_installed = payload.get("installed")
    installed_rows = raw_installed if isinstance(raw_installed, list) else []
    skipped = [
        item for item in installed_rows
        if isinstance(item, Mapping) and item.get("status") == "skipped_user_file"
    ]
    if skipped:
        lines.append("")
        lines.append("Skipped user-owned files:")
        for item in skipped:
            lines.append(f"- `{item.get('command')}` at `{item.get('path')}`")
    raw_notes = payload.get("notes")
    notes = [note for note in raw_notes if isinstance(note, str)] if isinstance(raw_notes, list) else []
    if notes:
        lines.append("")
        lines.append("Notes:")
        for note in notes:
            lines.append(f"- {note}")
    lines.append("")
    lines.append("Restart the host if its slash-command menu was already open.")
    return "\n".join(lines)
