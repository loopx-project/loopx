from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from ..agent_registry import normalize_registered_agents
from ..capabilities.multi_subagent import (
    apply_codex_subagent_capacity,
    plan_codex_subagent_capacity,
)
from ..configure_goal import configure_goal, render_configure_goal_markdown
from ..control_plane.goals.configure_goal_service import (
    configure_goal_with_global_sync,
)
from ..file_lock import exclusive_file_lock, lock_timeout_error_fields
from ..global_registry import sync_project_registry_to_global
from ..history import load_registry
from ..registry import registry_goals
from ..registry_writability import probe_registry_write_path
from ..thread_agent_binding import (
    bind_thread_agent_in_registry,
    resolve_thread_agent_binding,
    unbind_thread_agent_in_registry,
)
from ..upgrade import build_upgrade_plan
from .goal_lifecycle import (
    handle_goal_lifecycle_command,
    register_goal_lifecycle_command,
)
from .goal_actions import handle_goal_actions_command, register_goal_actions_command
from .registry_admin_configure import register_configure_goal_command
from .registry_admin_lifecycle import (
    REGISTRY_LIFECYCLE_COMMANDS,
    handle_registry_lifecycle_command,
    register_registry_lifecycle_commands,
)
from .registry_admin_peer import render_register_agent_markdown
from .registry_admin_thread_resolution import (
    REGISTRY_THREAD_RESOLUTION_COMMANDS,
    handle_registry_thread_resolution_command,
    register_registry_thread_resolution_command,
)
from .registry_authority import (
    REGISTRY_AUTHORITY_COMMANDS,
    handle_registry_authority_command,
    register_registry_authority_commands,
)
from .support_control_registry import explicit_global_registry

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]

REGISTRY_ADMIN_COMMANDS = {
    "configure-goal",
    "goal-lifecycle",
    "goal-actions",
    "register-agent",
    "resolve-agent-thread",
    "bind-agent-thread",
    "unbind-agent-thread",
} | REGISTRY_AUTHORITY_COMMANDS | REGISTRY_THREAD_RESOLUTION_COMMANDS | REGISTRY_LIFECYCLE_COMMANDS


def _registry_goal(path: Path, goal_id: str) -> dict[str, object]:
    payload = load_registry(path)
    goal = next((item for item in registry_goals(payload) if item.get("id") == goal_id), None)
    if goal is None:
        raise ValueError(f"{goal_id}: registry does not contain the goal: {path}")
    return goal


def _goal_registered_agents(goal: dict[str, object]) -> list[str]:
    coordination = goal.get("coordination") if isinstance(goal.get("coordination"), dict) else {}
    return normalize_registered_agents(coordination.get("registered_agents"))


def _fresh_agent_collision_payload(
    *,
    execute: bool,
    goal_id: str,
    global_path: Path,
    source_registry_path: Path,
    existing_agents: list[str],
    requested_agents: list[str],
    collisions: list[str],
) -> dict[str, object]:
    return {
        "ok": False,
        "dry_run": not execute,
        "execute": execute,
        "goal_id": goal_id,
        "global_registry": str(global_path),
        "source_registry": str(source_registry_path),
        "existing_agents": existing_agents,
        "requested_agents": requested_agents,
        "registered_agents": existing_agents,
        "changed": False,
        "written": False,
        "error_kind": "agent_identity_already_registered",
        "error": (
            "fresh agent registration requires an unused agent id; already "
            f"registered: {', '.join(collisions)}"
        ),
        "recommended_action": (
            "choose a different public-safe agent id and rerun the preview; continue "
            "only after the execute result reports ok=true, changed=true, and written=true"
        ),
    }


def _agent_registration_readback(
    *,
    source_registry_path: Path,
    global_path: Path,
    goal_id: str,
    requested_agents: list[str],
) -> dict[str, object]:
    source_agents = _goal_registered_agents(_registry_goal(source_registry_path, goal_id))
    global_agents = _goal_registered_agents(_registry_goal(global_path, goal_id))
    requested_present = all(
        agent_id in source_agents and agent_id in global_agents
        for agent_id in requested_agents
    )
    return {
        "schema_version": "loopx_agent_registration_readback_v0",
        "performed": True,
        "verified": requested_present and source_agents == global_agents,
        "requested_agents": requested_agents,
        "source_registered_agents": source_agents,
        "global_registered_agents": global_agents,
    }


