from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent_registry import registered_agent_ids_for_goal
from .bootstrap import default_goal_id
from .paths import registered_goal_state_file
from .capabilities.issue_fix.candidate_preflight import (
    candidate_preflight_input_contract,
)
from .capabilities.issue_fix.workflow_plan import (
    ISSUE_FIX_GOAL_CANDIDATE_DISCOVERY_COMMAND_TEMPLATE,
    build_issue_fix_goal_command_templates,
)
from .control_plane.effect_program import effect_program_from_ordered_steps
from .control_plane.goals.orphaned_goal_state import (
    ORPHANED_GOAL_STATE_CONNECTION,
    absent_goal_connection,
    fence_command_pack,
    fenced_standalone_message,
    guided_fence,
    render_guided_lines,
)
from .control_plane.goals.start_contract import (
    build_goal_start_contract,
    build_goal_start_prompt,
)
from .control_plane.goals.start_goal_todo_delta import (
    append_todo_delta_render_line,
    existing_runnable_agent_frontier,
    todo_authoring_steps,
)
from .control_plane.scheduler.execution_context import (
    GUIDED_START_TURN_RUNTIME_PROFILES,
)
from .host_loop_activation import (
    agent_type_for_host_surface,
    build_host_loop_activation_packet,
    scheduler_command_binding_for_agent_type,
)
from .history import load_registry
from .project_alias import resolve_canonical_project_alias
from .project_prompt import (
    DEFAULT_HANDOFF_ADAPTER_KIND,
    DEFAULT_HANDOFF_ADAPTER_STATUS,
    render_available_capability_args,
    render_cli_command_prefix,
    render_goal_start_bootstrap_command,
    render_optional_cli_arg,
    render_quota_guard_command,
    render_refresh_state_command,
    shell_arg,
)
from .paths import resolve_runtime_root
from .registry import registry_goals, resolve_state_file
from .slash_commands import build_slash_command_catalog
from .thread_agent_binding import normalize_thread_id, resolve_thread_agent_binding

SCHEMA_VERSION = "loopx_bootstrap_command_pack_v0"
CANONICAL_SLASH_COMMAND = "/loopx"
GUIDED_START_SCHEMA_VERSION = "loopx_start_goal_guided_v0"
PACKET_SUMMARY_SCHEMA_VERSION = "loopx_start_goal_packet_summary_v0"
PACKET_MEASUREMENT_SCHEMA_VERSION = "loopx_packet_duplication_measurement_v0"
GUIDED_COMMAND_PACK_PROJECTION_SCHEMA_VERSION = (
    "loopx_guided_command_pack_projection_v0"
)
HOST_SURFACE_SELECTION_SCHEMA_VERSION = "loopx_host_surface_selection_gate_v0"
GOAL_CAPABILITY_ROUTE_SCHEMA_VERSION = "loopx_goal_capability_route_v0"
START_GOAL_CAPABILITY_ROUTES = ("issue-fix",)
START_GOAL_HOST_SURFACES = (
    "codex-app",
    "trae_app",
    "codex-app-ssh",
    "codex-ide-plugin",
    "codex-cli-tui",
    "claude-code",
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
    "ark-managed-agent",
    "shell",
    "other-agent",
)


def _iter_string_leaves(
    value: Any, path: tuple[str, ...] = ()
) -> list[tuple[tuple[str, ...], str]]:
    leaves: list[tuple[tuple[str, ...], str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "packet_summary":
                continue
            leaves.extend(_iter_string_leaves(child, (*path, str(key))))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            leaves.extend(_iter_string_leaves(child, (*path, str(index))))
    elif isinstance(value, str):
        leaves.append((path, value))
    return leaves


def _without_packet_summary(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_packet_summary(child)
            for key, child in value.items()
            if key != "packet_summary"
        }
    if isinstance(value, list):
        return [_without_packet_summary(child) for child in value]
    return value


def _is_command_value(path: tuple[str, ...]) -> bool:
    if not path:
        return False
    leaf = path[-1]
    return (
        (len(path) >= 2 and path[-2] == "commands")
        or leaf in {"command", "command_template", "prompt", "canonical_cli_command"}
        or leaf.endswith(("_command", "_prompt", "_command_if_needed"))
    )


def _measure_packet_duplication(
    payload: dict[str, Any], *, objective: str | None
) -> dict[str, Any]:
    compatibility_payload = _without_packet_summary(payload)
    leaves = _iter_string_leaves(compatibility_payload)
    objective_occurrences = 0
    objective_field_count = 0
    if objective:
        for _, value in leaves:
            count = value.count(objective)
            if count:
                objective_occurrences += count
                objective_field_count += 1

    command_values: list[str] = []
    for path, value in leaves:
        if not value or not _is_command_value(path):
            continue
        command_values.append(value)
    unique_command_count = len(set(command_values))

    compatibility_json = json.dumps(
        compatibility_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "schema_version": PACKET_MEASUREMENT_SCHEMA_VERSION,
        "measurement_scope": "compatibility_projection_without_packet_summary",
        "serialized_bytes": len(compatibility_json.encode("utf-8")),
        "objective_content": {
            "substring_occurrences": objective_occurrences,
            "containing_field_count": objective_field_count,
            "duplicate_occurrences": max(0, objective_occurrences - 1),
        },
        "command_content": {
            "candidate_occurrences": len(command_values),
            "unique_values": unique_command_count,
            "duplicate_occurrences": len(command_values) - unique_command_count,
        },
    }


def _build_packet_summary(
    payload: dict[str, Any],
    *,
    packet_kind: str,
    detail_refs: dict[str, str],
    legacy_fields_retained: bool = True,
    compact_projection_default: bool = False,
) -> dict[str, Any]:
    next_step = payload.get("recommended_next_step")
    next_step = next_step if isinstance(next_step, dict) else {}
    return {
        "schema_version": PACKET_SUMMARY_SCHEMA_VERSION,
        "packet_kind": packet_kind,
        "goal_id": payload.get("goal_id"),
        "objective": payload.get("goal_text"),
        "next_step_kind": next_step.get("kind"),
        "detail_refs": {
            name: {
                "schema_version": "loopx_packet_json_pointer_ref_v0",
                "json_pointer": pointer,
            }
            for name, pointer in detail_refs.items()
        },
        "compatibility": {
            "legacy_fields_retained": legacy_fields_retained,
            "compact_projection_default": compact_projection_default,
            "removal_gate": "explicit_host_shadow_parity",
        },
        "duplication_measurement": _measure_packet_duplication(
            payload,
            objective=str(payload.get("goal_text"))
            if payload.get("goal_text")
            else None,
        ),
    }


def _start_goal_command(
    *,
    project: str,
    goal_id: str | None,
    agent_id: str | None,
    thread_id: str | None,
    new_peer: bool,
    cli_bin: str,
    runtime_root: str | None = None,
    host_surface: str,
    goal_text: str,
    available_capabilities: list[str] | None,
    capability_route: str | None,
    fine_grained: bool,
    include_command_pack_detail: bool,
    display_name: str | None = None,
) -> str:
    return (
        f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)} "
        f"--format json start-goal --guided "
        f"--project {shell_arg(project)}"
        + (f" --goal-id {shell_arg(goal_id)}" if goal_id else "")
        + (f" --agent-id {shell_arg(agent_id)}" if agent_id else "")
        + (f" --thread-id {shell_arg(thread_id)}" if thread_id else "")
        + (" --new-peer" if new_peer else "")
        + f" --host-surface {shell_arg(host_surface)}"
        + render_available_capability_args(available_capabilities)
        + (" --fine-grained" if fine_grained else "")
        + (
            f" --capability-route {shell_arg(capability_route)}"
            if capability_route
            else ""
        )
        + f" --goal-text {shell_arg(goal_text)}"
        + render_optional_cli_arg("--display-name", display_name)
        + (" --include-command-pack-detail" if include_command_pack_detail else "")
    )


def _start_goal_detail_command(
    *,
    project: str,
    goal_id: str | None,
    agent_id: str | None,
    thread_id: str | None,
    new_peer: bool,
    cli_bin: str,
    runtime_root: str | None = None,
    host_surface: str,
    goal_text: str,
    available_capabilities: list[str] | None,
    capability_route: str | None,
    fine_grained: bool,
    display_name: str | None = None,
) -> str:
    return _start_goal_command(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        thread_id=thread_id,
        new_peer=new_peer,
        cli_bin=cli_bin,
        runtime_root=runtime_root,
        host_surface=host_surface,
        goal_text=goal_text,
        available_capabilities=available_capabilities,
        capability_route=capability_route,
        fine_grained=fine_grained,
        include_command_pack_detail=True,
        display_name=display_name,
    )


def _guided_command_pack_projection(
    command_pack: dict[str, Any],
    *,
    detail_command: str,
) -> dict[str, Any]:
    """Keep the guided hot path actionable without nesting the full host packet."""

    connection = command_pack.get("project_connection")
    registry_path = connection.get("registry") if isinstance(connection, dict) else None

    projection: dict[str, Any] = {
        "ok": command_pack.get("ok"),
        "schema_version": command_pack.get("schema_version"),
        "projection_schema_version": GUIDED_COMMAND_PACK_PROJECTION_SCHEMA_VERSION,
        "projection_mode": "guided_start_compatibility",
        "read_only": command_pack.get("read_only"),
        "project": command_pack.get("project"),
        "registry_path": registry_path,
        "goal_id": command_pack.get("goal_id"),
        "agent_id": command_pack.get("agent_id"),
        "host_surface": command_pack.get("host_surface"),
        "canonical_cli_command": command_pack.get("canonical_cli_command"),
        "goal_start_contract": command_pack.get("goal_start_contract"),
        "commands": command_pack.get("commands"),
        "host_loop_activation": command_pack.get("host_loop_activation"),
        "safety_contract": command_pack.get("safety_contract"),
        "detail_command": detail_command,
    }
    projection["packet_summary"] = _build_packet_summary(
        projection,
        packet_kind="bootstrap_command_pack_projection",
        detail_refs={
            "goal_start_contract": "#/goal_start_contract",
            "commands": "#/commands",
            "host_loop_activation": "#/host_loop_activation",
            "safety_contract": "#/safety_contract",
            "full_command_pack": "#/detail_command",
        },
        legacy_fields_retained=False,
        compact_projection_default=True,
    )
    return projection


