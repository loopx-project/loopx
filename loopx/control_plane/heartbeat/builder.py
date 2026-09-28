"""Heartbeat prompt builder inside the heartbeat bounded context."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from ...agent_registry import normalize_registered_agents
from ...ark_managed_agent_host import build_ark_managed_agent_host_contract
from ...execution_profile import (
    TURN_GRANULARITY_FINE,
    execution_profile_turn_granularity,
)
from ..agents.runtime_model import AgentRuntimeModel
from ..quota.spend_sources import (
    DEFAULT_SLOT_SPEND_SOURCE,
    VISIBLE_GOAL_SLOT_SPEND_SOURCE,
)
from ..scheduler.execution_context import (
    SchedulerRuntimeProfile,
    render_scheduler_execution_args,
)
from ..todos.contract import (
    normalize_required_capabilities,
    normalize_todo_claimed_by,
)
from .agent import (
    agent_prompt_command_args,
    agent_profile_prompt_projection,
    agent_profile_scopes,
    build_peer_identity_required_error,
    normalize_agent_scopes,
    render_peer_agent_scope_instruction,
)
from .budget import (
    NATIVE_GOAL_HOST_MAX_CHARS,
    build_interface_budget,
)
from .host import (
    resolve_exact_heartbeat_turn_identity,
    uses_ark_managed_agent_goal_host,
    uses_native_goal_host_loop,
)
from .rules import (
    DEFAULT_MATERIAL_QUEUE_RULE,
    DEFAULT_PERMISSION_RULE,
    REWARD_MEMORY_OUTCOME_COMPACT_RULE,
    REWARD_MEMORY_OUTCOME_RULE,
)
from .task_body import (
    bind_exact_turn_settlement_task_body,
    render_ark_managed_agent_goal_task_body,
    render_brief_heartbeat_task_body,
    render_compact_heartbeat_task_body,
    render_heartbeat_task_body,
    render_thin_heartbeat_task_body,
    render_traex_visible_goal_task_body,
    render_visible_goal_task_body,
)
from .visible_goal import (
    build_visible_goal_initial_runtime_capability_projection,
    validate_visible_goal_policy_rule,
)
from ...project_prompt import (
    render_accountable_progress_refresh_command,
    render_available_capability_args,
    render_cli_command_prefix,
    render_cli_preflight,
    render_quota_guard_command,
    render_quota_spend_command,
    render_refresh_state_command,
)

FINE_GRAINED_TURN_RULE = (
    "Fine-grained planning contract: each Todo must be an independently verifiable checkpoint; "
    "split independent decisions before delivery. Work follows one coherent direction "
    "and may complete one or more causally related Agent advancement Todos. After each "
    "completion inspect fresh evidence before creating or claiming a successor; continue "
    "only while the direction remains unchanged. Validate and durably complete each Todo, "
    "then perform accountable refresh and spend to settle the turn once after the work. "
    "A direction change or bounded-chain review must use the existing replan obligation/"
    "ACK path before further delivery. Protocol/setup and capability re-entry steps are "
    "inline non-advancement work: never create Todos or settle a turn for them alone."
)


def _select_task_body_renderer(
    *,
    traex_visible_goal: bool,
    ark_managed_agent_goal: bool,
    native_goal_host: bool,
    thin: bool,
    brief: bool,
    compact: bool,
) -> Any:
    if traex_visible_goal:
        return render_traex_visible_goal_task_body
    if ark_managed_agent_goal:
        return render_ark_managed_agent_goal_task_body
    if native_goal_host:
        return render_visible_goal_task_body
    if thin:
        return render_thin_heartbeat_task_body
    if brief:
        return render_brief_heartbeat_task_body
    if compact:
        return render_compact_heartbeat_task_body
    return render_heartbeat_task_body


def _reward_memory_rule_kwargs(
    *,
    full: bool,
    reward_memory_enabled: bool,
    native_goal_host: bool,
    ark_managed_agent_goal: bool,
) -> dict[str, str]:
    if native_goal_host or ark_managed_agent_goal:
        return {}
    if not reward_memory_enabled:
        return {"reward_memory_rule": ""}
    return {
        "reward_memory_rule": (
            REWARD_MEMORY_OUTCOME_RULE
            if full
            else REWARD_MEMORY_OUTCOME_COMPACT_RULE
        )
    }


def _heartbeat_regeneration_commands(
    *,
    cli_bin: str,
    runtime_root: str | Path | None,
    goal_id: str,
    active_state_arg: str,
    agent_args: str,
    capability_args: str,
    scheduler_args: str,
    turn_identity_arg: str,
) -> tuple[str, str, str, str]:
    suffix = (
        f" --goal-id {goal_id}{active_state_arg}{agent_args}"
        f"{capability_args}{scheduler_args}{turn_identity_arg}"
    )
    command_prefix = render_cli_command_prefix(
        cli_bin=cli_bin,
        runtime_root=runtime_root,
    )
    return tuple(
        f"{command_prefix} heartbeat-prompt --{mode}{suffix}"
        for mode in ("full", "compact", "brief", "thin")
    )


def _heartbeat_prompt_commands(
    *,
    goal_id: str,
    cli_bin: str,
    runtime_root: str | Path | None,
    normalized_agent_id: str | None,
    normalized_available_capabilities: tuple[str, ...],
    task_body_available_capabilities: tuple[str, ...],
    runtime_profile: str | None,
    scheduler_execution_context: Any,
    native_goal_host: bool,
    traex_visible_goal: bool,
    active_state_arg: str,
    agent_args: str,
    capability_args: str,
    turn_identity_arg: str,
) -> dict[str, str | None]:
    quota_guard_command = render_quota_guard_command(
        goal_id,
        cli_bin=cli_bin,
        runtime_root=runtime_root,
        agent_id=normalized_agent_id,
        available_capabilities=normalized_available_capabilities,
        runtime_profile=runtime_profile,
        scheduler_execution_context=scheduler_execution_context,
        heartbeat_turn_receipt=not native_goal_host,
    )
    quota_spend_command = render_quota_spend_command(
        goal_id,
        source=(
            VISIBLE_GOAL_SLOT_SPEND_SOURCE
            if native_goal_host
            else DEFAULT_SLOT_SPEND_SOURCE
        ),
        cli_bin=cli_bin,
        runtime_root=runtime_root,
        agent_id=normalized_agent_id,
        available_capabilities=normalized_available_capabilities,
    )
    task_body_quota_guard_command = quota_guard_command
    task_body_quota_spend_command = quota_spend_command
    if traex_visible_goal:
        task_body_quota_guard_command = render_quota_guard_command(
            goal_id,
            cli_bin=cli_bin,
            runtime_root=runtime_root,
            agent_id=normalized_agent_id,
            available_capabilities=task_body_available_capabilities,
            runtime_profile=runtime_profile,
            scheduler_execution_context=scheduler_execution_context,
        )
        task_body_quota_spend_command = render_quota_spend_command(
            goal_id,
            source=VISIBLE_GOAL_SLOT_SPEND_SOURCE,
            cli_bin=cli_bin,
            runtime_root=runtime_root,
            agent_id=normalized_agent_id,
        )
    scheduler_args = render_scheduler_execution_args(
        runtime_profile=runtime_profile,
        scheduler_execution_context=scheduler_execution_context,
    )
    (
        expanded_prompt_command,
        compact_prompt_command,
        brief_prompt_command,
        thin_prompt_command,
    ) = _heartbeat_regeneration_commands(
        cli_bin=cli_bin,
        runtime_root=runtime_root,
        goal_id=goal_id,
        active_state_arg=active_state_arg,
        agent_args=agent_args,
        capability_args=capability_args,
        scheduler_args=scheduler_args,
        turn_identity_arg=turn_identity_arg,
    )
    pr_review_pre_quota_command = (
        f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)} "
        f"heartbeat-prequota -g {shlex.quote(goal_id)} "
        f"-a {shlex.quote(normalized_agent_id)}"
        if normalized_agent_id
        and "external_evidence_poll" in normalized_available_capabilities
        else None
    )
    return {
        "quota_guard_command": quota_guard_command,
        "quota_spend_command": quota_spend_command,
        "task_body_quota_guard_command": task_body_quota_guard_command,
        "task_body_quota_spend_command": task_body_quota_spend_command,
        "refresh_state_command": render_refresh_state_command(
            goal_id,
            cli_bin=cli_bin,
            runtime_root=runtime_root,
            agent_id=normalized_agent_id,
        ),
        "progress_refresh_state_command": render_accountable_progress_refresh_command(
            goal_id,
            cli_bin=cli_bin,
            runtime_root=runtime_root,
            agent_id=normalized_agent_id,
        ),
        "pr_review_pre_quota_command": pr_review_pre_quota_command,
        "expanded_prompt_command": expanded_prompt_command,
        "compact_prompt_command": compact_prompt_command,
        "brief_prompt_command": brief_prompt_command,
        "thin_prompt_command": thin_prompt_command,
    }


def build_heartbeat_prompt(
    *,
    goal_id: str,
    active_state: Path | None = None,
    active_state_source: str = "explicit",
    resolved_active_state: Path | None = None,
    material_queue_rule: str | None = None,
    permission_rule: str | None = None,
    full: bool = False,
    compact: bool = False,
    brief: bool = False,
    thin: bool = False,
    cli_bin: str = "loopx",
    runtime_root: str | Path | None = None,
    agent_id: str | None = None,
    agent_scopes: list[str] | tuple[str, ...] | None = None,
    agent_profile: dict[str, Any] | None = None,
    registered_agents: list[str] | tuple[str, ...] | None = None,
    available_capabilities: list[str] | tuple[str, ...] | None = None,
    runtime_profile: str | None = None,
    scheduler_execution_context: dict[str, Any] | None = None,
    visible_goal_host: str | None = None,
    turn_granularity: str | None = None,
    turn_instance_id: str | None = None,
    reward_memory_enabled: bool = True,
) -> dict[str, Any]:
    if not (full or compact or brief or thin):
        thin = True
    normalized_turn_granularity = execution_profile_turn_granularity(
        {"turn_granularity": turn_granularity} if turn_granularity is not None else None
    )
    fine_grained = normalized_turn_granularity == TURN_GRANULARITY_FINE
    if visible_goal_host not in {None, "traex-cli"}:
        raise ValueError(f"unsupported visible goal host: {visible_goal_host}")
    if visible_goal_host == "traex-cli" and (
        runtime_profile != SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP.value
        or scheduler_execution_context is not None
    ):
        raise ValueError(
            "visible_goal_host='traex-cli' requires runtime_profile='generic_cli' "
            "without scheduler_execution_context"
        )
    traex_visible_goal = visible_goal_host == "traex-cli"
    native_goal_host = uses_native_goal_host_loop(
        runtime_profile=runtime_profile,
        scheduler_execution_context=scheduler_execution_context,
    ) or traex_visible_goal
    ark_managed_agent_goal = uses_ark_managed_agent_goal_host(
        runtime_profile=runtime_profile,
        scheduler_execution_context=scheduler_execution_context,
    )
    effective_resolved_active_state = resolved_active_state or active_state
    active_state_text = str(active_state.expanduser()) if active_state else "the registry-declared active state"
    if active_state:
        resolved_active_state_source = active_state_source
    else:
        resolved_active_state_source = "registry" if active_state_source == "explicit" else active_state_source
    active_state_arg = f" --active-state {active_state_text}" if active_state else ""
    resolved_material_rule = material_queue_rule or DEFAULT_MATERIAL_QUEUE_RULE
    resolved_permission_rule = permission_rule or DEFAULT_PERMISSION_RULE
    if traex_visible_goal:
        validate_visible_goal_policy_rule(
            field="material_queue_rule",
            value=resolved_material_rule,
        )
        validate_visible_goal_policy_rule(
            field="permission_rule",
            value=resolved_permission_rule,
        )
    normalized_agent_id = normalize_todo_claimed_by(agent_id) if agent_id else None
    if agent_id and not normalized_agent_id:
        raise ValueError("agent_id must be a public-safe token such as codex-main-control")
    (
        normalized_turn_instance_id,
        turn_identity_arg,
        turn_identity_payload,
    ) = resolve_exact_heartbeat_turn_identity(
        turn_instance_id=turn_instance_id,
        agent_id=normalized_agent_id,
        runtime_profile=runtime_profile,
        scheduler_execution_context=scheduler_execution_context,
    )
    explicit_agent_scopes = normalize_agent_scopes(agent_scopes)
    if explicit_agent_scopes:
        normalized_agent_scopes = explicit_agent_scopes
        agent_scope_source = "argument"
    else:
        normalized_agent_scopes = agent_profile_scopes(agent_profile)
        agent_scope_source = "agent_profile_v1" if normalized_agent_scopes else None
    if traex_visible_goal:
        for scope in normalized_agent_scopes:
            validate_visible_goal_policy_rule(field="agent_scope", value=scope)
    if normalized_agent_scopes and not normalized_agent_id:
        raise ValueError("--agent-scope requires --agent-id so claimed_by uses a registered agent")
    normalized_registered_agents = normalize_registered_agents(registered_agents)
    if normalized_registered_agents and not normalized_agent_id:
        raise ValueError(
            build_peer_identity_required_error(
                goal_id=goal_id,
                cli_bin=cli_bin,
                active_state_arg=active_state_arg,
                full=full,
                compact=compact,
                brief=brief,
                thin=thin,
                registered_agents=normalized_registered_agents,
            )
        )
    if normalized_agent_id:
        if registered_agents is not None and not normalized_registered_agents:
            raise ValueError("agent_id cannot be used until registered_agents are configured")
        if normalized_registered_agents and normalized_agent_id not in normalized_registered_agents:
            raise ValueError(
                f"agent_id={normalized_agent_id!r} is not registered; "
                f"registered_agents={', '.join(normalized_registered_agents)}"
            )
    agent_role = "peer-agent" if normalized_agent_id else None
    command_agent_scopes = explicit_agent_scopes
    agent_args = agent_prompt_command_args(
        agent_id=normalized_agent_id,
        agent_scopes=command_agent_scopes,
    )
    normalized_available_capabilities = normalize_required_capabilities(
        available_capabilities
    )
    initial_runtime_capability_projection = (
        build_visible_goal_initial_runtime_capability_projection(
            normalized_available_capabilities
        )
        if traex_visible_goal
        else None
    )
    if initial_runtime_capability_projection:
        task_body_available_capabilities = initial_runtime_capability_projection[
            "capabilities"
        ]
    elif traex_visible_goal:
        task_body_available_capabilities = []
    else:
        task_body_available_capabilities = normalized_available_capabilities
    capability_args = render_available_capability_args(
        normalized_available_capabilities
    )
    agent_scope_instruction = render_peer_agent_scope_instruction(
        goal_id=goal_id,
        agent_id=normalized_agent_id,
        agent_scopes=normalized_agent_scopes,
        cli_bin=cli_bin,
        compact=compact or brief,
        thin=thin,
    )
    commands = _heartbeat_prompt_commands(
        goal_id=goal_id,
        cli_bin=cli_bin,
        runtime_root=runtime_root,
        normalized_agent_id=normalized_agent_id,
        normalized_available_capabilities=normalized_available_capabilities,
        task_body_available_capabilities=task_body_available_capabilities,
        runtime_profile=runtime_profile,
        scheduler_execution_context=scheduler_execution_context,
        native_goal_host=native_goal_host,
        traex_visible_goal=traex_visible_goal,
        active_state_arg=active_state_arg,
        agent_args=agent_args,
        capability_args=capability_args,
        turn_identity_arg=turn_identity_arg,
    )
    cli_preflight = render_cli_preflight(cli_bin=cli_bin)
    task_body_renderer = _select_task_body_renderer(
        traex_visible_goal=traex_visible_goal,
        ark_managed_agent_goal=ark_managed_agent_goal,
        native_goal_host=native_goal_host,
        thin=thin,
        brief=brief,
        compact=compact,
    )
    reward_memory_rule_kwargs = _reward_memory_rule_kwargs(
        full=full,
        reward_memory_enabled=reward_memory_enabled,
        native_goal_host=native_goal_host,
        ark_managed_agent_goal=ark_managed_agent_goal,
    )
    task_body = task_body_renderer(
        goal_id=goal_id,
        active_state=active_state_text,
        cli_preflight=cli_preflight,
        pr_review_pre_quota_command=(
            "" if traex_visible_goal else commands["pr_review_pre_quota_command"] or ""
        ),
        quota_guard_command=str(commands["task_body_quota_guard_command"]),
        quota_spend_command=str(commands["task_body_quota_spend_command"]),
        refresh_state_command=str(commands["refresh_state_command"]),
        progress_refresh_state_command=str(commands["progress_refresh_state_command"]),
        material_queue_rule=resolved_material_rule,
        permission_rule=resolved_permission_rule,
        cli_bin=cli_bin,
        agent_scope_instruction=agent_scope_instruction,
        expanded_prompt_command=str(commands["expanded_prompt_command"]),
        compact_prompt_command=str(commands["compact_prompt_command"]),
        brief_prompt_command=str(commands["brief_prompt_command"]),
        thin_prompt_command=str(commands["thin_prompt_command"]),
        **reward_memory_rule_kwargs,
    )
    task_body = bind_exact_turn_settlement_task_body(
        task_body,
        turn_instance_id=normalized_turn_instance_id,
    )
    if fine_grained:
        task_body = f"{task_body}\n\n{FINE_GRAINED_TURN_RULE}"
    if native_goal_host and len(task_body) > NATIVE_GOAL_HOST_MAX_CHARS:
        host_limit = (
            "Ark Managed Agent goal prompt"
            if ark_managed_agent_goal
            else "visible TraeX /goal task body"
            if traex_visible_goal
            else "visible Codex /goal task body"
        )
        raise ValueError(
            f"generated {host_limit} exceeds the 4000-character host budget; "
            "shorten agent scopes or project-specific prompt rules"
        )
    payload = {
        "ok": True,
        "goal_id": goal_id,
        "active_state": active_state_text,
        "active_state_source": resolved_active_state_source,
        "resolved_active_state": str(effective_resolved_active_state.expanduser())
        if effective_resolved_active_state
        else None,
        "compact": compact,
        "brief": brief,
        "thin": thin,
        "cli_bin": cli_bin,
        "agent_id": normalized_agent_id,
        **turn_identity_payload,
        "agent_role": agent_role,
        "agent_scopes": normalized_agent_scopes,
        "agent_scope_source": agent_scope_source,
        "agent_profile": agent_profile_prompt_projection(agent_profile),
        "registered_agents": normalized_registered_agents,
        "runtime_profile": runtime_profile,
        "reward_memory_enabled": reward_memory_enabled,
        "scheduler_execution_context": scheduler_execution_context,
        **(
            {"visible_goal_host": visible_goal_host}
            if visible_goal_host
            else {}
        ),
        **(
            {
                "initial_runtime_capability_projection": (
                    initial_runtime_capability_projection
                )
            }
            if initial_runtime_capability_projection
            else {}
        ),
        **(
            {"host_contract": build_ark_managed_agent_host_contract()}
            if ark_managed_agent_goal
            else {}
        ),
        "expanded_prompt_command": commands["expanded_prompt_command"],
        "compact_prompt_command": commands["compact_prompt_command"],
        "brief_prompt_command": commands["brief_prompt_command"],
        "thin_prompt_command": commands["thin_prompt_command"],
        "pr_review_pre_quota_command": commands["pr_review_pre_quota_command"],
        "quota_guard_command": commands["quota_guard_command"],
        "quota_spend_command": commands["quota_spend_command"],
        "refresh_state_command": commands["refresh_state_command"],
        "progress_refresh_state_command": commands["progress_refresh_state_command"],
        "cli_preflight": cli_preflight,
        "material_queue_rule": resolved_material_rule,
        "permission_rule": resolved_permission_rule,
        "interface_budget": build_interface_budget(
            task_body=task_body,
            goal_id=goal_id,
            active_state=active_state_text,
            full=full,
            compact=compact,
            brief=brief,
            thin=thin,
            native_goal_host=native_goal_host,
        ),
        "task_body": task_body,
    }
    if runtime_root:
        payload["runtime_root"] = str(Path(runtime_root).expanduser())
    if fine_grained:
        payload["turn_granularity"] = TURN_GRANULARITY_FINE
        payload["turn_mode"] = "fine_grained"
    payload["agent_model"] = AgentRuntimeModel.PEER_V1.value
    if thin:
        payload.pop("compact_prompt_command", None)
        payload.pop("brief_prompt_command", None)
        # Thin heartbeat consumers can distinguish an unresolved state path
        # by absence. Keeping a null placeholder spends hot-path budget while
        # conveying no additional routing or execution authority.
        if payload.get("resolved_active_state") is None:
            payload.pop("resolved_active_state", None)
        for key in (
            "agent_profile",
            "agent_role",
            "agent_scope_source",
            "agent_scopes",
            "registered_agents",
        ):
            if not payload.get(key):
                payload.pop(key, None)
    return payload
def build_heartbeat_prompt_error_payload(
    *,
    goal_id: str,
    error: str,
    active_state: Path | None = None,
    active_state_source: str | None = None,
    resolved_active_state: Path | None = None,
    full: bool = False,
    compact: bool = False,
    brief: bool = False,
    thin: bool = False,
    cli_bin: str = "loopx",
    runtime_root: str | Path | None = None,
    agent_id: str | None = None,
    agent_scopes: list[str] | tuple[str, ...] | None = None,
    registered_agents: list[str] | tuple[str, ...] | None = None,
    available_capabilities: list[str] | tuple[str, ...] | None = None,
    material_queue_rule: str | None = None,
    permission_rule: str | None = None,
) -> dict[str, Any]:
    if not (full or compact or brief or thin):
        thin = True
    active_state_text = str(active_state.expanduser()) if active_state else "the registry-declared active state"
    source = active_state_source or ("explicit" if active_state else "registry")
    active_state_arg = f" --active-state {active_state_text}" if active_state else ""
    projected_agent_scopes = []
    for value in agent_scopes or []:
        scope = " ".join(str(value or "").strip().split())
        if scope and scope not in projected_agent_scopes:
            projected_agent_scopes.append(scope)
    agent_args = agent_prompt_command_args(
        agent_id=str(agent_id).strip() if agent_id else None,
        agent_scopes=projected_agent_scopes,
    )
    projected_available_capabilities = normalize_required_capabilities(
        available_capabilities
    )
    capability_args = render_available_capability_args(
        projected_available_capabilities
    )
    command_prefix = render_cli_command_prefix(
        cli_bin=cli_bin,
        runtime_root=runtime_root,
    )
    expanded_prompt_command = f"{command_prefix} heartbeat-prompt --full --goal-id {goal_id}{active_state_arg}{agent_args}{capability_args}"
    compact_prompt_command = f"{command_prefix} heartbeat-prompt --compact --goal-id {goal_id}{active_state_arg}{agent_args}{capability_args}"
    brief_prompt_command = f"{command_prefix} heartbeat-prompt --brief --goal-id {goal_id}{active_state_arg}{agent_args}{capability_args}"
    thin_prompt_command = f"{command_prefix} heartbeat-prompt --thin --goal-id {goal_id}{active_state_arg}{agent_args}{capability_args}"
    normalized_registered_agents = normalize_registered_agents(registered_agents)
    payload = {
        "ok": False,
        "goal_id": goal_id,
        "error": error,
        "active_state": active_state_text,
        "active_state_source": source,
        "resolved_active_state": str(resolved_active_state.expanduser()) if resolved_active_state else None,
        "compact": compact,
        "brief": brief,
        "thin": thin,
        "cli_bin": cli_bin,
        "agent_id": str(agent_id).strip() if agent_id else None,
        "agent_role": None,
        "agent_scopes": projected_agent_scopes,
        "agent_scope_source": "argument" if projected_agent_scopes else None,
        "agent_profile": None,
        "registered_agents": normalized_registered_agents,
        "expanded_prompt_command": expanded_prompt_command,
        "compact_prompt_command": compact_prompt_command,
        "brief_prompt_command": brief_prompt_command,
        "thin_prompt_command": thin_prompt_command,
        "quota_guard_command": None,
        "quota_spend_command": None,
        "cli_preflight": None,
        "material_queue_rule": material_queue_rule,
        "permission_rule": permission_rule,
        "interface_budget": None,
        "task_body": None,
    }
    if runtime_root:
        payload["runtime_root"] = str(Path(runtime_root).expanduser())
    payload["agent_model"] = AgentRuntimeModel.PEER_V1.value
    return payload
