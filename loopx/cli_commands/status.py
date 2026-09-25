from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..contract import check_contract, render_contract_markdown
from ..control_plane.runtime.status_projection_cache import (
    load_status_projection_cache,
    resolve_status_projection_cache_runtime_root,
    write_status_projection_cache,
)
from ..control_plane.post_writeback_composition_retry import (
    collect_pending_composition_retry_projection,
)
from ..control_plane.status.agent_lane_projection import (
    compact_agent_lane_status_payload_for_display,
)
from ..control_plane.todos.contract import normalize_todo_claimed_by
from ..control_plane.todos.quota_summary import (
    compact_agent_lane_todos_for_status_display,
)
from ..control_plane.todos.todo_index import (
    compact_agent_lane_todo_index_for_status_display,
)
from ..diagnose import collect_diagnosis, render_diagnosis_markdown
from ..control_plane.handoff.project_agent_context import build_project_agent_handoff
from ..control_plane.handoff.handoff_fragments import render_handoff_transport
from ..handoff_budget import build_handoff_interface_budget
from ..presentation.renderers.status_markdown import render_status_markdown
from ..quota import build_quota_should_run
from ..review_packet import (
    build_review_packet,
    render_review_packet_markdown,
)
from ..status import AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK, collect_status
from .status_registration import register_status_commands as register_status_commands

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]


def _scan_roots(args: argparse.Namespace) -> list[Path]:
    scan_roots = [Path(item).expanduser() for item in args.scan_path]
    return scan_roots or [Path(args.scan_root).expanduser()]


def _status_collection_limit_for_agent_lane(
    *, requested_limit: int, agent_id: str | None
) -> int:
    safe_limit = max(0, int(requested_limit or 0))
    if str(agent_id or "").strip():
        return max(safe_limit, AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK)
    return safe_limit


def _trim_run_history_for_status_display(
    payload: dict[str, object],
    *,
    display_limit: int,
    collection_limit: int,
) -> None:
    if collection_limit <= display_limit:
        return
    run_history = payload.get("run_history")
    if not isinstance(run_history, dict):
        return
    safe_display_limit = max(0, display_limit)
    recent_runs = run_history.get("recent_runs")
    trimmed = False
    if isinstance(recent_runs, list) and len(recent_runs) > safe_display_limit:
        run_history["recent_runs"] = recent_runs[:safe_display_limit]
        trimmed = True
    goals = run_history.get("goals")
    if isinstance(goals, list):
        for goal in goals:
            if not isinstance(goal, dict):
                continue
            latest_runs = goal.get("latest_runs")
            if isinstance(latest_runs, list) and len(latest_runs) > safe_display_limit:
                goal["latest_runs"] = latest_runs[:safe_display_limit]
                trimmed = True
    if trimmed:
        payload["agent_lane_projection_lookback"] = {
            "schema_version": "agent_lane_projection_lookback_v0",
            "collection_limit": collection_limit,
            "display_limit": safe_display_limit,
            "reason": (
                "status --agent-id collected quota-equivalent run history for "
                "agent-lane frontier projection, then restored the requested "
                "status display limit"
            ),
        }


def review_packet_handoff_only_payload(payload: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": bool(payload.get("ok")),
        "goal_id": payload.get("goal_id"),
        "handoff_only": True,
    }
    if not payload.get("ok"):
        result["error"] = payload.get("error")
        return result
    handoff_text = str(payload.get("project_agent_handoff") or "")
    raw_contract = payload.get("handoff_delivery_contract")
    agent_contract = raw_contract
    if isinstance(raw_contract, dict):
        agent_contract = {
            "mode": raw_contract.get("mode"),
            "instruction": raw_contract.get("instruction"),
            "minimum_scale": raw_contract.get("minimum_scale"),
            "must_include": raw_contract.get("must_include"),
            "if_blocked": raw_contract.get("if_blocked"),
        }
    handoff_budget = payload.get("handoff_interface_budget")
    if not isinstance(handoff_budget, dict):
        handoff_budget = build_handoff_interface_budget(handoff_text)
    result.update(
        {
            "kind": payload.get("kind"),
            "status": payload.get("status"),
            "waiting_on": payload.get("waiting_on"),
            "project_agent_command": payload.get("project_agent_command"),
            "project_agent_handoff": handoff_text,
            "handoff_text": handoff_text,
            "project_agent_required_reads": payload.get("project_agent_required_reads")
            or [],
            "operator_gate_approved_handoff": payload.get(
                "operator_gate_approved_handoff"
            ),
            "connected_delivery_handoff": payload.get("connected_delivery_handoff"),
            "handoff_delivery_contract": agent_contract,
            "handoff_interface_budget": handoff_budget,
            "line_count": handoff_budget.get("line_count"),
            "char_count": handoff_budget.get("char_count"),
            "within_budget": handoff_budget.get("within_budget"),
        }
    )
    fragment_texts = payload.get("project_agent_handoff_fragments")
    if isinstance(fragment_texts, list) and fragment_texts:
        result["project_agent_handoff_fragments"] = fragment_texts
        result["handoff_fragment_manifest"] = payload.get(
            "handoff_fragment_manifest"
        )
    return result