def build_start_goal_host_surface_selection_packet(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    goal_text: str,
    thread_id: str | None = None,
    new_peer: bool = False,
    available_capabilities: list[str] | None = None,
    capability_route: str | None = None,
    fine_grained: bool = False,
    include_command_pack_detail: bool = False,
    display_name: str | None = None,
    runtime_root_arg: str | None = None,
) -> dict[str, Any]:
    """Fail closed when the caller has not identified the current Codex host."""

    resolved_project = str(_resolve_project(project))
    registry_path = Path(resolved_project) / ".loopx" / "registry.json"
    registry_payload, _registry_error = _read_registry(registry_path)
    command_runtime_root, _ = _runtime_roots(
        registry_payload,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
    )
    normalized_goal_text = " ".join(goal_text.split())
    host_descriptions = {
        "codex-app": "Codex desktop app with heartbeat automation support",
        "trae_app": "Trae desktop app with heartbeat automation support",
        "codex-app-ssh": "Codex desktop app over SSH with visible /goal support",
        "codex-ide-plugin": "Codex IDE plugin; activate its visible goal mode",
        "codex-cli-tui": "terminal Codex TUI with visible /goal support",
        "claude-code": "Claude Code with native /loop",
        "opencode": "OpenCode LoopX goal bridge",
        "opencode2": "OpenCode 2 session driven by the LoopX goal worker",
        "traex-cli": "terminal TraeX TUI with visible /goal support (needs [features] goals = true)",
        "pi": "Pi LoopX goal extension",
        "gemini-cli": "Gemini CLI driving its own loop through the LoopX skill facade",
        "cursor-agent": "cursor-agent driving its own loop through the LoopX skill facade and MCP server",
        "zcode": "ZCode loop via the LoopX skill facade",
        "agy": "agy session loop via the LoopX skill facade; native /goal + schedule wakes while the session lives",
        "kiro-cli": "Kiro CLI loop via the LoopX skill facade; native /goal iteration budget",
        "deepseek-harness": "DeepSeek Harness automation loop through loopx.dsh_goal_mode (compat: scripts/dsh_turn_host_adapter.py)",
        "deepseek-harness-native": "DeepSeek Harness same-session LoopX skill and plugin driver",
        "ark-managed-agent": "Ark Managed Agent with one-shot Goal submission",
        "shell": "manual shell or an explicitly configured external scheduler",
        "other-agent": "custom agent host using the returned activation contract",
    }
    choices: list[dict[str, Any]] = []
    for host_surface in START_GOAL_HOST_SURFACES:
        rerun_command = (
            f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=command_runtime_root)} "
            f"start-goal --guided "
            f"--project {shell_arg(resolved_project)}"
            + (f" --goal-id {shell_arg(goal_id)}" if goal_id else "")
            + (f" --agent-id {shell_arg(agent_id)}" if agent_id else "")
            + (f" --thread-id {shell_arg(thread_id)}" if thread_id else "")
            + (" --new-peer" if new_peer else "")
            + f" --host-surface {shell_arg(host_surface)}"
            + render_available_capability_args(available_capabilities)
            + (" --fine-grained" if fine_grained else "")
            + (
                f" --capability-route {shell_arg(capability_route)}"
                if capability_route
                else ""
            )
            + f" --goal-text {shell_arg(normalized_goal_text)}"
            + render_optional_cli_arg("--display-name", display_name)
            + (" --include-command-pack-detail" if include_command_pack_detail else "")
        )
        choices.append(
            {
                "host_surface": host_surface,
                "description": host_descriptions[host_surface],
                "rerun_command": rerun_command,
            }
        )
    reason = (
        "host surface is required because Codex App automation, Codex App over SSH, "
        "the Codex IDE plugin, Codex CLI, and Ark Managed Agent "
        "have different continuation contracts"
    )
    gate = {
        "schema_version": HOST_SURFACE_SELECTION_SCHEMA_VERSION,
        "state": "selection_required",
        "action_required": True,
        "reason": reason,
        "required_cli_arg": "--host-surface <exact-host-surface>",
        "choices": choices,
    }
    transaction = {
        "schema_version": GUIDED_START_SCHEMA_VERSION,
        "mode": "dry_run_preview",
        "writes_now": False,
        "spends_quota_now": False,
        "goal_text": normalized_goal_text,
        "display_name": display_name,
        "blocked_by": "host_surface_selection",
        "host_surface_selection_gate": gate,
        "ordered_steps": [
            {
                "id": "select_host_surface",
                "kind": "host_surface_selection_gate",
                "choices": choices,
                "purpose": "select the current host before planning, mutation, or loop activation",
            }
        ],
        "idempotency_policy": {"safe_to_rerun_preview": True},
        "preserve_todos_policy": {
            "force_bootstrap_default": "forbidden_in_guided_flow",
            "before_destructive_reconnect": "select a host before any mutation",
            "preferred_scope_change": "select a host before any mutation",
        },
    }
    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": GUIDED_START_SCHEMA_VERSION,
        "read_only": True,
        "guided": True,
        "project": resolved_project,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "thread_id": normalize_thread_id(thread_id),
        "new_peer": new_peer,
        "host_surface": None,
        "goal_text": normalized_goal_text,
        "display_name": display_name,
        "host_surface_selection_gate": gate,
        "recommended_next_step": {
            "kind": "select_host_surface",
            "requires_user_confirmation": False,
            "requires_host_surface_selection": True,
            "summary": reason,
        },
        "guided_transaction": transaction,
        "command_pack_detail_included": include_command_pack_detail,
        "safety_contract": {
            "writes_registry": False,
            "writes_state_file": False,
            "creates_heartbeat": False,
            "spends_quota": False,
            "mutation_commands_are_previewed": False,
            "force_bootstrap_allowed": False,
        },
    }
    payload["message"] = render_start_goal_guided_markdown(payload)
    payload["packet_summary"] = _build_packet_summary(
        payload,
        packet_kind="guided_start_host_surface_selection",
        detail_refs={
            "host_surface_selection_gate": "#/host_surface_selection_gate",
            "guided_transaction": "#/guided_transaction",
            "safety_contract": "#/safety_contract",
            "compatibility_message": "#/message",
        },
    )
    return payload


def _resolve_project(project: Path) -> Path:
    project = project.expanduser()
    try:
        return project.resolve()
    except OSError:
        return project.absolute()


def _read_registry(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        payload = load_registry(path)
    except (OSError, ValueError) as exc:
        return None, str(exc)
    return payload, None


def _runtime_roots(
    registry: dict[str, Any] | None,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
) -> tuple[str | None, str | None]:
    if runtime_root_arg:
        resolved = str(
            resolve_runtime_root(
                registry or {},
                override=runtime_root_arg,
                registry_path=registry_path,
            )
        )
        return resolved, resolved
    if not isinstance(registry, dict) or not registry.get("common_runtime_root"):
        return None, None
    return None, str(resolve_runtime_root(registry, registry_path=registry_path))


def _select_goal(goals: list[dict[str, Any]], goal_id: str | None) -> tuple[str, dict[str, Any] | None]:
    if goal_id:
        for goal in goals:
            if goal.get("id") == goal_id:
                return goal_id, goal
        return goal_id, None
    if goals:
        first_goal_id = str(goals[0].get("id"))
        return first_goal_id, goals[0]
    return "", None


def inspect_bootstrap_connection(
    project: Path,
    *,
    goal_id: str | None = None,
    resolve_linked_worktree_alias: bool = True,
) -> dict[str, Any]:
    """Inspect either the canonical project route or the caller's exact route."""

    input_project = _resolve_project(project)
    alias = (
        resolve_canonical_project_alias(input_project, goal_id=goal_id)
        if resolve_linked_worktree_alias
        else {
            "applied": False,
            "kind": "exact_project_route",
            "input_project": str(input_project),
            "reason": "the caller requires the explicitly requested project route",
        }
    )
    resolved_project = (
        _resolve_project(Path(str(alias.get("canonical_project"))))
        if alias.get("applied") and alias.get("canonical_project")
        else input_project
    )
    registry_path = resolved_project / ".loopx" / "registry.json"
    registry_exists = registry_path.exists()
    registry, registry_error = _read_registry(registry_path) if registry_exists else (None, None)
    inferred_goal_id = goal_id or default_goal_id(resolved_project)
    state_file = registered_goal_state_file(resolved_project, inferred_goal_id, registry)
    base_connection = {
        "input_project": str(input_project),
        "project": str(resolved_project),
        "canonical_project_alias": alias,
        "registry": str(registry_path),
    }

    if registry_error:
        return absent_goal_connection(
            base_connection=base_connection,
            goal_id=inferred_goal_id,
            state_file=state_file,
            registry_exists=registry_exists,
            absence_connection="registry_invalid",
            absence_reason=registry_error,
        )

    if not registry:
        return absent_goal_connection(
            base_connection=base_connection,
            goal_id=inferred_goal_id,
            state_file=state_file,
            registry_exists=False,
            absence_connection="not_connected",
            absence_reason="project-local .loopx/registry.json is missing",
        )

    goals = registry_goals(registry)
    selected_goal_id, selected_goal = _select_goal(goals, goal_id)
    resolved_goal_id = selected_goal_id or inferred_goal_id
    fallback_state_file = registered_goal_state_file(resolved_project, resolved_goal_id, registry)
    goal_state_file = (
        resolve_state_file(resolved_project, str(selected_goal.get("state_file")))
        if selected_goal and selected_goal.get("state_file")
        else None
    )
    state_file = goal_state_file or fallback_state_file

    if selected_goal is None:
        return absent_goal_connection(
            base_connection=base_connection,
            goal_id=resolved_goal_id,
            state_file=state_file,
            registry_exists=True,
            absence_connection="registry_without_goal",
            absence_reason="registry exists but no matching goal entry was found",
            known_goal_ids=[str(goal.get("id")) for goal in goals],
        )

    if not selected_goal.get("state_file"):
        return {
            **base_connection,
            "registry_exists": True,
            "goal_id": resolved_goal_id,
            "goal_found": True,
            "state_file": str(state_file),
            "state_file_exists": state_file.exists(),
            "connection_state": "registry_goal_missing_state_file",
            "mutation_confirmation_required": True,
            "reason": "goal entry does not declare state_file",
        }

    if not state_file.exists():
        return {
            **base_connection,
            "registry_exists": True,
            "goal_id": resolved_goal_id,
            "goal_found": True,
            "state_file": str(state_file),
            "state_file_exists": False,
            "connection_state": "state_file_missing",
            "mutation_confirmation_required": True,
            "reason": "registry goal points at a state_file that is missing",
        }

    return {
        **base_connection,
        "registry_exists": True,
        "goal_id": resolved_goal_id,
        "goal_found": True,
        "state_file": str(state_file),
        "state_file_exists": True,
        "connection_state": "connected",
        "mutation_confirmation_required": False,
        "reason": "registry goal and active state_file are present",
    }


def _bootstrap_command(
    *,
    project: str,
    goal_id: str,
    cli_bin: str,
    runtime_root: str | None,
    dry_run: bool,
    fine_grained: bool = False,
) -> str:
    lines = [
        f"cd {shell_arg(project)}",
        f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)} bootstrap \\",
        "  --project . \\",
        f"  --goal-id {shell_arg(goal_id)} \\",
        f"  --adapter-kind {shell_arg(DEFAULT_HANDOFF_ADAPTER_KIND)} \\",
        f"  --adapter-status {shell_arg(DEFAULT_HANDOFF_ADAPTER_STATUS)}",
    ]
    if fine_grained:
        lines[-1] += " \\"
        lines.append("  --fine-grained")
    if dry_run:
        lines[-1] += " \\"
        lines.append("  --dry-run")
    return "\n".join(lines)


