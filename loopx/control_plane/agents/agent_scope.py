from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict, TypeGuard

from .agent_scope_frontier import (
    AGENT_LANE_FRONTIER_HINT_SCHEMA_VERSION,
    AgentLaneFrontierHintDecision,
    AgentScopeFrontierAction as AgentScopeFrontierAction,
    agent_scope_frontier_action as _frontier_action,
    build_agent_scope_frontier_payload,
)
from ..todos.contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_MONITOR,
    normalize_todo_blocks_agent,
    normalize_todo_bound_agent,
    normalize_todo_claimed_by,
    normalize_todo_excluded_agents,
    normalize_todo_id,
)
from ..todos.decision_scope import select_scoped_gate_fallback
from ..todos.handoff_gate import HandoffGateState
from ..todos.resume_planning import project_todo_resume_planning
from ..todos.summary_item import compact_todo_summary_item, compact_todo_summary_items
from ..todos.todo_semantics import (
    todo_item_claimed_by_agent_or_unclaimed,
    todo_item_excludes_agent,
    todo_item_is_actionable_open,
    todo_item_is_deferred,
    todo_item_task_class,
    todo_presentation_sort_key,
    todo_scoped_runnable_advancement_count,
)
from ..todos.user_gate import (
    open_todo_count as _open_todo_count,
    open_user_gate_todo_items as _open_user_gate_todo_items,
)
from ..work_items.work_lane import (
    work_lane_contract_is_due_monitor_attempt,
    work_lane_contract_requires_current_agent_attempt,
)

_agent_scope_frontier_action = _frontier_action

AGENT_TASK_SCOPE = "goal_all_read_claimed_run_global_read_v0"


class _TodoPatch(TypedDict):
    status: str
    waiting_on: str
    recommended_action: str


def _as_dict(value: object) -> dict[str, object]:
    """Return ``value`` when it is a dict, otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


def _int_value(value: object) -> int:
    """Coerce numeric or string count values to int, defaulting to zero."""
    return int(value or 0) if isinstance(value, (int, float, str)) else 0


def _dict_items(value: object) -> list[dict[str, object]]:
    """Filter a list value down to dictionary items."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _first_dict(mapping: Mapping[str, object], key: str) -> dict[str, object]:
    """Return the first dictionary element stored under ``key``, or an empty dict."""
    items = _dict_items(mapping.get(key))
    return items[0] if items else {}


def _todo_label(
    item: Mapping[str, object],
    *,
    keys: tuple[str, ...] = ("todo_id",),
    fallback: str,
) -> str:
    """Return the first non-empty stripped label among ``keys``, else ``fallback``."""
    for key in keys:
        label = str(item.get(key) or "").strip()
        if label:
            return label
    return fallback


def _todo_identity(
    item: Mapping[str, object],
    *,
    keys: tuple[str, ...] = ("todo_id", "index", "text"),
) -> str:
    """Build a deduplication identity from the first truthy field in ``keys``."""
    for key in keys:
        if value := item.get(key):
            return str(value)
    return ""


def _sorted_claimants(
    items: Sequence[Mapping[str, object]],
    *,
    excluded_agent: str | None = None,
) -> list[str]:
    """Return sorted unique claimants, optionally filtered to items excluding an agent."""
    return sorted(
        {
            claimed_by
            for item in items
            if excluded_agent is None or excluded_agent in agent_scope_item_excluded_agents(item)
            if (claimed_by := agent_scope_item_claimed_by(item))
        }
    )


def _agent_identity_has_scoped_lane(
    agent_identity: Mapping[str, object] | None,
) -> TypeGuard[Mapping[str, object]]:
    return isinstance(agent_identity, dict) and bool(
        normalize_todo_claimed_by(agent_identity.get("agent_id"))
    )


def _attach_agent_identity_contracts(
    *, payload: dict[str, Any], agent_identity: Mapping[str, object] | None
) -> dict[str, Any]:
    if not agent_identity:
        return payload
    payload["agent_identity"] = agent_identity
    payload["task_scope"] = AGENT_TASK_SCOPE
    return payload


def _todo_task_class(item: Mapping[str, object]) -> str:
    return todo_item_task_class(item)


def _todo_item_is_actionable_open(item: dict[str, object]) -> bool:
    return todo_item_is_actionable_open(item)


def agent_scope_item_claimed_by(item: Mapping[str, object]) -> str | None:
    return normalize_todo_claimed_by(item.get("claimed_by"))


def agent_scope_item_blocks_agent(item: Mapping[str, object]) -> str | None:
    return normalize_todo_blocks_agent(item.get("blocks_agent"))


def agent_scope_item_bound_agent(item: Mapping[str, object]) -> str | None:
    return normalize_todo_bound_agent(item.get("bound_agent"))


def agent_scope_item_excluded_agents(item: Mapping[str, object]) -> list[str]:
    return normalize_todo_excluded_agents(item.get("excluded_agents"))


def agent_scope_item_claimed_by_agent_or_unclaimed(
    item: dict[str, object], *, agent_id: str | None
) -> bool:
    return todo_item_claimed_by_agent_or_unclaimed(item, agent_id=agent_id)


def agent_scope_item_matches_agent_or_unclaimed(
    item: dict[str, object], *, agent_id: str | None
) -> bool:
    normalized_agent_id = normalize_todo_claimed_by(agent_id)
    if not normalized_agent_id:
        return True
    if todo_item_excludes_agent(item, agent_id=normalized_agent_id):
        return False
    return agent_scope_item_claimed_by_agent_or_unclaimed(item, agent_id=normalized_agent_id)