def handle_check_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    allow_missing_registry: bool,
    print_payload: PrintPayload,
) -> int:
    try:
        payload = check_contract(
            registry_path=registry_path,
            runtime_root_override=runtime_root_arg,
            scan_roots=_scan_roots(args),
            limit=max(0, args.limit),
            allow_missing_registry=allow_missing_registry,
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "registry": str(registry_path),
            "runtime_root": runtime_root_arg,
            "scan_roots": args.scan_path or [args.scan_root],
            "summary": {"errors": 1, "warnings": 0, "checks": 0},
            "errors": [str(exc)],
            "warnings": [],
            "checks": [],
        }
    print_payload(payload, args.format, render_contract_markdown)
    return 0 if payload.get("ok") else 1


def handle_status_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int:
    try:
        scan_roots = _scan_roots(args)
        display_limit = max(0, args.limit)
        collection_limit = _status_collection_limit_for_agent_lane(
            requested_limit=display_limit,
            agent_id=args.agent_id,
        )
        runtime_root = resolve_status_projection_cache_runtime_root(
            registry_path=registry_path,
            runtime_root_override=runtime_root_arg,
        )
        payload = None
        cache_metadata = None
        if args.use_projection_cache:
            payload, cache_metadata = load_status_projection_cache(
                registry_path=registry_path,
                runtime_root=runtime_root,
                scan_roots=scan_roots,
                limit=collection_limit,
                include_task_graph=args.include_task_graph,
                goal_id=args.goal_id,
                max_age_seconds=args.projection_cache_ttl_seconds,
                available_capabilities=args.available_capabilities,
                agent_lane_id=args.agent_id,
            )
        if payload is None:
            payload = collect_status(
                registry_path=registry_path,
                runtime_root_override=runtime_root_arg,
                scan_roots=scan_roots,
                limit=collection_limit,
                include_task_graph=args.include_task_graph,
                goal_id=args.goal_id,
                available_capabilities=args.available_capabilities,
                agent_lane_id=args.agent_id,
            )
            if args.write_projection_cache:
                cache_metadata = write_status_projection_cache(
                    registry_path=registry_path,
                    runtime_root=runtime_root,
                    scan_roots=scan_roots,
                    limit=collection_limit,
                    include_task_graph=args.include_task_graph,
                    goal_id=args.goal_id,
                    payload=payload,
                    max_age_seconds=args.projection_cache_ttl_seconds,
                    available_capabilities=args.available_capabilities,
                    agent_lane_id=args.agent_id,
                )
                payload["projection_cache"] = cache_metadata
            elif cache_metadata:
                payload["projection_cache"] = cache_metadata
        if args.agent_id:
            attach_agent_lane_next_actions(payload, agent_id=args.agent_id)
            _trim_run_history_for_status_display(
                payload,
                display_limit=display_limit,
                collection_limit=collection_limit,
            )
            compact_agent_lane_todos_for_status_display(payload)
            compact_agent_lane_status_payload_for_display(
                payload,
                agent_id=args.agent_id,
            )
            compact_agent_lane_todo_index_for_status_display(payload)
        pending_composition_retries = collect_pending_composition_retry_projection(
            runtime_root, args.goal_id, agent_id=args.agent_id
        )
        if pending_composition_retries is not None:
            payload["pending_composition_retry_receipts"] = pending_composition_retries
    except Exception as exc:
        payload = {
            "ok": False,
            "registry": str(registry_path),
            "runtime_root": runtime_root_arg,
            "error": str(exc),
            "attention_queue": {
                "available": False,
                "item_count": 1,
                "needs_user_or_controller": 0,
                "needs_codex": 1,
                "watching_external_evidence": 0,
                "items": [
                    {
                        "goal_id": "loopx-status",
                        "status": "status_collection_failed",
                        "waiting_on": "codex",
                        "severity": "high",
                        "recommended_action": str(exc),
                        "source": "status",
                    }
                ],
            },
        }
    print_payload(payload, output_format(args), render_status_markdown)
    return 0 if payload.get("ok") else 1