def _project_command(project: str, command: str) -> str:
    return "\n".join([f"cd {shell_arg(project)}", command])


def _selected_goal_capability_route(
    capability_route: str | None,
) -> dict[str, Any] | None:
    if capability_route is None:
        return None
    if capability_route not in START_GOAL_CAPABILITY_ROUTES:
        raise ValueError(
            "unsupported --capability-route; expected one of: "
            + ", ".join(START_GOAL_CAPABILITY_ROUTES)
        )
    return {
        "schema_version": GOAL_CAPABILITY_ROUTE_SCHEMA_VERSION,
        "capability_id": "issue-fix",
        "selection_source": "explicit_capability_route",
        "selection_reason_code": "capability_route_enabled",
        "entry_command_key": "issue_fix_workflow_plan_template",
        "admission_command_key": "issue_fix_feasibility_template",
        "candidate_authority": "public_open_tracker_issue",
        "authority_refresh_required": "current issue body and latest comments",
        "candidate_preflight": candidate_preflight_input_contract(),
        "implementation_admission": {
            "status": "qualification_required",
            "state_owner": "issue_fix",
            "route_scope": "start_goal_bootstrap_only",
            "candidate_receipt_stream": "candidate-preflight",
            "feasibility_required_for_route": "proceed",
            "durable_execution_binding": {
                "schema_version": "capability_execution_binding_v0",
                "capability_id": "issue-fix",
                "todo_binding_ref_field": "capability_binding_ref",
                "authority_source": "admission_result.capability_execution_binding",
                "authority_stream": "feasibility",
                "authority_packet_schema_version": "issue_fix_feasibility_v0",
                "bound_todo_fields": ["action_kind", "target_key"],
                "legacy_unbound_todo_fallback": {
                    "authority": "current_feasibility_row",
                    "match": "exact_action_kind_and_target_key",
                    "prefix_match_allowed": False,
                },
            },
        },
        "activation_condition": (
            "after selecting a public issue candidate and before substantive "
            "implementation"
        ),
    }



