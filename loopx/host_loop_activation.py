from __future__ import annotations

from typing import Any
import shlex

from .agent_registry import normalize_registered_agents
from .agy_goal_mode import AGY_ACCEPTED_INPUTS
from .control_plane.scheduler.execution_context import SchedulerRuntimeProfile
from .host_loop_activation_skill_facade import (
    agy_cli_activation,
    cursor_agent_activation,
    gemini_cli_activation,
    kiro_cli_activation,
    zcode_activation,
)
from .kiro_cli_goal_mode import KIRO_CLI_AGENT_TYPE_CATALOG_ENTRY
from .control_plane.todos.contract import (
    normalize_required_capabilities,
    normalize_todo_claimed_by,
)
from .project_prompt import (
    render_heartbeat_prompt_command,
    render_heartbeat_prompt_json_command,
    render_register_agent_command,
)

SCHEMA_VERSION = "loopx_host_loop_activation_v1"
AGENT_TYPE_CATALOG_SCHEMA_VERSION = "loopx_agent_type_catalog_v0"
IDENTITY_SELECTION_SCHEMA_VERSION = "loopx_host_loop_identity_selection_v0"
PI_OPTIONAL_COMPAT_ECHO = "optional compatibility echo; host authority derives the value"
HOST_MANAGED_SKILL_AGENT_TYPES = frozenset(
    {
        "ark-managed-agent",
        "deepseek-harness-native",
        "trae_app",
        "traex-cli",
        "other-agent",
    }
)


def scheduler_command_binding_for_agent_type(
    agent_type: str,
) -> dict[str, Any]:
    canonical = normalize_agent_type(agent_type)
    runtime_profile = {
        "ark-managed-agent": SchedulerRuntimeProfile.ARK_MANAGED_AGENT_GOAL,
        "codex-app": SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT,
        "codex-app-ssh": SchedulerRuntimeProfile.CODEX_APP_SSH_VISIBLE,
        "codex-cli": SchedulerRuntimeProfile.CODEX_CLI_VISIBLE,
        "codex-ide-plugin": SchedulerRuntimeProfile.CODEX_CLI_VISIBLE,
        "trae_app": SchedulerRuntimeProfile.TRAE_APP,
        "claude-code": SchedulerRuntimeProfile.CLAUDE_CODE_VISIBLE,
        "kunluncode": SchedulerRuntimeProfile.KUNLUNCODE_VISIBLE,
        "opencode": SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP,
        "opencode2": SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP,
        "traex-cli": SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP,
        "pi": SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP,
        "gemini-cli": SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP,
        "cursor-agent": SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP,
        "zcode": SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP,
        "agy": SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP,
        "kiro-cli": SchedulerRuntimeProfile.KIRO_CLI_VISIBLE,
        "deepseek-harness": SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP,
        "deepseek-harness-native": SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP,
    }.get(canonical)
    if runtime_profile is not None:
        return {"runtime_profile": runtime_profile.value}
    return {}


def agent_type_uses_host_managed_skills(agent_type: str) -> bool:
    return normalize_agent_type(agent_type) in HOST_MANAGED_SKILL_AGENT_TYPES


SUPPORTED_AGENT_TYPES = [
    "ark-managed-agent",
    "codex-app",
    "codex-app-ssh",
    "codex-ide-plugin",
    "codex-cli",
    "trae_app",
    "claude-code",
    "kunluncode",
    "opencode",
    "opencode2",
    "traex-cli",
    "pi",
    "gemini-cli",
    "cursor-agent",
    "zcode",
    "agy",
    "kiro-cli",
    "deepseek-harness",
    "deepseek-harness-native",
    "manual",
    "other-agent",
]

