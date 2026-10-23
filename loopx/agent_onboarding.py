from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .agent_registry import registered_agent_ids_from_registry
from .bootstrap_command_pack import inspect_bootstrap_connection
from .capabilities.change_quality.policy import change_quality_goal_policy
from .host_loop_activation import (
    agent_type_uses_host_managed_skills,
    build_host_loop_activation_packet,
    normalize_agent_type,
    render_agent_type_catalog_markdown,
    scheduler_command_binding_for_agent_type,
)
from .history import load_registry
from .install_contract import NO_CLONE_INSTALL_URL
from .kiro_cli_goal_mode import (
    KIRO_CLI_GOAL_CLEAR_COMMAND,
    KIRO_CLI_GOAL_COMPLETION_TOOL,
    KIRO_CLI_GOAL_DEFAULT_MAX_ITERATIONS,
    kiro_cli_goal_invocation,
)
from .kiro_cli_goal_mode import (
    SKILLS_ROOT_LABEL as KIRO_CLI_SKILLS_ROOT_LABEL,
)
from .project_prompt import (
    host_command,
    render_available_capability_args,
    render_codex_cli_install_preflight,
    render_quota_guard_command,
    shell_arg,
    shell_arg_for_host,
)
from .registry import registry_goals
from .skill_install_readback import (
    ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS,
    configured_host_skills_dir,
    inspect_skill_install_readback,
)

SCHEMA_VERSION = "loopx_agent_onboarding_v0"
HOST_SKILL_DELIVERY_SCHEMA_VERSION = "loopx_host_skill_delivery_v0"
REQUIRED_HOST_SKILL_IDS = ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS
CHANGE_QUALITY_SKILL_ID = "loopx-change-quality"


def _surface_install_command(agent_type: str, cli_bin: str, project: str) -> str | None:
    if agent_type in {"codex-app", "codex-app-ssh", "codex-ide-plugin", "codex-cli"}:
        return f"{host_command(cli_bin)} slash-commands --install --surface codex"
    if agent_type == "claude-code":
        return f"{host_command(cli_bin)} slash-commands --install --surface claude-code"
    if agent_type == "opencode":
        return (
            f"{host_command(cli_bin)} slash-commands --install --surface opencode "
            "--with-goal-bridge"
        )
    if agent_type == "gemini-cli":
        return f"{host_command(cli_bin)} slash-commands --install --surface gemini"
    if agent_type == "cursor-agent":
        return f"{host_command(cli_bin)} slash-commands --install --surface cursor"
    if agent_type == "zcode":
        return f"{host_command(cli_bin)} slash-commands --install --surface zcode"
    if agent_type == "agy":
        return f"{host_command(cli_bin)} slash-commands --install --surface agy"
    if agent_type == "kiro-cli":
        return f"{host_command(cli_bin)} slash-commands --install --surface kiro-cli"
    if agent_type == "pi":
        # The slash-commands installer resolves the Pi extension target through
        # --pi-project; pass the resolved project so the command stays correct
        # when agent-onboard runs from any cwd.
        return (
            f"{host_command(cli_bin)} slash-commands --install --surface pi "
            f"--pi-project {shell_arg_for_host(project)}"
        )
    return None


def _project_skill_surface(agent_type: str) -> str | None:
    if agent_type in {
        "codex-app",
        "codex-app-ssh",
        "codex-ide-plugin",
        "codex-cli",
    }:
        return "codex"
    if agent_type == "claude-code":
        return "claude-code"
    if agent_type == "opencode":
        return "opencode"
    if agent_type == "pi":
        return "pi"
    return None


def _project_skill_command(
    command: str,
    *,
    project: str,
    skill_id: str,
    surface: str,
    cli_bin: str,
    execute: bool = False,
) -> str:
    parts = [
        host_command(cli_bin),
        "project-skill",
        command,
        "--project",
        shell_arg_for_host(project),
        "--skill",
        shell_arg_for_host(skill_id),
        "--surface",
        shell_arg_for_host(surface),
    ]
    if execute:
        parts.append("--execute")
    return " ".join(parts)