def build_loopx_bootstrap_command_pack(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    host_surface: str,
    goal_text: str | None = None,
    thread_id: str | None = None,
    new_peer: bool = False,
    available_capabilities: list[str] | None = None,
    capability_route: str | None = None,
    fine_grained: bool = False,
    resolve_linked_worktree_alias: bool = True,
    display_name: str | None = None,
    runtime_root_arg: str | None = None,
) -> dict[str, Any]:
    inspection = inspect_bootstrap_connection(
        project,
        goal_id=goal_id,
        resolve_linked_worktree_alias=resolve_linked_worktree_alias,
    )
    resolved_project = str(inspection["project"])
    resolved_goal_id = str(inspection["goal_id"])
    connected = inspection.get("connection_state") == "connected"
    mutation_confirmation_required = bool(inspection.get("mutation_confirmation_required"))
    normalized_goal_text = " ".join(goal_text.split()) if goal_text else None
    normalized_thread_id = normalize_thread_id(thread_id)
    explicit_goal_start = bool(normalized_goal_text)
    issue_fix_hint_commands = build_issue_fix_goal_command_templates(
        cli_bin=cli_bin,
        goal_id="<goal-id>",
    )
    agent_type = agent_type_for_host_surface(host_surface)
    registry_path = Path(str(inspection["registry"]))
    registry_payload, _registry_error = _read_registry(registry_path)
    registry_goal = next(
        (
            goal
            for goal in registry_goals(registry_payload or {})
            if str(goal.get("id")) == resolved_goal_id
        ),
        None,
    )
    registered_agents = registered_agent_ids_for_goal(registry_goal)
    command_runtime_root, identity_runtime_root = _runtime_roots(
        registry_payload,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
    )
    thread_binding = resolve_thread_agent_binding(
        registry_goal,
        host_surface=host_surface,
        thread_id=normalized_thread_id,
    )
    has_registered_agents = bool(registered_agents)
    binding_missing = thread_binding.get("status") == "missing"
    thread_binding["selection_required"] = bool(
        explicit_goal_start
        and not new_peer
        and has_registered_agents
        and (not normalized_thread_id or binding_missing)
    )
    effective_agent_id = agent_id
    if not effective_agent_id and thread_binding.get("status") == "bound":
        effective_agent_id = str(thread_binding.get("agent_id"))

    fresh_agent_default = bool(
        explicit_goal_start
        and (new_peer or not has_registered_agents)
    )

    bootstrap_preview_command = _bootstrap_command(
        project=resolved_project,
        goal_id=resolved_goal_id,
        cli_bin=cli_bin,
        runtime_root=command_runtime_root,
        dry_run=True,
        fine_grained=fine_grained,
    )
    bootstrap_after_confirmation_command = _bootstrap_command(
        project=resolved_project,
        goal_id=resolved_goal_id,
        cli_bin=cli_bin,
        runtime_root=command_runtime_root,
        dry_run=False,
        fine_grained=fine_grained,
    )
    host_loop_activation = build_host_loop_activation_packet(
        agent_type=agent_type,
        goal_id=resolved_goal_id,
        cli_bin=cli_bin,
        runtime_root=command_runtime_root,
        identity_runtime_root=identity_runtime_root,
        agent_id=effective_agent_id,
        registered_agents=registered_agents,
        available_capabilities=available_capabilities,
        fresh_agent_default=fresh_agent_default,
        thread_binding=thread_binding,
    )
    selected_agent_id = host_loop_activation.get("agent_id")
    thread_binding_projection = {"status": thread_binding.get("status")}
    if thread_binding.get("agent_id"):
        thread_binding_projection["agent_id"] = thread_binding["agent_id"]
    issue_fix_commands = build_issue_fix_goal_command_templates(
        cli_bin=cli_bin,
        goal_id=resolved_goal_id,
        agent_id=str(selected_agent_id) if selected_agent_id else "<agent-id>",
    )
    activation_allowed = bool(host_loop_activation.get("activation_allowed"))
    activation_commands = host_loop_activation.get("commands")
    activation_commands = activation_commands if isinstance(activation_commands, dict) else {}
    heartbeat_prompt_command = activation_commands.get("heartbeat_prompt")
    heartbeat_prompt_json_command = activation_commands.get("heartbeat_prompt_json")
    scheduler_command_binding = scheduler_command_binding_for_agent_type(agent_type)
    guided_start_begins_turn = bool(
        explicit_goal_start
        and selected_agent_id
        and scheduler_command_binding.get("runtime_profile")
        in {profile.value for profile in GUIDED_START_TURN_RUNTIME_PROFILES}
    )
    quota_guard_command = (
        render_quota_guard_command(
            resolved_goal_id,
            cli_bin=cli_bin,
            runtime_root=command_runtime_root,
            agent_id=str(selected_agent_id) if selected_agent_id else None,
            available_capabilities=available_capabilities,
            begin_turn=guided_start_begins_turn,
            include_shared_registry=False,
            **scheduler_command_binding,
        )
        if activation_allowed
        else None
    )
    goal_start_quota_should_run = quota_guard_command
    command_prefix = render_cli_command_prefix(
        cli_bin=cli_bin,
        runtime_root=command_runtime_root,
    )
    status_command = _project_command(resolved_project, f"{command_prefix} status")
    goal_start_bootstrap_command = render_goal_start_bootstrap_command(
        project=resolved_project,
        goal_id=resolved_goal_id,
        goal_text=normalized_goal_text,
        cli_bin=cli_bin,
        runtime_root=command_runtime_root,
        fine_grained=fine_grained,
        display_name=display_name,
    )
    goal_start_plan_prompt = build_goal_start_prompt(
        goal_text=normalized_goal_text,
        goal_id=resolved_goal_id,
        agent_id=str(selected_agent_id) if selected_agent_id else None,
        fine_grained=fine_grained,
    )
    slash_command_catalog = build_slash_command_catalog(cli_bin=cli_bin)

    identity_selection_gate = host_loop_activation.get("identity_selection_gate")
    if isinstance(identity_selection_gate, dict):
        recommended_next_step = {
            "kind": "select_agent_identity",
            "requires_user_confirmation": False,
            "requires_agent_selection": True,
            "summary": identity_selection_gate.get("reason"),
            "identity_selection_gate": identity_selection_gate,
        }
    elif explicit_goal_start:
        recommended_next_step = {
            "kind": "goal_plan_write_and_activate",
            "requires_user_confirmation": False,
            "confirmation_source": "/loopx <goal text>",
            "summary": (
                "The slash command includes an explicit goal. Connect the project if needed, plan ranked todos, "
                "write them in exact plan order, refresh state, activate the host loop if missing/stale, "
                "and enter the quota-gated automation flow."
            ),
            "connect_command_if_needed": goal_start_bootstrap_command,
            "plan_prompt": goal_start_plan_prompt,
        }
    else:
        recommended_next_step = {
            "kind": "status_and_loop_activation" if connected else "confirm_before_bootstrap_mutation",
            "requires_user_confirmation": mutation_confirmation_required,
            "summary": (
                "Project is connected; show status, then generate the heartbeat prompt only if the user wants a loop surface."
                if connected
                else "Project is not fully connected; show the dry-run preview and ask before running bootstrap/connect."
            ),
        }
        if mutation_confirmation_required:
            recommended_next_step["dry_run_command"] = bootstrap_preview_command
            recommended_next_step["after_confirmation_command"] = bootstrap_after_confirmation_command

    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "slash_command": CANONICAL_SLASH_COMMAND,
        "slash_forms": [
            {"form": "/loopx", "mode": "inspect_or_connect_preview"},
            {"form": "/loopx <goal text>", "mode": "goal_plan_write_and_activate"},
        ],
        "canonical_cli_command": (
            f"{command_prefix} bootstrap-command-pack --project {shell_arg(resolved_project)} "
            f"--goal-id {shell_arg(resolved_goal_id)}"
            + (
                f" --agent-id {shell_arg(str(selected_agent_id))}"
                if selected_agent_id
                else ""
            )
            + (
                f" --thread-id {shell_arg(normalized_thread_id)}"
                if normalized_thread_id
                else ""
            )
            + (" --new-peer" if new_peer else "")
            + f" --host-surface {shell_arg(host_surface)}"
            + render_available_capability_args(available_capabilities)
            + (" --fine-grained" if fine_grained else "")
            + (
                f" --capability-route {shell_arg(capability_route)}"
                if capability_route
                else ""
            )
            + (
                f" --goal-text {shell_arg(normalized_goal_text)}"
                if normalized_goal_text
                else ""
            )
            + render_optional_cli_arg("--display-name", display_name)
        ),
        "read_only": True,
        "goal_text": normalized_goal_text,
        "display_name": display_name,
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": selected_agent_id,
        "requested_agent_id": agent_id,
        "command_runtime_root": command_runtime_root,
        "agent_type": agent_type,
        "host_surface": host_surface,
        "project_connection": inspection,
        "host_loop_activation": host_loop_activation,
        "available_slash_commands": slash_command_catalog,
        "onboarding_hint": slash_command_catalog["onboarding"],
        "recommended_next_step": recommended_next_step,
        "goal_start_contract": build_goal_start_contract(
            goal_text=normalized_goal_text,
            selected_capability_route=_selected_goal_capability_route(
                capability_route
            ),
            connected=connected,
            agent_type=agent_type,
            issue_fix_commands=issue_fix_hint_commands,
            fine_grained=fine_grained,
        ),
        "commands": {
            "doctor": f"{command_prefix} doctor",
            "status": status_command,
            "quota_guard": quota_guard_command,
            "heartbeat_prompt": heartbeat_prompt_command,
            "heartbeat_prompt_json": heartbeat_prompt_json_command,
            "bootstrap_dry_run_preview": bootstrap_preview_command,
            "bootstrap_after_user_confirmation": bootstrap_after_confirmation_command,
            "goal_start_connect_if_needed": goal_start_bootstrap_command,
            **(
                {
                    "goal_start_configure_turn_granularity": _project_command(
                        resolved_project,
                        f"{command_prefix} configure-goal --goal-id "
                        f"{shell_arg(resolved_goal_id)} "
                        "--execution-turn-granularity fine --execute",
                    )
                }
                if fine_grained
                else {}
            ),
            "goal_start_plan_prompt": goal_start_plan_prompt,
            "goal_start_bind_thread": (
                f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=identity_runtime_root)} "
                f"bind-agent-thread --goal-id {shell_arg(resolved_goal_id)} "
                f"--thread-id {shell_arg(normalized_thread_id)} --host-surface {shell_arg(host_surface)} "
                f"--agent-id {shell_arg(str(selected_agent_id))} --execute"
                if (
                    normalized_thread_id
                    and selected_agent_id
                    and thread_binding.get("status") == "missing"
                )
                else None
            ),
            "goal_start_refresh_state": render_refresh_state_command(
                resolved_goal_id,
                cli_bin=cli_bin,
                runtime_root=command_runtime_root,
                project=".",
                agent_id=str(selected_agent_id) if selected_agent_id else None,
                progress_scope="agent_lane" if selected_agent_id else None,
            ),
            "goal_start_host_loop_activation": host_loop_activation.get("activation_input_command"),
            "goal_start_agent_onboard_recheck": (
                f"{command_prefix} agent-onboard "
                f"--agent-type {shell_arg(agent_type)} "
                f"--project {shell_arg(resolved_project)} "
                f"--goal-id {shell_arg(resolved_goal_id)}"
                + (
                    f" --agent-id {shell_arg(str(selected_agent_id))}"
                    if selected_agent_id
                    else ""
                )
                + render_available_capability_args(available_capabilities)
            ),
            "goal_start_quota_should_run": goal_start_quota_should_run,
            "identity_selection_choices": (
                identity_selection_gate.get("choices")
                if isinstance(identity_selection_gate, dict)
                else []
            ),
            "fresh_agent_registration": (
                identity_selection_gate.get("fresh_agent_registration")
                if isinstance(identity_selection_gate, dict)
                else None
            ),
            **issue_fix_commands,
        },
        "safety_contract": {
            "runs_bootstrap": False,
            "writes_registry": False,
            "writes_state_file": False,
            "creates_heartbeat": False,
            "spends_quota": False,
            "mutation_requires_user_confirmation": mutation_confirmation_required and not explicit_goal_start,
            "bare_command_mutation_requires_user_confirmation": mutation_confirmation_required,
            "explicit_goal_start_may_write_project_local_state": explicit_goal_start,
            "explicit_goal_start_must_activate_host_loop": explicit_goal_start,
            "host_loop_activation_allowed": activation_allowed,
        },
    }
    fence_command_pack(payload, command_prefix=command_prefix)
    if normalized_thread_id:
        payload["thread_id"] = normalized_thread_id
        payload["thread_agent_binding"] = thread_binding_projection
    if new_peer:
        payload["new_peer"] = True
    payload["message"] = render_loopx_bootstrap_command_pack_message(payload)
    payload["packet_summary"] = _build_packet_summary(
        payload,
        packet_kind="bootstrap_command_pack",
        detail_refs={
            "project_connection": "#/project_connection",
            "host_loop_activation": "#/host_loop_activation",
            "recommended_next_step": "#/recommended_next_step",
            "goal_start_contract": "#/goal_start_contract",
            "commands": "#/commands",
            "safety_contract": "#/safety_contract",
            "compatibility_message": "#/message",
        },
    )
    return payload


