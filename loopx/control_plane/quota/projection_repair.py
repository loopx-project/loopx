from __future__ import annotations
from .effective_action import EffectiveAction

import fnmatch
from typing import Any

from ...state_projection import actions_are_projection_aligned, is_user_wait_text
from ..todos.contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
    normalize_required_write_scopes,
)
from ..todos.todo_semantics import (
    todo_item_is_actionable_open,
    todo_item_task_class,
)
from ..work_items.primary_action import protocol_action_text


def open_todo_count(summary: dict[str, Any] | None) -> int:
    if not isinstance(summary, dict):
        return 0
    try:
        return max(0, int(summary.get("open_count") or 0))
    except (TypeError, ValueError):
        return 0


def build_state_projection_gap(
    item: dict[str, Any],
    project_asset: dict[str, Any],
) -> dict[str, Any] | None:
    gap = (
        item.get("state_projection_gap")
        if isinstance(item.get("state_projection_gap"), dict)
        else project_asset.get("state_projection_gap")
        if isinstance(project_asset.get("state_projection_gap"), dict)
        else None
    )
    if not isinstance(gap, dict):
        return None
    if gap.get("requires_todo_expansion") is not True:
        return None
    return revalidate_state_projection_gap(gap)


def revalidate_state_projection_gap(gap: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(gap, dict):
        return None
    evidence_items = gap.get("first_evidence")
    if not isinstance(evidence_items, list) or not evidence_items:
        return gap

    retained: list[dict[str, Any]] = []
    removed_user_wait = False
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        is_user_wait_evidence = (
            item.get("target_role") == "user"
            and item.get("kind") == "next_action_waits_without_user_todo"
        )
        if is_user_wait_evidence and not is_user_wait_text(item.get("text")):
            removed_user_wait = True
            continue
        retained.append(item)

    if not removed_user_wait:
        return gap
    if not retained:
        target_roles = {
            str(role).strip()
            for role in gap.get("target_roles", [])
            if str(role or "").strip()
        }
        return None if target_roles <= {"user"} else gap

    revised = dict(gap)
    revised["first_evidence"] = retained
    revised["evidence_count"] = len(retained)
    revised["target_roles"] = sorted(
        {
            str(item.get("target_role") or "").strip()
            for item in retained
            if str(item.get("target_role") or "").strip()
        }
    )
    return revised


def build_state_projection_gap_repair_hint(
    gap: dict[str, Any] | None,
    *,
    candidate_should_run: bool,
    user_todo_summary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    work_lane_contract: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not gap:
        return None
    if open_todo_count(user_todo_summary) > 0 or open_todo_count(agent_todo_summary) > 0:
        return None
    # A deferred advancement Todo with a pending dated resume is already the
    # concrete projection of Next Action.  It is temporarily unrunnable, not
    # missing, so asking for Todo expansion would defeat its bounded wait.
    evidence = gap.get("first_evidence")
    deferred = (
        agent_todo_summary.get("deferred_items")
        if isinstance(agent_todo_summary, dict)
        else None
    )
    if (
        isinstance(evidence, list)
        and evidence
        and gap.get("evidence_count") == len(evidence)
        and isinstance(deferred, list)
        and all(
            isinstance(entry, dict)
            and entry.get("kind") == "next_action_executable_without_agent_todo"
            and entry.get("target_role") == "agent"
            and any(
                isinstance(item, dict)
                and item.get("task_class") == TODO_TASK_CLASS_ADVANCEMENT
                and item.get("status") == "deferred"
                and item.get("resume_ready") is False
                and str(item.get("resume_when") or "").startswith("resume_at:")
                and actions_are_projection_aligned(entry.get("text"), item.get("text"))
                for item in deferred
            )
            for entry in evidence
        )
    ):
        return None
    must_attempt = bool(
        work_lane_contract
        and work_lane_contract.get("must_attempt_work") is True
    )
    if not candidate_should_run and not must_attempt:
        return None
    return {
        "source": "quota.should-run",
        "trigger": "state_projection_gap",
        "recommended_mode": "repair_state_projection_gap",
        "effective_action": EffectiveAction.STATE_PROJECTION_GAP_REPAIR.value,
        "allowed": True,
        "notify": "DONT_NOTIFY",
        "reason": (
            "active state has actionable Next Action / gate prose but no open "
            "User Todo or Agent Todo projection"
        ),
        "repair_focus": (
            "run one replan/todo-expansion/blocker-writeback slice: convert executable "
            "Next Action into concrete Agent Todo, or convert owner/user gates into "
            "User Todo, then refresh state before normal delivery"
        ),
        "spend_policy": (
            "append exactly one heartbeat spend only after the projection repair is "
            "validated and written back; do not spend for merely reporting the gap"
        ),
        "state_projection_gap": gap,
    }


def write_scope_allowed(required_scope: str, allowed_scopes: list[str]) -> bool:
    required = str(required_scope or "").strip()
    if not required:
        return True
    for allowed in allowed_scopes:
        pattern = str(allowed or "").strip()
        if not pattern:
            continue
        if required == pattern:
            return True
        if fnmatch.fnmatchcase(required, pattern):
            return True
    return False


def build_boundary_projection_repair_hint(
    goal_boundary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    *,
    candidate_should_run: bool,
    capability_gate: dict[str, Any] | None = None,
    selected_todo: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not candidate_should_run or not isinstance(agent_todo_summary, dict):
        return None
    candidate_items: list[dict[str, Any]] = []
    if isinstance(selected_todo, dict) and selected_todo:
        candidate_items.append(selected_todo)
    elif isinstance(capability_gate, dict):
        if capability_gate.get("action") != "run":
            return None
        runnable_candidates = capability_gate.get("runnable_candidates")
        if isinstance(runnable_candidates, list) and runnable_candidates:
            candidate_items.extend(
                item for item in runnable_candidates if isinstance(item, dict)
            )
    if not candidate_items and not selected_todo:
        for key in ("first_executable_items", "first_open_items"):
            value = agent_todo_summary.get(key)
            if isinstance(value, list):
                candidate_items.extend(item for item in value if isinstance(item, dict))
    selected: dict[str, Any] | None = None
    for item in candidate_items:
        if not todo_item_is_actionable_open(item):
            continue
        if todo_item_task_class(item) != TODO_TASK_CLASS_ADVANCEMENT:
            continue
        selected = item
        break
    if not selected:
        return None

    required_scopes = normalize_required_write_scopes(
        selected.get("required_write_scopes")
    )
    if not required_scopes:
        return None
    boundary = goal_boundary if isinstance(goal_boundary, dict) else {}
    allowed_scopes = normalize_required_write_scopes(boundary.get("write_scope"))
    missing_scopes = [
        scope
        for scope in required_scopes
        if not write_scope_allowed(scope, allowed_scopes)
    ]
    if not missing_scopes:
        return None
    selected_text = protocol_action_text(selected.get("text"), limit=220)
    boundary_authority = (
        boundary.get("checkpointed_boundary_authority")
        if isinstance(boundary.get("checkpointed_boundary_authority"), dict)
        else None
    )
    repair: dict[str, Any] = {
        "source": "quota.should-run",
        "trigger": "required_write_scope_missing_from_goal_boundary",
        "recommended_mode": "repair_boundary_projection",
        "effective_action": EffectiveAction.BOUNDARY_PROJECTION_REPAIR.value,
        "blocked_action_scope": "boundary_projection",
        "allowed": True,
        "notify": "DONT_NOTIFY",
        "reason": (
            "selected executable todo requires write scope not present in "
            "goal_boundary.write_scope"
        ),
        "repair_focus": (
            "repair the checkpointed decision projection: either add the approved "
            "scope to goal_boundary.write_scope, rewrite the todo inside the current "
            "boundary, or create a concrete user/controller gate"
        ),
        "spend_policy": (
            "append exactly one heartbeat spend only after boundary projection repair, "
            "todo rewrite, or blocker writeback is validated"
        ),
        "required_write_scopes": required_scopes,
        "allowed_write_scopes": allowed_scopes,
        "missing_write_scopes": missing_scopes,
        "selected_todo": {
            key: selected.get(key)
            for key in ("todo_id", "index", "priority", "task_class", "action_kind")
            if selected.get(key) is not None
        },
        "selected_todo_text": selected_text,
    }
    if boundary_authority:
        repair["checkpointed_boundary_authority"] = {
            "schema_version": boundary_authority.get("schema_version"),
            "active_count": boundary_authority.get("active_count"),
            "inactive_count": boundary_authority.get("inactive_count"),
            "active_write_scope": boundary_authority.get("active_write_scope"),
        }
        inactive_candidates = []
        for entry in boundary_authority.get("entries") or []:
            if not isinstance(entry, dict) or entry.get("active") is True:
                continue
            scopes = normalize_required_write_scopes(entry.get("write_scope"))
            if any(write_scope_allowed(scope, scopes) for scope in missing_scopes):
                inactive_candidates.append(
                    {
                        key: entry.get(key)
                        for key in (
                            "decision_id",
                            "source",
                            "recorded_at",
                            "expires_at",
                            "freshness",
                            "inactive_reasons",
                            "write_scope",
                        )
                        if entry.get(key) is not None
                    }
                )
        if inactive_candidates:
            repair["inactive_authority_candidates"] = inactive_candidates[:3]
    return repair
