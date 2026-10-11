from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, TypeGuard

from .agent_scope_frontier import (
    AGENT_LANE_FRONTIER_HINT_SCHEMA_VERSION,
    AgentLaneFrontierHintDecision,
    AgentScopeFrontierAction,
    agent_scope_frontier_action as _agent_scope_frontier_action,
    build_agent_scope_frontier_payload,
)
from ..todos.decision_scope import select_scoped_gate_fallback
from ..work_items.work_lane import (
    work_lane_contract_is_due_monitor_attempt,
    work_lane_contract_requires_current_agent_attempt,
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
from ..todos.handoff_gate import HandoffGateState
from ..todos.resume_planning import project_todo_resume_planning
from ..todos.todo_semantics import (
    todo_item_claimed_by_agent_or_unclaimed,
    todo_item_excludes_agent,
    todo_item_is_actionable_open,
    todo_item_is_deferred,
    todo_item_task_class,
    todo_presentation_sort_key,
)
from ..todos.summary_item import compact_todo_summary_item, compact_todo_summary_items
from ..todos.user_gate import (
    open_todo_count as _open_todo_count,
    open_user_gate_todo_items as _open_user_gate_todo_items,
)

_ACTION_SCOPE_STOPWORDS = {
    "a",
    "an",
    "and",
    "approve",
    "approved",
    "approval",
    "before",
    "blocked",
    "continue",
    "decide",
    "decision",
    "for",
    "from",
    "gate",
    "gated",
    "goal",
    "harness",
    "internal",
    "material",
    "next",
    "open",
    "owner",
    "p0",
    "p1",
    "p2",
    "p3",
    "provide",
    "read",
    "registered",
    "safe",
    "should",
    "sync",
    "the",
    "todo",
    "until",
    "user",
    "whether",
    "with",
}

AGENT_TASK_SCOPE = "goal_all_read_claimed_run_global_read_v0"
def _agent_identity_has_scoped_lane(
    agent_identity: dict[str, Any] | None,
) -> TypeGuard[dict[str, object]]:
    return isinstance(agent_identity, dict) and bool(
        normalize_todo_claimed_by(agent_identity.get("agent_id"))
    )
def _attach_agent_identity_contracts(
    *,
    payload: dict[str, Any],
    agent_identity: dict[str, Any] | None,
) -> dict[str, Any]:
    if not agent_identity:
        return payload
    payload["agent_identity"] = agent_identity
    payload["task_scope"] = AGENT_TASK_SCOPE
    return payload


def _todo_task_class(item: dict[str, Any]) -> str:
    return todo_item_task_class(item)
def _todo_projection_sort_key(item: dict[str, Any]) -> tuple[int, int, str, str]:
    return todo_presentation_sort_key(item)


def _todo_item_is_actionable_open(item: dict[str, Any]) -> bool:
    return todo_item_is_actionable_open(item)


def agent_scope_item_claimed_by(item: dict[str, Any]) -> str | None:
    return normalize_todo_claimed_by(item.get("claimed_by"))


def agent_scope_item_blocks_agent(item: dict[str, Any]) -> str | None:
    return normalize_todo_blocks_agent(item.get("blocks_agent"))


def agent_scope_item_bound_agent(item: dict[str, Any]) -> str | None:
    return normalize_todo_bound_agent(item.get("bound_agent"))


def agent_scope_item_excluded_agents(item: dict[str, Any]) -> list[str]:
    return normalize_todo_excluded_agents(item.get("excluded_agents"))


def agent_scope_item_claimed_by_agent_or_unclaimed(
    item: dict[str, Any],
    *,
    agent_id: str | None,
) -> bool:
    return todo_item_claimed_by_agent_or_unclaimed(item, agent_id=agent_id)


def agent_scope_item_matches_agent_or_unclaimed(
    item: dict[str, Any],
    *,
    agent_id: str | None,
) -> bool:
    normalized_agent_id = normalize_todo_claimed_by(agent_id)
    if not normalized_agent_id:
        return True
    if todo_item_excludes_agent(item, agent_id=normalized_agent_id):
        return False
    return agent_scope_item_claimed_by_agent_or_unclaimed(
        item,
        agent_id=normalized_agent_id,
    )


def agent_scope_count_advancement_items(
    items: Any,
    *,
    claimed_by: str | None = None,
) -> int:
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
        if not isinstance(item, dict):
            continue
        if not _todo_item_is_actionable_open(item):
            continue
        if _todo_task_class(item) != TODO_TASK_CLASS_ADVANCEMENT:
            continue
        item_claimed_by = agent_scope_item_claimed_by(item)
        if normalized_claimed_by == "__unclaimed__":
            if item_claimed_by:
                continue
        elif normalized_claimed_by is not None and item_claimed_by != normalized_claimed_by:
            continue
        count += 1
    return count


def _protocol_action_text(value: Any, *, limit: int = 220) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _action_scope_tokens_from_text(text: str) -> set[str]:
    return {
        token
        for token in re.findall(
            r"[a-z0-9]+",
            text.lower().replace("_", " ").replace("-", " "),
        )
        if len(token) > 1 and token not in _ACTION_SCOPE_STOPWORDS
    }


def _todo_item_claimed_by_agent_or_unclaimed(item: dict[str, Any], *, agent_id: str) -> bool:
    return agent_scope_item_claimed_by_agent_or_unclaimed(item, agent_id=agent_id)


def _scoped_user_gate_fallback(
    user_todo_summary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    *,
    capability_gate: dict[str, Any] | None = None,
    allow_unrelated_gate: bool = False,
    monitor_debt_backoff_active: bool = False,
) -> dict[str, Any] | None:
    gates = _open_user_gate_todo_items(user_todo_summary)
    if not gates or not isinstance(agent_todo_summary, dict):
        return None
    raw_due_monitor_candidates = agent_todo_summary.get("monitor_due_items")
    due_monitor_candidates = raw_due_monitor_candidates if isinstance(raw_due_monitor_candidates, list) else []
    raw_ready_deferred_candidates = agent_todo_summary.get(
        "deferred_resume_candidates"
    )
    ready_deferred_candidates = raw_ready_deferred_candidates if isinstance(raw_ready_deferred_candidates, list) else []

    # An empty capability projection is authoritative for advancement work.
    # Due monitors are projected separately from advancement candidates and are
    # already current-agent scoped, due, and capability-runnable here.
    capability_candidates = (
        capability_gate.get("runnable_candidates")
        if isinstance(capability_gate, dict)
        else None
    )
    if isinstance(capability_candidates, list):
        executable_items = [
            *due_monitor_candidates,
            *capability_candidates,
            *ready_deferred_candidates,
        ]
    else:
        raw_backlog_items = agent_todo_summary.get("executable_backlog_items")
        raw_first_items = agent_todo_summary.get("first_executable_items")
        advancement_items = (
            raw_backlog_items
            if isinstance(raw_backlog_items, list)
            else raw_first_items
            if isinstance(raw_first_items, list)
            else []
        )
        executable_items = [
            *due_monitor_candidates,
            *advancement_items,
            *ready_deferred_candidates,
        ]
    executable_items = [item for item in executable_items if isinstance(item, dict)]
    claim_scope = (
        agent_todo_summary.get("claim_scope")
        if isinstance(agent_todo_summary.get("claim_scope"), dict)
        else None
    )
    selection = select_scoped_gate_fallback(
        gates, executable_items, agent_id=claim_scope.get("agent_id") if claim_scope else None,
        allow_unrelated_gate=allow_unrelated_gate, monitor_debt_backoff_active=monitor_debt_backoff_active,
    )
    if selection is None:
        return None
    selected = executable_items[selection["selected_index"]]
    gate_to_surface = gates[selection["gate_index"]]
    blocking_gate = selection["has_blocking_gate"]
    blocked_items = []
    for blocked in selection["blocked"][:3]:
        item = executable_items[blocked["candidate_index"]]
        blocked_items.append({**compact_todo_summary_item(item, text=str(item.get("text") or "").strip()),
                              "todo_gate_relation": blocked["relation"]})
    selected_text = str(selected.get("text") or "").strip()
    selected_item = compact_todo_summary_item(selected, text=selected_text)
    selected_is_deferred_replan = selection["deferred_replan"]
    if selected_is_deferred_replan:
        selected_item["fallback_kind"] = "deferred_successor_replan"
    selected_relation = selection["selected_relation"]
    if selected_relation:
        selected_item["todo_gate_relation"] = selected_relation
    gate_text = str(gate_to_surface.get("text") or "").strip()
    reason = (
        "an open user_gate blocks a scoped agent todo, but a non-dependent "
        "executable fallback remains available"
        if blocking_gate
        else (
            "an open user_gate blocks a different action scope, but the ready "
            "deferred successor can be replanned independently"
            if selected_is_deferred_replan
            else "an open user_gate blocks a different action scope, but the selected "
            "executable agent todo is non-dependent and safe to advance"
        )
    )
    return {
        "schema_version": "scoped_user_gate_fallback_v0",
        "kind": "scoped_user_gate_fallback",
        "notify_user": True,
        "requires_user_action": True,
        "reason": reason,
        "blocked_user_gate": compact_todo_summary_item(gate_to_surface, text=gate_text),
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


def _first_executable_todo_text(agent_todo_summary: dict[str, Any] | None) -> str | None:
    if not isinstance(agent_todo_summary, dict):
        return None
    raw_items = agent_todo_summary.get("first_executable_items")
    items = raw_items if isinstance(raw_items, list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not _todo_item_is_actionable_open(item):
            continue
        if _todo_task_class(item) != TODO_TASK_CLASS_ADVANCEMENT:
            continue
        text = _protocol_action_text(item.get("text"), limit=320)
        if text:
            return text
    return None


def _first_monitor_todo_text(agent_todo_summary: dict[str, Any] | None) -> str | None:
    if not isinstance(agent_todo_summary, dict):
        return None
    for key in ("monitor_due_items", "monitor_open_items"):
        raw_items = agent_todo_summary.get(key)
        items = raw_items if isinstance(raw_items, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            if not _todo_item_is_actionable_open(item):
                continue
            if _todo_task_class(item) != TODO_TASK_CLASS_MONITOR:
                continue
            text = _protocol_action_text(item.get("text"), limit=320)
            if text:
                return text
    return None


def _agent_scoped_user_todo_override(
    *,
    state: str,
    item: dict[str, Any],
    user_todo_summary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    agent_identity: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if state != "operator_gate":
        return None
    if not isinstance(agent_identity, dict):
        return None
    if not isinstance(user_todo_summary, dict):
        return None
    # A non-blocking user_action may still need a reminder without preserving
    # an operator gate inherited from a different agent's user_gate.
    if _open_user_gate_todo_items(user_todo_summary):
        return None
    other_gate_count = _open_todo_count(
        {"open_count": user_todo_summary.get("other_agent_scoped_open_count")}
    )
    if item.get("operator_question") or item.get("missing_gates"):
        return None
    selected_action = _first_executable_todo_text(agent_todo_summary)
    if not selected_action:
        selected_action = _first_monitor_todo_text(agent_todo_summary)
    agent_id = normalize_todo_claimed_by(agent_identity.get("agent_id"))
    if other_gate_count > 0:
        if not selected_action:
            return None
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
        }
    other_action_count = _open_todo_count(
        {
            "open_count": user_todo_summary.get(
                "other_agent_bound_user_action_open_count"
            )
        }
    )
    if other_action_count <= 0:
        return None
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
        "quota_patch": {
            "safe_bypass_allowed": False,
            "safe_bypass_kind": None,
            "blocked_action_scope": None,
        },
        "item_patch": {
            "status": "active_state_agent_todo" if selected_action else "agent_scope_wait",
            "waiting_on": "codex" if selected_action else "agent_scope",
            "recommended_action": (
                selected_action or "wait for work or a user action bound to this agent"
            ),
        },
    }
    if selected_action:
        result["selected_action"] = selected_action
    return result


def _count_advancement_items(items: Any, *, claimed_by: str | None = None) -> int:
    return agent_scope_count_advancement_items(items, claimed_by=claimed_by)


def _selectable_unclaimed_advancement_count(
    agent_todo_summary: dict[str, Any],
) -> int:
    executable_items = agent_todo_summary.get("executable_backlog_items")
    source_items = (
        executable_items
        if isinstance(executable_items, list)
        else agent_todo_summary.get("unclaimed_priority_open_items")
    )
    return _count_advancement_items(source_items, claimed_by="__unclaimed__")


def _first_compact_todo_id(items: Any) -> str | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        todo_id = normalize_todo_id(item.get("todo_id"))
        if todo_id:
            return todo_id
    return None


def _agent_lane_frontier_hint(
    *,
    goal_id: str,
    agent_identity: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    agent_lane_next_action: dict[str, Any] | None,
    agent_scope_frontier: dict[str, Any] | None,
    work_lane_contract: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not _agent_identity_has_scoped_lane(agent_identity):
        return None
    agent_id = normalize_todo_claimed_by(agent_identity.get("agent_id"))
    if not agent_id:
        return None

    def build_hint(
        decision: AgentLaneFrontierHintDecision,
        *,
        source: str,
        reason_code: str,
        target_todo_id: str | None = None,
        quiet_noop_allowed: bool,
        next_cli_action: str | None = None,
    ) -> dict[str, Any]:
        hint: dict[str, Any] = {
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

    if isinstance(agent_lane_next_action, dict):
        selected_by = str(agent_lane_next_action.get("selected_by") or "")
        claim_required = agent_lane_next_action.get("claim_required_before_work") is True
        target_todo_id = normalize_todo_id(agent_lane_next_action.get("todo_id"))
        if selected_by == "unclaimed_todo" or claim_required:
            action = None
            if target_todo_id:
                action = (
                    f"loopx todo claim --goal-id {goal_id} --todo-id {target_todo_id} "
                    f"--claimed-by {agent_id} --agent-id {agent_id}"
                )
            return build_hint(
                AgentLaneFrontierHintDecision.CLAIM_UNOWNED_IN_SCOPE,
                source="agent_lane_next_action",
                reason_code="unclaimed_advancement_selected",
                target_todo_id=target_todo_id,
                quiet_noop_allowed=False,
                next_cli_action=action,
            )

    if work_lane_contract_is_due_monitor_attempt(work_lane_contract):
        return None
    if work_lane_contract_requires_current_agent_attempt(work_lane_contract):
        return None

    frontier = agent_scope_frontier if isinstance(agent_scope_frontier, dict) else {}
    frontier_action = _agent_scope_frontier_action(frontier.get("action"))
    if frontier_action == AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED:
        cleared_todo_id = _first_compact_todo_id(
            frontier.get("cleared_without_successor_handoff_gates")
        )
        if cleared_todo_id:
            return build_hint(
                AgentLaneFrontierHintDecision.RECORD_NO_FOLLOWUP,
                source="agent_scope_frontier",
                reason_code="cleared_handoff_without_successor",
                target_todo_id=cleared_todo_id,
                quiet_noop_allowed=False,
                next_cli_action=(
                    f"loopx todo complete --goal-id {goal_id} --todo-id {cleared_todo_id} "
                    f"--agent-id {agent_id} --no-follow-up "
                    "--evidence '<public-safe rationale>'"
                ),
            )
        monitor_candidates = (
            frontier.get("monitor_blocked_resume_candidates")
            if isinstance(frontier.get("monitor_blocked_resume_candidates"), list)
            else []
        )
        monitor_candidate = (
            monitor_candidates[0]
            if monitor_candidates and isinstance(monitor_candidates[0], dict)
            else {}
        )
        monitor_blocker_id = normalize_todo_id(
            monitor_candidate.get("blocking_monitor_todo_id")
        )
        if monitor_candidate:
            target_todo_id = normalize_todo_id(monitor_candidate.get("todo_id"))
            return build_hint(
                AgentLaneFrontierHintDecision.ADD_NEXT_ADVANCEMENT,
                source="agent_scope_frontier",
                reason_code="resume_blocked_by_open_monitor",
                target_todo_id=target_todo_id or monitor_blocker_id,
                quiet_noop_allowed=False,
                next_cli_action=(
                    f"loopx todo complete --goal-id {goal_id} --todo-id {monitor_blocker_id} "
                    f"--agent-id {agent_id} --evidence '<validated gate evidence>'"
                    if monitor_blocker_id
                    else None
                ),
            )
        deferred_todo_id = _first_compact_todo_id(frontier.get("deferred_resume_candidates"))
        route_todo_id = _first_compact_todo_id(
            frontier.get("route_continuation_replan_candidates")
        )
        route_candidates = (
            frontier.get("route_continuation_replan_candidates")
            if isinstance(frontier.get("route_continuation_replan_candidates"), list)
            else []
        )
        route_candidate = (
            route_candidates[0]
            if route_candidates and isinstance(route_candidates[0], dict)
            else {}
        )
        route_target = (
            route_todo_id
            or normalize_todo_id(route_candidate.get("route_id"))
            or normalize_todo_id(route_candidate.get("route_key"))
        )
        if route_candidate:
            return build_hint(
                AgentLaneFrontierHintDecision.ADD_NEXT_ADVANCEMENT,
                source="agent_scope_frontier",
                reason_code="route_continuation_replan_required",
                target_todo_id=route_target,
                quiet_noop_allowed=False,
                next_cli_action=(
                    f"loopx todo add --goal-id {goal_id} --role agent "
                    "--text '<public-safe route continuation advancement todo>'"
                ),
            )
        return build_hint(
            AgentLaneFrontierHintDecision.ADD_NEXT_ADVANCEMENT,
            source="agent_scope_frontier",
            reason_code="successor_replan_required",
            target_todo_id=deferred_todo_id,
            quiet_noop_allowed=False,
        )

    if frontier_action in {
        AgentScopeFrontierAction.AGENT_SCOPE_WAIT,
        AgentScopeFrontierAction.REASSIGNMENT_REQUIRED,
    }:
        blocked_successor_todo_id = _first_compact_todo_id(
            frontier.get("blocked_successor_wait_candidates")
        )
        if blocked_successor_todo_id:
            return build_hint(
                AgentLaneFrontierHintDecision.QUIET_NOOP_BLOCKER,
                source="agent_scope_frontier",
                reason_code="blocked_successor_resume_pending",
                target_todo_id=blocked_successor_todo_id,
                quiet_noop_allowed=True,
            )
        blocker_todo_id = _first_compact_todo_id(frontier.get("blocking_handoff_gates"))
        if not blocker_todo_id:
            blocker_todo_id = _first_compact_todo_id(frontier.get("other_agent_claimed_items"))
        if blocker_todo_id:
            return build_hint(
                AgentLaneFrontierHintDecision.QUIET_NOOP_BLOCKER,
                source="agent_scope_frontier",
                reason_code="blocked_by_other_agent_frontier",
                target_todo_id=blocker_todo_id,
                quiet_noop_allowed=True,
            )
        return build_hint(
            AgentLaneFrontierHintDecision.ADD_NEXT_ADVANCEMENT,
            source="agent_scope_frontier",
            reason_code="no_current_agent_advancement_todo",
            quiet_noop_allowed=True,
        )

    if frontier_action == AgentScopeFrontierAction.AGENT_SCOPE_EXHAUSTED:
        return build_hint(
            AgentLaneFrontierHintDecision.ADD_NEXT_ADVANCEMENT,
            source="agent_scope_frontier",
            reason_code="agent_scope_exhausted",
            quiet_noop_allowed=True,
        )

    if not isinstance(agent_todo_summary, dict):
        return None
    current_advancement_count = int(
        agent_todo_summary.get("current_agent_claimed_advancement_count") or 0
    )
    current_monitor_count = int(agent_todo_summary.get("current_agent_claimed_monitor_count") or 0)
    unclaimed_count = _selectable_unclaimed_advancement_count(agent_todo_summary)
    lane = str(work_lane_contract.get("lane") or "") if isinstance(work_lane_contract, dict) else ""
    if current_advancement_count == 0 and unclaimed_count == 0 and current_monitor_count > 0:
        return build_hint(
            AgentLaneFrontierHintDecision.QUIET_NOOP_BLOCKER,
            source="agent_todo_summary",
            reason_code="only_current_agent_monitor_work_remains",
            quiet_noop_allowed=lane != TODO_TASK_CLASS_ADVANCEMENT,
        )
    return None


def _agent_scope_deferred_resume_candidates(
    agent_todo_summary: dict[str, Any],
    *,
    agent_id: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in (
        "current_agent_deferred_resume_candidates",
        "unclaimed_deferred_resume_candidates",
    ):
        value = agent_todo_summary.get(key)
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
    if not candidates:
        value = agent_todo_summary.get("deferred_resume_candidates")
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                if not agent_scope_item_claimed_by_agent_or_unclaimed(
                    item,
                    agent_id=agent_id,
                ):
                    continue
                candidates.append(item)

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        if todo_item_excludes_agent(item, agent_id=agent_id):
            continue
        if item.get("resume_ready") is not True:
            continue
        if not todo_item_is_deferred(item):
            continue
        identity = str(item.get("todo_id") or item.get("index") or item.get("text") or "")
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    return sorted(compact_todo_summary_items(unique, strip_text=True), key=_todo_projection_sort_key)


def _agent_scope_monitor_blocked_resume_candidates(
    agent_todo_summary: dict[str, Any] | None,
    *,
    agent_id: str | None,
) -> list[dict[str, Any]]:
    if not isinstance(agent_todo_summary, dict):
        return []
    candidates: list[dict[str, Any]] = []
    if agent_id:
        for key in (
            "current_agent_monitor_blocked_resume_candidates",
            "unclaimed_monitor_blocked_resume_candidates",
        ):
            value = agent_todo_summary.get(key)
            if isinstance(value, list):
                candidates.extend(item for item in value if isinstance(item, dict))
    else:
        value = agent_todo_summary.get("monitor_blocked_resume_candidates")
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        if todo_item_excludes_agent(item, agent_id=agent_id):
            continue
        if _todo_task_class(item) != TODO_TASK_CLASS_ADVANCEMENT:
            continue
        if item.get("resume_ready") is not False:
            continue
        # The typed resume-planning owner has already diagnosed and selected
        # this repair lane. This consumer keeps executor scope and presentation,
        # not a second interpretation of condition target/class/status.
        identity = str(item.get("todo_id") or item.get("index") or item.get("text") or "")
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    compacts = compact_todo_summary_items(unique, strip_text=True)
    for item, compact in zip(unique, compacts, strict=True):
        if item.get("blocking_monitor_todo_id"):
            compact["blocking_monitor_todo_id"] = item["blocking_monitor_todo_id"]
    return sorted(compacts, key=_todo_projection_sort_key)


def _agent_scope_cleared_without_successor_handoff_gates(
    agent_todo_summary: dict[str, Any],
    *,
    agent_id: str,
) -> list[dict[str, Any]]:
    unique = _agent_scope_handoff_gates_by_state(
        agent_todo_summary,
        agent_id=agent_id,
        gate_state=HandoffGateState.CLEARED_WITHOUT_SUCCESSOR.value,
        preferred_key="current_agent_cleared_without_successor_handoff_gates",
    )
    return sorted(
        unique,
        key=lambda item: (
            -int(item.get("index") or 0),
            str(item.get("todo_id") or ""),
        ),
    )


def _agent_scope_handoff_gates_by_state(
    agent_todo_summary: dict[str, Any],
    *,
    agent_id: str | None,
    gate_state: str,
    preferred_key: str,
) -> list[dict[str, Any]]:
    if not agent_id:
        return []
    gates = agent_todo_summary.get(preferred_key)
    if not isinstance(gates, list):
        gates = agent_todo_summary.get("handoff_gates")
    if not isinstance(gates, list):
        return []
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in gates:
        if not isinstance(item, dict):
            continue
        if agent_id not in agent_scope_item_excluded_agents(item):
            continue
        if item.get("gate_state") != gate_state:
            continue
        identity = str(item.get("todo_id") or item.get("index") or item.get("text") or "")
        if identity in seen:
            continue
        seen.add(identity)
        compact = dict(item)
        compact["text"] = str(item.get("text") or "").strip()
        selected.append(compact)
    return selected


def agent_scope_blocking_handoff_gates(
    agent_todo_summary: dict[str, Any],
    *,
    agent_id: str | None,
) -> list[dict[str, Any]]:
    selected = _agent_scope_handoff_gates_by_state(
        agent_todo_summary,
        agent_id=agent_id,
        gate_state=HandoffGateState.BLOCKING.value,
        preferred_key="current_agent_handoff_gates",
    )
    return sorted(selected, key=_todo_projection_sort_key)


def _agent_scope_blocking_handoff_gates(
    agent_todo_summary: dict[str, Any],
    *,
    agent_id: str,
) -> list[dict[str, Any]]:
    return agent_scope_blocking_handoff_gates(agent_todo_summary, agent_id=agent_id)


def _route_continuation_candidate_matches_agent(
    item: dict[str, Any],
    *,
    agent_id: str,
) -> bool:
    return agent_scope_item_matches_agent_or_unclaimed(item, agent_id=agent_id)


def _agent_scope_route_continuation_replan_candidates(
    agent_todo_summary: dict[str, Any],
    *,
    agent_id: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in (
        "current_agent_route_continuation_replan_candidates",
        "unclaimed_route_continuation_replan_candidates",
    ):
        value = agent_todo_summary.get(key)
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
    if not candidates:
        value = agent_todo_summary.get("route_continuation_replan_candidates")
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                if not _route_continuation_candidate_matches_agent(item, agent_id=agent_id):
                    continue
                candidates.append(item)

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        if not _route_continuation_candidate_matches_agent(
            item,
            agent_id=agent_id,
        ):
            continue
        if item.get("route_continuation_replan_required") is False:
            continue
        identity = str(
            item.get("todo_id")
            or item.get("route_id")
            or item.get("route_key")
            or item.get("index")
            or item.get("text")
            or ""
        )
        if not identity or identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    compacts = compact_todo_summary_items(unique, strip_text=True)
    for item, compact in zip(unique, compacts, strict=True):
        compact["route_continuation_replan_required"] = True
        if item.get("route_continuation_reason") is not None:
            compact["route_continuation_reason"] = item.get("route_continuation_reason")
        if item.get("route_id") is not None:
            compact["route_id"] = item.get("route_id")
        if item.get("route_key") is not None:
            compact["route_key"] = item.get("route_key")
    return sorted(compacts, key=_todo_projection_sort_key)


@dataclass(frozen=True)
class _AgentScopeNoCandidateContext:
    agent_id: str
    summary: dict[str, Any]
    current_agent_count: int
    unclaimed_count: int
    has_advancement_contract: bool

    @property
    def candidate_counts(self) -> dict[str, int]:
        return {
            "current_agent_claimed_advancement_count": self.current_agent_count,
            "unclaimed_advancement_count": self.unclaimed_count,
        }


def _selected_candidate_priority_frontier(
    *,
    agent_id: str,
    summary: dict[str, Any],
    selected: dict[str, Any],
) -> dict[str, Any] | None:
    candidates = _agent_scope_deferred_resume_candidates(summary, agent_id=agent_id)
    if not candidates:
        return None
    first = candidates[0]
    if _todo_projection_sort_key(first)[0] >= _todo_projection_sort_key(selected)[0]:
        return None
    candidate_todo_id = str(first.get("todo_id") or "").strip() or "<todo_id>"
    selected_todo_id = str(selected.get("todo_id") or "").strip() or "<open_todo_id>"
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
        candidate_counts={
            "deferred_resume_candidate_count": len(candidates),
            "preempted_open_candidate_count": 1,
        },
        extra_fields={
            "deferred_resume_candidates": candidates[:3],
            "preempted_open_candidate": compact_todo_summary_item(
                selected,
                text=str(selected.get("text") or "").strip(),
            ),
            "priority_preemption": True,
        },
    )


def _agent_scope_advancement_counts(
    summary: dict[str, Any],
    *,
    agent_id: str,
) -> tuple[int, int]:
    current = max(
        int(summary.get("current_agent_claimed_advancement_count") or 0),
        _count_advancement_items(
            summary.get("current_agent_claimed_advancement_items"),
            claimed_by=agent_id,
        ),
    )
    unclaimed = _selectable_unclaimed_advancement_count(summary)
    executable_items = summary.get("executable_backlog_items")
    if isinstance(executable_items, list):
        current = max(
            current,
            _count_advancement_items(executable_items, claimed_by=agent_id),
        )
        unclaimed = _count_advancement_items(
            executable_items,
            claimed_by="__unclaimed__",
        )
    return current, unclaimed


def _monitor_blocked_resume_frontier(
    context: _AgentScopeNoCandidateContext,
) -> dict[str, Any] | None:
    candidates = _agent_scope_monitor_blocked_resume_candidates(
        context.summary,
        agent_id=context.agent_id,
    )
    if not candidates:
        return None
    first = candidates[0]
    candidate_id = str(first.get("todo_id") or "").strip() or "<todo_id>"
    monitor_id = (
        str(first.get("blocking_monitor_todo_id") or "").strip()
        or "<monitor_todo_id>"
    )
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
        candidate_counts={
            **context.candidate_counts,
            "monitor_blocked_resume_candidate_count": len(candidates),
        },
        extra_fields={"monitor_blocked_resume_candidates": candidates[:3]},
    )


def _deferred_resume_frontier(
    context: _AgentScopeNoCandidateContext,
) -> dict[str, Any] | None:
    candidates = _agent_scope_deferred_resume_candidates(
        context.summary,
        agent_id=context.agent_id,
    )
    if not candidates:
        return None
    candidate_id = str(candidates[0].get("todo_id") or "").strip() or "<todo_id>"
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
        candidate_counts={
            **context.candidate_counts,
            "deferred_resume_candidate_count": len(candidates),
        },
        extra_fields={"deferred_resume_candidates": candidates[:3]},
    )


def _blocked_successor_wait_frontier(
    context: _AgentScopeNoCandidateContext,
) -> dict[str, Any] | None:
    candidates = project_todo_resume_planning(
        context.summary,
        agent_id=context.agent_id,
    )["blocked_successor_items"]
    if not candidates:
        return None
    first = candidates[0]
    candidate_id = str(first.get("todo_id") or "").strip() or "<todo_id>"
    resume_when = str(first.get("resume_when") or "").strip()
    return build_agent_scope_frontier_payload(
        agent_id=context.agent_id,
        action=AgentScopeFrontierAction.AGENT_SCOPE_WAIT,
        quiet_noop_allowed=True,
        spend_policy="no quota spend while an exact successor resume condition is pending",
        reason=(
            f"current agent {context.agent_id} has exact blocked successor {candidate_id} "
            f"waiting on {resume_when}; another successor replan would duplicate the "
            "existing route"
        ),
        recommended_action=(
            f"Keep {context.agent_id} active but quiet until {resume_when} becomes ready; "
            "then resume ordinary todo/deferred-successor routing automatically."
        ),
        requires_replan=False,
        candidate_counts={
            **context.candidate_counts,
            "blocked_successor_wait_count": len(candidates),
        },
        extra_fields={"blocked_successor_wait_candidates": candidates[:3]},
    )


def _route_continuation_frontier(
    context: _AgentScopeNoCandidateContext,
) -> dict[str, Any] | None:
    candidates = _agent_scope_route_continuation_replan_candidates(
        context.summary,
        agent_id=context.agent_id,
    )
    if not candidates:
        return None
    first = candidates[0]
    route_label = (
        str(
            first.get("route_id")
            or first.get("route_key")
            or first.get("todo_id")
            or ""
        ).strip()
        or "<route>"
    )
    return build_agent_scope_frontier_payload(
        agent_id=context.agent_id,
        action=AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED,
        quiet_noop_allowed=False,
        spend_policy="spend once after validated route continuation replan/todo writeback",
        reason=(
            f"current agent {context.agent_id} has no open current/unclaimed advancement "
            "candidate, but the route continuation projection requires a successor "
            f"replan for {route_label}"
        ),
        recommended_action=(
            "Run a bounded route continuation replan before quiet wait: create or claim "
            f"the next concrete {context.agent_id}/unclaimed advancement todo for "
            f"{route_label}, or record an explicit no-follow-up rationale."
        ),
        requires_replan=True,
        candidate_counts={
            **context.candidate_counts,
            "route_continuation_replan_candidate_count": len(candidates),
        },
        extra_fields={"route_continuation_replan_candidates": candidates[:3]},
    )


def _blocking_handoff_frontier(
    context: _AgentScopeNoCandidateContext,
) -> dict[str, Any] | None:
    gates = _agent_scope_blocking_handoff_gates(
        context.summary,
        agent_id=context.agent_id,
    )
    if not gates:
        return None
    claimants = sorted(
        {
            claimed_by
            for item in gates
            for claimed_by in [agent_scope_item_claimed_by(item)]
            if claimed_by
        }
    )
    owner = ", ".join(claimants) or "the owning agent"
    return build_agent_scope_frontier_payload(
        agent_id=context.agent_id,
        action=AgentScopeFrontierAction.AGENT_SCOPE_WAIT,
        quiet_noop_allowed=True,
        spend_policy="no quota spend while the current agent has no in-scope runnable candidate",
        reason=(
            f"current agent {context.agent_id} has no current/unclaimed advancement "
            f"candidate; blocking handoff work is claimed by {owner}"
        ),
        recommended_action=(
            f"Keep {context.agent_id} active but quiet: wait for {owner} to finish the "
            "blocking handoff, reassign it, or create a concrete current-agent/"
            "unclaimed advancement todo before delivery."
        ),
        candidate_counts={
            **context.candidate_counts,
            "blocking_handoff_gate_count": len(gates),
        },
        extra_fields={
            "other_claimants": claimants,
            "blocking_handoff_claimants": claimants,
            "blocking_handoff_gates": gates[:3],
        },
    )


def _cleared_handoff_frontier(
    context: _AgentScopeNoCandidateContext,
) -> dict[str, Any] | None:
    gates = _agent_scope_cleared_without_successor_handoff_gates(
        context.summary,
        agent_id=context.agent_id,
    )
    if not gates:
        return None
    first = gates[0]
    blocker_id = str(first.get("todo_id") or "").strip() or "<todo_id>"
    unblocks_id = normalize_todo_id(first.get("unblocks_todo_id"))
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
        candidate_counts={
            **context.candidate_counts,
            "cleared_without_successor_handoff_count": len(gates),
        },
        extra_fields={"cleared_without_successor_handoff_gates": gates[:3]},
    )


def _other_agent_or_exhausted_frontier(
    context: _AgentScopeNoCandidateContext,
) -> dict[str, Any] | None:
    claimed_items = context.summary.get("claimed_advancement_open_items")
    claimed_items = claimed_items if isinstance(claimed_items, list) else []
    other_items = [
        item
        for item in claimed_items
        if isinstance(item, dict)
        and _todo_item_is_actionable_open(item)
        and _todo_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
        and agent_scope_item_claimed_by(item) not in {None, "", context.agent_id}
    ]
    if not context.has_advancement_contract and not other_items:
        return None
    other_claimants = sorted(
        {
            claimed_by
            for item in other_items
            for claimed_by in [agent_scope_item_claimed_by(item)]
            if claimed_by
        }
    )
    blocking_claimants = sorted(
        {
            claimed_by
            for item in other_items
            if context.agent_id in agent_scope_item_excluded_agents(item)
            for claimed_by in [agent_scope_item_claimed_by(item)]
            if claimed_by
        }
    )
    if blocking_claimants:
        action = AgentScopeFrontierAction.AGENT_SCOPE_WAIT
        owner = ", ".join(blocking_claimants)
        reason = (
            f"current agent {context.agent_id} has no current/unclaimed advancement "
            f"candidate; blocking handoff work is claimed by {owner}"
        )
        recommended_action = (
            f"Keep {context.agent_id} active but quiet: wait for {owner} to finish the "
            "blocking handoff, reassign it, or create a concrete current-agent/"
            "unclaimed advancement todo before delivery."
        )
    elif other_items:
        action = AgentScopeFrontierAction.REASSIGNMENT_REQUIRED
        owner = ", ".join(other_claimants) or "the owning agent"
        reason = (
            f"current agent {context.agent_id} has no current/unclaimed advancement "
            f"candidate; visible advancement work is claimed by {owner}"
        )
        recommended_action = (
            f"Keep {context.agent_id} active but quiet: wait for {owner} to finish, "
            "reassign, or create a concrete current-agent/unclaimed advancement todo "
            "before delivery."
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
    claim_scope = context.summary.get("claim_scope")
    claim_scope = claim_scope if isinstance(claim_scope, dict) else {}
    return build_agent_scope_frontier_payload(
        agent_id=context.agent_id,
        action=action,
        quiet_noop_allowed=True,
        spend_policy="no quota spend while the current agent has no in-scope runnable candidate",
        reason=reason,
        recommended_action=recommended_action,
        candidate_counts={
            **context.candidate_counts,
            "other_agent_claimed_advancement_count": len(other_items),
            "other_agent_claimed_open_count": int(
                claim_scope.get("other_agent_claimed_open_count") or 0
            ),
        },
        extra_fields={
            "other_claimants": other_claimants,
            "blocking_handoff_claimants": blocking_claimants,
            "other_agent_claimed_items": compact_todo_summary_items(other_items[:3], strip_text=True),
        },
    )


_AGENT_SCOPE_NO_CANDIDATE_RULES = (
    _monitor_blocked_resume_frontier,
    _deferred_resume_frontier,
    _blocked_successor_wait_frontier,
    _route_continuation_frontier,
    _blocking_handoff_frontier,
    _cleared_handoff_frontier,
    _other_agent_or_exhausted_frontier,
)


def _agent_scope_no_candidate_frontier(
    *,
    agent_identity: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    agent_lane_next_action: dict[str, Any] | None,
    work_lane_contract: dict[str, Any] | None,
    candidate_should_run: bool,
) -> dict[str, Any] | None:
    if not candidate_should_run or not _agent_identity_has_scoped_lane(agent_identity):
        return None
    agent_id = normalize_todo_claimed_by(agent_identity.get("agent_id"))
    if not agent_id or not isinstance(agent_todo_summary, dict):
        return None
    if work_lane_contract_is_due_monitor_attempt(work_lane_contract):
        return None
    if isinstance(agent_lane_next_action, dict):
        return _selected_candidate_priority_frontier(
            agent_id=agent_id,
            summary=agent_todo_summary,
            selected=agent_lane_next_action,
        )
    if work_lane_contract_requires_current_agent_attempt(work_lane_contract):
        return None

    current_count, unclaimed_count = _agent_scope_advancement_counts(
        agent_todo_summary,
        agent_id=agent_id,
    )
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
        frontier = rule(context)
        if frontier is not None:
            return frontier
    return None