AGENT_TYPE_CATALOG: dict[str, dict[str, Any]] = {
    "ark-managed-agent": {
        "display_name": "Ark Managed Agent",
        "host_loop": "one-shot Goal activation owned by the Goal runtime",
        "entry": "submit the generated task_body as one Goal",
        "accepted_inputs": [
            "ark-managed-agent",
            "ark_managed_agent",
            "ark managed agent",
            "managed-agent",
            "managed_agent",
            "managed agent",
        ],
    },
    "codex-app": {
        "display_name": "Codex App",
        "host_loop": "Codex App heartbeat automation",
        "entry": "$loopx <task> or the explicit LoopX skill from /skills",
        "accepted_inputs": ["codex-app", "codex_app", "codex app", "codex-desktop", "codex desktop"],
    },
    "codex-app-ssh": {
        "display_name": "Codex App over SSH",
        "host_loop": "visible Codex App /goal when host automation is unavailable over SSH",
        "entry": "$loopx <task> or the explicit LoopX skill from /skills",
        "accepted_inputs": [
            "codex-app-ssh",
            "codex_app_ssh",
            "codex app ssh",
            "codex-ssh",
            "codex ssh",
            "codex-app-remote",
            "codex app remote",
        ],
    },
    "codex-cli": {
        "display_name": "Codex CLI TUI",
        "host_loop": "visible Codex CLI /goal",
        "entry": "$loopx <task> or the explicit LoopX skill from /skills",
        "accepted_inputs": [
            "codex-cli",
            "codex_cli",
            "codex cli",
            "codex-cli-tui",
            "codex_cli_tui",
            "codex tui",
        ],
    },
    "trae_app": {
        "display_name": "Trae App",
        "host_loop": "Trae App heartbeat automation",
        "entry": "$loopx <task> or the explicit LoopX skill from /skills",
        "accepted_inputs": [
            "trae_app",
        ],
    },
    "codex-ide-plugin": {
        "display_name": "Codex IDE plugin",
        "host_loop": "visible Codex IDE plugin /goal",
        "entry": "$loopx <task> or the explicit LoopX skill from /skills",
        "accepted_inputs": [
            "codex-ide-plugin",
            "codex_ide_plugin",
            "codex ide plugin",
            "codex-ide",
            "codex_ide",
            "codex ide",
            "codex-ide-extension",
            "codex ide extension",
            "codex-vscode",
            "codex vscode",
            "vscode-codex",
            "vscode codex",
        ],
    },
    "claude-code": {
        "display_name": "Claude Code",
        "host_loop": "native /loop gated by LoopX",
        "entry": "/loopx <task> then /loop",
        "accepted_inputs": ["claude-code", "claude_code", "claude code", "cc"],
    },
    "kunluncode": {
        "display_name": "KunlunCode",
        "host_loop": "recoverable native Goal Pro controlled by LoopX",
        "entry": "loopx-kunluncode add <task> then loopx-kunluncode run",
        "accepted_inputs": [
            "kunluncode",
            "kunlun-code",
            "kunlun_code",
            "kunlun code",
            "kunlun",
        ],
    },
    "opencode": {
        "display_name": "OpenCode",
        "host_loop": "visible OpenCode goal plugin gated by LoopX",
        "entry": "/loopx <task> with the LoopX OpenCode bridge installed",
        "accepted_inputs": ["opencode", "open-code", "open_code", "open code"],
    },
    "opencode2": {
        "display_name": "OpenCode 2",
        "host_loop": "visible OpenCode 2 session driven by the LoopX goal worker",
        "entry": "/loopx <task> with OpenCode 2 and the LoopX goal worker",
        "accepted_inputs": [
            "opencode2",
            "opencode-2",
            "opencode_2",
            "open-code-2",
            "open code 2",
        ],
    },
    "traex-cli": {
        "display_name": "TraeX CLI TUI",
        "host_loop": "visible TraeX /goal gated by LoopX",
        "entry": "$loopx <task> or the explicit LoopX skill from /skills",
        "accepted_inputs": [
            "traex-cli",
            "traex_cli",
            "traex cli",
            "traex",
            "traex-cli-tui",
            "traex tui",
            "trae-cli",
            "trae_cli",
            "trae cli",
        ],
    },
    "pi": {
        "display_name": "Pi",
        "host_loop": "visible Pi goal extension gated by LoopX",
        "entry": "/loopx <task> with the LoopX Pi extension installed",
        "accepted_inputs": [
            "pi",
            "pi-agent",
            "pi_agent",
            "pi agent",
            "earendil-pi",
            "earendil pi",
        ],
    },
    "gemini-cli": {
        "display_name": "Gemini CLI",
        "host_loop": "agent-driven Gemini CLI loop gated by LoopX quota should-run",
        "entry": "the LoopX skill installed in GEMINI_HOME/skills",
        "accepted_inputs": [
            "gemini-cli",
            "gemini_cli",
            "gemini cli",
            "gemini",
            "gemini-code",
            "gemini code",
            "google-gemini",
            "google gemini",
        ],
    },
    "cursor-agent": {
        "display_name": "Cursor Agent CLI",
        "host_loop": "agent-driven cursor-agent loop gated by LoopX quota should-run",
        "entry": "the LoopX skill installed in CURSOR_HOME/skills, with the LoopX MCP server registered",
        "accepted_inputs": [
            "cursor-agent",
            "cursor_agent",
            "cursor agent",
            "cursor",
            "cursor-cli",
            "cursor cli",
        ],
    },
    "zcode": {
        "display_name": "ZCode",
        "host_loop": "agent-driven ZCode loop gated by LoopX quota should-run",
        "entry": "$loopx <task> or the LoopX skill from ZCODE_HOME/skills",
        "accepted_inputs": [
            "zcode",
            "z_code",
            "z code",
            "z-code",
        ],
    },
    "agy": {
        "display_name": "Antigravity CLI",
        "host_loop": "Antigravity CLI native /goal loop with schedule self-wakes (LoopX quota pacing is advisory)",
        "entry": "the LoopX skill installed in ~/.gemini/antigravity-cli/skills",
        "accepted_inputs": list(AGY_ACCEPTED_INPUTS),
    },
    "kiro-cli": KIRO_CLI_AGENT_TYPE_CATALOG_ENTRY,
    "deepseek-harness": {
        "display_name": "DeepSeek Harness",
        "host_loop": "DeepSeek Harness headless/automation loop gated by LoopX quota",
        "entry": "loopx turn run-once with loopx.dsh_goal_mode (python -m loopx.dsh_goal_mode; compat: scripts/dsh_turn_host_adapter.py)",
        "accepted_inputs": [
            "deepseek-harness",
            "deepseek_harness",
            "deepseek harness",
            "dsh",
        ],
    },
    "deepseek-harness-native": {
        "display_name": "DeepSeek Harness (native session)",
        "host_loop": "DeepSeek Harness same-session loop driven by the LoopX plugin",
        "entry": "the LoopX skill plus the DSH `/loopx-init` command and same-session driver",
        "accepted_inputs": [
            "deepseek-harness-native",
            "deepseek_harness_native",
            "deepseek harness native",
            "dsh-native",
            "dsh_native",
            "dsh native",
        ],
    },
    "manual": {
        "display_name": "Manual shell / external scheduler",
        "host_loop": "external scheduler or manual quota/status loop",
        "entry": "CLI packet plus an external loop driver",
        "accepted_inputs": ["manual", "shell", "manual-shell", "external-scheduler"],
    },
    "other-agent": {
        "display_name": "Other explicit agent host",
        "host_loop": "custom agent loop driver",
        "entry": "@loopx <task>, $loopx <task>, or another host facade",
        "accepted_inputs": ["other-agent", "other_agent", "custom-agent", "custom agent"],
    },
}

AMBIGUOUS_AGENT_TYPE_INPUTS: dict[str, list[str]] = {
    "codex": ["codex-app", "codex-app-ssh", "codex-ide-plugin", "codex-cli"],
    "openai-codex": ["codex-app", "codex-app-ssh", "codex-ide-plugin", "codex-cli"],
    "openai codex": ["codex-app", "codex-app-ssh", "codex-ide-plugin", "codex-cli"],
    "cli": ["codex-cli", "manual", "other-agent"],
}


def _agent_type_key(value: str | None) -> str:
    return (value or "").strip().lower().replace("_", "-")


AGENT_TYPE_ALIASES = {
    _agent_type_key(alias): canonical
    for canonical, metadata in AGENT_TYPE_CATALOG.items()
    for alias in metadata["accepted_inputs"]
    if alias != canonical
}


class AgentTypeError(ValueError):
    def __init__(self, *, value: str | None, reason: str, suggestions: list[str] | None = None) -> None:
        self.value = value
        self.reason = reason
        self.suggestions = suggestions or []
        super().__init__(reason)

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": False,
            "schema_version": "loopx_agent_type_error_v0",
            "error_kind": "ambiguous_or_unsupported_agent_type",
            "agent_type": self.value,
            "reason": self.reason,
            "suggestions": self.suggestions,
            "agent_type_catalog": build_agent_type_catalog(),
        }


HOST_SURFACE_TO_AGENT_TYPE = {
    "ark-managed-agent": "ark-managed-agent",
    "ark_managed_agent": "ark-managed-agent",
    "codex-app": "codex-app",
    "codex-app-ssh": "codex-app-ssh",
    "chat-box": "codex-app",
    "trae_app": "trae_app",
    "codex-ide-plugin": "codex-ide-plugin",
    "codex-ide": "codex-ide-plugin",
    "codex-cli-tui": "codex-cli",
    "claude-code": "claude-code",
    "opencode": "opencode",
    "opencode2": "opencode2",
    "opencode-v2": "opencode2",
    "opencode_2": "opencode2",
    "traex-cli": "traex-cli",
    "traex-cli-tui": "traex-cli",
    "traex": "traex-cli",
    "pi": "pi",
    "pi-tui": "pi",
    "gemini-cli": "gemini-cli",
    "gemini": "gemini-cli",
    "cursor-agent": "cursor-agent",
    "cursor": "cursor-agent",
    "zcode": "zcode",
    "z-code": "zcode",
    "agy": "agy",
    "antigravity": "agy",
    "antigravity-cli": "agy",
    "kiro-cli": "kiro-cli",
    "kiro": "kiro-cli",
    "deepseek-harness": "deepseek-harness",
    "dsh": "deepseek-harness",
    "deepseek-harness-native": "deepseek-harness-native",
    "dsh-native": "deepseek-harness-native",
    "shell": "manual",
    "http": "other-agent",
    "worker-bridge": "other-agent",
}