def register_agent_via_source_registry(
    *,
    runtime_root_arg: str | None,
    goal_id: str,
    agent_ids: list[str],
    execute: bool,
    require_new: bool = False,
) -> dict[str, object]:
    global_path = explicit_global_registry(runtime_root_arg)
    if not global_path.exists():
        raise FileNotFoundError(f"global registry does not exist: {global_path}")
    global_registry = load_registry(global_path)
    goal = next((item for item in registry_goals(global_registry) if item.get("id") == goal_id), None)
    if goal is None:
        raise ValueError(f"goal_id not found in global registry: {goal_id}")
    source_registry = goal.get("source_registry")
    if not source_registry:
        raise ValueError(
            f"{goal_id}: global registry entry has no source_registry; "
            "use configure-goal with an explicit --registry instead of connect"
        )
    source_registry_path = Path(str(source_registry)).expanduser()
    source_goal = _registry_goal(source_registry_path, goal_id)
    existing_agents = _goal_registered_agents(source_goal)
    requested_agents = normalize_registered_agents(agent_ids)
    collisions = [agent_id for agent_id in requested_agents if agent_id in existing_agents]
    if require_new and collisions:
        return _fresh_agent_collision_payload(
            execute=execute,
            goal_id=goal_id,
            global_path=global_path,
            source_registry_path=source_registry_path,
            existing_agents=existing_agents,
            requested_agents=requested_agents,
            collisions=collisions,
        )
    merged_agents = list(existing_agents)
    for agent_id in requested_agents:
        if agent_id not in merged_agents:
            merged_agents.append(agent_id)
    global_writability: dict[str, object] | None = None
    if execute:
        global_writability = probe_registry_write_path(global_path, create_parent=True)
        if not global_writability.get("ok"):
            return {
                "ok": False,
                "dry_run": False,
                "execute": True,
                "goal_id": goal_id,
                "global_registry": str(global_path),
                "source_registry": str(source_registry_path),
                "existing_agents": existing_agents,
                "requested_agents": requested_agents,
                "registered_agents": merged_agents,
                "changed": merged_agents != existing_agents,
                "written": False,
                "host_loop_activation": loop_activation_for_goal(
                    registry_path=global_path,
                    runtime_root_arg=runtime_root_arg,
                    goal_id=goal_id,
                ),
                "global_registry_writability": global_writability,
                "global_sync": {
                    "ok": False,
                    "enabled": True,
                    "wrote": False,
                    "write_denied": True,
                    "error_kind": "global_registry_write_denied",
                    "global_registry": str(global_path),
                    "global_registry_writability": global_writability,
                    "recommended_action": global_writability.get("recommended_action"),
                },
                "error": str(global_writability.get("error") or "global registry is not writable"),
                "recommended_action": global_writability.get("recommended_action"),
            }
    sync_payload: dict[str, object] | None = None
    readback_payload: dict[str, object] = {
        "schema_version": "loopx_agent_registration_readback_v0",
        "performed": False,
        "verified": False,
    }
    if execute:
        with exclusive_file_lock(
            source_registry_path,
            agent_id=requested_agents[0] if len(requested_agents) == 1 else None,
            operation="register_agent",
        ):
            source_goal = _registry_goal(source_registry_path, goal_id)
            existing_agents = _goal_registered_agents(source_goal)
            collisions = [
                agent_id for agent_id in requested_agents if agent_id in existing_agents
            ]
            if require_new and collisions:
                return _fresh_agent_collision_payload(
                    execute=True,
                    goal_id=goal_id,
                    global_path=global_path,
                    source_registry_path=source_registry_path,
                    existing_agents=existing_agents,
                    requested_agents=requested_agents,
                    collisions=collisions,
                )
            merged_agents = list(existing_agents)
            for agent_id in requested_agents:
                if agent_id not in merged_agents:
                    merged_agents.append(agent_id)
            configure_payload = configure_goal(
                registry_path=source_registry_path,
                goal_id=goal_id,
                registered_agents=merged_agents,
                agent_model="peer_v1",
                execute=True,
            )
            if configure_payload.get("written"):
                sync_payload = sync_project_registry_to_global(
                    registry_path=source_registry_path,
                    # Sync back to the same shared registry that supplied source_registry.
                    # A project-local common_runtime_root must not redirect this write.
                    runtime_root_override=str(global_path.parent),
                    goal_id=goal_id,
                    dry_run=False,
                )
                readback_payload = _agent_registration_readback(
                    source_registry_path=source_registry_path,
                    global_path=global_path,
                    goal_id=goal_id,
                    requested_agents=requested_agents,
                )
    else:
        configure_payload = configure_goal(
            registry_path=source_registry_path,
            goal_id=goal_id,
            registered_agents=merged_agents,
            agent_model="peer_v1",
            execute=False,
        )
    sync_ok = bool(sync_payload.get("ok", True)) if isinstance(sync_payload, dict) else True
    readback_ok = (
        bool(readback_payload.get("verified"))
        if execute and configure_payload.get("written")
        else True
    )
    overall_ok = sync_ok and readback_ok
    result = {
        "ok": overall_ok,
        "dry_run": not execute,
        "execute": execute,
        "goal_id": goal_id,
        "global_registry": str(global_path),
        "source_registry": str(source_registry_path),
        "existing_agents": existing_agents,
        "requested_agents": requested_agents,
        "registered_agents": merged_agents,
        "changed": configure_payload.get("changed"),
        "written": configure_payload.get("written"),
        "configure_goal": configure_payload,
        "global_registry_writability": global_writability or {},
        "partial_write": bool(execute and configure_payload.get("written") and not overall_ok),
        "recommended_action": (
            sync_payload.get("recommended_action")
            if isinstance(sync_payload, dict) and sync_payload.get("ok") is False
            else (
                "repair the source/global registration readback mismatch before continuation"
                if not readback_ok
                else None
            )
        ),
        "global_sync": sync_payload or {"enabled": bool(execute), "wrote": False},
        "registration_readback": readback_payload,
    }
    result["host_loop_activation"] = loop_activation_for_goal(
        registry_path=global_path,
        runtime_root_arg=runtime_root_arg,
        goal_id=goal_id,
    )
    return result