def agent_scope_count_advancement_items(items: object, *, claimed_by: str | None = None) -> int:
    if not isinstance(items, list):
        return 0
    normalized_claimed_by = (
        "__unclaimed__"
        if claimed_by == "__unclaimed__"
        else normalize_todo_claimed_by(claimed_by)
        if claimed_by is not None
        else None
    )
    count = 0
    for item in items:
        if (
            not isinstance(item, dict)
            or not _todo_item_is_actionable_open(item)
            or _todo_task_class(item) != TODO_TASK_CLASS_ADVANCEMENT
        ):
            continue
        item_claimed_by = agent_scope_item_claimed_by(item)
        if normalized_claimed_by == "__unclaimed__":
            if item_claimed_by:
                continue
        elif normalized_claimed_by is not None and item_claimed_by != normalized_claimed_by:
            continue
        count += 1
    return count


def _protocol_action_text(value: object, *, limit: int = 220) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."


def _gate_fallback_candidates(
    agent_todo_summary: Mapping[str, object],
    capability_gate: Mapping[str, object] | None,
    requested_candidate: dict[str, object] | None,
) -> list[dict[str, object]]:
    """Collect due monitors, runnable advancement items, and ready deferred candidates."""
    # An empty capability projection is authoritative for advancement work.
    # Due monitors are projected separately and are already scoped and due here.
    capability_candidates = (
        capability_gate.get("runnable_candidates") if isinstance(capability_gate, Mapping) else None
    )
    if isinstance(capability_candidates, list):
        advancement_candidates = _dict_items(capability_candidates)
    else:
        raw_backlog = agent_todo_summary.get("executable_backlog_items")
        advancement_candidates = _dict_items(
            raw_backlog
            if isinstance(raw_backlog, list)
            else agent_todo_summary.get("first_executable_items")
        )
    executable_items: list[dict[str, object]] = [
        *_dict_items(agent_todo_summary.get("monitor_due_items")),
        *advancement_candidates,
        *_dict_items(agent_todo_summary.get("deferred_resume_candidates")),
    ]
    # Exact selection was qualified from the complete inventory; carry it first
    # so display lanes dropping it never lose gate metadata.
    return [requested_candidate, *executable_items] if requested_candidate is not None else executable_items


def _scoped_user_gate_fallback(
    user_todo_summary: Mapping[str, object] | None,
    agent_todo_summary: Mapping[str, object] | None,
    *,
    capability_gate: Mapping[str, object] | None = None,
    allow_unrelated_gate: bool = False,
    monitor_debt_backoff_active: bool = False,
    requested_todo_id: str | None = None,
    requested_candidate: dict[str, object] | None = None,
) -> dict[str, Any] | None:
    gates = _open_user_gate_todo_items(user_todo_summary if isinstance(user_todo_summary, dict) else None)
    if not gates or not isinstance(agent_todo_summary, dict):
        return None
    executable_items = _gate_fallback_candidates(agent_todo_summary, capability_gate, requested_candidate)
    raw_scope_agent_id = _as_dict(agent_todo_summary.get("claim_scope")).get("agent_id")
    selection = select_scoped_gate_fallback(
        gates,
        executable_items,
        agent_id=None if raw_scope_agent_id is None else str(raw_scope_agent_id),
        allow_unrelated_gate=allow_unrelated_gate,
        monitor_debt_backoff_active=monitor_debt_backoff_active,
        requested_todo_id=requested_todo_id,
    )
    if selection is None:
        return None
    selected = executable_items[selection["selected_index"]]
    gate_to_surface = gates[selection["gate_index"]]
    blocked_items = [
        {
            **compact_todo_summary_item(item, text=str(item.get("text") or "").strip()),
            "todo_gate_relation": blocked["relation"],
        }
        for blocked in selection["blocked"][:3]
        for item in (executable_items[blocked["candidate_index"]],)
    ]
    selected_item = compact_todo_summary_item(selected, text=str(selected.get("text") or "").strip())
    selected_is_deferred_replan = selection["deferred_replan"]
    if selected_is_deferred_replan:
        selected_item["fallback_kind"] = "deferred_successor_replan"
    if selection["selected_relation"]:
        selected_item["todo_gate_relation"] = selection["selected_relation"]
    if selection["has_blocking_gate"]:
        reason = (
            "an open user_gate blocks a scoped agent todo, but a non-dependent "
            "executable fallback remains available"
        )
    elif selected_is_deferred_replan:
        reason = (
            "an open user_gate blocks a different action scope, but the ready "
            "deferred successor can be replanned independently"
        )
    else:
        reason = (
            "an open user_gate blocks a different action scope, but the selected "
            "executable agent todo is non-dependent and safe to advance"
        )
    return {
        "schema_version": "scoped_user_gate_fallback_v0",
        "kind": "scoped_user_gate_fallback",
        "notify_user": True,
        "requires_user_action": True,
        "reason": reason,
        "blocked_user_gate": compact_todo_summary_item(
            gate_to_surface, text=str(gate_to_surface.get("text") or "").strip()
        ),
        "blocked_agent_items": blocked_items[:3],
        "selected_executable": selected_item,
        "recommended_action": (
            "Notify the user about the scoped gate; then reopen, supersede, or "
            "record a no-follow-up rationale for the selected ready deferred "
            "successor, spending only after validated writeback."
            if selected_is_deferred_replan
            else "Notify the user about the scoped gate; then execute the selected "
            "non-gated fallback and spend only after validated writeback."
        ),
    }


def _first_todo_text_for_class(
    agent_todo_summary: Mapping[str, object] | None,
    *,
    keys: tuple[str, ...],
    task_class: str,
) -> str | None:
    """Return the first normalized open todo text matching ``task_class`` across ``keys``."""
    if not isinstance(agent_todo_summary, Mapping):
        return None
    for key in keys:
        for item in _dict_items(agent_todo_summary.get(key)):
            if _todo_item_is_actionable_open(item) and _todo_task_class(item) == task_class:
                if text := _protocol_action_text(item.get("text"), limit=320):
                    return text
    return None