def build_agent_type_catalog() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": AGENT_TYPE_CATALOG_SCHEMA_VERSION,
        "canonical_agent_types": [
            {
                "agent_type": agent_type,
                "display_name": metadata["display_name"],
                "host_loop": metadata["host_loop"],
                "entry": metadata["entry"],
                "accepted_inputs": metadata["accepted_inputs"],
            }
            for agent_type, metadata in AGENT_TYPE_CATALOG.items()
        ],
        "ambiguous_inputs": [
            {"input": value, "use_one_of": choices}
            for value, choices in AMBIGUOUS_AGENT_TYPE_INPUTS.items()
        ],
        "selection_rule": (
            "Agents should pass a canonical agent_type. Ambiguous values such as "
            "`codex` are rejected because Codex App automation, Codex App over SSH, "
            "the Codex IDE plugin, and Codex CLI have different "
            "host-loop activation paths."
        ),
    }


def render_agent_type_catalog_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok") and isinstance(payload.get("agent_type_catalog"), dict):
        catalog = payload["agent_type_catalog"]
        header = [
            "# LoopX Agent Type Error",
            "",
            f"- ok: `{payload.get('ok')}`",
            f"- error_kind: `{payload.get('error_kind')}`",
            f"- agent_type: `{payload.get('agent_type')}`",
            f"- reason: {payload.get('reason')}",
            f"- suggestions: `{', '.join(payload.get('suggestions') or [])}`",
            "",
        ]
        return "\n".join(header) + render_agent_type_catalog_markdown(catalog)
    lines = [
        "# LoopX Agent Types",
        "",
        str(payload.get("selection_rule") or ""),
        "",
        "| agent_type | Host loop | Entry | Accepted inputs |",
        "| --- | --- | --- | --- |",
    ]
    for item in payload.get("canonical_agent_types") or []:
        if not isinstance(item, dict):
            continue
        aliases = ", ".join(f"`{value}`" for value in item.get("accepted_inputs") or [])
        lines.append(
            "| "
            f"`{item.get('agent_type')}` | "
            f"{item.get('host_loop')} | "
            f"{item.get('entry')} | "
            f"{aliases} |"
        )
    lines.extend(["", "Ambiguous inputs:"])
    for item in payload.get("ambiguous_inputs") or []:
        if isinstance(item, dict):
            choices = ", ".join(f"`{value}`" for value in item.get("use_one_of") or [])
            lines.append(f"- `{item.get('input')}` -> use one of {choices}")
    return "\n".join(lines)


def normalize_agent_type(value: str | None) -> str:
    canonical = (value or "").strip().lower()
    if canonical in AGENT_TYPE_CATALOG:
        return canonical
    key = _agent_type_key(value)
    if not key:
        raise AgentTypeError(
            value=value,
            reason="agent_type is required",
            suggestions=SUPPORTED_AGENT_TYPES,
        )
    if key in AMBIGUOUS_AGENT_TYPE_INPUTS:
        suggestions = AMBIGUOUS_AGENT_TYPE_INPUTS[key]
        raise AgentTypeError(
            value=value,
            reason=(
                f"agent_type {value!r} is ambiguous; choose the exact host runtime "
                f"because each one has a different host_loop_activation"
            ),
            suggestions=suggestions,
        )
    try:
        return AGENT_TYPE_ALIASES[key]
    except KeyError as exc:
        raise AgentTypeError(
            value=value,
            reason=f"unsupported agent_type {value!r}",
            suggestions=SUPPORTED_AGENT_TYPES,
        ) from exc


def agent_type_for_host_surface(value: str | None) -> str:
    key = (value or "codex-app").strip().lower()
    if key in HOST_SURFACE_TO_AGENT_TYPE:
        return HOST_SURFACE_TO_AGENT_TYPE[key]
    return normalize_agent_type(key)


def _heartbeat_commands(
    *,
    goal_id: str,
    agent_type: str,
    cli_bin: str,
    runtime_root: str | None = None,
    agent_id: str | None,
    available_capabilities: list[str] | None = None,
) -> dict[str, str]:
    scope_by_type = {
        "ark-managed-agent": "Ark Managed Agent one-shot Goal activation",
        "codex-app": "Codex App heartbeat automation",
        "trae_app": "Trae App heartbeat automation",
        "codex-app-ssh": "Codex App SSH /goal visible task loop",
        "codex-ide-plugin": "Codex IDE plugin /goal visible task loop",
        "codex-cli": "Codex CLI /goal visible TUI loop",
        "claude-code": "Claude Code native /loop gated by LoopX",
        "opencode": "OpenCode visible goal loop gated by LoopX",
        "opencode2": "OpenCode 2 visible goal loop driven by the LoopX worker",
        "traex-cli": "TraeX CLI /goal visible TUI loop gated by LoopX",
        "pi": "Pi visible goal loop gated by LoopX",
        "gemini-cli": "Gemini CLI agent loop gated by LoopX",
        "cursor-agent": "Cursor Agent CLI loop gated by LoopX",
        "zcode": "ZCode agent loop gated by LoopX",
        "agy": "Antigravity CLI agent loop with advisory LoopX quota pacing",
        "kiro-cli": "Kiro CLI native /goal loop with advisory LoopX quota pacing",
        "deepseek-harness": "DeepSeek Harness automation loop gated by LoopX",
        "deepseek-harness-native": "DeepSeek Harness same-session plugin loop gated by LoopX",
        "manual": "External scheduler or manual shell LoopX poll",
        "other-agent": "Custom agent host loop gated by LoopX",
    }
    agent_scope = scope_by_type.get(agent_type, scope_by_type["other-agent"])
    scheduler_binding = scheduler_command_binding_for_agent_type(agent_type)
    renderer_binding = {"visible_goal_host": "traex-cli"} if agent_type == "traex-cli" else {}
    commands = {
        "heartbeat_prompt_json": render_heartbeat_prompt_json_command(
            goal_id,
            cli_bin=cli_bin,
            runtime_root=runtime_root,
            agent_id=agent_id,
            agent_scope=agent_scope,
            available_capabilities=available_capabilities,
            **scheduler_binding,
            **renderer_binding,
        ),
        "heartbeat_prompt": render_heartbeat_prompt_command(
            goal_id,
            cli_bin=cli_bin,
            runtime_root=runtime_root,
            agent_id=agent_id,
            agent_scope=agent_scope,
            available_capabilities=available_capabilities,
            **scheduler_binding,
            **renderer_binding,
        ),
    }
    if agent_type in {"codex-app", "trae_app", "codex-app-ssh", "codex-cli", "codex-ide-plugin",
                      "ark-managed-agent"}:
        commands = {key: command + " --bootstrap" for key, command in commands.items()}
    if renderer_binding:
        commands["visible_goal_prompt_json"] = commands["heartbeat_prompt_json"]
    return commands