def _build_multi_goal_start_selection_packet(
    *,
    project: Path,
    agent_id: str | None,
    thread_id: str | None,
    new_peer: bool,
    cli_bin: str,
    runtime_root_arg: str | None,
    host_surface: str,
    goal_text: str,
    available_capabilities: list[str] | None,
    capability_route: str | None,
    fine_grained: bool,
    include_command_pack_detail: bool,
    display_name: str | None = None,
) -> dict[str, Any] | None:
    inspection = inspect_bootstrap_connection(
        project,
        resolve_linked_worktree_alias=False,
    )
    registry_path = Path(str(inspection.get("registry") or ""))
    registry, registry_error = _read_registry(registry_path)
    if registry_error or not registry:
        return None
    goals = registry_goals(registry)
    if len(goals) <= 1:
        return None

    normalized_goal_text = " ".join(goal_text.split())
    normalized_thread_id = normalize_thread_id(thread_id)
    resolved_project = str(inspection["project"])
    command_runtime_root, _ = _runtime_roots(
        registry,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
    )
    issue_fix_commands = build_issue_fix_goal_command_templates(
        cli_bin=cli_bin,
        goal_id="<selected-goal-id>",
    )
    choices: list[dict[str, Any]] = []
    for goal in goals:
        candidate_goal_id = str(goal.get("id") or "").strip()
        if not candidate_goal_id:
            continue
        rerun_command = (
            f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=command_runtime_root)} "
            f"start-goal --guided "
            f"--project {shell_arg(resolved_project)} "
            f"--goal-id {shell_arg(candidate_goal_id)}"
            + (f" --agent-id {shell_arg(agent_id)}" if agent_id else "")
            + (
                f" --thread-id {shell_arg(normalized_thread_id)}"
                if normalized_thread_id
                else ""
            )
            + (" --new-peer" if new_peer else "")
            + f" --host-surface {shell_arg(host_surface)}"
            + render_available_capability_args(available_capabilities)
            + (" --fine-grained" if fine_grained else "")
            + (
                f" --capability-route {shell_arg(capability_route)}"
                if capability_route
                else ""
            )
            + f" --goal-text {shell_arg(normalized_goal_text)}"
        )
        choices.append(
            {
                "goal_id": candidate_goal_id,
                "status": goal.get("status"),
                "state_file": goal.get("state_file"),
                "registered_agents": registered_agent_ids_for_goal(goal),
                "rerun_command": rerun_command,
            }
        )

    reason = (
        "multiple registered goals exist for this project; select one explicitly before "
        "planning todos, registering an agent, or activating a host loop"
    )
    goal_selection_gate = {
        "schema_version": "loopx_goal_selection_gate_v0",
        "state": "selection_required",
        "action_required": True,
        "reason": reason,
        "required_cli_arg": "--goal-id <registered-goal-id>",
        "choices": choices,
    }
    connection = {
        **inspection,
        "goal_id": None,
        "goal_found": False,
        "state_file": None,
        "state_file_exists": None,
        "known_goal_ids": [choice["goal_id"] for choice in choices],
        "connection_state": "goal_selection_required",
        "mutation_confirmation_required": False,
        "reason": reason,
    }
    recommended_next_step = {
        "kind": "select_goal",
        "requires_user_confirmation": False,
        "requires_goal_selection": True,
        "summary": reason,
        "goal_selection_gate": goal_selection_gate,
    }
    slash_command_catalog = build_slash_command_catalog(cli_bin=cli_bin)
    command_pack: dict[str, Any] = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "slash_command": CANONICAL_SLASH_COMMAND,
        "slash_forms": [
            {"form": "/loopx", "mode": "inspect_or_connect_preview"},
            {"form": "/loopx <goal text>", "mode": "goal_plan_write_and_activate"},
        ],
        "canonical_cli_command": None,
        "read_only": True,
        "goal_text": normalized_goal_text,
        "project": resolved_project,
        "goal_id": None,
        "agent_id": None,
        "requested_agent_id": agent_id,
        "agent_type": agent_type_for_host_surface(host_surface),
        "host_surface": host_surface,
        "project_connection": connection,
        "goal_selection_gate": goal_selection_gate,
        "host_loop_activation": {
            "activation_state": "goal_selection_required",
            "activation_allowed": False,
            "activation_required_after_todo_write": False,
        },
        "available_slash_commands": slash_command_catalog,
        "onboarding_hint": slash_command_catalog["onboarding"],
        "recommended_next_step": recommended_next_step,
        "goal_start_contract": build_goal_start_contract(
            goal_text=normalized_goal_text,
            selected_capability_route=_selected_goal_capability_route(
                capability_route
            ),
            connected=True,
            agent_type=agent_type_for_host_surface(host_surface),
            issue_fix_commands=issue_fix_commands,
            fine_grained=fine_grained,
        ),
        "commands": {
            "doctor": f"{shell_arg(cli_bin)} doctor",
            "status": _project_command(resolved_project, f"{shell_arg(cli_bin)} status"),
            "goal_selection_choices": choices,
        },
        "safety_contract": {
            "runs_bootstrap": False,
            "writes_registry": False,
            "writes_state_file": False,
            "creates_heartbeat": False,
            "spends_quota": False,
            "explicit_goal_start_may_write_project_local_state": False,
            "explicit_goal_start_must_activate_host_loop": False,
            "host_loop_activation_allowed": False,
        },
    }
    command_pack["message"] = (
        f"{reason}. Rerun exactly one goal_selection_gate choice; no mutation or host-loop "
        "command is valid before that selection."
    )
    command_pack["packet_summary"] = _build_packet_summary(
        command_pack,
        packet_kind="bootstrap_command_pack",
        detail_refs={
            "project_connection": "#/project_connection",
            "goal_selection_gate": "#/goal_selection_gate",
            "recommended_next_step": "#/recommended_next_step",
            "commands": "#/commands",
            "safety_contract": "#/safety_contract",
            "compatibility_message": "#/message",
        },
    )
    guided_transaction = {
        "schema_version": GUIDED_START_SCHEMA_VERSION,
        "mode": "dry_run_preview",
        "writes_now": False,
        "spends_quota_now": False,
        "command_cwd_source": "#/project",
        "goal_text": normalized_goal_text,
        "blocked_by": "goal_selection",
        "goal_selection_gate": goal_selection_gate,
        "ordered_steps": [
            {
                "id": "inspect_connection",
                "kind": "read_only",
                "purpose": "inspect the requested project route and discover registered goals",
            },
            {
                "id": "select_goal",
                "kind": "goal_selection_gate",
                "choices": choices,
                "purpose": "select one registered goal before any todo, agent, state, or host-loop mutation",
            },
        ],
        "idempotency_policy": {
            "safe_to_rerun_preview": True,
            "reuse_connected_goal": True,
        },
        "preserve_todos_policy": {
            "force_bootstrap_default": "forbidden_in_guided_flow",
            "before_destructive_reconnect": "run backup-state and stop for an explicit preserve-todos confirmation",
            "preferred_scope_change": "use configure-goal incremental updates instead of force bootstrap when state already exists",
        },
    }
    detail_command = _start_goal_detail_command(
        project=resolved_project,
        goal_id=None,
        agent_id=agent_id,
        thread_id=thread_id,
        new_peer=new_peer,
        cli_bin=cli_bin,
        host_surface=host_surface,
        goal_text=normalized_goal_text,
        available_capabilities=available_capabilities,
        capability_route=capability_route,
        fine_grained=fine_grained,
        display_name=display_name,
    )
    selected_command_pack = (
        command_pack
        if include_command_pack_detail
        else _guided_command_pack_projection(
            command_pack,
            detail_command=detail_command,
        )
    )
    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": GUIDED_START_SCHEMA_VERSION,
        "read_only": True,
        "guided": True,
        "project": resolved_project,
        "goal_id": None,
        "agent_id": None,
        "host_surface": host_surface,
        "goal_text": normalized_goal_text,
        "project_connection": connection,
        "goal_selection_gate": goal_selection_gate,
        "recommended_next_step": recommended_next_step,
        "guided_transaction": guided_transaction,
        "command_pack": selected_command_pack,
        "command_pack_detail_included": include_command_pack_detail,
        "safety_contract": {
            "writes_registry": False,
            "writes_state_file": False,
            "creates_heartbeat": False,
            "spends_quota": False,
            "mutation_commands_are_previewed": False,
            "force_bootstrap_allowed": False,
        },
    }
    payload["message"] = render_start_goal_guided_markdown(payload)
    payload["packet_summary"] = _build_packet_summary(
        payload,
        packet_kind="guided_start_goal",
        detail_refs={
            "command_pack_summary": "#/command_pack/packet_summary",
            "project_connection": "#/project_connection",
            "goal_selection_gate": "#/goal_selection_gate",
            "recommended_next_step": "#/recommended_next_step",
            "guided_transaction": "#/guided_transaction",
            "commands": "#/guided_transaction/ordered_steps",
            "safety_contract": "#/safety_contract",
            "compatibility_message": "#/message",
        },
        legacy_fields_retained=include_command_pack_detail,
        compact_projection_default=not include_command_pack_detail,
    )
    return payload