def _agent_scoped_user_todo_override(
    *,
    state: str,
    item: Mapping[str, object],
    user_todo_summary: Mapping[str, object] | None,
    agent_todo_summary: Mapping[str, object] | None,
    agent_identity: Mapping[str, object] | None,
) -> dict[str, Any] | None:
    if state != "operator_gate" or not isinstance(agent_identity, dict) or not isinstance(user_todo_summary, dict):
        return None
    # Non-blocking user_action rows may remain a reminder without preserving an
    # operator gate inherited from another agent's user_gate.
    if _open_user_gate_todo_items(user_todo_summary):
        return None
    if item.get("operator_question") or item.get("missing_gates"):
        return None
    selected_action = _first_todo_text_for_class(
        agent_todo_summary, keys=("first_executable_items",), task_class=TODO_TASK_CLASS_ADVANCEMENT
    ) or _first_todo_text_for_class(
        agent_todo_summary, keys=("monitor_due_items", "monitor_open_items"), task_class=TODO_TASK_CLASS_MONITOR
    )
    agent_id = normalize_todo_claimed_by(agent_identity.get("agent_id"))
    other_gate_count = _open_todo_count({"open_count": user_todo_summary.get("other_agent_scoped_open_count")})
    if other_gate_count > 0:
        if not selected_action:
            return None
        gate_patch: _TodoPatch = {
            "status": "active_state_agent_todo",
            "waiting_on": "codex",
            "recommended_action": selected_action,
        }
        return {
            "schema_version": "agent_scoped_user_gate_override_v0",
            "kind": "agent_scoped_user_gate_override",
            "agent_id": agent_id,
            "from_state": "operator_gate",
            "to_state": "eligible",
            "other_agent_scoped_open_count": other_gate_count,
            "selected_action": selected_action,
            "reason": (
                "the open user gate is scoped to another agent via blocks_agent or "
                "claimed_by; this agent still has an executable in-scope todo"
            ),
            "item_patch": gate_patch,
        }
    other_action_count = _open_todo_count(
        {"open_count": user_todo_summary.get("other_agent_bound_user_action_open_count")}
    )
    if other_action_count <= 0:
        return None
    action_patch: _TodoPatch = {
        "status": "active_state_agent_todo" if selected_action else "agent_scope_wait",
        "waiting_on": "codex" if selected_action else "agent_scope",
        "recommended_action": selected_action or "wait for work or a user action bound to this agent",
    }
    result: dict[str, Any] = {
        "schema_version": "agent_scoped_user_action_override_v0",
        "kind": "agent_scoped_user_action_override",
        "agent_id": agent_id,
        "from_state": "operator_gate",
        "to_state": "eligible" if selected_action else "waiting",
        "other_agent_bound_user_action_open_count": other_action_count,
        "reason": (
            "the only open user actions are bound to another agent; they remain "
            "diagnostic-only for this lane and cannot create an operator gate"
        ),
        "quota_patch": {"safe_bypass_allowed": False, "safe_bypass_kind": None, "blocked_action_scope": None},
        "item_patch": action_patch,
    }
    if selected_action:
        result["selected_action"] = selected_action
    return result


def _selectable_unclaimed_advancement_count(agent_todo_summary: Mapping[str, object]) -> int:
    executable_items = agent_todo_summary.get("executable_backlog_items")
    source_items = (
        executable_items if isinstance(executable_items, list) else agent_todo_summary.get("unclaimed_priority_open_items")
    )
    return agent_scope_count_advancement_items(source_items, claimed_by="__unclaimed__")


def _first_compact_todo_id(items: object) -> str | None:
    for item in _dict_items(items):
        if todo_id := normalize_todo_id(item.get("todo_id")):
            return todo_id
    return None


def _build_frontier_hint(
    agent_id: str,
    decision: AgentLaneFrontierHintDecision,
    *,
    reason_code: str,
    source: str = "agent_scope_frontier",
    quiet_noop_allowed: bool = False,
    target_todo_id: str | None = None,
    next_cli_action: str | None = None,
) -> dict[str, object]:
    """Construct the structured hint projection for a scoped agent frontier."""
    hint: dict[str, object] = {
        "schema_version": AGENT_LANE_FRONTIER_HINT_SCHEMA_VERSION,
        "decision": decision.value,
        "agent_id": agent_id,
        "agent_model": "peer_v1",
        "source": source,
        "reason_code": reason_code,
        "quiet_noop_allowed": quiet_noop_allowed,
        "uses_structured_frontier": True,
    }
    if target_todo_id:
        hint["target_todo_id"] = target_todo_id
    if next_cli_action:
        hint["next_cli_action"] = next_cli_action
    return hint


