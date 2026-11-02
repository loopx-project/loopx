from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..agents.capability_gate import missing_required_capabilities
from ..agents.runtime_model import peer_work_key, select_peer_for_work
from ..effect_runtime import effect_runtime_result
from ..todos.contract import (
    normalize_required_capabilities,
    normalize_required_write_scopes,
    normalize_todo_claimed_by,
    normalize_todo_excluded_agents,
    normalize_todo_task_domain,
    normalize_todo_task_repository,
)
from ..todos.todo_semantics import todo_item_is_actionable_open
from ..work_items.primary_action import protocol_action_text
from .projection_repair import write_scope_allowed


SUBAGENT_SPAWN_CAPABILITY = "subagent_spawn"
SUBAGENT_CONTEXT_FORK_CAPABILITY = "subagent_context_fork"
SUBAGENT_RESUME_CAPABILITY = "subagent_resume"
READ_ONLY_ACTION_KINDS = frozenset(
    {
        "analyze",
        "compare",
        "inspect",
        "review",
        "test",
        "validate",
    }
)


@dataclass(frozen=True)
class _NormalizedAdmissionLane:
    item: dict[str, Any]
    todo_id: str | None
    task_domain: str | None
    task_repository: str | None
    effective_repository: str | None
    repository_declared: bool
    required_write_scopes: tuple[str, ...]
    action_kind: str


@dataclass(frozen=True)
class _NormalizedAdmissionInput:
    lanes: tuple[_NormalizedAdmissionLane, ...]
    user_items: tuple[dict[str, Any], ...]
    allowed_domains: frozenset[str] | None
    available_capabilities: tuple[str, ...]
    goal_write_scope: tuple[str, ...]
    goal_repository: str | None