def _skill_delivery_contract(
    agent_type: str,
    *,
    project: str = ".",
    cli_bin: str = "loopx",
    active_project_skill_ids: list[str] | None = None,
    host_skills_dir: Path | None = None,
) -> dict[str, Any]:
    active_project_skills = list(dict.fromkeys(active_project_skill_ids or []))
    project_surface = _project_skill_surface(agent_type)
    project_skill_commands = []
    if project_surface:
        for skill_id in active_project_skills:
            project_skill_commands.append(
                {
                    "skill_id": skill_id,
                    "surface": project_surface,
                    "status": _project_skill_command(
                        "status",
                        project=project,
                        skill_id=skill_id,
                        surface=project_surface,
                        cli_bin=cli_bin,
                    ),
                    "preview_install": _project_skill_command(
                        "install",
                        project=project,
                        skill_id=skill_id,
                        surface=project_surface,
                        cli_bin=cli_bin,
                    ),
                    "apply_install": _project_skill_command(
                        "install",
                        project=project,
                        skill_id=skill_id,
                        surface=project_surface,
                        cli_bin=cli_bin,
                        execute=True,
                    ),
                }
            )
    if not agent_type_uses_host_managed_skills(agent_type):
        return {
            "schema_version": HOST_SKILL_DELIVERY_SCHEMA_VERSION,
            "mode": "surface_managed",
            "owner": "loopx_surface_installer",
            "codex_skills_root_required": agent_type.startswith("codex-"),
            "host_readback_required": False,
            "active_project_skill_ids": active_project_skills,
            "project_skill_commands": project_skill_commands,
        }
    required_skill_ids = [
        *REQUIRED_HOST_SKILL_IDS,
        *active_project_skills,
    ]
    ark_managed_agent = agent_type == "ark-managed-agent"
    dsh_native = agent_type == "deepseek-harness-native"
    filesystem_readback = (
        inspect_skill_install_readback(
            skills_dir=host_skills_dir,
            required_skill_ids=REQUIRED_HOST_SKILL_IDS,
            source_root=Path(__file__).resolve().parents[1],
        )
        if ark_managed_agent or dsh_native
        else None
    )
    return {
        "schema_version": HOST_SKILL_DELIVERY_SCHEMA_VERSION,
        "mode": "host_managed",
        "owner": (
            "loopx_install_script"
            if ark_managed_agent
            else "dsh_loopx_plugin"
            if dsh_native
            else "custom_agent_host"
        ),
        "status": (
            str(filesystem_readback["status"])
            if filesystem_readback
            else "pending_host_readback"
        ),
        "codex_skills_root_required": False,
        "required_for_cli_health": False,
        "required_for_loopx_workflow": True,
        "required_skill_ids": required_skill_ids,
        "active_project_skill_ids": active_project_skills,
        "delivery_options": [
            *(["fixed_install_script"] if ark_managed_agent else []),
            *(["dsh_loopx_init_command"] if dsh_native else []),
            "host_skill_manifest",
            "prompt_injection",
        ],
        **(
            {
                "preferred_delivery": "fixed_install_script",
                "onboarding_role": "read_only_verifier",
                "onboarding_required_for_install": False,
                "install_script": "scripts/install-local.sh",
                "no_clone_install_command": (
                    f"curl -fsSL {NO_CLONE_INSTALL_URL}"
                    " | env "
                    f"LOOPX_SKILLS_DIR={shell_arg(f'{project}/.agents/skills')} "
                    "LOOPX_ENTRY_HOST_SURFACE=ark-managed-agent "
                    "LOOPX_INSTALL_SLASH_COMMANDS=0 bash"
                ),
                "skills_dir_env": "LOOPX_SKILLS_DIR",
                "entry_host_surface_env": "LOOPX_ENTRY_HOST_SURFACE",
                "entry_host_surface": "ark-managed-agent",
                "target_layout": f"{project}/.agents/skills",
                "fixed_installer_skill_ids": REQUIRED_HOST_SKILL_IDS,
                "filesystem_readback": filesystem_readback,
            }
            if ark_managed_agent
            else (
                {
                    "preferred_delivery": "dsh_loopx_init_command",
                    "onboarding_role": "read_only_verifier",
                    "onboarding_required_for_install": False,
                    "install_command": "/loopx-init",
                    "entry_host_surface": "deepseek-harness-native",
                    "target_layout": "~/.agents/skills",
                }
                if dsh_native
                else {}
            )
        ),
        "source_repository": "https://github.com/loopx-project/loopx",
        "source_directories": [
            f"skills/{skill_id}"
            for skill_id in required_skill_ids
            if skill_id != "loopx"
        ],
        "generated_skill_ids": ["loopx"],
        "source_contract": (
            "Use the same LoopX release or repository revision as the CLI when "
            "materializing skills or injecting equivalent SKILL.md instructions "
            "through the custom host."
        ),
        "host_readback_required": True,
        "readback_fields": [
            "integration_mode",
            "loaded_skill_ids",
            "source_revision",
        ],
        "success_criteria": [
            "the host reports which integration mode it selected",
            "the host reports every required LoopX skill id as loaded",
            "the host reports every active project skill id as loaded or injects its equivalent instructions",
            "the host reports the source revision or digest used for delivery",
            "no Codex-specific skill directory is assumed",
        ],
    }