def _successor_replan_frontier_hint(
    frontier: Mapping[str, object], *, goal_id: str, agent_id: str
) -> dict[str, object] | None:
    """Select the highest-precedence hint when successor replan is required."""
    if cleared_todo_id := _first_compact_todo_id(frontier.get("cleared_without_successor_handoff_gates")):
        return _build_frontier_hint(
            agent_id,
            AgentLaneFrontierHintDecision.RECORD_NO_FOLLOWUP,
            reason_code="cleared_handoff_without_successor",
            target_todo_id=cleared_todo_id,
            next_cli_action=(
                f"loopx todo complete --goal-id {goal_id} --todo-id {cleared_todo_id} "
                f"--agent-id {agent_id} --no-follow-up --evidence '<public-safe rationale>'"
            ),
        )
    if frontier.get("dependency_cycle_candidates"):
        # The typed replan carries the repair; no legacy hint describes dependency repair safely.
        return None
    if monitor_candidate := _first_dict(frontier, "monitor_blocked_resume_candidates"):
        monitor_blocker_id = normalize_todo_id(monitor_candidate.get("blocking_monitor_todo_id"))
        target_todo_id = normalize_todo_id(monitor_candidate.get("todo_id"))
        return _build_frontier_hint(
            agent_id,
            AgentLaneFrontierHintDecision.ADD_NEXT_ADVANCEMENT,
            reason_code="resume_blocked_by_open_monitor",
            target_todo_id=target_todo_id or monitor_blocker_id,
            next_cli_action=(
                f"loopx todo complete --goal-id {goal_id} --todo-id {monitor_blocker_id} "
                f"--agent-id {agent_id} --evidence '<validated gate evidence>'"
                if monitor_blocker_id
                else None
            ),
        )
    if route_candidate := _first_dict(frontier, "route_continuation_replan_candidates"):
        route_target = (
            normalize_todo_id(route_candidate.get("todo_id"))
            or normalize_todo_id(route_candidate.get("route_id"))
            or normalize_todo_id(route_candidate.get("route_key"))
        )
        return _build_frontier_hint(
            agent_id,
            AgentLaneFrontierHintDecision.ADD_NEXT_ADVANCEMENT,
            reason_code="route_continuation_replan_required",
            target_todo_id=route_target,
            next_cli_action=(
                f"loopx todo add --goal-id {goal_id} --role agent "
                "--text '<public-safe route continuation advancement todo>'"
            ),
        )
    return _build_frontier_hint(
        agent_id,
        AgentLaneFrontierHintDecision.ADD_NEXT_ADVANCEMENT,
        reason_code="successor_replan_required",
        target_todo_id=_first_compact_todo_id(frontier.get("deferred_resume_candidates")),
    )


_WAIT_HINT_CANDIDATE_KEYS: tuple[tuple[str, str], ...] = (
    ("blocked_todo_wait_candidates", "current_agent_blocker"),
    ("blocked_successor_wait_candidates", "blocked_successor_resume_pending"),
    ("blocking_handoff_gates", "blocked_by_other_agent_frontier"),
    ("other_agent_claimed_items", "blocked_by_other_agent_frontier"),
)


def _wait_frontier_hint(frontier: Mapping[str, object], *, agent_id: str) -> dict[str, object]:
    """Select the wait-blocker hint in precedence order."""
    for key, reason_code in _WAIT_HINT_CANDIDATE_KEYS:
        if target_todo_id := _first_compact_todo_id(frontier.get(key)):
            return _build_frontier_hint(
                agent_id,
                AgentLaneFrontierHintDecision.QUIET_NOOP_BLOCKER,
                reason_code=reason_code,
                target_todo_id=target_todo_id,
                quiet_noop_allowed=True,
            )
    return _build_frontier_hint(
        agent_id,
        AgentLaneFrontierHintDecision.ADD_NEXT_ADVANCEMENT,
        reason_code="no_current_agent_advancement_todo",
        quiet_noop_allowed=True,
    )


def _agent_lane_frontier_hint(
    *,
    goal_id: str,
    agent_identity: Mapping[str, object] | None,
    agent_todo_summary: Mapping[str, object] | None,
    agent_lane_next_action: Mapping[str, object] | None,
    agent_scope_frontier: Mapping[str, object] | None,
    work_lane_contract: dict[str, object] | None,
) -> dict[str, object] | None:
    if not _agent_identity_has_scoped_lane(agent_identity):
        return None
    agent_id = normalize_todo_claimed_by(agent_identity.get("agent_id"))
    if not agent_id:
        return None

    if isinstance(agent_lane_next_action, dict):
        selected_by = str(agent_lane_next_action.get("selected_by") or "")
        claim_required = agent_lane_next_action.get("claim_required_before_work") is True
        target_todo_id = normalize_todo_id(agent_lane_next_action.get("todo_id"))
        if selected_by == "unclaimed_todo" or claim_required:
            return _build_frontier_hint(
                agent_id,
                AgentLaneFrontierHintDecision.CLAIM_UNOWNED_IN_SCOPE,
                source="agent_lane_next_action",
                reason_code="unclaimed_advancement_selected",
                target_todo_id=target_todo_id,
                next_cli_action=(
                    f"loopx todo claim --goal-id {goal_id} --todo-id {target_todo_id} "
                    f"--claimed-by {agent_id} --agent-id {agent_id}"
                    if target_todo_id
                    else None
                ),
            )

    if (
        work_lane_contract_is_due_monitor_attempt(work_lane_contract)
        or work_lane_contract_requires_current_agent_attempt(work_lane_contract)
    ):
        return None

    frontier = _as_dict(agent_scope_frontier)
    frontier_action = _agent_scope_frontier_action(frontier.get("action"))
    if frontier_action == AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED:
        return _successor_replan_frontier_hint(frontier, goal_id=goal_id, agent_id=agent_id)
    if frontier_action in {AgentScopeFrontierAction.AGENT_SCOPE_WAIT, AgentScopeFrontierAction.REASSIGNMENT_REQUIRED}:
        return _wait_frontier_hint(frontier, agent_id=agent_id)
    if frontier_action == AgentScopeFrontierAction.AGENT_SCOPE_EXHAUSTED:
        return _build_frontier_hint(
            agent_id,
            AgentLaneFrontierHintDecision.ADD_NEXT_ADVANCEMENT,
            reason_code="agent_scope_exhausted",
            quiet_noop_allowed=True,
        )

    if not isinstance(agent_todo_summary, Mapping):
        return None
    current_advancement_count = _int_value(agent_todo_summary.get("current_agent_claimed_advancement_count"))
    current_monitor_count = _int_value(agent_todo_summary.get("current_agent_claimed_monitor_count"))
    unclaimed_count = _selectable_unclaimed_advancement_count(agent_todo_summary)
    lane = str(work_lane_contract.get("lane") or "") if isinstance(work_lane_contract, dict) else ""
    if current_advancement_count == 0 and unclaimed_count == 0 and current_monitor_count > 0:
        return _build_frontier_hint(
            agent_id,
            AgentLaneFrontierHintDecision.QUIET_NOOP_BLOCKER,
            source="agent_todo_summary",
            reason_code="only_current_agent_monitor_work_remains",
            quiet_noop_allowed=lane != TODO_TASK_CLASS_ADVANCEMENT,
        )
    return None