def _compact_member_text(value: Any, *, limit: int = 180) -> str | None:
    if isinstance(value, list):
        compact = "; ".join(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, str):
        compact = value
    else:
        return None
    compact = " ".join(compact.split())
    if not compact or any(char in compact for char in "<>"):
        return None
    if len(compact) > limit:
        compact = compact[: limit - 1].rstrip() + "…"
    return compact


def _agent_profile_for(coordination: dict[str, Any], agent_id: str) -> dict[str, Any]:
    profiles = coordination.get("agent_profiles")
    if not isinstance(profiles, dict):
        return {}
    profile = profiles.get(agent_id)
    return profile if isinstance(profile, dict) else {}


def _profile_scope_summary(profile: dict[str, Any]) -> str | None:
    for key in (
        "scope_summary",
        "default_scope",
        "scope",
        "scope_summaries",
        "default_scopes",
        "scopes",
    ):
        summary = _compact_member_text(profile.get(key))
        if summary:
            return summary
    return None


def _current_claims_for_agent(item: dict[str, Any], *, agent_id: str) -> list[str]:
    claims: list[str] = []
    todo_sources: list[Any] = [item.get("agent_todos")]
    project_asset = item.get("project_asset")
    if isinstance(project_asset, dict):
        todo_sources.append(project_asset.get("agent_todos"))
    for todos in todo_sources:
        if not isinstance(todos, dict):
            continue
        raw_items = todos.get("items") if isinstance(todos.get("items"), list) else []
        if not raw_items and isinstance(todos.get("first_open_items"), list):
            raw_items = todos.get("first_open_items") or []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict) or raw_item.get("done"):
                continue
            if normalize_todo_claimed_by(raw_item.get("claimed_by")) != agent_id:
                continue
            todo_id = str(raw_item.get("todo_id") or "").strip()
            if todo_id and todo_id not in claims:
                claims.append(todo_id)
    return claims


def _selected_claim_for_agent(guard: dict[str, Any], *, agent_id: str) -> str | None:
    next_action = guard.get("agent_lane_next_action")
    if not isinstance(next_action, dict):
        return None
    todo_id = str(next_action.get("todo_id") or "").strip()
    if not todo_id:
        return None
    lane_agent = normalize_todo_claimed_by(next_action.get("agent_id"))
    if lane_agent and lane_agent != agent_id:
        return None
    if next_action.get("claim_required_before_work") is True:
        return None
    claimed_by = normalize_todo_claimed_by(next_action.get("claimed_by"))
    selected_by = str(next_action.get("selected_by") or "").strip()
    if claimed_by == agent_id or selected_by == "current_agent_claimed_todo":
        return todo_id
    return None


def _current_claims_with_selected_lane(
    item: dict[str, Any],
    *,
    guard: dict[str, Any],
    agent_id: str,
) -> list[str]:
    claims = _current_claims_for_agent(item, agent_id=agent_id)
    selected_claim = _selected_claim_for_agent(guard, agent_id=agent_id)
    if not selected_claim:
        return claims
    claims = [claim for claim in claims if claim != selected_claim]
    return [selected_claim, *claims]