def _bootstrap_pack_command(
    *,
    project: str,
    goal_id: str,
    agent_id: str | None,
    agent_type: str,
    cli_bin: str,
    task_text: str | None,
    available_capabilities: list[str] | None,
) -> str:
    surface_by_type = {
        "codex-app": "codex-app",
        "trae_app": "trae_app",
        "codex-app-ssh": "codex-app-ssh",
        "codex-ide-plugin": "codex-ide-plugin",
        "codex-cli": "codex-cli-tui",
        "claude-code": "claude-code",
        "opencode": "opencode",
        "traex-cli": "traex-cli",
        "pi": "pi",
        "gemini-cli": "gemini-cli",
        "cursor-agent": "cursor-agent",
        "zcode": "zcode",
        "agy": "agy",
        "kiro-cli": "kiro-cli",
        "deepseek-harness": "deepseek-harness",
        "deepseek-harness-native": "deepseek-harness-native",
        "ark-managed-agent": "ark-managed-agent",
        "manual": "shell",
        "other-agent": "other-agent",
    }
    parts = [
        host_command(cli_bin),
        "bootstrap-command-pack",
        "--project",
        shell_arg_for_host(project),
        "--goal-id",
        shell_arg_for_host(goal_id),
        "--host-surface",
        shell_arg_for_host(surface_by_type[agent_type]),
    ]
    if agent_id:
        parts.extend(["--agent-id", shell_arg_for_host(agent_id)])
    if task_text:
        parts.extend(["--goal-text", shell_arg_for_host(task_text)])
    for capability in available_capabilities or []:
        parts.extend(["--available-capability", shell_arg_for_host(capability)])
    return " ".join(parts)