def _select_and_compact_candidates(
    items: Sequence[dict[str, object]],
    *,
    predicate: Callable[[dict[str, object]], bool],
    identity_keys: tuple[str, ...] = ("todo_id", "index", "text"),
    require_identity: bool = False,
    copy_fields: tuple[str, ...] = (),
    copy_truthy_only: bool = False,
    defaults: Mapping[str, object] | None = None,
) -> list[dict[str, Any]]:
    """Filter, deduplicate, compact, and sort todo candidate records."""
    unique: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in items:
        if not predicate(item):
            continue
        identity = _todo_identity(item, keys=identity_keys)
        if (require_identity and not identity) or identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    compacts = compact_todo_summary_items(unique, strip_text=True)
    for item, compact in zip(unique, compacts, strict=True):
        if defaults:
            compact.update(defaults)
        for field in copy_fields:
            value = item.get(field)
            if value if copy_truthy_only else value is not None:
                compact[field] = value
    return sorted(compacts, key=todo_presentation_sort_key)


def _collect_lane_or_fallback(
    summary: Mapping[str, object],
    *,
    lane_keys: tuple[str, ...],
    fallback_key: str,
    fallback_predicate: Callable[[dict[str, object]], bool],
) -> list[dict[str, object]]:
    """Collect candidate rows from lane keys, falling back when all lane lists are empty."""
    candidates = [item for key in lane_keys for item in _dict_items(summary.get(key))]
    if candidates:
        return candidates
    return [item for item in _dict_items(summary.get(fallback_key)) if fallback_predicate(item)]


def _agent_scope_deferred_resume_candidates(
    agent_todo_summary: Mapping[str, object], *, agent_id: str | None
) -> list[dict[str, Any]]:
    candidates = _collect_lane_or_fallback(
        agent_todo_summary,
        lane_keys=("current_agent_deferred_resume_candidates", "unclaimed_deferred_resume_candidates"),
        fallback_key="deferred_resume_candidates",
        fallback_predicate=lambda item: agent_scope_item_claimed_by_agent_or_unclaimed(item, agent_id=agent_id),
    )
    return _select_and_compact_candidates(
        candidates,
        predicate=lambda item: (
            not todo_item_excludes_agent(item, agent_id=agent_id)
            and item.get("resume_ready") is True
            and todo_item_is_deferred(item)
        ),
    )


def _agent_scope_monitor_blocked_resume_candidates(
    agent_todo_summary: Mapping[str, object] | None, *, agent_id: str | None
) -> list[dict[str, Any]]:
    if not isinstance(agent_todo_summary, Mapping):
        return []
    keys = (
        ("current_agent_monitor_blocked_resume_candidates", "unclaimed_monitor_blocked_resume_candidates")
        if agent_id
        else ("monitor_blocked_resume_candidates",)
    )
    candidates = [item for key in keys for item in _dict_items(agent_todo_summary.get(key))]
    # The typed resume-planning owner already selected this repair lane; preserve executor scope only.
    return _select_and_compact_candidates(
        candidates,
        predicate=lambda item: (
            not todo_item_excludes_agent(item, agent_id=agent_id)
            and _todo_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
            and item.get("resume_ready") is False
        ),
        copy_fields=("blocking_monitor_todo_id",),
        copy_truthy_only=True,
    )


def _agent_scope_handoff_gates_by_state(
    agent_todo_summary: Mapping[str, object],
    *,
    agent_id: str | None,
    gate_state: str,
    preferred_key: str,
) -> list[dict[str, Any]]:
    if not agent_id:
        return []
    raw_gates = agent_todo_summary.get(preferred_key)
    if not isinstance(raw_gates, list):
        raw_gates = agent_todo_summary.get("handoff_gates")
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _dict_items(raw_gates):
        if agent_id not in agent_scope_item_excluded_agents(item) or item.get("gate_state") != gate_state:
            continue
        identity = _todo_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        selected.append({**item, "text": str(item.get("text") or "").strip()})
    return selected


def agent_scope_blocking_handoff_gates(
    agent_todo_summary: Mapping[str, object], *, agent_id: str | None
) -> list[dict[str, Any]]:
    selected = _agent_scope_handoff_gates_by_state(
        agent_todo_summary,
        agent_id=agent_id,
        gate_state=HandoffGateState.BLOCKING.value,
        preferred_key="current_agent_handoff_gates",
    )
    return sorted(selected, key=todo_presentation_sort_key)


def _agent_scope_route_continuation_replan_candidates(
    agent_todo_summary: Mapping[str, object], *, agent_id: str
) -> list[dict[str, Any]]:
    candidates = _collect_lane_or_fallback(
        agent_todo_summary,
        lane_keys=(
            "current_agent_route_continuation_replan_candidates",
            "unclaimed_route_continuation_replan_candidates",
        ),
        fallback_key="route_continuation_replan_candidates",
        fallback_predicate=lambda item: agent_scope_item_matches_agent_or_unclaimed(item, agent_id=agent_id),
    )
    return _select_and_compact_candidates(
        candidates,
        predicate=lambda item: (
            agent_scope_item_matches_agent_or_unclaimed(item, agent_id=agent_id)
            and item.get("route_continuation_replan_required") is not False
        ),
        identity_keys=("todo_id", "route_id", "route_key", "index", "text"),
        require_identity=True,
        copy_fields=("route_continuation_reason", "route_id", "route_key"),
        defaults={"route_continuation_replan_required": True},
    )