def _build_agent_member_projection(
    item: dict[str, Any],
    *,
    guard: dict[str, Any],
    agent_id: str,
) -> dict[str, Any] | None:
    identity = guard.get("agent_identity")
    if not isinstance(identity, dict):
        return None
    coordination = (
        item.get("coordination") if isinstance(item.get("coordination"), dict) else {}
    )
    profile = _agent_profile_for(coordination, agent_id)
    role = _compact_member_text(profile.get("profile_role"), limit=80)
    claims = _current_claims_with_selected_lane(item, guard=guard, agent_id=agent_id)
    member: dict[str, Any] = {
        "schema_version": "agent_member_v1",
        "agent_id": agent_id,
        "agent_model": "peer_v1",
        "profile_source": "registry.coordination.agent_profiles"
        if profile
        else "quota.agent_identity",
        "authority_source": "registry+quota_should_run+todo_projection",
        "current_claims": claims[:10],
        "current_claim_count": len(claims),
        "lease_projection": {
            "schema_version": "agent_lease_projection_v0",
            "source": "todo.claimed_by",
            "soft_claim_default": True,
            "hard_lease_available": True,
            "hard_lease_mode": "explicit_cli_opt_in",
            "hard_lease_enforced_by_quota": False,
        },
    }
    if role:
        member["profile_role"] = role
        member["profile_role_is_advisory"] = True
    scope_summary = _profile_scope_summary(profile)
    if scope_summary:
        member["scope_summary"] = scope_summary
    vision_requirement = _compact_member_text(
        profile.get("vision_requirement"),
        limit=16,
    )
    if vision_requirement:
        member["vision_requirement"] = vision_requirement
    member["handoff_assignment_status"] = "task_policy_selected"
    return member


def _agent_lane_text(next_action: dict[str, Any]) -> str | None:
    text = str(next_action.get("text") or next_action.get("title") or "").strip()
    return text or None


def _guard_allows_agent_lane_next_action(guard: dict[str, object]) -> bool:
    interaction = guard.get("interaction_contract")
    if not isinstance(interaction, dict):
        return False
    user_channel = interaction.get("user_channel")
    agent_channel = interaction.get("agent_channel")
    user_action_required = (
        bool(user_channel.get("action_required"))
        if isinstance(user_channel, dict)
        else False
    )
    agent_must_attempt = (
        bool(agent_channel.get("must_attempt"))
        if isinstance(agent_channel, dict)
        else False
    )
    return agent_must_attempt and not user_action_required


def _build_agent_interaction_summary(
    guard: dict[str, object],
    *,
    agent_id: str,
) -> dict[str, object] | None:
    interaction = guard.get("interaction_contract")
    if not isinstance(interaction, dict):
        return None
    user_channel = interaction.get("user_channel")
    agent_channel = interaction.get("agent_channel")
    cli_channel = interaction.get("cli_channel")
    if not isinstance(user_channel, dict) or not isinstance(agent_channel, dict):
        return None
    user_todo_summary = (
        guard.get("user_todo_summary")
        if isinstance(guard.get("user_todo_summary"), dict)
        else {}
    )
    user_open_count = user_todo_summary.get("open_count")
    if user_open_count is None:
        user_open_count = guard.get("open_count")
    summary: dict[str, object] = {
        "schema_version": "agent_interaction_summary_v0",
        "agent_id": agent_id,
        "mode": interaction.get("mode"),
        "user_action_required": bool(user_channel.get("action_required")),
        "user_open_count": user_open_count,
        "user_notify": user_channel.get("notify"),
        "agent_must_attempt": bool(agent_channel.get("must_attempt")),
        "delivery_allowed": agent_channel.get("delivery_allowed"),
        "quiet_noop_allowed": agent_channel.get("quiet_noop_allowed"),
        "primary_action": agent_channel.get("primary_action"),
    }
    if isinstance(cli_channel, dict):
        summary["spend_after_validation"] = cli_channel.get("spend_after_validation")
        summary["spend_policy"] = (
            "bind eligible Todo before delivery/spend"
            if cli_channel.get("selection_required") is True
            else cli_channel.get("spend_policy")
        )
    return summary