def _start_instruction(agent_type: str) -> str:
    if agent_type == "codex-app":
        return "Use `$loopx <task>` or select the LoopX skill from `/skills`; Codex App should then create/update the heartbeat automation."
    if agent_type == "trae_app":
        return "Use `$loopx <task>` or select the LoopX skill from `/skills`; Trae App should then create/update the heartbeat automation."
    if agent_type == "codex-app-ssh":
        return "Use `$loopx <task>` or select the LoopX skill from `/skills`; after todos are written, set `/goal <task_body>` in the visible Codex App task."
    if agent_type == "codex-ide-plugin":
        return "Use `$loopx <task>` or select the LoopX skill from `/skills`; after todos are written, set `/goal <task_body>` in the visible IDE plugin."
    if agent_type == "codex-cli":
        return "Use `$loopx <task>` or select the LoopX skill from `/skills`; after todos are written, set `/goal <task_body>` in the visible TUI."
    if agent_type == "claude-code":
        return "Run `/loopx <task>` to arm LoopX, then run native `/loop`."
    if agent_type == "opencode":
        return "Run `/loopx <task>`; after todo writeback, call `loopx_goal_activate` with the generated heartbeat task body."
    if agent_type == "traex-cli":
        return "Use `$loopx <task>` or select the LoopX skill from `/skills`; after todos are written, set `/goal <task_body>` in the visible TraeX TUI (enable `[features] goals = true` first if goal mode is off)."
    if agent_type == "pi":
        return "Run `/loopx <task>`; after todo writeback, call `loopx_goal_activate` with the generated heartbeat task body."
    if agent_type == "gemini-cli":
        return (
            "Invoke the LoopX skill from `GEMINI_HOME/skills`; after todo writeback, "
            "carry the generated heartbeat task body as the session objective and "
            "start every following turn with `quota should-run`."
        )
    if agent_type == "cursor-agent":
        return (
            "Invoke the LoopX skill from `CURSOR_HOME/skills`; after todo writeback, "
            "carry the generated heartbeat task body as the session objective and "
            "start every following turn with `quota should-run`, reading state through "
            "the registered `loopx` MCP server or the CLI."
        )
    if agent_type == "zcode":
        return (
            "Invoke `$loopx` (or the LoopX skill from `ZCODE_HOME/skills`); after "
            "todo writeback, carry the generated heartbeat task body as the "
            "session objective and start every following turn with `quota should-run`."
        )
    if agent_type == "agy":
        return (
            "Invoke the LoopX skill from the fixed agy skills root "
            "`~/.gemini/antigravity-cli/skills` via `/loopx <task>`; after todo "
            "writeback, bind the objective with the native `/goal <task_body>` "
            "command (agy audits work until `<!-- GOAL_COMPLETE -->`; cancel "
            "with `<!-- GOAL_CANCELLED -->`), start every following turn, wake "
            "and audit-continuation with `quota should-run`, and arm the next "
            "bounded wake with the native `schedule` tool (DurationSeconds + "
            "wake Prompt; recurring via MaxIterations) when quota allows more "
            "work."
        )
    if agent_type == "kiro-cli":
        return (
            f"Run `/loopx <task>` (the LoopX skill installed in "
            f"`{KIRO_CLI_SKILLS_ROOT_LABEL}`); after todo writeback, bind the "
            f"objective with the native `{kiro_cli_goal_invocation()}` command, "
            f"stating the todo's acceptance criteria inside the goal statement "
            f"because the host derives them from it, and taking N from the "
            f"remaining quota slots (host default is "
            f"{KIRO_CLI_GOAL_DEFAULT_MAX_ITERATIONS}; "
            f"`{KIRO_CLI_GOAL_CLEAR_COMMAND}` cancels). "
            f"Start every turn and native goal iteration with `quota "
            f"should-run`, and settle through the built-in "
            f"`{KIRO_CLI_GOAL_COMPLETION_TOOL}` tool only after LoopX writeback "
            f"so its completion contract cites the same evidence."
        )
    if agent_type == "deepseek-harness":
        return (
            "Install `loopx[deepseek-harness]`, prepare a dsh cordis.yml, and run "
            "`loopx turn run-once` with the `loopx.dsh_goal_mode` adapter "
            "(`python -m loopx.dsh_goal_mode`; the legacy "
            "`scripts/dsh_turn_host_adapter.py` launcher still works) as the "
            "generic-cli host adapter; every tick starts from `quota should-run`."
        )
    if agent_type == "deepseek-harness-native":
        return (
            "Install the DSH LoopX plugin, run `/loopx-init`, then invoke the `loopx` "
            "skill with the task text. The plugin driver continues the exact live "
            "DSH session only after `quota should-run` admits another step."
        )
    if agent_type == "ark-managed-agent":
        return (
            "Use `$loopx <task>` as the ordinary task entry; after its todo "
            "writeback, submit the generated Goal task body exactly once."
        )
    if agent_type == "manual":
        return "Use the CLI packet and wire an external scheduler, or run quota/status/todo commands manually."
    return (
        "Deliver the required LoopX skills through the custom host and verify its "
        "loaded-skill readback; then use the host's explicit LoopX command facade "
        "and wire its scheduler through this packet."
    )