@dataclass(frozen=True)
class _AgentScopeNoCandidateContext:
    agent_id: str
    summary: Mapping[str, object]
    current_agent_count: int
    unclaimed_count: int
    has_advancement_contract: bool

    def counts(self, **extra: int) -> dict[str, int]:
        return {
            "current_agent_claimed_advancement_count": self.current_agent_count,
            "unclaimed_advancement_count": self.unclaimed_count,
            **extra,
        }


def _blocking_handoff_copy(agent_id: str, owner: str) -> tuple[str, str]:
    """Return shared ``reason`` and ``recommended_action`` prose for blocking handoffs."""
    reason = (
        f"current agent {agent_id} has no current/unclaimed advancement "
        f"candidate; blocking handoff work is claimed by {owner}"
    )
    recommended_action = (
        f"Keep {agent_id} active but quiet: wait for {owner} to finish the "
        "blocking handoff, reassign it, or create a concrete current-agent/"
        "unclaimed advancement todo before delivery."
    )
    return reason, recommended_action


def _selected_candidate_priority_frontier(
    *, agent_id: str, summary: Mapping[str, object], selected: dict[str, object]
) -> dict[str, object] | None:
    candidates = _agent_scope_deferred_resume_candidates(summary, agent_id=agent_id)
    if not candidates or todo_presentation_sort_key(candidates[0])[0] >= todo_presentation_sort_key(selected)[0]:
        return None
    candidate_todo_id = _todo_label(candidates[0], fallback="<todo_id>")
    selected_todo_id = _todo_label(selected, fallback="<open_todo_id>")
    return build_agent_scope_frontier_payload(
        agent_id=agent_id,
        action=AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED,
        quiet_noop_allowed=False,
        spend_policy="spend once after validated priority successor replan/todo writeback",
        reason=(
            f"ready deferred successor {candidate_todo_id} has higher priority than "
            f"selected open advancement {selected_todo_id}; its lifecycle must be "
            "resolved before lower-priority delivery"
        ),
        recommended_action=(
            "Run a bounded successor replan before lower-priority delivery: reopen, "
            f"supersede, or record a no-follow-up rationale for {candidate_todo_id}."
        ),
        requires_replan=True,
        candidate_counts={"deferred_resume_candidate_count": len(candidates), "preempted_open_candidate_count": 1},
        extra_fields={
            "deferred_resume_candidates": candidates[:3],
            "preempted_open_candidate": compact_todo_summary_item(selected, text=str(selected.get("text") or "").strip()),
            "priority_preemption": True,
        },
    )


def _agent_scope_advancement_counts(summary: Mapping[str, object], *, agent_id: str) -> tuple[int, int]:
    current = max(
        _int_value(summary.get("current_agent_claimed_advancement_count")),
        agent_scope_count_advancement_items(summary.get("current_agent_claimed_advancement_items"), claimed_by=agent_id),
    )
    unclaimed = _selectable_unclaimed_advancement_count(summary)
    executable_items = summary.get("executable_backlog_items")
    if isinstance(executable_items, list):
        current = max(current, agent_scope_count_advancement_items(executable_items, claimed_by=agent_id))
        unclaimed = agent_scope_count_advancement_items(executable_items, claimed_by="__unclaimed__")
    return current, unclaimed


def _dependency_cycle_frontier(context: _AgentScopeNoCandidateContext) -> dict[str, object] | None:
    repair = project_todo_resume_planning(context.summary, agent_id=context.agent_id).get("dependency_cycle_replan")
    if not repair:
        return None
    return build_agent_scope_frontier_payload(
        agent_id=context.agent_id,
        action=AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED,
        quiet_noop_allowed=False,
        requires_replan=True,
        spend_policy=repair["spend_policy"],
        reason=repair["reason"],
        recommended_action=repair["recommended_action"],
        candidate_counts=context.counts(dependency_cycle_count=repair["count"]),
        extra_fields={"dependency_cycle_candidates": repair["candidates"]},
    )


def _monitor_blocked_resume_frontier(context: _AgentScopeNoCandidateContext) -> dict[str, object] | None:
    candidates = _agent_scope_monitor_blocked_resume_candidates(context.summary, agent_id=context.agent_id)
    if not candidates:
        return None
    candidate_id = _todo_label(candidates[0], fallback="<todo_id>")
    monitor_id = _todo_label(candidates[0], keys=("blocking_monitor_todo_id",), fallback="<monitor_todo_id>")
    return build_agent_scope_frontier_payload(
        agent_id=context.agent_id,
        action=AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED,
        quiet_noop_allowed=False,
        spend_policy="spend once after validated standing-monitor gate repair/todo writeback",
        reason=(
            f"current agent {context.agent_id} has advancement todo {candidate_id} gated "
            f"by open continuous_monitor {monitor_id}; a standing monitor cannot be the "
            "todo_done prerequisite for autonomous continuation"
        ),
        recommended_action=(
            "Run a bounded gate-model repair before quiet wait: close/supersede "
            f"{monitor_id} after validated evidence, or rewrite {candidate_id} to use "
            "a non-blocking monitor contract before delivery."
        ),
        requires_replan=True,
        candidate_counts=context.counts(monitor_blocked_resume_candidate_count=len(candidates)),
        extra_fields={"monitor_blocked_resume_candidates": candidates[:3]},
    )


def _deferred_resume_frontier(context: _AgentScopeNoCandidateContext) -> dict[str, object] | None:
    candidates = _agent_scope_deferred_resume_candidates(context.summary, agent_id=context.agent_id)
    if not candidates:
        return None
    candidate_id = _todo_label(candidates[0], fallback="<todo_id>")
    return build_agent_scope_frontier_payload(
        agent_id=context.agent_id,
        action=AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED,
        quiet_noop_allowed=False,
        spend_policy="spend once after validated successor replan/todo writeback",
        reason=(
            f"current agent {context.agent_id} has no open current/unclaimed advancement "
            "candidate, but a deferred successor resume condition is satisfied"
        ),
        recommended_action=(
            "Run a bounded successor replan before delivery: reopen, supersede, or "
            f"record a no-follow-up rationale for {candidate_id}."
        ),
        requires_replan=True,
        candidate_counts=context.counts(deferred_resume_candidate_count=len(candidates)),
        extra_fields={"deferred_resume_candidates": candidates[:3]},
    )