def build_adaptive_task_orchestration_contract(
    *,
    agent_id: str,
    agent_identity: dict[str, Any],
    goal_boundary: dict[str, Any],
    orchestration: dict[str, Any],
    raw_agent_todo_summary: dict[str, Any] | None,
    raw_user_todo_summary: dict[str, Any] | None,
    agent_todo_source_items: list[dict[str, Any]] | None,
    user_todo_source_items: list[dict[str, Any]] | None,
    available_capabilities: list[str],
    parent_goal_id: str | None,
    max_children: int,
) -> dict[str, Any] | None:
    admission = _normalize_admission_input(
        goal_boundary=goal_boundary,
        orchestration=orchestration,
        raw_agent_todo_summary=raw_agent_todo_summary,
        raw_user_todo_summary=raw_user_todo_summary,
        agent_todo_source_items=agent_todo_source_items,
        user_todo_source_items=user_todo_source_items,
        available_capabilities=available_capabilities,
    )
    global_candidates = [
        lane for lane in admission.lanes if _is_adaptive_candidate(lane.item)
    ]
    election_candidates = [
        lane
        for lane in global_candidates
        if todo_item_is_actionable_open(lane.item)
        and not _has_open_dependency(
            lane.item,
            user_items=admission.user_items,
        )
    ]
    if len(election_candidates) < 2:
        return None
    claimed_owners = {
        claimed_by
        for lane in election_candidates
        for claimed_by in [normalize_todo_claimed_by(lane.item.get("claimed_by"))]
        if claimed_by
    }
    assignment_key = peer_work_key(
        {
            "mode": "adaptive",
            "todo_ids": sorted(lane.todo_id or "" for lane in election_candidates),
        },
        fallback="adaptive_task_orchestration",
    )
    coordinator = (
        next(iter(claimed_owners))
        if len(claimed_owners) == 1
        else select_peer_for_work(
            agent_identity.get("registered_agents") or [agent_id],
            work_key=assignment_key,
        )
    )
    if coordinator != agent_id:
        return None
    candidates = [
        lane
        for lane in global_candidates
        if _candidate_owned_by_coordinator(lane.item, agent_id=agent_id)
    ]
    if len(candidates) < 2:
        return None

    admitted: list[_NormalizedAdmissionLane] = []
    blocked: list[dict[str, Any]] = []
    for lane in candidates:
        reason_codes = _block_reasons(
            lane,
            agent_id=agent_id,
            admission=admission,
        )
        if reason_codes:
            blocked.append(_blocked_lane(lane, reason_codes=reason_codes))
            continue
        admitted.append(lane)
    if len(admitted) < 2:
        return None

    primary = admitted[0]
    child_brief_defaults = _child_brief_defaults(
        parent_goal_id=parent_goal_id,
        available_capabilities=available_capabilities,
    )
    selected_children: list[dict[str, Any]] = []
    admitted_write_lanes = [primary]
    for item_index, lane in enumerate(admitted[1:]):
        if len(selected_children) >= max_children:
            blocked.extend(
                _blocked_lane(
                    deferred_lane,
                    reason_codes=["capacity_deferred"],
                )
                for deferred_lane in admitted[item_index + 1 :]
            )
            break
        conflict = next(
            (
                existing
                for existing in admitted_write_lanes
                if _write_scopes_conflict(existing, lane)
            ),
            None,
        )
        if conflict is not None:
            blocked.append(
                {
                    **_blocked_lane(
                        lane,
                        reason_codes=["write_scope_conflict"],
                    ),
                    "conflicts_with_todo_id": conflict.todo_id,
                }
            )
            continue
        selected_children.append(_eligible_child_lane(lane))
        admitted_write_lanes.append(lane)
    if not selected_children:
        return None
    return {
        "schema_version": "task_orchestration_contract_v2",
        "mode": "adaptive",
        "coordinator_agent_id": coordinator,
        "assignment_key": assignment_key,
        "activation_required": False,
        "activation_allowed": True,
        "strategy_owner": "task_coordinator",
        "max_children": max_children,
        "primary_todo_id": primary.todo_id,
        "child_brief_defaults": child_brief_defaults,
        "eligible_child_lanes": selected_children,
        "blocked_lanes": blocked,
        "writeback_owner": "task_coordinator",
        "coordinator_obligation": (
            "choose whether admitted child lanes shorten the critical path; "
            "review returned evidence, then write accepted state/todos and spend once"
        ),
    }