def build_agent_onboarding_packet(
    *,
    project: Path,
    agent_type: str,
    goal_id: str | None = None,
    agent_id: str | None = None,
    cli_bin: str = "loopx",
    task_text: str | None = None,
    available_capabilities: list[str] | None = None,
) -> dict[str, Any]:
    canonical_agent_type = normalize_agent_type(agent_type)
    inspection = inspect_bootstrap_connection(project, goal_id=goal_id)
    resolved_project = str(inspection["project"])
    resolved_goal_id = str(inspection["goal_id"])
    registry_path = Path(str(inspection["registry"]))
    registry = load_registry(registry_path) if registry_path.exists() else {}
    goal = next(
        (
            item
            for item in registry_goals(registry)
            if str(item.get("id") or "") == resolved_goal_id
        ),
        {},
    )
    from .capabilities.machine_configuration.builtins import (
        build_builtin_machine_configuration_registry,
        project_goal_with_builtin_machine_configuration,
    )
    from .capabilities.machine_configuration.store import read_machine_configuration
    from .paths import resolve_runtime_root

    runtime_root = resolve_runtime_root(
        registry,
        registry_path=registry_path,
    )
    goal = project_goal_with_builtin_machine_configuration(
        goal,
        read_machine_configuration(
            runtime_root,
            registry=build_builtin_machine_configuration_registry(),
        ),
    )
    active_project_skill_ids = (
        [CHANGE_QUALITY_SKILL_ID]
        if change_quality_goal_policy(goal)["enabled"]
        else []
    )
    skill_delivery = _skill_delivery_contract(
        canonical_agent_type,
        project=resolved_project,
        cli_bin=cli_bin,
        active_project_skill_ids=active_project_skill_ids,
        host_skills_dir=configured_host_skills_dir(os.environ),
    )
    registered_agents = registered_agent_ids_from_registry(
        registry_path,
        resolved_goal_id,
    )
    host_loop_activation = build_host_loop_activation_packet(
        agent_type=canonical_agent_type,
        goal_id=resolved_goal_id,
        cli_bin=cli_bin,
        agent_id=agent_id,
        registered_agents=registered_agents,
        available_capabilities=available_capabilities,
        fresh_agent_default=True,
    )
    selected_agent_id = host_loop_activation.get("agent_id")
    activation_allowed = bool(host_loop_activation.get("activation_allowed"))
    normalized_available_capabilities = list(
        host_loop_activation.get("available_capabilities") or []
    )
    install_command = _surface_install_command(canonical_agent_type, cli_bin, resolved_project)
    bootstrap_pack_command = _bootstrap_pack_command(
        project=resolved_project,
        goal_id=resolved_goal_id,
        agent_id=str(selected_agent_id) if selected_agent_id else None,
        agent_type=canonical_agent_type,
        cli_bin=cli_bin,
        task_text=task_text,
        available_capabilities=normalized_available_capabilities,
    )
    commands: dict[str, Any] = {
        "doctor_or_install": render_codex_cli_install_preflight(
            cli_bin=cli_bin,
            doctor_agent_type=canonical_agent_type,
        ),
        "bootstrap_command_pack": bootstrap_pack_command,
        "quota_guard": (
            render_quota_guard_command(
                resolved_goal_id,
                cli_bin=cli_bin,
                agent_id=str(selected_agent_id) if selected_agent_id else None,
                available_capabilities=normalized_available_capabilities,
                **scheduler_command_binding_for_agent_type(canonical_agent_type),
            )
            if activation_allowed
            else None
        ),
        "agent_onboard_recheck": (
            f"{host_command(cli_bin)} agent-onboard "
            f"--agent-type {shell_arg_for_host(canonical_agent_type)} "
            f"--project {shell_arg_for_host(resolved_project)} "
            f"--goal-id {shell_arg_for_host(resolved_goal_id)}"
            + (
                f" --agent-id {shell_arg_for_host(str(selected_agent_id))}"
                if selected_agent_id
                else ""
            )
            + render_available_capability_args(normalized_available_capabilities)
        ),
    }
    if install_command:
        commands["install_command_facade"] = install_command
    if canonical_agent_type == "codex-cli":
        commands["codex_cli_bootstrap_message"] = (
            f"{host_command(cli_bin)} codex-cli-bootstrap-message "
            f"--project {shell_arg_for_host(resolved_project)} "
            f"--goal-id {shell_arg_for_host(resolved_goal_id)}"
            + (
                f" --agent-id {shell_arg_for_host(str(selected_agent_id))}"
                if selected_agent_id
                else ""
            )
        )
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "agent_type": canonical_agent_type,
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": selected_agent_id,
        "requested_agent_id": agent_id,
        "available_capabilities": normalized_available_capabilities,
        "identity_selection_gate": host_loop_activation.get("identity_selection_gate"),
        "task_text": task_text,
        "project_connection": inspection,
        "host_loop_activation": host_loop_activation,
        "skill_delivery": skill_delivery,
        "recommended_start": (
            "Register a fresh agent id by default, or select an existing lane only "
            "after explicit takeover intent; then rerun onboarding with --agent-id."
            if not activation_allowed
            else _start_instruction(canonical_agent_type)
        ),
        "commands": commands,
        "cost_model": {
            "onboarding": "run once during install/connect or when the active host loop is missing/stale",
            "loopx_task_start": (
                "`/loopx <task>` or the explicit skill should call this packet only when "
                "host_loop_activation is missing, unknown, stale, or the agent type changed"
            ),
            "normal_ticks": "read quota/status/state directly; do not recompute onboarding every turn",
        },
        "slash_command_contract": {
            "after_todo_writeback": (
                "refresh state, activate the host loop through host_loop_activation, "
                "or report the concrete host-tool gate; then run quota should-run"
            ),
            "connected_registry_is_not_enough": True,
            "setup_complete_requires": [
                "project-local LoopX state exists or was intentionally previewed",
                "ordered todos are written when task text was supplied",
                *(
                    [
                        "host-managed loaded-skill readback satisfies skill_delivery.success_criteria"
                    ]
                    if skill_delivery.get("host_readback_required") is True
                    else []
                ),
                "host_loop_activation success criteria are satisfied or a concrete gate is reported",
            ],
        },
    }