def _blocked_todo_wait_frontier(context: _AgentScopeNoCandidateContext) -> dict[str, object] | None:
    blocked_items = project_todo_resume_planning(context.summary, agent_id=context.agent_id).get("blocked_wait_items")
    if not blocked_items:
        return None
    return build_agent_scope_frontier_payload(
        agent_id=context.agent_id,
        action=AgentScopeFrontierAction.AGENT_SCOPE_WAIT,
        quiet_noop_allowed=True,
        spend_policy="no quota spend while scoped work is blocked",
        reason=f"current agent {context.agent_id} has blocked work and no runnable candidate",
        recommended_action=(
            "Wait for the blocker to be resolved or its resume condition to become ready; "
            "recheck when the Todo state or available work changes."
        ),
        candidate_counts=context.counts(blocked_todo_wait_count=len(blocked_items)),
        extra_fields={"blocked_todo_wait_candidates": blocked_items[:3]},
    )


def _blocked_successor_wait_frontier(context: _AgentScopeNoCandidateContext) -> dict[str, object] | None:
    planning = project_todo_resume_planning(context.summary, agent_id=context.agent_id)
    candidates = planning["blocked_successor_items"]
    if not candidates:
        return None
    candidate_id = _todo_label(candidates[0], fallback="<todo_id>")
    resume_when = str(candidates[0].get("resume_when") or "").strip()
    extra_fields: dict[str, object] = {"blocked_successor_wait_candidates": candidates[:3]}
    if "automatic_resume_todo" in planning:
        extra_fields["automatic_resume_todo"] = planning["automatic_resume_todo"]
    return build_agent_scope_frontier_payload(
        agent_id=context.agent_id,
        action=AgentScopeFrontierAction.AGENT_SCOPE_WAIT,
        quiet_noop_allowed=True,
        spend_policy="no quota spend while an exact successor resume condition is pending",
        reason=(
            f"current agent {context.agent_id} has exact blocked successor {candidate_id} "
            f"waiting on {resume_when}; another successor replan would duplicate the existing route"
        ),
        recommended_action=(
            f"Keep {context.agent_id} active but quiet until {resume_when} becomes ready; "
            "then resume ordinary todo/deferred-successor routing automatically."
        ),
        requires_replan=False,
        candidate_counts=context.counts(blocked_successor_wait_count=len(candidates)),
        extra_fields=extra_fields,
    )


def _route_continuation_frontier(context: _AgentScopeNoCandidateContext) -> dict[str, object] | None:
    candidates = _agent_scope_route_continuation_replan_candidates(context.summary, agent_id=context.agent_id)
    if not candidates:
        return None
    route_label = _todo_label(candidates[0], keys=("route_id", "route_key", "todo_id"), fallback="<route>")
    return build_agent_scope_frontier_payload(
        agent_id=context.agent_id,
        action=AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED,
        quiet_noop_allowed=False,
        spend_policy="spend once after validated route continuation replan/todo writeback",
        reason=(
            f"current agent {context.agent_id} has no open current/unclaimed advancement "
            f"candidate, but the route continuation projection requires a successor replan for {route_label}"
        ),
        recommended_action=(
            "Run a bounded route continuation replan before quiet wait: create or claim "
            f"the next concrete {context.agent_id}/unclaimed advancement todo for "
            f"{route_label}, or record an explicit no-follow-up rationale."
        ),
        requires_replan=True,
        candidate_counts=context.counts(route_continuation_replan_candidate_count=len(candidates)),
        extra_fields={"route_continuation_replan_candidates": candidates[:3]},
    )


def _blocking_handoff_frontier(context: _AgentScopeNoCandidateContext) -> dict[str, object] | None:
    gates = agent_scope_blocking_handoff_gates(context.summary, agent_id=context.agent_id)
    if not gates:
        return None
    claimants = _sorted_claimants(gates)
    reason, recommended_action = _blocking_handoff_copy(
        context.agent_id, ", ".join(claimants) or "the owning agent"
    )
    return build_agent_scope_frontier_payload(
        agent_id=context.agent_id,
        action=AgentScopeFrontierAction.AGENT_SCOPE_WAIT,
        quiet_noop_allowed=True,
        spend_policy="no quota spend while the current agent has no in-scope runnable candidate",
        reason=reason,
        recommended_action=recommended_action,
        candidate_counts=context.counts(blocking_handoff_gate_count=len(gates)),
        extra_fields={
            "other_claimants": claimants,
            "blocking_handoff_claimants": claimants,
            "blocking_handoff_gates": gates[:3],
        },
    )


def _cleared_handoff_frontier(context: _AgentScopeNoCandidateContext) -> dict[str, object] | None:
    gates = sorted(
        _agent_scope_handoff_gates_by_state(
            context.summary,
            agent_id=context.agent_id,
            gate_state=HandoffGateState.CLEARED_WITHOUT_SUCCESSOR.value,
            preferred_key="current_agent_cleared_without_successor_handoff_gates",
        ),
        key=lambda item: (-int(item.get("index") or 0), str(item.get("todo_id") or "")),
    )
    if not gates:
        return None
    blocker_id = _todo_label(gates[0], fallback="<todo_id>")
    unblocks_id = normalize_todo_id(gates[0].get("unblocks_todo_id"))
    target_text = f" for unblocked todo {unblocks_id}" if unblocks_id else ""
    return build_agent_scope_frontier_payload(
        agent_id=context.agent_id,
        action=AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED,
        quiet_noop_allowed=False,
        spend_policy="spend once after validated successor replan/todo writeback",
        reason=(
            f"current agent {context.agent_id} has no open current/unclaimed advancement "
            f"candidate, but blocking handoff {blocker_id}{target_text} is already done "
            "without a projected successor"
        ),
        recommended_action=(
            "Run a bounded successor replan before quiet wait: reopen or supersede "
            f"{blocker_id} into a concrete {context.agent_id}/unclaimed advancement "
            "todo, or record an explicit no-follow-up rationale."
        ),
        requires_replan=True,
        candidate_counts=context.counts(cleared_without_successor_handoff_count=len(gates)),
        extra_fields={"cleared_without_successor_handoff_gates": gates[:3]},
    )