def build_start_goal_guided_packet(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    host_surface: str,
    goal_text: str,
    thread_id: str | None = None,
    new_peer: bool = False,
    available_capabilities: list[str] | None = None,
    capability_route: str | None = None,
    fine_grained: bool = False,
    include_command_pack_detail: bool = False,
    display_name: str | None = None,
    runtime_root_arg: str | None = None,
) -> dict[str, Any]:
    if goal_id is None:
        selection_packet = _build_multi_goal_start_selection_packet(
            project=project,
            agent_id=agent_id,
            thread_id=thread_id,
            new_peer=new_peer,
            cli_bin=cli_bin,
            runtime_root_arg=runtime_root_arg,
            host_surface=host_surface,
            goal_text=goal_text,
            available_capabilities=available_capabilities,
            capability_route=capability_route,
            fine_grained=fine_grained,
            include_command_pack_detail=include_command_pack_detail,
            display_name=display_name,
        )
        if selection_packet is not None:
            return selection_packet
    command_pack = build_loopx_bootstrap_command_pack(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        thread_id=thread_id,
        new_peer=new_peer,
        cli_bin=cli_bin,
        host_surface=host_surface,
        goal_text=goal_text,
        available_capabilities=available_capabilities,
        capability_route=capability_route,
        fine_grained=fine_grained,
        resolve_linked_worktree_alias=False,
        display_name=display_name,
        runtime_root_arg=runtime_root_arg,
    )
    commands = command_pack.get("commands")
    commands = commands if isinstance(commands, dict) else {}
    activation = command_pack.get("host_loop_activation")
    activation = activation if isinstance(activation, dict) else {}
    identity_selection_gate = activation.get("identity_selection_gate")
    if isinstance(identity_selection_gate, dict):
        def rerun_start_goal(selected_agent_id: str) -> str:
            return _start_goal_command(
                project=str(command_pack.get("project") or project),
                goal_id=str(command_pack.get("goal_id") or "") or None,
                agent_id=selected_agent_id,
                thread_id=thread_id,
                new_peer=new_peer,
                cli_bin=cli_bin,
                runtime_root=command_pack.get("command_runtime_root"),
                host_surface=host_surface,
                goal_text=str(command_pack.get("goal_text") or goal_text),
                available_capabilities=available_capabilities,
                capability_route=capability_route,
                fine_grained=fine_grained,
                include_command_pack_detail=False,
                display_name=str(command_pack.get("display_name") or display_name or ""),
            )

        fresh_registration = identity_selection_gate.get("fresh_agent_registration")
        if isinstance(fresh_registration, dict):
            fresh_agent_id = str(
                fresh_registration.get("agent_id") or "<new-public-safe-agent-id>"
            )
            fresh_registration["connect_if_needed_command"] = commands.get(
                "goal_start_connect_if_needed"
            )
            fresh_registration["rerun_start_goal_command"] = rerun_start_goal(
                fresh_agent_id
            )
        for choice in identity_selection_gate.get("choices") or []:
            if not isinstance(choice, dict) or not choice.get("agent_id"):
                continue
            choice["rerun_start_goal_command"] = rerun_start_goal(
                str(choice["agent_id"])
            )
    goal_start_contract = command_pack.get("goal_start_contract")
    goal_start_contract = (
        goal_start_contract if isinstance(goal_start_contract, dict) else {}
    )
    selected_capability_route = goal_start_contract.get("selected_capability_route")
    selected_capability_route = (
        selected_capability_route
        if isinstance(selected_capability_route, dict)
        else None
    )
    scheduler_ack_steps = (
        [
            {
                "id": "scheduler_ack_when_needed",
                "kind": "scheduler_state",
                "command_source": "quota.should-run.scheduler_hint.app_automation.ack_hint.cli_args",
                "purpose": "ack an applied App automation RRULE without spending quota",
            }
        ]
        if host_surface in {"codex-app", "trae_app"}
        else []
    )
    bind_thread_steps = (
        [
            {
                "id": "bind_thread_identity",
                "kind": "identity_mutation",
                "command": commands.get("goal_start_bind_thread"),
                "purpose": "bind thread; verify readback before Todo writeback",
                "must_stop_on_failure": True,
                "result_contract": {
                    "schema_version": "loopx_thread_agent_binding_continuation_v0",
                    "required_result": {
                        "ok": True,
                        "global_sync": {"ok": True},
                        "registration_readback": {"verified": True},
                    },
                },
            }
        ]
        if commands.get("goal_start_bind_thread")
        else []
    )
    fine_mode_steps = (
        [
            {
                "id": "configure_fine_grained_turn_mode",
                "kind": "conditional_mutation",
                "command": commands.get("goal_start_configure_turn_granularity"),
                "purpose": (
                    "persist fine turn granularity for new or already-connected goals "
                    "before planning and heartbeat activation"
                ),
            }
        ]
        if fine_grained
        else []
    )
    project_connection = command_pack.get("project_connection")
    guided_frontier = (
        existing_runnable_agent_frontier(
            project_connection,
            resolved_goal_id=str(command_pack.get("goal_id") or ""),
            effective_agent_id=str(command_pack.get("agent_id") or "") or None,
            runtime_root_arg=runtime_root_arg,
        )
        if isinstance(project_connection, dict)
        and not isinstance(identity_selection_gate, dict)
        else None
    )
    guided_transaction = {
        "schema_version": GUIDED_START_SCHEMA_VERSION,
        "mode": "dry_run_preview",
        "writes_now": False,
        "spends_quota_now": False,
        "command_cwd_source": "#/project",
        **(
            {
                "checkpoint_policy": {
                    "task_frontier": "agent_advancement_todos_only",
                    "protocol_step_todo_projection": "forbidden",
                    "protocol_step_advancement_checkpoint": False,
                    "protocol_step_turn_settlement": False,
                    "settlement": "once_after_task_facing_slice",
                }
            }
            if fine_grained
            else {}
        ),
        "ordered_steps": [
            {
                "id": "inspect_connection",
                "kind": "read_only",
                "command_source": "#/command_pack/canonical_cli_command",
                "purpose": "resolve the requested project route, goal id, registry, and active state before any mutation",
            },
            {
                "id": "connect_if_needed",
                "kind": "conditional_mutation",
                "command": commands.get("goal_start_connect_if_needed"),
                "purpose": "create or reuse project-local LoopX state only when no matching goal exists",
            },
            *bind_thread_steps,
            *fine_mode_steps,
            *todo_authoring_steps(
                existing_runnable_frontier=guided_frontier,
                plan_prompt=commands.get("goal_start_plan_prompt"),
                fine_grained=fine_grained,
                cli_bin=cli_bin,
                runtime_root=command_pack.get("command_runtime_root"),
                goal_id=str(command_pack.get("goal_id") or ""),
                agent_id=command_pack.get("agent_id"),
            ),
            {
                "id": "refresh_state",
                "kind": "state_sync",
                "command": commands.get("goal_start_refresh_state"),
                "purpose": "project the accepted plan and next action into active state/history",
            },
            {
                "id": "activate_host_loop",
                "kind": "identity_gate" if identity_selection_gate else "host_loop",
                "command": commands.get("goal_start_host_loop_activation"),
                "purpose": "install or refresh the host loop only when it is missing, unknown, stale, or agent type changed",
            },
            {
                "id": "quota_guard",
                "kind": "guard",
                "command": commands.get("goal_start_quota_should_run"),
                "purpose": "let LoopX choose the first bounded segment and scheduler cadence",
            },
            *scheduler_ack_steps,
        ],
        "idempotency_policy": {
            "safe_to_rerun_preview": True,
            "reuse_connected_goal": True,
            "do_not_duplicate_existing_todos": (
                "Do not duplicate existing todos; before writing, compare active todos by text/action_kind "
                "and update or skip matching items."
            ),
            "host_loop_recheck": (
                "Only regenerate or update automation when the host loop is missing, unknown, stale, or agent type changed."
            ),
        },
        "preserve_todos_policy": {
            "force_bootstrap_default": "forbidden_in_guided_flow",
            "before_destructive_reconnect": "run backup-state and stop for an explicit preserve-todos confirmation",
            "preferred_scope_change": "use configure-goal incremental updates instead of force bootstrap when state already exists",
        },
    }
    if isinstance(identity_selection_gate, dict):
        guided_transaction["blocked_by"] = "agent_identity_selection"
        guided_transaction["identity_selection_gate"] = identity_selection_gate
        guided_transaction["ordered_steps"].insert(
            2,
            {
                "id": "select_agent_identity",
                "kind": "identity_gate",
                "default_action": identity_selection_gate.get("default_action"),
                "fresh_agent_registration": identity_selection_gate.get(
                    "fresh_agent_registration"
                ),
                "choices": identity_selection_gate.get("choices") or [],
                "purpose": (
                    "follow identity_selection_gate.default_action: select an existing "
                    "lane (and persist the thread binding when a thread id is available) "
                    "unless default_action is register_fresh_agent for a genuinely new "
                    "peer or a goal with no registered lanes"
                ),
            },
        )
    if selected_capability_route is not None:
        entry_key = str(selected_capability_route["entry_command_key"])
        admission_key = str(selected_capability_route["admission_command_key"])
        guided_transaction["ordered_steps"].insert(
            3 if isinstance(identity_selection_gate, dict) else 2,
            {
                "id": "qualify_selected_capability",
                "kind": "capability_guard",
                "candidate_discovery_command_template": (
                    ISSUE_FIX_GOAL_CANDIDATE_DISCOVERY_COMMAND_TEMPLATE
                ),
                "candidate_authority": selected_capability_route[
                    "candidate_authority"
                ],
                "authority_refresh_required": selected_capability_route[
                    "authority_refresh_required"
                ],
                "candidate_preflight": selected_capability_route[
                    "candidate_preflight"
                ],
                "command_source": f"#/command_pack/commands/{entry_key}",
                "admission_command_source": (
                    f"#/command_pack/commands/{admission_key}"
                ),
                "activation_condition": selected_capability_route[
                    "activation_condition"
                ],
                "durable_successor_sources": {
                    "proceed": "admission_result.transition.projected_todo",
                    "non_proceed": (
                        "entry_result.ordered_loopx_todo_writeback_preview"
                    ),
                },
                "completion_condition": (
                    "persist candidate preflight; for proceed, persist feasibility "
                    "and write its exact successor Todo; for non-proceed, write the "
                    "preflight successor or record no-follow-up"
                ),
                "when_condition_unmet": (
                    "keep candidate discovery or qualification as a normal Todo, "
                    "then re-enter this guard after selecting a public candidate"
                ),
                "purpose": (
                    "select public issue authority and persist capability-owned "
                    "qualification before planning implementation"
                ),
            }
        )
        guided_transaction["selected_capability_route"] = selected_capability_route
    detail_command = _start_goal_detail_command(
        project=str(command_pack.get("project") or project),
        goal_id=str(command_pack.get("goal_id") or "") or None,
        agent_id=str(command_pack.get("agent_id") or "") or None,
        thread_id=thread_id,
        new_peer=new_peer,
        cli_bin=cli_bin,
        runtime_root=command_pack.get("command_runtime_root"),
        host_surface=host_surface,
        goal_text=str(command_pack.get("goal_text") or goal_text),
        available_capabilities=available_capabilities,
        capability_route=capability_route,
        fine_grained=fine_grained,
        display_name=str(command_pack.get("display_name") or display_name or ""),
    )
    selected_command_pack = (
        command_pack
        if include_command_pack_detail
        else _guided_command_pack_projection(
            command_pack,
            detail_command=detail_command,
        )
    )
    orphaned_gate = command_pack.get("orphaned_goal_state")
    if isinstance(orphaned_gate, dict):
        guided_transaction.update(guided_fence(orphaned_gate))
        guided_transaction.pop("identity_selection_gate", None)
    payload = {
        "ok": True,
        "schema_version": GUIDED_START_SCHEMA_VERSION,
        "read_only": True,
        "guided": True,
        "project": command_pack.get("project"),
        "goal_id": command_pack.get("goal_id"),
        "agent_id": command_pack.get("agent_id"),
        "host_surface": command_pack.get("host_surface"),
        "goal_text": command_pack.get("goal_text"),
        "project_connection": command_pack.get("project_connection"),
        "recommended_next_step": command_pack.get("recommended_next_step"),
        "guided_transaction": guided_transaction,
        "command_pack": selected_command_pack,
        "command_pack_detail_included": include_command_pack_detail,
        "safety_contract": {
            "writes_registry": False,
            "writes_state_file": False,
            "creates_heartbeat": False,
            "spends_quota": False,
            "mutation_commands_are_previewed": True,
            "force_bootstrap_allowed": False,
            "orphaned_goal_state_blocks_continuation": isinstance(orphaned_gate, dict),
        },
    }
    if command_pack.get("thread_id"):
        payload["thread_id"] = command_pack["thread_id"]
        payload["thread_agent_binding"] = command_pack.get("thread_agent_binding")
    if command_pack.get("new_peer"):
        payload["new_peer"] = True
    payload["message"] = render_start_goal_guided_markdown(payload)
    payload["packet_summary"] = _build_packet_summary(
        payload,
        packet_kind="guided_start_goal",
        detail_refs={
            "command_pack_summary": "#/command_pack/packet_summary",
            "project_connection": "#/project_connection",
            "recommended_next_step": "#/recommended_next_step",
            "guided_transaction": "#/guided_transaction",
            "commands": "#/guided_transaction/ordered_steps",
            "safety_contract": "#/safety_contract",
            "compatibility_message": "#/message",
        },
        legacy_fields_retained=include_command_pack_detail,
        compact_projection_default=not include_command_pack_detail,
    )
    return payload