def loop_activation_for_goal(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
) -> dict[str, object]:
    try:
        plan = build_upgrade_plan(
            registry_path=registry_path,
            runtime_root_override=runtime_root_arg,
            goal_ids=[goal_id],
        )
        goals = plan.get("managed_heartbeats") if isinstance(plan.get("managed_heartbeats"), list) else []
        if not goals:
            return {
                "schema_version": "loopx_host_loop_activation_v0",
                "host_surface": "codex_app_heartbeat",
                "status": "unavailable",
                "activated": False,
                "recommended_action": (
                    "run loopx upgrade-plan for this goal; do not claim setup complete until "
                    "host_loop_activation.activated=true or a concrete host-tool gate is reported"
                ),
            }
        activation = goals[0].get("host_loop_activation")
        if isinstance(activation, dict):
            return activation
    except Exception as exc:
        return {
            "schema_version": "loopx_host_loop_activation_v0",
            "host_surface": "codex_app_heartbeat",
            "status": "error",
            "activated": False,
            "error": str(exc),
            "recommended_action": (
                "repair the host-loop activation check; do not claim setup complete until "
                "host_loop_activation.activated=true or a concrete host-tool gate is reported"
            ),
        }
    return {
        "schema_version": "loopx_host_loop_activation_v0",
        "host_surface": "codex_app_heartbeat",
        "status": "unknown",
        "activated": False,
        "recommended_action": (
            "create or update the Codex App heartbeat automation from loopx heartbeat-prompt"
        ),
    }


def register_registry_admin_commands(subparsers: argparse._SubParsersAction) -> None:
    register_configure_goal_command(subparsers)
    register_goal_lifecycle_command(subparsers)
    register_goal_actions_command(subparsers)

    register_agent_parser = subparsers.add_parser(
        "register-agent",
        help="Register an automation agent through the existing global source_registry without reconnecting the goal.",
    )
    register_agent_parser.add_argument("--goal-id", required=True, help="Goal id already present in the global registry.")
    register_agent_parser.add_argument(
        "--agent-id",
        action="append",
        required=True,
        help="Public-safe agent id to add. Repeatable; comma-separated values are also accepted.",
    )
    register_agent_parser.add_argument(
        "--require-new",
        action="store_true",
        help=(
            "Fail when any requested id is already registered. Fresh-agent onboarding "
            "uses this to prevent accidental takeover; ordinary registration remains "
            "idempotent without the flag."
        ),
    )
    register_agent_parser.add_argument(
        "--execute",
        action="store_true",
        help="Write the source registry and sync it globally. Without this flag, preview only.",
    )

    register_registry_thread_resolution_command(subparsers)

    bind_thread_parser = subparsers.add_parser(
        "bind-agent-thread",
        help="Bind a stable host thread to one already registered LoopX agent.",
    )
    bind_thread_parser.add_argument("--goal-id", required=True, help="Goal id already present in the global registry.")
    bind_thread_parser.add_argument("--thread-id", required=True, help="Stable opaque host thread id.")
    bind_thread_parser.add_argument("--host-surface", required=True, help="Host surface such as codex-app.")
    bind_thread_parser.add_argument("--agent-id", required=True, help="Already registered public-safe agent id.")
    bind_thread_parser.add_argument("--execute", action="store_true", help="Write the binding; otherwise preview only.")

    unbind_thread_parser = subparsers.add_parser(
        "unbind-agent-thread",
        help="Remove one exact host-thread binding from its expected LoopX agent.",
    )
    unbind_thread_parser.add_argument("--goal-id", required=True, help="Goal id already present in the global registry.")
    unbind_thread_parser.add_argument("--thread-id", required=True, help="Stable opaque host thread id.")
    unbind_thread_parser.add_argument("--host-surface", required=True, help="Host surface such as codex-app.")
    unbind_thread_parser.add_argument("--agent-id", required=True, help="Expected registered public-safe agent id.")
    unbind_thread_parser.add_argument("--execute", action="store_true", help="Remove the binding; otherwise preview only.")

    register_registry_lifecycle_commands(subparsers)
    register_registry_authority_commands(subparsers)