def _identity_state(
    *,
    agent_id: str | None,
    registered_agents: list[str] | None,
    fresh_agent_default: bool,
    thread_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registered = normalize_registered_agents(registered_agents)
    selected = normalize_todo_claimed_by(agent_id)

    def identity_payload(values: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": IDENTITY_SELECTION_SCHEMA_VERSION,
            "agent_model": "peer_v1",
            **values,
        }

    binding = thread_binding if isinstance(thread_binding, dict) else {}
    binding_status = str(binding.get("status") or "")
    bound_agent = normalize_todo_claimed_by(binding.get("agent_id"))
    if binding_status == "conflict":
        return identity_payload(
            {
                "state": "thread_binding_conflict",
                "activation_allowed": False,
                "selected_agent_id": None,
                "requested_agent_id": selected,
                "registered_agents": registered,
                "action_required": True,
                "thread_binding": binding,
                "reason": (
                    "the current host thread is bound to conflicting agent lanes; repair the "
                    "binding before host-loop activation"
                ),
                "required_cli_arg": "--agent-id <registered-agent-id>",
            }
        )
    if binding_status == "bound":
        if not bound_agent or bound_agent not in registered:
            return identity_payload(
                {
                    "state": "thread_binding_invalid",
                    "activation_allowed": False,
                    "selected_agent_id": None,
                    "requested_agent_id": selected,
                    "registered_agents": registered,
                    "action_required": True,
                    "thread_binding": binding,
                    "reason": "the current host thread binding points to an unregistered agent",
                    "required_cli_arg": "--agent-id <registered-agent-id>",
                }
            )
        if selected and selected != bound_agent:
            if selected not in registered:
                return identity_payload(
                    {
                        "state": "invalid_selection",
                        "activation_allowed": False,
                        "selected_agent_id": None,
                        "requested_agent_id": selected,
                        "registered_agents": registered,
                        "action_required": True,
                        "thread_binding": binding,
                        "reason": f"agent_id={selected!r} is not registered for this goal",
                        "required_cli_arg": "--agent-id <registered-agent-id>",
                    }
                )
            return identity_payload(
                {
                    "state": "explicit_agent_selected",
                    "activation_allowed": True,
                    "selected_agent_id": selected,
                    "registered_agents": registered,
                    "action_required": False,
                    "thread_binding": binding,
                    "binding_override": True,
                }
            )
        return identity_payload(
            {
                "state": "thread_binding_selected",
                "activation_allowed": True,
                "selected_agent_id": bound_agent,
                "registered_agents": registered,
                "action_required": False,
                "thread_binding": binding,
            }
        )
    if (
        (binding_status == "missing" or binding.get("selection_required"))
        and not selected
        and not fresh_agent_default
    ):
        return identity_payload(
            {
                "state": "thread_binding_selection_required",
                "activation_allowed": False,
                "selected_agent_id": None,
                "registered_agents": registered,
                "action_required": True,
                "thread_binding": binding,
                "reason": (
                    "current host thread has no stored agent binding or stable thread id; "
                    "select an existing lane and do not register a new one unless a new "
                    "peer/session was explicitly requested"
                ),
                "required_cli_arg": "--agent-id <registered-agent-id>",
            }
        )

    if fresh_agent_default and not selected:
        return identity_payload(
            {
                "state": "fresh_agent_registration_required",
                "activation_allowed": False,
                "selected_agent_id": None,
                "registered_agents": registered,
                "action_required": True,
                "reason": (
                    "new agent onboarding has no explicit identity; register a fresh "
                    "public-safe agent id by default. Reuse an existing identity only "
                    "when the user explicitly requests takeover of that exact agent"
                ),
                "required_cli_arg": "--agent-id <freshly-registered-agent-id>",
            }
        )
    if fresh_agent_default and selected not in registered:
        return identity_payload(
            {
                "state": "invalid_selection",
                "activation_allowed": False,
                "selected_agent_id": None,
                "requested_agent_id": selected,
                "registered_agents": registered,
                "action_required": True,
                "reason": (
                    f"agent_id={selected!r} is not registered for this goal; register "
                    "that fresh identity before host-loop activation"
                ),
                "required_cli_arg": "--agent-id <freshly-registered-agent-id>",
            }
        )
    if not registered:
        return identity_payload(
            {
                "state": "legacy_unscoped",
                "activation_allowed": True,
                "selected_agent_id": selected,
                "registered_agents": [],
                "action_required": False,
            }
        )
    if not selected and len(registered) == 1:
        selected = registered[0]
        return identity_payload(
            {
                "state": "single_registered_agent_selected",
                "activation_allowed": True,
                "selected_agent_id": selected,
                "registered_agents": registered,
                "action_required": False,
            }
        )
    if selected in registered:
        return identity_payload(
            {
                "state": "selected",
                "activation_allowed": True,
                "selected_agent_id": selected,
                "registered_agents": registered,
                "action_required": False,
            }
        )
    return identity_payload(
        {
            "state": "invalid_selection" if selected else "selection_required",
            "activation_allowed": False,
            "selected_agent_id": None,
            "requested_agent_id": selected,
            "registered_agents": registered,
            "action_required": True,
            "reason": (
                f"agent_id={selected!r} is not registered for this goal"
                if selected
                else "multiple registered agent lanes exist; select one before host-loop activation"
            ),
            "required_cli_arg": "--agent-id <registered-agent-id>",
        }
    )


def _codex_app_activation(commands: dict[str, str]) -> dict[str, Any]:
    return {
        "host_surface": "codex_app_heartbeat_automation",
        "entry_command_hint": "$loopx <task> or the explicit LoopX skill from /skills",
        "activation_method": "create_or_update_codex_app_automation",
        "activation_input_command": commands["heartbeat_prompt_json"],
        "host_mutation": {
            "owner": "Codex App host",
            "preferred_tool": "automation_update",
            "cli_can_mutate_directly": False,
            "missing_host_tool_gate": (
                "Codex App automation_update is unavailable; surface a pasteable "
                "heartbeat task_body gate instead of claiming autonomous setup."
            ),
        },
        "activation_steps": [
            "Run the heartbeat-prompt JSON command after project state and todos are written.",
            "Require ok=true; save the v2 bootstrap task_body through automation_update.",
            "Read commands.automation_cadence_json before choosing a schedule; for an existing automation also read its automation-id scope.",
            "New heartbeat: use max(3 minutes, configured minimum). Existing: preserve schedule unless it violates the configured minimum; preserve status, prompt and task binding.",
            "Apply through automation_update, then view the automation and verify its actual RRULE. If the host rejects the required interval, hold the affected automation; never shorten the owner minimum.",
            "On later ticks, follow quota should-run scheduler_hint for backoff, reset, and scheduler-ack.",
        ],
        "success_criteria": [
            "A Codex App heartbeat automation exists for this goal and uses the generated task_body.",
            "The next wakeup starts from LoopX quota/status/state, not stale chat memory.",
        ],
    }


def _trae_app_activation(commands: dict[str, str]) -> dict[str, Any]:
    return {
        "host_surface": "trae_app",
        "entry_command_hint": "$loopx <task> or the explicit LoopX skill from /skills",
        "activation_method": "create_or_update_trae_app_automation",
        "activation_input_command": commands["heartbeat_prompt_json"],
        "host_mutation": {
            "owner": "Trae App host",
            "preferred_tool": "automation_update",
            "cli_can_mutate_directly": False,
            "missing_host_tool_gate": (
                "Trae App automation_update is unavailable; surface a pasteable "
                "heartbeat task_body gate instead of claiming autonomous setup."
            ),
        },
        "activation_steps": [
            "Run the heartbeat-prompt JSON command after project state and todos are written.",
            "Read task_body from the JSON payload.",
            "Create or update a Trae App heartbeat automation starting at 3 minutes.",
            "On later ticks, follow quota should-run scheduler_hint for backoff, reset, and scheduler-ack.",
        ],
        "success_criteria": [
            "A Trae App heartbeat automation exists for this goal and uses the generated task_body.",
            "A settled non-terminal turn leaves the automation active for a fresh successor turn.",
            "The next wakeup starts from LoopX quota/status/state, not stale chat memory.",
        ],
    }


def _ark_managed_agent_activation(commands: dict[str, str]) -> dict[str, Any]:
    return {
        "host_surface": "ark_managed_agent_goal_mode",
        "entry_command_hint": "submit the generated task_body as one Goal",
        "activation_method": "submit_goal_once",
        "activation_input_command": commands["heartbeat_prompt_json"],
        "host_mutation": {
            "owner": "Ark Managed Agent Goal host",
            "transport_contract": "goal_prompt_v0",
            "prompt_field": "task_body",
            "cli_can_mutate_directly": False,
            "missing_host_tool_gate": (
                "No Goal transport is available; surface the generated task_body "
                "without claiming host activation."
            ),
        },
        "activation_steps": [
            "Run the heartbeat-prompt JSON command after project state and todos are written.",
            "Read task_body from the JSON payload.",
            "Submit that exact task_body once through either the local-development or cloud Goal transport.",
            "Let the Goal runtime own inner iterations; do not wrap them in LoopX Turn.",
            "Read runtime_capability_reentry_v0 from quota tool results; do not rewrite task_body.",
        ],
        "success_criteria": [
            "The selected transport submitted the generated task_body exactly once.",
            "The Goal runtime owns continuation while LoopX state remains authoritative.",
            "Runtime capability repair remains outside the Goal prompt.",
        ],
    }


def _codex_goal_activation(
    commands: dict[str, str],
    *,
    host_label: str,
    host_surface: str,
) -> dict[str, Any]:
    return {
        "host_surface": host_surface,
        "entry_command_hint": "$loopx <task> or the explicit LoopX skill from /skills",
        "activation_method": "set_visible_goal",
        "activation_input_command": commands["heartbeat_prompt_json"],
        "host_mutation": {
            "owner": host_label,
            "host_command": "/goal <task_body>",
            "cli_can_mutate_directly": False,
            "missing_host_tool_gate": (
                f"Current session cannot set {host_label} /goal; show the exact "
                "`/goal <task_body>` text for the user to paste."
            ),
        },
        "activation_steps": [
            "Run the heartbeat-prompt JSON command after project state and todos are written.",
            "Read task_body from the JSON payload.",
            f"Set the visible {host_label} goal to `/goal <task_body>`.",
            "Keep delivery in the visible task; do not switch to hidden headless execution.",
        ],
        "success_criteria": [
            f"The visible {host_label} has `/goal <task_body>` active for this goal.",
            "Future goal turns enter through LoopX quota/status/state before delivery work.",
        ],
    }


def _codex_cli_activation(commands: dict[str, str]) -> dict[str, Any]:
    return _codex_goal_activation(
        commands,
        host_label="Codex CLI TUI",
        host_surface="codex_cli_visible_goal_mode",
    )


def _codex_app_ssh_activation(commands: dict[str, str]) -> dict[str, Any]:
    activation = _codex_goal_activation(
        commands,
        host_label="Codex App SSH task",
        host_surface="codex_app_ssh_visible_goal_mode",
    )
    activation["success_criteria"].append(
        "After three unchanged blocked turns, native update_goal marks only the "
        "host Goal blocked; LoopX remains active until user /goal resume."
    )
    return activation


def _codex_ide_activation(commands: dict[str, str]) -> dict[str, Any]:
    return _codex_goal_activation(
        commands,
        host_label="Codex IDE plugin composer",
        host_surface="codex_ide_visible_goal_mode",
    )


def _claude_code_activation(commands: dict[str, str], cli_bin: str) -> dict[str, Any]:
    return {
        "host_surface": "claude_code_native_loop",
        "entry_command_hint": "/loopx <task> then /loop",
        "activation_method": "arm_loopx_then_run_native_loop",
        "activation_input_command": commands["heartbeat_prompt_json"],
        "setup_command": f"{cli_bin} slash-commands --install --surface claude-code",
        "host_mutation": {
            "owner": "Claude Code",
            "host_command": "/loop",
            "cli_can_mutate_directly": False,
            "missing_host_tool_gate": (
                "Claude Code adapter or native /loop is unavailable; install the "
                "Claude Code LoopX surface or report the exact gate."
            ),
        },
        "activation_steps": [
            "Install or refresh the Claude Code LoopX surface when needed.",
            "Run `/loopx <task>` to arm LoopX state for the task.",
            "Run native `/loop`; the adapter gates each tick through LoopX should_run.",
        ],
        "success_criteria": [
            "Claude Code has the LoopX command surface installed.",
            "Native `/loop` is running with LoopX should_run gating, not an unrelated free-running loop.",
        ],
    }


def _pi_activation(commands: dict[str, str], cli_bin: str) -> dict[str, Any]:
    return {
        "host_surface": "pi_visible_goal_mode",
        "entry_command_hint": "/loopx <task>",
        "activation_method": "activate_loopx_pi_goal_extension",
        "activation_input_command": commands["heartbeat_prompt_json"],
        "setup_command": f"{cli_bin} slash-commands --install --surface pi",
        "host_mutation": {
            "owner": "Pi LoopX goal extension",
            "host_tool": "loopx_goal_activate",
            "tool_argument_mapping": {
                "activationToken": "pi_session_authority.token from the host startup/session packet",
                "goalId": PI_OPTIONAL_COMPAT_ECHO,
                "objective": "heartbeat_prompt.task_body",
                "agentId": PI_OPTIONAL_COMPAT_ECHO,
                "registryPath": PI_OPTIONAL_COMPAT_ECHO,
                "availableCapabilities": PI_OPTIONAL_COMPAT_ECHO,
            },
            "cli_can_mutate_directly": False,
            "missing_host_tool_gate": (
                "The LoopX Pi extension or loopx_goal_activate tool is unavailable; "
                "install the Pi surface and restart Pi before claiming autonomous "
                "heartbeat support."
            ),
        },
        "activation_steps": [
            "Install or refresh the LoopX Pi surface when needed.",
            "Run the heartbeat-prompt JSON command after project state and todos are written.",
            "Call loopx_goal_activate with the activationToken from the host startup/session packet and objective from task_body; authority fields are host-derived and must not be changed by the model.",
            "Let the extension gate every settled continuation and timer wake through LoopX quota should-run.",
        ],
        "success_criteria": [
            "The visible Pi session has a LoopX-backed goal bound through loopx_goal_activate.",
            "Quiet waits make no model call, active work auto-continues, and validated terminal no-follow-up stops the goal.",
        ],
    }


def _kunluncode_activation(commands: dict[str, str]) -> dict[str, Any]:
    return {
        "host_surface": "kunluncode_native_goal_controller",
        "entry_command_hint": (
            "loopx-kunluncode add <task> then loopx-kunluncode run"
        ),
        "activation_method": "bind_project_then_run_native_goal",
        "activation_input_command": commands["heartbeat_prompt_json"],
        "setup_command": (
            "loopx-kunluncode connect --project . --goal-id <goal-id> "
            "--agent-id <registered-agent-id>"
        ),
        "host_mutation": {
            "owner": "KunlunCode user MCP configuration",
            "host_command": "loopx-kunluncode run --project .",
            "cli_can_mutate_directly": True,
            "managed_mcp_server": "loopx-kunluncode",
            "missing_host_tool_gate": (
                "KunlunCode or its managed LoopX MCP server is unavailable; run "
                "the connect command and mcp test before claiming activation."
            ),
        },
        "activation_steps": [
            "Connect the project goal to a dedicated registered KunlunCode agent.",
            "Read back the managed MCP tools with `kunluncode mcp test loopx-kunluncode`.",
            "Add one bounded todo, then invoke or schedule `loopx-kunluncode run`.",
            "The outer controller creates or resumes native Goal Pro through app-server; the model does not type `/goal-pro`.",
            "Accept LoopX completion and quota writeback only after KunlunCode reports verifier-backed terminal success.",
        ],
        "success_criteria": [
            "KunlunCode resolves its own project binding and registered agent identity.",
            "The native strict goal auto-continues and preserves its thread across controller restarts.",
            "Model-visible MCP tools and same-goal lifecycle CLI writes cannot bypass outer-controller writeback ownership.",
            "The outer controller records delivery, completes the todo, and spends quota only after independent verification passes.",
        ],
    }


def _opencode_activation(commands: dict[str, str], cli_bin: str) -> dict[str, Any]:
    return {
        "host_surface": "opencode_visible_goal_mode",
        "entry_command_hint": "/loopx <task>",
        "activation_method": "activate_loopx_opencode_goal_bridge",
        "activation_input_command": commands["heartbeat_prompt_json"],
        "setup_command": (
            f"{cli_bin} slash-commands --install --surface opencode --with-goal-bridge"
        ),
        "host_mutation": {
            "owner": "OpenCode LoopX goal bridge",
            "host_tool": "loopx_goal_activate",
            "tool_argument_mapping": {
                "goalId": "heartbeat_prompt.goal_id",
                "objective": "heartbeat_prompt.task_body",
                "agentId": "heartbeat_prompt.agent_id when present",
                "registryPath": "explicit registry path when present",
                "availableCapabilities": "declared host capabilities when present",
            },
            "cli_can_mutate_directly": False,
            "missing_host_tool_gate": (
                "The LoopX OpenCode bridge or loopx_goal_activate tool is unavailable; "
                "install the OpenCode surface and restart OpenCode before claiming "
                "autonomous heartbeat support."
            ),
        },
        "activation_steps": [
            "Install or refresh the LoopX OpenCode surface when needed.",
            "Run the heartbeat-prompt JSON command after project state and todos are written.",
            "Call loopx_goal_activate with goalId from goal_id, objective from task_body, and optional agentId, registryPath, or availableCapabilities when those values are present.",
            "Let the bridge gate every idle continuation and timer wake through LoopX quota should-run.",
        ],
        "success_criteria": [
            "The visible OpenCode session has a LoopX-backed goal bound through loopx_goal_activate.",
            "Quiet waits make no model call, active work auto-continues, and validated terminal no-follow-up stops the goal.",
        ],
    }


def _opencode2_activation(commands: dict[str, str], cli_bin: str) -> dict[str, Any]:
    return {
        "host_surface": "opencode2_goal_worker_mode",
        "entry_command_hint": "/loopx <task>",
        "activation_method": "start_opencode2_goal_worker",
        "activation_input_command": commands["heartbeat_prompt_json"],
        "setup_command": None,
        "host_mutation": {
            "owner": "LoopX OpenCode 2 goal worker",
            "host_tool": "opencode2-goal-worker",
            "tool_argument_mapping": {
                "goalId": "heartbeat_prompt.goal_id",
                "directory": "the project directory",
                "agentId": "heartbeat_prompt.agent_id when present",
                "registryPath": "explicit registry path when present",
                "availableCapabilities": "declared host capabilities when present",
                "taskBody": "heartbeat_prompt.task_body",
                "sessionId": "an existing OpenCode 2 session id when reattaching",
            },
            "cli_can_mutate_directly": True,
            "missing_host_tool_gate": (
                "The loopx opencode2-goal-worker command or the opencode2 binary "
                "is unavailable; install LoopX and OpenCode 2 before claiming "
                "autonomous heartbeat support."
            ),
        },
        "activation_steps": [
            "Run the heartbeat-prompt JSON command after project state and todos are written.",
            "Start the worker from the project directory: loopx opencode2-goal-worker --goal-id <goal_id> --directory . --task-body <task_body>, with --agent-id and --capability flags when those values are present.",
            "Let the worker create or attach the visible OpenCode 2 session, gate every turn through LoopX quota should-run, and keep quiet waits free of model calls.",
        ],
        "success_criteria": [
            "A visible OpenCode 2 session runs the goal and the worker survives TUI close because it owns the timers.",
            "Quiet waits make no model call, active work auto-continues, user intervention pauses visibly, and validated terminal no-follow-up stops the worker.",
        ],
    }


def _traex_activation(commands: dict[str, str]) -> dict[str, Any]:
    return {
        "host_surface": "traex_visible_goal_mode",
        "entry_command_hint": "$loopx <task> or the explicit LoopX skill from /skills",
        "activation_method": "set_visible_goal",
        "activation_input_command": commands["visible_goal_prompt_json"],
        "host_mutation": {
            "owner": "TraeX CLI TUI",
            "host_command": "/goal <task_body>",
            "cli_can_mutate_directly": False,
            "requires_host_feature_flag": "[features] goals = true in ~/.trae/traecli.toml",
            "missing_host_tool_gate": (
                "TraeX /goal is unavailable; if goal mode is disabled, show the exact "
                "`/goal <task_body>` text for the user to paste after enabling "
                "`[features] goals = true`. Do not claim another host-loop surface "
                "without a verified LoopX adapter."
            ),
        },
        "activation_steps": [
            "Run the visible-goal prompt JSON command after project state and todos are written.",
            "Read task_body from the JSON payload.",
            "Set the visible TraeX goal to `/goal <task_body>`; enable `[features] goals = true` first if goal mode is off.",
            "Keep delivery in the visible TUI turn; gate each continuation through LoopX quota should-run and do not switch to hidden headless execution.",
        ],
        "success_criteria": [
            "The visible TraeX TUI has `/goal <task_body>` active for this goal.",
            "Future goal turns enter through LoopX quota/status/state before delivery work.",
        ],
    }


def _deepseek_harness_activation(commands: dict[str, str]) -> dict[str, Any]:
    return {
        "host_surface": "deepseek_harness_automation_loop",
        "entry_command_hint": (
            "loopx turn run-once --host dsh "
            "(compat: --host generic-cli with python -m loopx.dsh_goal_mode or "
            "scripts/dsh_turn_host_adapter.py)"
        ),
        "activation_method": "external_loop_driver",
        "activation_input_command": commands["heartbeat_prompt_json"],
        "host_mutation": {
            "owner": "DeepSeek Harness adapter",
            "host_loop_primitive": "deepseek-harness-sdk",
            "cli_can_mutate_directly": False,
            "missing_host_tool_gate": (
                "DeepSeek Harness SDK or dsh runtime is unavailable; install "
                "`loopx[deepseek-harness]` and verify the dsh cordis configuration "
                "before claiming an automation loop."
            ),
        },
        "activation_steps": [
            "Install the optional DeepSeek Harness SDK (`loopx[deepseek-harness]`).",
            "Prepare a dsh cordis.yml and any DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL settings.",
            "Run the heartbeat-prompt JSON command after project state and todos are written.",
            "Run `loopx turn run-once --host dsh`; use the generic-cli adapter "
            "command only as a compatibility or rollback path.",
            "Start every automatic tick from quota should-run and stop when it says stop.",
        ],
        "success_criteria": [
            "The DeepSeek Harness adapter returns a typed loopx_turn_result_v0.",
            "Structured dsh failures reach the Turn journal without provider prose.",
            "Independent validation passes before LoopX writes state or spends quota.",
            "Opaque dsh session roots stay outside public LoopX evidence.",
        ],
    }


def _deepseek_harness_native_activation(commands: dict[str, str]) -> dict[str, Any]:
    return {
        "host_surface": "deepseek_harness_native_same_session",
        "entry_command_hint": (
            "install the DSH LoopX plugin, run /loopx-init, then invoke the loopx skill"
        ),
        "activation_method": "same_session_plugin_driver",
        "activation_input_command": commands["heartbeat_prompt_json"],
        "host_mutation": {
            "owner": "DSH LoopX plugin",
            "host_loop_primitive": "exact live Agent.followup",
            "cli_can_mutate_directly": False,
            "missing_host_tool_gate": (
                "The DSH LoopX plugin or its same-session driver is unavailable; install "
                "the plugin and run `/loopx-init` before claiming an autonomous loop."
            ),
        },
        "activation_steps": [
            "Install the DSH LoopX plugin and run `/loopx-init`.",
            "Invoke the installed `loopx` skill with the original task text.",
            "Bind the exact DSH session while starting or attaching the Goal.",
            "Let the plugin driver call `quota should-run` before each same-session follow-up.",
        ],
        "success_criteria": [
            "The exact live DSH Agent and session id match the durable LoopX binding.",
            "Each automatic follow-up has a fresh positive quota decision.",
            "Human input cancels any reserved automatic follow-up before delivery.",
        ],
    }


def _manual_activation(commands: dict[str, str]) -> dict[str, Any]:
    return {
        "host_surface": "external_scheduler_or_manual_shell",
        "entry_command_hint": "run loopx agent-onboard, then wire a scheduler or invoke quota manually",
        "activation_method": "external_loop_driver",
        "activation_input_command": commands["heartbeat_prompt_json"],
        "host_mutation": {
            "owner": "external agent or operator",
            "cli_can_mutate_directly": False,
            "missing_host_tool_gate": (
                "No host loop is declared. Wire a cron/task/agent loop that starts "
                "from quota should-run, or run LoopX manually."
            ),
        },
        "activation_steps": [
            "Generate the heartbeat-prompt JSON task body or equivalent lifecycle prompt.",
            "Configure the external loop driver to call quota should-run before each delivery slice.",
            "Record evidence/writeback and spend quota only after validated delivery work.",
        ],
        "success_criteria": [
            "An external loop driver reliably starts from the LoopX quota/status contract.",
            "The driver has an explicit stop/backoff policy for no-progress or unchanged polls.",
        ],
    }


def build_host_loop_activation_packet(
    *,
    agent_type: str,
    goal_id: str,
    cli_bin: str = "loopx",
    runtime_root: str | None = None,
    identity_runtime_root: str | None = None,
    agent_id: str | None = None,
    registered_agents: list[str] | None = None,
    available_capabilities: list[str] | None = None,
    fresh_agent_default: bool = False,
    thread_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    canonical = normalize_agent_type(agent_type)
    identity = _identity_state(
        agent_id=agent_id,
        registered_agents=registered_agents,
        fresh_agent_default=fresh_agent_default,
        thread_binding=thread_binding,
    )
    selected_agent_id = identity.get("selected_agent_id")
    activation_allowed = bool(identity.get("activation_allowed"))
    normalized_available_capabilities = normalize_required_capabilities(
        available_capabilities
    )
    commands: dict[str, Any] = (
        _heartbeat_commands(
            goal_id=goal_id,
            agent_type=canonical,
            cli_bin=cli_bin,
            runtime_root=runtime_root,
            agent_id=str(selected_agent_id) if selected_agent_id else None,
            available_capabilities=normalized_available_capabilities,
        )
        if activation_allowed
        else {
            "heartbeat_prompt_json": None,
            "heartbeat_prompt": None,
            "visible_goal_prompt_json": None,
        }
    )
    if canonical == "codex-app" and activation_allowed:
        cadence_args = [cli_bin, "--format", "json"]
        if runtime_root:
            cadence_args.extend(["--runtime-root", runtime_root])
        cadence_args.extend(["automation-cadence", "--goal-id", goal_id])
        if selected_agent_id:
            cadence_args.extend(["--agent-id", str(selected_agent_id)])
        commands["automation_cadence_json"] = shlex.join(cadence_args)
    if canonical == "ark-managed-agent":
        surface = _ark_managed_agent_activation(commands)
    elif canonical == "codex-app":
        surface = _codex_app_activation(commands)
    elif canonical == "trae_app":
        surface = _trae_app_activation(commands)
    elif canonical == "codex-app-ssh":
        surface = _codex_app_ssh_activation(commands)
    elif canonical == "codex-ide-plugin":
        surface = _codex_ide_activation(commands)
    elif canonical == "codex-cli":
        surface = _codex_cli_activation(commands)
    elif canonical == "claude-code":
        surface = _claude_code_activation(commands, cli_bin)
    elif canonical == "kunluncode":
        surface = _kunluncode_activation(commands)
    elif canonical == "opencode":
        surface = _opencode_activation(commands, cli_bin)
    elif canonical == "opencode2":
        surface = _opencode2_activation(commands, cli_bin)
    elif canonical == "traex-cli":
        surface = _traex_activation(commands)
    elif canonical == "pi":
        surface = _pi_activation(commands, cli_bin)
    elif canonical == "gemini-cli":
        surface = gemini_cli_activation(commands, cli_bin)
    elif canonical == "cursor-agent":
        surface = cursor_agent_activation(commands, cli_bin)
    elif canonical == "zcode":
        surface = zcode_activation(commands, cli_bin)
    elif canonical == "agy":
        surface = agy_cli_activation(commands, cli_bin)
    elif canonical == "kiro-cli":
        surface = kiro_cli_activation(commands, cli_bin)
    elif canonical == "deepseek-harness":
        surface = _deepseek_harness_activation(commands)
    elif canonical == "deepseek-harness-native":
        surface = _deepseek_harness_native_activation(commands)
    else:
        surface = _manual_activation(commands)
        if canonical == "other-agent":
            surface["entry_command_hint"] = "@loopx <task>, $loopx <task>, or another explicit host command facade"
            surface["host_surface"] = "custom_agent_loop_driver"
    identity_selection_gate = None
    if not activation_allowed:
        choices = []
        for candidate in identity["registered_agents"]:
            candidate_commands = _heartbeat_commands(
                goal_id=goal_id,
                agent_type=canonical,
                cli_bin=cli_bin,
                runtime_root=runtime_root,
                agent_id=candidate,
                available_capabilities=normalized_available_capabilities,
            )
            choice: dict[str, Any] = {
                "agent_id": candidate,
                "activation_input_command": (
                    candidate_commands["visible_goal_prompt_json"]
                    if canonical == "traex-cli"
                    else candidate_commands["heartbeat_prompt_json"]
                ),
            }
            if canonical != "traex-cli":
                choice.update(
                    {
                        "heartbeat_prompt_json": candidate_commands[
                            "heartbeat_prompt_json"
                        ],
                        "heartbeat_prompt": candidate_commands["heartbeat_prompt"],
                    }
                )
            choice.update(
                {
                    "mode": "takeover_existing_agent",
                    "requires_explicit_takeover_intent": True,
                }
            )
            choices.append(choice)
        requested_agent_id = identity.get("requested_agent_id")
        fresh_agent_id = str(requested_agent_id or "<new-public-safe-agent-id>")
        register_command = render_register_agent_command(
            goal_id,
            agent_id=fresh_agent_id,
            cli_bin=cli_bin,
            runtime_root=identity_runtime_root or runtime_root,
        )
        fresh_registration = (
            {
                "mode": "register_fresh_agent",
                "recommended": True,
                "agent_id": fresh_agent_id,
                "preview_command": register_command,
                "execute_command": f"{register_command} --execute",
                "continuation_contract": {
                    "schema_version": "loopx_fresh_agent_registration_continuation_v0",
                    "requires_execute_result": True,
                    "required_result": {
                        "ok": True,
                        "changed": True,
                        "written": True,
                        "global_sync": {"ok": True},
                        "registration_readback": {"verified": True},
                    },
                },
                "continuation": (
                    "treat preview as advisory; continue only when the execute result "
                    "reports ok=true, changed=true, written=true, global_sync.ok=true, "
                    "and registration_readback.verified=true, then rerun onboarding with "
                    "the newly registered --agent-id before todo writeback or host-loop "
                    "activation"
                ),
            }
            if fresh_agent_default
            else None
        )
        identity_selection_gate = {
            **identity,
            "choices": choices,
            "default_action": (
                "register_fresh_agent" if fresh_registration else "select_agent_identity"
            ),
            "fresh_agent_registration": fresh_registration,
            "external_write_required": bool(fresh_registration),
        }
        surface["activation_method"] = (
            "register_fresh_agent_or_explicit_takeover_before_host_loop_activation"
            if fresh_registration
            else "select_agent_identity_before_host_loop_activation"
        )
        surface["activation_input_command"] = None
        if fresh_registration:
            gate_steps = [
                "Register a fresh public-safe agent id from identity_selection_gate by default.",
                "Only select an existing lane when the user explicitly requests takeover of that exact agent.",
                "Rerun onboarding with the selected --agent-id.",
            ]
            gate_criterion = (
                "A fresh registered agent identity is selected, or exact takeover intent is recorded."
            )
        else:
            gate_steps = [
                "Select one registered agent lane from identity_selection_gate; do not register a fresh agent.",
                "When a thread id is available, persist the thread-to-agent binding so later /loopx calls reuse this lane.",
                "Run that choice's host-specific activation_input_command.",
            ]
            gate_criterion = "One registered agent identity is explicitly selected."
        surface["activation_steps"] = [
            *gate_steps,
            *surface["activation_steps"][1:],
        ]
        surface["success_criteria"] = [
            gate_criterion,
            *surface["success_criteria"],
        ]
    return {
        "schema_version": SCHEMA_VERSION,
        "agent_type": canonical,
        "agent_model": "peer_v1",
        "goal_id": goal_id,
        "agent_id": selected_agent_id,
        "requested_agent_id": normalize_todo_claimed_by(agent_id),
        "available_capabilities": normalized_available_capabilities,
        "activation_state": identity["state"],
        "activation_allowed": activation_allowed,
        "identity_contract": identity,
        "identity_selection_gate": identity_selection_gate,
        "activation_required_after_todo_write": True,
        "status_probe_policy": {
            "check_once_during_onboarding": True,
            "cheap_recheck_on_loopx": "only when activation is missing, unknown, stale, or the agent is newly installed",
            "do_not_recompute_every_loopx_turn": True,
        },
        "commands": commands,
        **surface,
    }