def render_start_goal_guided_markdown(payload: dict[str, Any]) -> str:
    def command_summary(value: Any) -> str:
        parts = str(value or "").split()
        return " ".join(parts[:3]) + (" ..." if len(parts) > 3 else "")

    def actionable_shell_command(value: Any) -> str:
        lines = [
            line.removesuffix("\\").strip()
            for line in str(value or "").splitlines()
            if line.strip()
        ]
        if len(lines) > 1 and lines[0].startswith("cd "):
            return f"{lines[0]} && {' '.join(lines[1:])}"
        return " ".join(lines)

    transaction = payload.get("guided_transaction")
    transaction = transaction if isinstance(transaction, dict) else {}
    command_pack = payload.get("command_pack")
    command_pack = command_pack if isinstance(command_pack, dict) else {}
    commands = command_pack.get("commands")
    commands = commands if isinstance(commands, dict) else {}
    selected_route = transaction.get("selected_capability_route")
    selected_route = selected_route if isinstance(selected_route, dict) else {}
    ordered_steps = transaction.get("ordered_steps")
    ordered_steps = ordered_steps if isinstance(ordered_steps, list) else []
    program = effect_program_from_ordered_steps(ordered_steps)
    step_lines: list[str] = []
    for index, step in enumerate(program.steps, start=1):
        raw_step = step.raw
        command = (
            step.command
            or raw_step.get("command_source")
            or raw_step.get("prompt")
        )
        if step.kind == "capability_guard":
            entry_key = str(selected_route.get("entry_command_key") or "")
            admission_key = str(selected_route.get("admission_command_key") or "")
            entry_command = commands.get(entry_key)
            admission_command = commands.get(admission_key)
            discovery_command = raw_step.get("candidate_discovery_command_template")
            authority = (
                "open public issue; source clues are evidence only"
                if raw_step.get("candidate_authority") == "public_open_tracker_issue"
                else raw_step.get("candidate_authority")
            )
            step_lines.extend(
                [
                    f"{index}. `{step.step_id}` ({step.kind})",
                    f"   - authority: {authority}",
                ]
            )
            if discovery_command:
                step_lines.append(
                    f"   - discover: `{str(discovery_command).splitlines()[0]}`"
                )
            preflight = raw_step.get("candidate_preflight")
            if isinstance(preflight, dict):
                required_evidence = " + ".join(
                    str(field)
                    for field in preflight.get("required_evidence_fields") or []
                )
                step_lines.append(
                    "   - preflight: refresh "
                    f"{raw_step.get('authority_refresh_required')}; "
                    f"provide {required_evidence}; "
                    f"{preflight.get('decision_rule')}"
                )
            step_lines.extend(
                [
                    f"   - qualify: `{command_summary(entry_command)}`",
                    f"   - proceed: `{command_summary(admission_command)}`",
                ]
            )
            continue
        step_label = f"{index}. `{step.step_id}` ({step.kind}): "
        step_lines.append(step_label + str(step.purpose))
        append_todo_delta_render_line(raw_step, step_lines)
        if command:
            rendered_command = (
                actionable_shell_command(command)
                if step.step_id == "connect_if_needed"
                else str(command).splitlines()[0]
            )
            step_lines.append(f"   - command/source: `{rendered_command}`")
    preserve = transaction.get("preserve_todos_policy")
    preserve = preserve if isinstance(preserve, dict) else {}
    identity_gate = transaction.get("identity_selection_gate")
    identity_gate = identity_gate if isinstance(identity_gate, dict) else {}
    identity_gate_lines = ""
    if identity_gate:
        fresh_registration = identity_gate.get("fresh_agent_registration")
        fresh_lines = []
        if isinstance(fresh_registration, dict):
            fresh_lines = [
                "- **default: register a fresh agent**",
                f"  - preview: `{fresh_registration.get('preview_command')}`",
                f"  - apply: `{fresh_registration.get('execute_command')}`",
                (
                    "  - rerun: "
                    f"`{fresh_registration.get('rerun_start_goal_command')}`"
                ),
            ]
        choices = [
            f"- explicit takeover `{choice.get('agent_id')}`: "
            f"`{choice.get('rerun_start_goal_command')}`"
            for choice in identity_gate.get("choices") or []
            if isinstance(choice, dict)
        ]
        identity_gate_lines = (
            "\n## Agent Identity Gate\n\n"
            f"{identity_gate.get('reason')}\n\n"
            + "\n".join([*fresh_lines, *choices])
            + "\n"
        )
    goal_gate = transaction.get("goal_selection_gate")
    goal_gate = goal_gate if isinstance(goal_gate, dict) else {}
    goal_gate_lines = ""
    if goal_gate:
        choices = [
            f"- `{choice.get('goal_id')}` status=`{choice.get('status')}`: "
            f"`{choice.get('rerun_command')}`"
            for choice in goal_gate.get("choices") or []
            if isinstance(choice, dict)
        ]
        goal_gate_lines = (
            "\n## Goal Selection Gate\n\n"
            f"{goal_gate.get('reason')}\n\n"
            + "\n".join(choices)
            + "\n"
        )
    orphan_gate_lines = render_guided_lines(transaction)
    host_gate = transaction.get("host_surface_selection_gate")
    host_gate = host_gate if isinstance(host_gate, dict) else {}
    host_gate_lines = ""
    if host_gate:
        choices = [
            f"- `{choice.get('host_surface')}`: {choice.get('description')}  "
            f"\n  `{choice.get('rerun_command')}`"
            for choice in host_gate.get("choices") or []
            if isinstance(choice, dict)
        ]
        host_gate_lines = (
            "\n## Host Surface Gate\n\n"
            f"{host_gate.get('reason')}\n\n"
            + "\n".join(choices)
            + "\n"
        )
    return f"""# Guided Start Goal

- project: `{payload.get("project")}`
- goal_id: `{payload.get("goal_id")}`
- goal_text: `{payload.get("goal_text")}`

Preview only; follow ordered commands to mutate.

## Ordered Transaction

{chr(10).join(step_lines)}
{host_gate_lines}
{goal_gate_lines}
{orphan_gate_lines}
{identity_gate_lines}

## Todo Preservation

- bootstrap: `{preserve.get("force_bootstrap_default")}`; reconnect: {preserve.get("before_destructive_reconnect")}; scope change: {preserve.get("preferred_scope_change")}
"""