def _summary_items(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(summary, dict) or not isinstance(summary.get("items"), list):
        return []
    return [item for item in summary["items"] if isinstance(item, dict)]


def _authoritative_items(
    source_items: list[dict[str, Any]] | None,
    summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if source_items is not None:
        return [item for item in source_items if isinstance(item, dict)]
    return _summary_items(summary)


def _normalize_allowed_domains(orchestration: dict[str, Any]) -> frozenset[str] | None:
    raw_domains = orchestration.get("allowed_domains")
    if not isinstance(raw_domains, list) or not raw_domains:
        return None
    return frozenset(
        domain
        for value in raw_domains
        for domain in [normalize_todo_task_domain(value)]
        if domain
    )


def _normalize_admission_lane(
    item: dict[str, Any],
    *,
    goal_repository: str | None,
) -> _NormalizedAdmissionLane:
    task_repository = normalize_todo_task_repository(item.get("task_repository"))
    return _NormalizedAdmissionLane(
        item=item,
        todo_id=str(item.get("todo_id") or "").strip() or None,
        task_domain=normalize_todo_task_domain(item.get("task_domain")),
        task_repository=task_repository,
        effective_repository=task_repository or goal_repository,
        repository_declared=bool(item.get("task_repository")),
        required_write_scopes=tuple(
            normalize_required_write_scopes(item.get("required_write_scopes"))
        ),
        action_kind=str(item.get("action_kind") or "").strip().lower(),
    )


def _normalize_admission_input(
    *,
    goal_boundary: dict[str, Any],
    orchestration: dict[str, Any],
    raw_agent_todo_summary: dict[str, Any] | None,
    raw_user_todo_summary: dict[str, Any] | None,
    agent_todo_source_items: list[dict[str, Any]] | None,
    user_todo_source_items: list[dict[str, Any]] | None,
    available_capabilities: list[str],
) -> _NormalizedAdmissionInput:
    goal_repository = normalize_todo_task_repository(
        goal_boundary.get("task_repository")
    )
    return _NormalizedAdmissionInput(
        lanes=tuple(
            _normalize_admission_lane(item, goal_repository=goal_repository)
            for item in _authoritative_items(
                agent_todo_source_items,
                raw_agent_todo_summary,
            )
        ),
        user_items=tuple(
            _authoritative_items(
                user_todo_source_items,
                raw_user_todo_summary,
            )
        ),
        allowed_domains=_normalize_allowed_domains(orchestration),
        available_capabilities=tuple(available_capabilities),
        goal_write_scope=tuple(
            normalize_required_write_scopes(goal_boundary.get("write_scope"))
        ),
        goal_repository=goal_repository,
    )


def _candidate_owned_by_coordinator(
    item: dict[str, Any],
    *,
    agent_id: str,
) -> bool:
    if item.get("done") is True:
        return False
    if str(item.get("task_class") or "") != "advancement_task":
        return False
    claimed_by = normalize_todo_claimed_by(item.get("claimed_by"))
    return not claimed_by or claimed_by == agent_id


def _is_adaptive_candidate(item: dict[str, Any]) -> bool:
    return bool(
        item.get("done") is not True
        and str(item.get("task_class") or "") == "advancement_task"
    )


def _block_reasons(
    lane: _NormalizedAdmissionLane,
    *,
    agent_id: str,
    admission: _NormalizedAdmissionInput,
) -> list[str]:
    item = lane.item
    reasons: list[str] = []
    if agent_id in normalize_todo_excluded_agents(item.get("excluded_agents")):
        reasons.append("coordinator_excluded")
    if not todo_item_is_actionable_open(item) or _has_open_dependency(
        item,
        user_items=admission.user_items,
    ):
        reasons.append("dependency_not_ready")
    if (
        admission.allowed_domains is not None
        and lane.task_domain not in admission.allowed_domains
    ):
        reasons.append("task_domain_not_allowed")
    if missing_required_capabilities(
        item,
        available_capabilities=list(admission.available_capabilities),
    ):
        reasons.append("capability_unavailable")
    if lane.repository_declared:
        if not lane.task_repository:
            reasons.append("task_repository_invalid")
        elif (
            not admission.goal_repository
            or lane.task_repository != admission.goal_repository
        ):
            reasons.append("task_repository_not_allowed")
    if (
        _action_requires_write_scope(lane.action_kind)
        and not lane.required_write_scopes
    ):
        reasons.append("write_scope_missing")
    if lane.required_write_scopes and (
        not admission.goal_write_scope
        or any(
            not write_scope_allowed(scope, list(admission.goal_write_scope))
            for scope in lane.required_write_scopes
        )
    ):
        reasons.append("write_scope_not_allowed")
    return reasons


def _action_requires_write_scope(action_kind: str) -> bool:
    return not any(
        action_kind == prefix or action_kind.startswith(f"{prefix}_")
        for prefix in READ_ONLY_ACTION_KINDS
    )


def _has_open_dependency(
    item: dict[str, Any],
    *,
    user_items: tuple[dict[str, Any], ...],
) -> bool:
    todo_id = str(item.get("todo_id") or "")
    return any(
        str(user_item.get("unblocks_todo_id") or "") == todo_id
        and user_item.get("done") is not True
        and str(user_item.get("status") or "open").lower() not in {"done", "deferred"}
        for user_item in user_items
    )


def _blocked_lane(
    lane: _NormalizedAdmissionLane,
    *,
    reason_codes: list[str],
) -> dict[str, Any]:
    return {
        "todo_id": lane.todo_id,
        "task_domain": lane.task_domain,
        "reason_codes": reason_codes,
    }


def _child_brief_defaults(
    *,
    parent_goal_id: str | None,
    available_capabilities: list[str],
) -> dict[str, Any]:
    allowed_contexts = ["fresh"]
    if SUBAGENT_CONTEXT_FORK_CAPABILITY in available_capabilities:
        allowed_contexts.append("forked_snapshot")
    if SUBAGENT_RESUME_CAPABILITY in available_capabilities:
        allowed_contexts.append("resume")
    return {
        "schema_version": "subagent_control_plane_handoff_v0",
        "parent_goal_id": parent_goal_id,
        "authority_artifact": "quota_should_run.goal_boundary",
        "latest_state_ref": "quota_should_run.action_signature.source_hash",
        "quota_gate_snapshot": "admitted_by_task_orchestration_contract",
        "context_policy": {
            "selection_owner": "task_coordinator",
            "default": "fresh",
            "allowed": allowed_contexts,
        },
        "expected_output": "public_safe_evidence",
        "execution_policy": {
            "timeout": "bounded_by_host_turn",
            "cancel": "task_coordinator_or_host_timeout",
        },
        "child_guard_policy": "prevention_first_v0",
        "validation_policy": "child reports evidence; parent runs declared todo gate",
        "acceptance": [
            "report completed scope and evidence",
            "report validation result and residual risk",
            "do not write LoopX state or spend quota",
        ],
        "writeback_spend_contract": (
            "child reports evidence only; task coordinator accepts evidence, "
            "writes state, and spends once"
        ),
    }


def _eligible_child_lane(lane: _NormalizedAdmissionLane) -> dict[str, Any]:
    item = lane.item
    required_capabilities = normalize_required_capabilities(
        item.get("required_capabilities")
    )
    required_write_scopes = list(lane.required_write_scopes)
    validation_declared = bool(
        item.get("validation_command") is not None
        or item.get("validation_command_argv") is not None
        or item.get("completion_validation_required") is True
    )
    child_brief = {
        "todo_id": lane.todo_id,
        "objective": protocol_action_text(
            item.get("title") or item.get("text"),
            limit=180,
        ),
        "action_kind": str(item.get("action_kind") or "").strip() or None,
        "task_domain": lane.task_domain,
        "required_capabilities": required_capabilities,
        "task_repository": lane.task_repository,
        "required_write_scopes": required_write_scopes,
        "workspace_isolation": (
            "independent_git_worktree" if required_write_scopes else "not_required"
        ),
        "continuation_policy": (
            str(item.get("continuation_policy") or "").strip() or None
        ),
        "target_key": str(item.get("target_key") or "").strip() or None,
    }
    if validation_declared:
        child_brief["validation_declared"] = True
        child_brief["validation_label"] = (
            str(item.get("validation_label") or "").strip() or None
        )
    return {
        "todo_id": child_brief["todo_id"],
        "task_domain": lane.task_domain,
        "execution_kind": "ephemeral_child",
        "child_brief": child_brief,
    }


def _write_scopes_conflict(
    left: _NormalizedAdmissionLane,
    right: _NormalizedAdmissionLane,
) -> bool:
    """Consume the scheduling overlap decision owned by the canonical lease module."""

    if not left.required_write_scopes or not right.required_write_scopes:
        return False
    if left.effective_repository != right.effective_repository:
        return False
    decision = effect_runtime_result(
        "quota.task_orchestration.write_scope_overlap",
        {
            "left": list(left.required_write_scopes),
            "right": list(right.required_write_scopes),
        },
    )
    if not isinstance(decision, dict) or not isinstance(decision.get("overlap"), bool):
        raise RuntimeError("quota scheduling write-scope decision shape mismatch")
    return bool(decision["overlap"])