def _sync_status_item_next_action_from_agent_lane(
    item: dict[str, object],
    *,
    next_action: dict[str, Any],
    guard: dict[str, object],
) -> None:
    if item.get("status") not in {"connected_without_run", "active_state_agent_todo"}:
        return
    if not _guard_allows_agent_lane_next_action(guard):
        return
    text = _agent_lane_text(next_action)
    if not text:
        return
    item["recommended_action"] = text
    project_asset = item.get("project_asset")
    if isinstance(project_asset, dict):
        project_asset["next_action"] = text
    goal_channel = item.get("goal_channel_projection")
    if isinstance(goal_channel, dict):
        goal_channel["next_action"] = text


def _sync_next_action_projection_warning_from_guard(
    item: dict[str, object],
    *,
    guard: dict[str, object],
) -> None:
    warning = guard.get("next_action_projection_warning")
    project_asset = item.get("project_asset")
    targets = (item, project_asset)
    for target in targets:
        if not isinstance(target, dict):
            continue
        if isinstance(warning, dict):
            target["next_action_projection_warning"] = warning
        else:
            target.pop("next_action_projection_warning", None)


def _mirror_agent_status_projections(
    item: dict[str, object],
    project_asset: object,
    *,
    projections: dict[str, object],
) -> set[str]:
    attached: set[str] = set()
    for field, projection in projections.items():
        if not isinstance(projection, dict):
            continue
        item[field] = projection
        if isinstance(project_asset, dict):
            project_asset[field] = projection
        attached.add(field)
    return attached


def _sync_agent_replan_obligation_from_guard(
    item: dict[str, object],
    project_asset: object,
    *,
    guard: dict[str, object],
    agent_id: str,
) -> None:
    """Keep agent-scoped status guidance identical to the quota decision."""

    targets = (item, project_asset)
    if not any(
        isinstance(target, dict)
        and (
            "autonomous_replan_obligation" in target
            or (
                isinstance(target.get("autonomous_replan_obligations_by_agent"), dict)
                and agent_id in target["autonomous_replan_obligations_by_agent"]
            )
        )
        for target in targets
    ):
        return
    obligation = guard.get("autonomous_replan_obligation")
    for target in targets:
        if not isinstance(target, dict):
            continue
        obligations_by_agent = target.get("autonomous_replan_obligations_by_agent")
        if isinstance(obligations_by_agent, dict):
            obligations_by_agent = dict(obligations_by_agent)
            if isinstance(obligation, dict):
                obligations_by_agent[agent_id] = obligation
            else:
                obligations_by_agent.pop(agent_id, None)
            if obligations_by_agent:
                target["autonomous_replan_obligations_by_agent"] = obligations_by_agent
            else:
                target.pop("autonomous_replan_obligations_by_agent", None)
        if isinstance(obligation, dict):
            target["autonomous_replan_obligation"] = obligation
        else:
            target.pop("autonomous_replan_obligation", None)


def _agent_reward_memory_projection(
    guard: dict[str, object],
    *,
    agent_id: str,
) -> dict[str, object] | None:
    boundary = guard.get("goal_boundary")
    if not isinstance(boundary, dict):
        return None
    capabilities = boundary.get("capabilities")
    if not isinstance(capabilities, dict):
        return None
    reward_memory = capabilities.get("reward_memory")
    if not isinstance(reward_memory, dict):
        return None
    compact: dict[str, object] = {
        "schema_version": "agent_reward_memory_projection_v1",
        "agent_id": agent_id,
    }
    for key in (
        "enabled",
        "experimental",
        "configured_for_agent",
        "experiment_status",
        "experiment_available",
        "reason_code",
        "repair",
        "config_schema_version",
        "automatic_ingest",
        "automatic_recall",
        "fail_open",
        "automation_projection_source",
        "automation_intent",
        "host_coverage",
        "isolation_mode",
        "enablement_receipt_status",
        "actor_binding_verified",
        "writability_verified",
        "exact_readback_verified",
    ):
        if key in reward_memory:
            compact[key] = reward_memory[key]
    route = reward_memory.get("config_runtime_route")
    if isinstance(route, dict):
        compact["config_runtime_route"] = dict(route)
    return compact