def render_loopx_bootstrap_command_pack_message(payload: dict[str, Any]) -> str:
    connection = payload.get("project_connection")
    connection = connection if isinstance(connection, dict) else {}
    if connection.get("connection_state") == ORPHANED_GOAL_STATE_CONNECTION:
        return fenced_standalone_message(payload)
    commands = payload.get("commands")
    commands = commands if isinstance(commands, dict) else {}
    next_step = payload.get("recommended_next_step")
    next_step = next_step if isinstance(next_step, dict) else {}
    requires_confirmation = bool(next_step.get("requires_user_confirmation"))
    project = payload.get("project")
    goal_id = payload.get("goal_id")
    goal_text = payload.get("goal_text")
    state = connection.get("connection_state")
    reason = connection.get("reason")
    alias = connection.get("canonical_project_alias")
    alias = alias if isinstance(alias, dict) else {}
    alias_note = (
        f"\nInput project: `{connection.get('input_project')}`\n"
        f"Canonical route: `{alias.get('canonical_project')}` via `{alias.get('source_registry')}`\n"
        if alias.get("applied")
        else ""
    )
    goal_start_contract = payload.get("goal_start_contract")
    goal_start_contract = goal_start_contract if isinstance(goal_start_contract, dict) else {}
    selected_capability_route = goal_start_contract.get(
        "selected_capability_route"
    )
    selected_capability_route = (
        selected_capability_route
        if isinstance(selected_capability_route, dict)
        else {}
    )
    issue_fix_action = ""
    if selected_capability_route.get("capability_id") == "issue-fix":
        issue_fix_action = f"""

The explicit capability route selects issue-fix. Preview its capability-owned admission:

```bash
{commands.get("issue_fix_workflow_plan_template", "")}
```

Persist candidate preflight. Non-proceed: write its successor/no-follow-up; skip feasibility. Proceed: run feasibility and write its successor:

```bash
{commands.get("issue_fix_feasibility_template", "")}
```

Private repro material, issue body/comment reads, external comments, PR creation, merge, publish, destructive git, and production actions stay explicit gates.

After a PR exists and external-review-request authority is active, request the default top requestable non-author reviewer and require post-write verification:

```bash
{commands.get("issue_fix_reviewer_request_template", "")}
```

After a PR exists, reconcile compact public PR state through the capability-owned lifecycle command:

```bash
{commands.get("issue_fix_pr_lifecycle_template", "")}
```"""
    onboarding = payload.get("onboarding_hint")
    onboarding = onboarding if isinstance(onboarding, dict) else {}
    activation = payload.get("host_loop_activation")
    activation = activation if isinstance(activation, dict) else {}
    identity_gate = activation.get("identity_selection_gate")
    identity_gate = identity_gate if isinstance(identity_gate, dict) else {}

    if identity_gate:
        choices = "\n".join(
            f"- explicit takeover `{choice.get('agent_id')}`: rerun with "
            f"`--agent-id {choice.get('agent_id')}`"
            for choice in identity_gate.get("choices") or []
            if isinstance(choice, dict)
        )
        fresh_registration = identity_gate.get("fresh_agent_registration")
        fresh_registration = (
            fresh_registration if isinstance(fresh_registration, dict) else {}
        )
        if fresh_registration:
            action = f"""Resolve agent identity before planning writes or host-loop activation.
No unscoped heartbeat or quota command is advertised for a new agent connection.

Reason: {identity_gate.get("reason")}

Default fresh-agent registration:
```bash
{fresh_registration.get("preview_command") or ""}
{fresh_registration.get("execute_command") or ""}
```

Only with explicit takeover intent, choose one existing identity:
{choices}

Rerun `{payload.get("canonical_cli_command")} --agent-id <selected-agent-id>`
after fresh registration or exact takeover selection before continuing."""
        else:
            action = f"""Select one registered agent lane before planning writes or host-loop activation.
No unscoped heartbeat or quota command is advertised for this multi-agent goal.

Reason: {identity_gate.get("reason")}

Identity-aware choices:
{choices}

Rerun `{payload.get("canonical_cli_command")} --agent-id <registered-agent-id>`
with the selected identity before continuing."""
    elif goal_text:
        action = f"""This is an explicit goal-start invocation. Connect project-local LoopX state if needed:

```bash
{commands.get("goal_start_connect_if_needed", "")}
```

Then plan before writing todos. Preserve relative priority by write order:

````text
{commands.get("goal_start_plan_prompt", "")}
````

Write the planned todos with `loopx todo add` in the exact planned order. Same-priority items use that write order as the tie-breaker.
{issue_fix_action}

After todo writeback:

```bash
{commands.get("goal_start_refresh_state", "")}
{commands.get("goal_start_host_loop_activation", "")}
{commands.get("goal_start_quota_should_run", "")}
```

Host loop activation is part of setup, not a nice-to-have:
- agent_type: `{payload.get("agent_type")}`
- host_surface: `{activation.get("host_surface")}`
- activation_method: `{activation.get("activation_method")}`

If the host loop is already proven current, skip the mutation. If it is missing,
unknown, or stale, use the command above to obtain `task_body` and activate the
right host loop: Codex App automation, Codex CLI `/goal <task_body>`, Claude
Code `/loop`, OpenCode bridge, or the custom host-loop gate.
If this session cannot mutate that
host surface, report the exact gate; do not claim autonomous setup complete.
Use `{commands.get("goal_start_agent_onboard_recheck", "")}` only when
activation state is missing/unknown/stale or the agent type changed."""
    elif requires_confirmation:
        action = f"""First show this dry-run preview, then ask me before running the mutation:

```bash
{commands.get("bootstrap_dry_run_preview", "")}
```

If I confirm, run:

```bash
{commands.get("bootstrap_after_user_confirmation", "")}
```"""
    else:
        action = f"""Start with the current LoopX status and do not reconnect:

```bash
{commands.get("status", "")}
```

Only after I ask for a recurring loop surface, generate the heartbeat body:

```bash
{commands.get("heartbeat_prompt", "")}
```"""

    return f"""Handle `{CANONICAL_SLASH_COMMAND}` for this project with explicit mutation boundaries.

Project: `{project}`
Goal id: `{goal_id}`
Detected state: `{state}` ({reason})
{alias_note}

Rules:
- This command pack preview is read-only. Do not run bootstrap/connect, create heartbeat automation, or spend quota while only previewing it.
- Bare `/loopx` is read/status-first: if the project is not fully connected, ask for explicit user confirmation before any command that writes `.loopx/` (or legacy `.codex/goals/`).
- `/loopx <goal text>` is explicit goal-start intent: it may create project-local LoopX state, but it must run the profile-appropriate planning checkpoint before writing todos, then activate the correct host loop if missing/stale.
- Same-priority todos are ranked by planner order, then by `todo add` write order; preserve the order exactly.
- If the project is connected, reuse the existing state and show the status/gate/todo snapshot.

Goal-start contract: `{goal_start_contract.get("schema_version")}`

Suggested new-user note:

```text
{onboarding.get("suggested_user_note", "")}
```

{action}

For ongoing work after the project is connected, use the quota guard:

```bash
{commands.get("quota_guard", "")}
```
"""


def render_loopx_bootstrap_command_pack_markdown(payload: dict[str, Any]) -> str:
    connection = payload.get("project_connection")
    connection = connection if isinstance(connection, dict) else {}
    next_step = payload.get("recommended_next_step")
    next_step = next_step if isinstance(next_step, dict) else {}
    safety = payload.get("safety_contract")
    safety = safety if isinstance(safety, dict) else {}
    commands = payload.get("commands")
    commands = commands if isinstance(commands, dict) else {}
    onboarding = payload.get("onboarding_hint")
    onboarding = onboarding if isinstance(onboarding, dict) else {}
    slash_catalog = payload.get("available_slash_commands")
    slash_catalog = slash_catalog if isinstance(slash_catalog, dict) else {}
    goal_start = payload.get("goal_start_contract")
    goal_start = goal_start if isinstance(goal_start, dict) else {}
    activation = payload.get("host_loop_activation")
    activation = activation if isinstance(activation, dict) else {}
    ordering = goal_start.get("priority_ordering")
    ordering = ordering if isinstance(ordering, dict) else {}
    return f"""# /loopx Bootstrap Command Pack

Canonical slash command: `{payload.get("slash_command")}`
Supported forms: `/loopx`, `/loopx <goal text>`

## Detected Project State

- project: `{payload.get("project")}`
- input_project: `{connection.get("input_project")}`
- goal_id: `{payload.get("goal_id")}`
- agent_type: `{payload.get("agent_type")}`
- goal_text: `{payload.get("goal_text") or ""}`
- connection_state: `{connection.get("connection_state")}`
- reason: `{connection.get("reason")}`
- registry: `{connection.get("registry")}`
- state_file: `{connection.get("state_file")}`
- canonical_project_alias: `{(connection.get("canonical_project_alias") or {}).get("kind") if isinstance(connection.get("canonical_project_alias"), dict) else None}`

## Recommended Next Step

- kind: `{next_step.get("kind")}`
- requires_user_confirmation: `{next_step.get("requires_user_confirmation")}`
- summary: {next_step.get("summary")}

## New User Command Hint

````text
{onboarding.get("suggested_user_note", "")}
````

- CLI help: `{(slash_catalog.get("help") or {}).get("cli_command") if isinstance(slash_catalog.get("help"), dict) else "loopx slash-commands"}`

## Paste Message

````text
{payload.get("message", "")}
````

## Goal Start Contract

- schema: `{goal_start.get("schema_version")}`
- planner_required_before_todo_write: `{(goal_start.get("planner") or {}).get("required_before_todo_write") if isinstance(goal_start.get("planner"), dict) else None}`
- same_priority_tie_breaker: `{ordering.get("same_priority_tie_breaker")}`
- prompt_constraint: {ordering.get("prompt_constraint")}
- host_loop_required_after_todo_writeback: `{(goal_start.get("activation") or {}).get("host_loop_required_after_todo_writeback") if isinstance(goal_start.get("activation"), dict) else None}`

## Host Loop Activation

- host_surface: `{activation.get("host_surface")}`
- activation_method: `{activation.get("activation_method")}`
- entry_command_hint: `{activation.get("entry_command_hint")}`
- activation_input_command: `{activation.get("activation_input_command")}`
- recheck_command: `{commands.get("goal_start_agent_onboard_recheck")}`

## Key Commands

```bash
{commands.get("status", "")}
```

```bash
{commands.get("bootstrap_dry_run_preview", "")}
```

```bash
{commands.get("goal_start_connect_if_needed", "")}
```

```bash
{commands.get("issue_fix_workflow_plan_template", "")}
```

## Safety Contract

- read_only: `{payload.get("read_only")}`
- writes_registry: `{safety.get("writes_registry")}`
- writes_state_file: `{safety.get("writes_state_file")}`
- creates_heartbeat: `{safety.get("creates_heartbeat")}`
- spends_quota: `{safety.get("spends_quota")}`
- explicit_goal_start_may_write_project_local_state: `{safety.get("explicit_goal_start_may_write_project_local_state")}`
- explicit_goal_start_must_activate_host_loop: `{safety.get("explicit_goal_start_must_activate_host_loop")}`
"""