def render_agent_onboarding_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return render_agent_type_catalog_markdown(payload)
    commands = payload.get("commands") if isinstance(payload.get("commands"), dict) else {}
    activation = (
        payload.get("host_loop_activation")
        if isinstance(payload.get("host_loop_activation"), dict)
        else {}
    )
    steps = activation.get("activation_steps") if isinstance(activation.get("activation_steps"), list) else []
    criteria = (
        activation.get("success_criteria")
        if isinstance(activation.get("success_criteria"), list)
        else []
    )
    identity_gate = payload.get("identity_selection_gate")
    identity_gate = identity_gate if isinstance(identity_gate, dict) else {}
    skill_delivery = (
        payload.get("skill_delivery")
        if isinstance(payload.get("skill_delivery"), dict)
        else {}
    )
    lines = [
        "# LoopX Agent Onboarding",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- agent_type: `{payload.get('agent_type')}`",
        f"- project: `{payload.get('project')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- recommended_start: {payload.get('recommended_start')}",
        "",
        "## Commands",
        "",
        "```bash",
        str(commands.get("doctor_or_install") or ""),
        "```",
    ]
    if commands.get("install_command_facade"):
        lines.extend(["", "```bash", str(commands.get("install_command_facade")), "```"])
    project_skill_commands = (
        skill_delivery.get("project_skill_commands")
        if isinstance(skill_delivery.get("project_skill_commands"), list)
        else []
    )
    if project_skill_commands:
        lines.extend(
            [
                "",
                "## Active Project Skills",
                "",
                f"- skill_ids: `{','.join(skill_delivery.get('active_project_skill_ids') or [])}`",
            ]
        )
        for item in project_skill_commands:
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    "",
                    "```bash",
                    str(item.get("status") or ""),
                    str(item.get("preview_install") or ""),
                    str(item.get("apply_install") or ""),
                    "```",
                ]
            )
    if skill_delivery.get("mode") == "host_managed":
        lines.extend(
            [
                "",
                "## Host-Managed Skill Delivery",
                "",
                f"- owner: `{skill_delivery.get('owner')}`",
                f"- onboarding_role: `{skill_delivery.get('onboarding_role')}`",
                (
                    "- onboarding_required_for_install: "
                    f"`{skill_delivery.get('onboarding_required_for_install')}`"
                ),
                f"- required_for_cli_health: `{skill_delivery.get('required_for_cli_health')}`",
                f"- required_for_loopx_workflow: `{skill_delivery.get('required_for_loopx_workflow')}`",
                f"- delivery_options: `{','.join(skill_delivery.get('delivery_options') or [])}`",
                f"- required_skill_ids: `{','.join(skill_delivery.get('required_skill_ids') or [])}`",
                f"- source_repository: `{skill_delivery.get('source_repository')}`",
                f"- source_directories: `{','.join(skill_delivery.get('source_directories') or [])}`",
                f"- host_readback_required: `{skill_delivery.get('host_readback_required')}`",
                f"- readback_fields: `{','.join(skill_delivery.get('readback_fields') or [])}`",
                f"- source_contract: {skill_delivery.get('source_contract')}",
            ]
        )
        if skill_delivery.get("no_clone_install_command"):
            lines.extend(
                [
                    "",
                    "```bash",
                    str(skill_delivery["no_clone_install_command"]),
                    "```",
                ]
            )
        filesystem_readback = skill_delivery.get("filesystem_readback")
        if isinstance(filesystem_readback, dict):
            lines.extend(
                [
                    f"- filesystem_readback_status: `{filesystem_readback.get('status')}`",
                    f"- filesystem_readback_ready: `{filesystem_readback.get('ready')}`",
                    f"- filesystem_readback_skills_dir: `{filesystem_readback.get('skills_dir')}`",
                    f"- filesystem_readback_source_revision: `{filesystem_readback.get('source_revision')}`",
                    (
                        "- filesystem_readback_source_revision_matches: "
                        f"`{filesystem_readback.get('source_revision_matches')}`"
                    ),
                    (
                        "- filesystem_readback_materialized_skill_ids: "
                        f"`{','.join(filesystem_readback.get('materialized_skill_ids') or [])}`"
                    ),
                ]
            )
    lines.extend(["", "```bash", str(commands.get("bootstrap_command_pack") or ""), "```"])
    if commands.get("codex_cli_bootstrap_message"):
        lines.extend(["", "```bash", str(commands.get("codex_cli_bootstrap_message")), "```"])
    lines.extend(
        [
            "",
            "## Host Loop Activation",
            "",
            f"- host_surface: `{activation.get('host_surface')}`",
            f"- activation_method: `{activation.get('activation_method')}`",
            f"- activation_input_command: `{activation.get('activation_input_command')}`",
            "",
            "Steps:",
        ]
    )
    lines.extend(f"- {step}" for step in steps)
    lines.append("")
    lines.append("Success criteria:")
    lines.extend(f"- {item}" for item in criteria)
    if identity_gate:
        lines.extend(["", "## Agent Identity Gate", ""])
        lines.append(f"- reason: {identity_gate.get('reason')}")
        fresh_registration = identity_gate.get("fresh_agent_registration")
        if isinstance(fresh_registration, dict):
            lines.extend(
                [
                    "- default_action: `register_fresh_agent`",
                    f"- preview: `{fresh_registration.get('preview_command')}`",
                    f"- apply: `{fresh_registration.get('execute_command')}`",
                    "- existing identities below require explicit takeover intent:",
                ]
            )
        for choice in identity_gate.get("choices") or []:
            if isinstance(choice, dict):
                lines.append(
                    f"- takeover `{choice.get('agent_id')}`: "
                    f"`{choice.get('activation_input_command')}`"
                )
    lines.extend(
        [
            "",
            "## Recheck Policy",
            "",
            f"- {payload.get('cost_model', {}).get('loopx_task_start') if isinstance(payload.get('cost_model'), dict) else ''}",
        ]
    )
    return "\n".join(lines)