def _other_agent_or_exhausted_frontier(context: _AgentScopeNoCandidateContext) -> dict[str, object] | None:
    other_items = [
        item
        for item in _dict_items(context.summary.get("claimed_advancement_open_items"))
        if _todo_item_is_actionable_open(item)
        and _todo_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
        and agent_scope_item_claimed_by(item) not in {None, "", context.agent_id}
    ]
    if not context.has_advancement_contract and not other_items:
        return None
    other_claimants = _sorted_claimants(other_items)
    blocking_claimants = _sorted_claimants(other_items, excluded_agent=context.agent_id)
    if blocking_claimants:
        action = AgentScopeFrontierAction.AGENT_SCOPE_WAIT
        reason, recommended_action = _blocking_handoff_copy(context.agent_id, ", ".join(blocking_claimants))
    elif other_items:
        action = AgentScopeFrontierAction.REASSIGNMENT_REQUIRED
        owner = ", ".join(other_claimants) or "the owning agent"
        reason = (
            f"current agent {context.agent_id} has no current/unclaimed advancement "
            f"candidate; visible advancement work is claimed by {owner}"
        )
        recommended_action = (
            f"Keep {context.agent_id} active but quiet: wait for {owner} to finish, "
            "reassign, or create a concrete current-agent/unclaimed advancement todo before delivery."
        )
    else:
        action = AgentScopeFrontierAction.AGENT_SCOPE_EXHAUSTED
        reason = (
            f"current agent {context.agent_id} has no projected current/unclaimed "
            "advancement candidate despite a goal-level advancement lane"
        )
        recommended_action = (
            f"Keep {context.agent_id} active but quiet until LoopX projects a concrete "
            "current-agent or unclaimed advancement todo, or another peer transfers work."
        )
    claim_scope = _as_dict(context.summary.get("claim_scope"))
    return build_agent_scope_frontier_payload(
        agent_id=context.agent_id,
        action=action,
        quiet_noop_allowed=True,
        spend_policy="no quota spend while the current agent has no in-scope runnable candidate",
        reason=reason,
        recommended_action=recommended_action,
        candidate_counts=context.counts(
            other_agent_claimed_advancement_count=len(other_items),
            other_agent_claimed_open_count=_int_value(claim_scope.get("other_agent_claimed_open_count")),
        ),
        extra_fields={
            "other_claimants": other_claimants,
            "blocking_handoff_claimants": blocking_claimants,
            "other_agent_claimed_items": compact_todo_summary_items(other_items[:3], strip_text=True),
        },
    )


_AGENT_SCOPE_NO_CANDIDATE_RULES = (
    _dependency_cycle_frontier,
    _monitor_blocked_resume_frontier,
    _deferred_resume_frontier,
    _blocked_successor_wait_frontier,
    _route_continuation_frontier,
    _blocking_handoff_frontier,
    _cleared_handoff_frontier,
    _other_agent_or_exhausted_frontier,
    _blocked_todo_wait_frontier,
)


def _agent_scope_no_candidate_frontier(
    *,
    agent_identity: Mapping[str, object] | None,
    agent_todo_summary: dict[str, object] | None,
    agent_lane_next_action: dict[str, object] | None,
    work_lane_contract: dict[str, object] | None,
    candidate_should_run: bool,
    vision_wait_state: Mapping[str, object] | None = None,
) -> dict[str, Any] | None:
    if not candidate_should_run or not _agent_identity_has_scoped_lane(agent_identity):
        return None
    agent_id = normalize_todo_claimed_by(agent_identity.get("agent_id"))
    if not agent_id or not isinstance(agent_todo_summary, dict):
        return None
    if work_lane_contract_is_due_monitor_attempt(work_lane_contract):
        return None
    if isinstance(agent_lane_next_action, dict):
        # Eligibility and real delivery gates are already qualified by the typed
        # action-selection owner; priority must not replace an explicit selection.
        if agent_lane_next_action.get("selected_by") == "agent_action_selection":
            return None
        return _selected_candidate_priority_frontier(
            agent_id=agent_id, summary=agent_todo_summary, selected=agent_lane_next_action
        )
    if work_lane_contract_requires_current_agent_attempt(work_lane_contract):
        return None

    current_count, unclaimed_count = _agent_scope_advancement_counts(agent_todo_summary, agent_id=agent_id)
    if vision_wait_state and todo_scoped_runnable_advancement_count(agent_todo_summary, agent_id=agent_id) == 0:
        # A committed retry preserves its unfinished wait route when selection proves no runnable work.
        current_count = unclaimed_count = 0
    if current_count > 0 or unclaimed_count > 0:
        return None
    context = _AgentScopeNoCandidateContext(
        agent_id=agent_id,
        summary=agent_todo_summary,
        current_agent_count=current_count,
        unclaimed_count=unclaimed_count,
        has_advancement_contract=bool(
            isinstance(work_lane_contract, dict)
            and work_lane_contract.get("lane") == TODO_TASK_CLASS_ADVANCEMENT
            and work_lane_contract.get("must_attempt_work") is True
        ),
    )
    for rule in _AGENT_SCOPE_NO_CANDIDATE_RULES:
        if (frontier := rule(context)) is not None:
            return frontier
    return None