def attach_agent_lane_next_actions(
    payload: dict[str, object], *, agent_id: str
) -> dict[str, object]:
    safe_agent_id = str(agent_id or "").strip()
    if not safe_agent_id:
        return payload
    queue = payload.get("attention_queue")
    if not isinstance(queue, dict):
        return payload
    items = queue.get("items")
    if not isinstance(items, list):
        return payload
    projection_counts = dict.fromkeys(
        (
            "agent_lane_next_action",
            "agent_scope_frontier",
            "agent_lane_frontier_hint",
            "goal_frontier_projection",
            "agent_member",
            "agent_interaction_summary",
            "agent_reward_memory",
        ),
        0,
    )
    current_agent_next_action: dict[str, Any] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        goal_id = str(item.get("goal_id") or "").strip()
        if not goal_id:
            continue
        try:
            guard = build_quota_should_run(
                payload,
                goal_id=goal_id,
                agent_id=safe_agent_id,
            )
        except Exception:
            continue
        next_action = guard.get("agent_lane_next_action")
        project_asset = item.get("project_asset")
        agent_member = _build_agent_member_projection(
            item,
            guard=guard,
            agent_id=safe_agent_id,
        )
        interaction_summary = _build_agent_interaction_summary(
            guard,
            agent_id=safe_agent_id,
        )
        _sync_agent_replan_obligation_from_guard(
            item,
            project_asset,
            guard=guard,
            agent_id=safe_agent_id,
        )
        reward_memory_projection = _agent_reward_memory_projection(
            guard,
            agent_id=safe_agent_id,
        )
        attached_fields = _mirror_agent_status_projections(
            item,
            project_asset,
            projections={
                "agent_lane_next_action": next_action,
                "agent_scope_frontier": guard.get("agent_scope_frontier"),
                "agent_lane_frontier_hint": guard.get("agent_lane_frontier_hint"),
                "goal_frontier_projection": guard.get("goal_frontier_projection"),
                "agent_member": agent_member,
                "agent_interaction_summary": interaction_summary,
                "agent_reward_memory": reward_memory_projection,
            },
        )
        for field in attached_fields:
            projection_counts[field] += 1
        if isinstance(next_action, dict):
            if current_agent_next_action is None:
                current_agent_next_action = next_action
            _sync_next_action_projection_warning_from_guard(item, guard=guard)
            _sync_status_item_next_action_from_agent_lane(
                item,
                next_action=next_action,
                guard=guard,
            )
        latest_action = guard.get("latest_run_recommended_action")
        for target in (item, project_asset):
            if not isinstance(target, dict):
                continue
            existing_recommendation = target.get("agent_lane_recommendation")
            had_latest_action = bool(
                target.get("latest_run_recommended_action")
                or target.get("latest_run_recommended_action_source")
            )
            for field in (
                "agent_lane_recommendation",
                "latest_run_recommended_action",
                "latest_run_recommended_action_source",
            ):
                target.pop(field, None)
            if isinstance(existing_recommendation, dict):
                existing_agent_id = normalize_todo_claimed_by(
                    existing_recommendation.get("agent_id")
                )
                if existing_agent_id == safe_agent_id:
                    target["agent_lane_recommendation"] = existing_recommendation
                elif latest_action:
                    target["agent_lane_recommendation"] = {
                        "schema_version": existing_recommendation.get("schema_version")
                        or "agent_lane_recommendation_v0",
                        "progress_scope": "agent_lane",
                        "agent_id": safe_agent_id,
                        "recommended_action": latest_action,
                    }
            if had_latest_action and latest_action:
                target["latest_run_recommended_action"] = latest_action
                target["latest_run_recommended_action_source"] = (
                    "agent_lane_recommendation"
                )
    if any(projection_counts.values()):
        projection: dict[str, object] = {
            "schema_version": "agent_lane_next_action_projection_v0",
            "agent_id": safe_agent_id,
            "attached_count": projection_counts["agent_lane_next_action"],
            "frontier_attached_count": projection_counts["agent_scope_frontier"],
            "frontier_hint_attached_count": projection_counts[
                "agent_lane_frontier_hint"
            ],
            "goal_frontier_attached_count": projection_counts[
                "goal_frontier_projection"
            ],
            "agent_member_attached_count": projection_counts["agent_member"],
            "agent_interaction_attached_count": projection_counts[
                "agent_interaction_summary"
            ],
            "reward_memory_attached_count": projection_counts["agent_reward_memory"],
            "preserves_goal_next_action": True,
        }
        if isinstance(current_agent_next_action, dict):
            current_action_text = _compact_member_text(
                _agent_lane_text(current_agent_next_action),
                limit=240,
            )
            current_todo_id = str(
                current_agent_next_action.get("todo_id") or ""
            ).strip()
            if current_todo_id:
                projection["current_agent_todo_id"] = current_todo_id
            if current_action_text:
                projection["current_agent_action"] = current_action_text
            selected_by = str(
                current_agent_next_action.get("selected_by") or ""
            ).strip()
            if selected_by:
                projection["selected_by"] = selected_by
            confidence = str(current_agent_next_action.get("confidence") or "").strip()
            if confidence:
                projection["confidence"] = confidence
        payload["agent_lane_next_action_projection"] = projection
    if projection_counts["agent_member"]:
        payload["agent_member_projection"] = {
            "schema_version": "agent_member_projection_v0",
            "agent_id": safe_agent_id,
            "attached_count": projection_counts["agent_member"],
            "source": "registry+quota_should_run+todo_projection",
            "projection_is_authoritative": False,
        }
    if projection_counts["agent_reward_memory"]:
        payload["agent_reward_memory_projection"] = {
            "schema_version": "agent_reward_memory_projection_summary_v1",
            "agent_id": safe_agent_id,
            "attached_count": projection_counts["agent_reward_memory"],
            "source": "quota.goal_boundary.capabilities.reward_memory",
        }
    return payload