def handle_registry_admin_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    print_payload: PrintPayload,
) -> int | None:
    if args.command not in REGISTRY_ADMIN_COMMANDS:
        return None

    if args.command == "goal-lifecycle":
        return handle_goal_lifecycle_command(
            args,
            registry_path=registry_path,
            print_payload=print_payload,
        )

    if args.command == "goal-actions":
        return handle_goal_actions_command(
            args,
            registry_path=registry_path,
            print_payload=print_payload,
        )

    if args.command == "configure-goal":
        try:
            agent_work_modes: dict[str, str] = {}
            for raw_work_mode in args.agent_work_modes or []:
                agent_id, separator, mode = str(raw_work_mode).partition("=")
                if not separator or not agent_id.strip() or not mode.strip():
                    raise ValueError(
                        "--agent-work-mode must use AGENT_ID=active|monitor_only"
                    )
                if agent_id.strip() in agent_work_modes:
                    raise ValueError(
                        f"duplicate --agent-work-mode for {agent_id.strip()}"
                    )
                agent_work_modes[agent_id.strip()] = mode.strip()
            agent_profiles = [
                json.loads(raw_profile)
                for raw_profile in (args.agent_profile_jsons or [])
            ]
            todo_lifecycle_authority = [
                json.loads(raw_grant)
                for raw_grant in (args.todo_lifecycle_authority_jsons or [])
            ]
            payload = configure_goal_with_global_sync(
                registry_path=registry_path,
                goal_id=args.goal_id,
                runtime_root_override=args.runtime_root,
                quota_compute=args.quota_compute,
                quota_window_hours=args.quota_window_hours,
                execution_turn_granularity=args.execution_turn_granularity,
                execution_replan_after_todos=args.execution_replan_after_todos,
                clear_execution_replan_after_todos=bool(
                    args.clear_execution_replan_after_todos
                ),
                self_repair_enabled=args.self_repair_enabled,
                self_repair_health=args.self_repair_health,
                self_repair_waiting_projection=args.self_repair_waiting_projection,
                pull_request_review_configuration=({"wait_for_ci": args.pr_review_wait_for_ci} if args.pr_review_wait_for_ci is not None else None),
                clear_pull_request_review_configuration=args.clear_pr_review_configuration,
                change_quality_enabled=args.change_quality_enabled,
                change_quality_safe_fix=args.change_quality_safe_fix,
                change_quality_strict_receipt=args.change_quality_strict_receipt,
                clear_change_quality_configuration=bool(
                    args.clear_change_quality_configuration
                ),
                multi_subagent_feature=args.multi_subagent_feature,
                orchestration_mode=args.orchestration_mode,
                spawn_allowed=args.spawn_allowed,
                max_children=args.max_children,
                align_codex_subagent_capacity=bool(
                    args.align_codex_subagent_capacity
                ),
                codex_host_capacity_planner=plan_codex_subagent_capacity,
                codex_host_capacity_applier=apply_codex_subagent_capacity,
                subagent_model=args.subagent_model,
                subagent_reasoning_effort=args.subagent_reasoning_effort,
                clear_subagent_model_config=args.clear_subagent_model_config,
                subagent_execution_config=args.subagent_execution_config,
                clear_subagent_execution_config=bool(
                    args.clear_subagent_execution_config
                ),
                allowed_domains=args.allowed_domain,
                clear_allowed_domains=bool(args.clear_allowed_domains),
                explore_harness_enabled=args.explore_harness_enabled,
                explore_harness_profile=args.explore_harness_profile,
                clear_explore_harness_profile=bool(args.clear_explore_harness_profile),
                explore_graph_enabled=args.explore_graph_enabled,
                lark_kanban_heartbeat_sync=args.lark_kanban_heartbeat_sync,
                registered_agents=args.registered_agents,
                clear_registered_agents=bool(args.clear_registered_agents),
                peer_task_coordinator=args.peer_task_coordinator,
                clear_peer_task_coordinator=bool(
                    args.clear_peer_task_coordinator
                ),
                agent_profiles=agent_profiles,
                clear_agent_profiles=args.clear_agent_profiles,
                agent_work_modes=agent_work_modes or None,
                clear_agent_work_modes=args.clear_agent_work_modes,
                todo_lifecycle_authority=todo_lifecycle_authority,
                clear_todo_lifecycle_authority=(
                    args.clear_todo_lifecycle_authority
                ),
                agent_model=args.agent_model,
                automation_prompt_migration_ack=args.ack_automation_prompt_migration,
                supervisor_agent=args.supervisor_agent,
                supervised_agents=args.supervised_agents,
                clear_supervisor=bool(args.clear_supervisor),
                write_scope=args.write_scope,
                replace_write_scope=bool(args.replace_write_scope),
                clear_write_scope=bool(args.clear_write_scope),
                local_authority_shadow_file=bool(
                    args.local_authority_shadow_file
                ),
                clear_local_authority_shadow=bool(
                    args.clear_local_authority_shadow
                ),
                coordination_runtime_shadow_file=bool(
                    args.coordination_runtime_shadow_file
                ),
                clear_coordination_runtime_shadow=bool(
                    args.clear_coordination_runtime_shadow
                ),
                waiting_on=args.waiting_on,
                clear_waiting_on=bool(args.clear_waiting_on),
                boundary_authority_scopes=args.boundary_authority_scope,
                boundary_authority_source=args.boundary_authority_source,
                boundary_authority_decision_id=args.boundary_authority_decision_id,
                boundary_authority_recorded_at=args.boundary_authority_recorded_at,
                boundary_authority_expires_at=args.boundary_authority_expires_at,
                clear_boundary_authority=bool(args.clear_boundary_authority),
                issue_fix_reviewer_notification_config=(
                    args.issue_fix_reviewer_notification_config
                ),
                clear_issue_fix_reviewer_notification_config=bool(
                    args.clear_issue_fix_reviewer_notification_config
                ),
                lark_event_inbox_config=args.lark_event_inbox_config,
                lark_event_inbox_agent_id=args.lark_event_inbox_agent_id,
                clear_lark_event_inbox_config=bool(
                    args.clear_lark_event_inbox_config
                ),
                reward_memory_config=args.reward_memory_config,
                reward_memory_agents=args.reward_memory_agents,
                clear_reward_memory_config=bool(
                    args.clear_reward_memory_config
                ),
                execute=bool(args.execute),
            )
            if payload.get("ok"):
                payload["host_loop_activation"] = loop_activation_for_goal(
                    registry_path=registry_path,
                    runtime_root_arg=args.runtime_root,
                    goal_id=args.goal_id,
                )
        except Exception as exc:
            payload = {
                "ok": False,
                "dry_run": not bool(args.execute),
                "execute": bool(args.execute),
                "registry": str(registry_path),
                "goal_id": args.goal_id,
                "changed": False,
                "written": False,
                "error": str(exc),
            }
        print_payload(payload, args.format, render_configure_goal_markdown)
        return 0 if payload.get("ok") else 1

    if args.command == "register-agent":
        try:
            payload = register_agent_via_source_registry(
                runtime_root_arg=args.runtime_root,
                goal_id=args.goal_id,
                agent_ids=args.agent_id,
                execute=bool(args.execute),
                require_new=bool(args.require_new),
            )
        except Exception as exc:
            payload = {
                "ok": False,
                "dry_run": not bool(args.execute),
                "execute": bool(args.execute),
                "goal_id": args.goal_id,
                "changed": False,
                "written": False,
                "error": str(exc),
                **lock_timeout_error_fields(exc),
            }
        print_payload(payload, args.format, render_register_agent_markdown)
        return 0 if payload.get("ok") else 1

    thread_resolution_result = handle_registry_thread_resolution_command(
        args,
        registry_path=registry_path,
        print_payload=print_payload,
    )
    if thread_resolution_result is not None:
        return thread_resolution_result

    if args.command in {"bind-agent-thread", "unbind-agent-thread"}:
        try:
            global_path = explicit_global_registry(args.runtime_root)
            global_goal = _registry_goal(global_path, args.goal_id)
            source_registry = global_goal.get("source_registry")
            if not source_registry:
                raise ValueError(f"{args.goal_id}: global registry entry has no source_registry")
            source_path = Path(str(source_registry)).expanduser()
            source_path_resolved = source_path.resolve()
            goal_repo = global_goal.get("repo")
            if not goal_repo:
                raise ValueError(f"{args.goal_id}: global registry entry has no repo")
            repo_path = Path(str(goal_repo)).expanduser().resolve()
            if source_path_resolved.name != "registry.json" or source_path_resolved.parent.name != ".loopx":
                raise ValueError("source_registry must point to a project .loopx/registry.json")
            try:
                source_path_resolved.relative_to(repo_path)
            except ValueError as exc:
                raise ValueError("source_registry is outside the goal repository") from exc
            source_path = source_path_resolved
            if args.execute:
                writability = probe_registry_write_path(global_path, create_parent=True)
                if not writability.get("ok"):
                    payload = {
                        "ok": False,
                        "dry_run": False,
                        "execute": True,
                        "goal_id": args.goal_id,
                        "changed": False,
                        "written": False,
                        "error_kind": "global_registry_write_denied",
                        "global_registry_writability": writability,
                        "recommended_action": writability.get("recommended_action"),
                    }
                    print_payload(payload, args.format, render_register_agent_markdown)
                    return 1
            binding_operation = (
                bind_thread_agent_in_registry
                if args.command == "bind-agent-thread"
                else unbind_thread_agent_in_registry
            )
            payload = binding_operation(
                registry_path=source_path,
                goal_id=args.goal_id,
                host_surface=args.host_surface,
                thread_id=args.thread_id,
                agent_id=args.agent_id,
                execute=bool(args.execute),
            )
            payload["global_registry"] = str(global_path)
            payload["source_registry"] = str(source_path)
            if args.execute and payload.get("ok"):
                payload["global_sync"] = sync_project_registry_to_global(
                    registry_path=source_path,
                    runtime_root_override=str(global_path.parent),
                    goal_id=args.goal_id,
                    dry_run=False,
                )
                source_binding = resolve_thread_agent_binding(
                    _registry_goal(source_path, args.goal_id),
                    host_surface=args.host_surface,
                    thread_id=args.thread_id,
                )
                global_binding = resolve_thread_agent_binding(
                    _registry_goal(global_path, args.goal_id),
                    host_surface=args.host_surface,
                    thread_id=args.thread_id,
                )
                if args.command == "bind-agent-thread":
                    readback_verified = all(
                        binding.get("status") == "bound"
                        and binding.get("agent_id") == args.agent_id
                        for binding in (source_binding, global_binding)
                    )
                else:
                    source_agents = _goal_registered_agents(
                        _registry_goal(source_path, args.goal_id)
                    )
                    global_agents = _goal_registered_agents(
                        _registry_goal(global_path, args.goal_id)
                    )
                    readback_verified = (
                        all(
                            binding.get("status") == "missing"
                            for binding in (source_binding, global_binding)
                        )
                        and args.agent_id in source_agents
                        and args.agent_id in global_agents
                    )
                payload["registration_readback"] = {"verified": readback_verified}
                payload["ok"] = bool(payload["global_sync"].get("ok")) and readback_verified
                if not payload["ok"]:
                    payload["error_kind"] = "thread_agent_binding_readback_failed"
            else:
                payload["global_sync"] = {"enabled": bool(args.execute), "wrote": False}
        except Exception as exc:
            payload = {
                "ok": False,
                "dry_run": not bool(args.execute),
                "execute": bool(args.execute),
                "goal_id": args.goal_id,
                "changed": False,
                "written": False,
                "error": str(exc),
                **lock_timeout_error_fields(exc),
            }
        print_payload(payload, args.format, render_register_agent_markdown)
        return 0 if payload.get("ok") else 1

    lifecycle_result = handle_registry_lifecycle_command(
        args,
        registry_path=registry_path,
        print_payload=print_payload,
    )
    if lifecycle_result is not None:
        return lifecycle_result

    return handle_registry_authority_command(
        args,
        registry_path=registry_path,
        print_payload=print_payload,
    )