def handle_diagnose_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int:
    try:
        payload = collect_diagnosis(
            registry_path=registry_path,
            runtime_root_override=runtime_root_arg,
            scan_roots=_scan_roots(args),
            limit=max(1, args.limit),
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            available_capabilities=args.available_capabilities,
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": "loopx_agent_diagnosis_packet_v0",
            "packet_kind": "agent_reasoning_evidence_packet",
            "agent_must_reason": True,
            "registry": str(registry_path),
            "runtime_root": runtime_root_arg,
            "error": str(exc),
            "selected": {
                "machine_signal": "diagnosis_collection_failed",
                "machine_signals_are_not_final_verdict": True,
                "recommended_action": (
                    "The agent should repair LoopX installation, registry path, or status collection "
                    "before making a delivery decision."
                ),
                "agent_reasoning_checklist": [
                    "Run or repair `loopx doctor`.",
                    "Verify the project registry path or connect the project.",
                    "Do not claim autonomous readiness until status and quota can be read.",
                ],
            },
        }
    print_payload(payload, output_format(args), render_diagnosis_markdown)
    return 0 if payload.get("ok") else 1


def handle_review_packet_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int:
    selected_format = output_format(args, "review_packet_format")
    try:
        status_payload = collect_status(
            registry_path=registry_path,
            runtime_root_override=runtime_root_arg,
            scan_roots=_scan_roots(args),
            limit=max(0, args.limit),
            include_task_graph=not args.handoff_only,
            goal_id=args.goal_id,
            available_capabilities=args.available_capabilities,
            agent_lane_id=args.agent_id,
        )
        if args.agent_id:
            attach_agent_lane_next_actions(status_payload, agent_id=args.agent_id)
        if args.handoff_only:
            payload = build_project_agent_handoff(
                status_payload, goal_id=args.goal_id, action_kind=args.action_kind,
            )
        else:
            payload = build_review_packet(
                status_payload, goal_id=args.goal_id, action_kind=args.action_kind,
                review_url=args.review_url,
            )
    except Exception as exc:
        payload = {
            "ok": False,
            "goal_id": args.goal_id,
            "error": str(exc),
        }
    if args.handoff_only:
        payload = review_packet_handoff_only_payload(payload)
    if args.handoff_only and selected_format != "json" and payload.get("ok"):
        fragment_texts = payload.get("project_agent_handoff_fragments")
        if not isinstance(fragment_texts, list):
            fragment_texts = []
        print(
            render_handoff_transport(
                str(payload.get("handoff_text") or ""), fragment_texts
            )
        )
    else:
        print_payload(payload, selected_format, render_review_packet_markdown)
    return 0 if payload.get("ok") else 1
