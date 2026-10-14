from __future__ import annotations

import hashlib
import json
from typing import Any

from ..effect_runtime import effect_runtime_result
from ..todos.todo_semantics import todo_item_excludes_agent
from ..todos.contract import (
    TODO_TASK_CLASS_BLOCKER,
    normalize_todo_claimed_by,
    normalize_todo_id,
    normalize_todo_status,
)
from ..todos.resume_planning import project_todo_resume_planning

GOAL_VISION_WAIT_STATE_SCHEMA_VERSION = "goal_vision_wait_state_v0"
VISION_ACCEPTANCE_GAP_KIND = "vision_acceptance_gap"


def exact_blocked_successor_wait_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    candidate = value
    if value.get("schema_version") != GOAL_VISION_WAIT_STATE_SCHEMA_VERSION:
        raw_candidate = value.get("vision_wait_state")
        candidate = raw_candidate if isinstance(raw_candidate, dict) else {}
        if not candidate:
            raw_projection = value.get("goal_frontier_projection")
            projection = raw_projection if isinstance(raw_projection, dict) else {}
            raw_candidate = projection.get("vision_wait_state")
            candidate = raw_candidate if isinstance(raw_candidate, dict) else {}
    if (
        candidate.get("schema_version") != GOAL_VISION_WAIT_STATE_SCHEMA_VERSION
        or candidate.get("state") != "waiting"
        or candidate.get("reason_code") != "exact_blocked_successor"
        or candidate.get("automatic_resume") is not True
        or not normalize_todo_id(candidate.get("selected_todo_id"))
        or not str(candidate.get("resume_when") or "").strip()
    ):
        return {}
    return candidate


def exact_blocked_successor_frontier_identity(value: Any) -> str | None:
    wait = exact_blocked_successor_wait_state(value)
    if not wait:
        return None
    parts = {
        "agent_id": str(wait.get("agent_id") or "").strip(),
        "reason_code": "exact_blocked_successor",
        "selected_todo_id": normalize_todo_id(wait.get("selected_todo_id")),
        "resume_when": str(wait.get("resume_when") or "").strip(),
    }
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _compact_resume_condition(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact = {
        key: value.get(key)
        for key in (
            "schema_version",
            "resume_when",
            "satisfied",
            "kind",
            "target",
            "target_todo_id",
            "target_status",
            "target_archive_state",
            "target_source_section",
            "target_task_class",
            "target_claimed_by",
            "capability",
            "provider",
            "scheduled_for",
            "evaluated_at",
            "clock_provider",
            "material_change",
            "material_change_generation",
            "invalid_state",
        )
        if value.get(key) is not None
    }
    if value.get("kind") == "resume_at":
        # Keep the complete positive clock proof for the typed interaction
        # owner, including the explicit absence of an already-triggered receipt.
        compact["resume_receipt"] = value.get("resume_receipt")
    compact["satisfied"] = False
    return compact


def _acceptance_gap_causal_todo_ids(
    gaps: list[dict[str, Any]],
) -> set[str]:
    todo_ids: set[str] = set()
    for gap in gaps:
        for key in (
            "todo_id",
            "current_todo_id",
            "blocked_todo_id",
            "completed_todo_id",
            "successor_todo_id",
        ):
            todo_id = normalize_todo_id(gap.get(key))
            if todo_id:
                todo_ids.add(todo_id)
        for key in (
            "vision_todo_ids",
            "lineage_todo_ids",
            "successor_todo_ids",
            "completed_todo_ids",
        ):
            raw_values = gap.get(key)
            values = raw_values if isinstance(raw_values, list) else []
            todo_ids.update(
                todo_id for value in values if (todo_id := normalize_todo_id(value))
            )
    return todo_ids


def _wait_lineage_edges(items: list[dict[str, Any]]) -> list[list[str]]:
    edges: set[tuple[str, str]] = set()
    for item in items:
        todo_id = normalize_todo_id(item.get("todo_id"))
        if not todo_id:
            continue
        for value in item.get("successor_todo_ids") or []:
            successor = normalize_todo_id(value)
            if successor:
                edges.add((todo_id, successor))
        condition = item.get("resume_condition")
        if isinstance(condition, dict) and condition.get("satisfied") is False:
            target = normalize_todo_id(condition.get("target_todo_id"))
            if target:
                # A prerequisite can explain its waiting successor, never the
                # reverse: shared prerequisites do not cover unrelated siblings.
                edges.add((target, todo_id))
    return [list(edge) for edge in sorted(edges)]


def _covered_wait_items(
    *,
    agent_todo_summary: dict[str, Any] | None,
    agent_id: str | None,
    causal_todo_ids: set[str],
    source_items: list[dict[str, Any]] | None,
    lineage_source_items: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if source_items is not None:
        # Source rows have already passed the canonical resume evaluator. Never
        # rebuild them from the quota's bounded deferred/backlog display lanes.
        agent_todo_summary = {
            "items": source_items,
            "deferred_items": [
                i for i in source_items if i.get("status") == "deferred"
            ],
            "resume_blocked_items": [
                i
                for i in source_items
                if i.get("resume_ready") is False and i.get("status") == "open"
            ],
            "current_agent_blocker_items": source_items,
        }

    raw_blockers = (agent_todo_summary or {}).get("current_agent_blocker_items")
    blocker_items = raw_blockers if isinstance(raw_blockers, list) else []
    safe_agent_id = normalize_todo_claimed_by(agent_id)
    blocker_items = [
        item
        for item in blocker_items
        if isinstance(item, dict)
        and safe_agent_id is not None
        and item.get("task_class") == TODO_TASK_CLASS_BLOCKER
        and normalize_todo_status(item.get("status")) == "blocked"
        and str(item.get("reason") or "").strip()
        and normalize_todo_claimed_by(item.get("claimed_by")) == safe_agent_id
        and not todo_item_excludes_agent(item, agent_id=safe_agent_id)
    ]
    candidates = project_todo_resume_planning(
        agent_todo_summary or {}, agent_id=agent_id
    )["blocked_successor_items"]
    coverage = effect_runtime_result(
        "goal.vision_wait.coverage",
        {
            "causal_todo_ids": sorted(causal_todo_ids),
            "waiting_todo_ids": [i["todo_id"] for i in candidates if i.get("todo_id")],
            "blocker_todo_ids": [
                i["todo_id"] for i in blocker_items if i.get("todo_id")
            ],
            "edges": _wait_lineage_edges(
                lineage_source_items
                if lineage_source_items is not None
                else source_items
                if source_items is not None
                else candidates
            ),
        },
    )
    if (
        not isinstance(coverage, dict)
        or coverage.get("schema_version") != "vision_wait_coverage_v0"
    ):
        raise RuntimeError("TypeScript vision wait coverage shape mismatch")
    if coverage.get("covered") is not True:
        return [], []
    witnesses = set(coverage["witness_todo_ids"])
    blocker_items = [i for i in blocker_items if i.get("todo_id") in witnesses]
    candidates = (
        []
        if blocker_items
        else [i for i in candidates if i.get("todo_id") in witnesses]
    )
    return blocker_items, candidates


def build_goal_vision_wait_state(
    *,
    agent_todo_summary: dict[str, Any] | None,
    agent_id: str | None,
    acceptance_gaps: list[dict[str, Any]] | None,
    selectable_advancement_count: int,
    source_items: list[dict[str, Any]] | None = None,
    lineage_source_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Project a temporary vision wait over an authoritative blocked frontier.

    This is deliberately a read model, not a new todo or vision lifecycle
    state. It may defer only ordinary open-vision acceptance gaps. Missing
    checkpoints and closed-stage successor requirements remain strict.
    """

    gaps = [gap for gap in (acceptance_gaps or []) if isinstance(gap, dict)]
    if not gaps or any(gap.get("kind") != VISION_ACCEPTANCE_GAP_KIND for gap in gaps):
        return None
    if selectable_advancement_count > 0:
        return None

    # A gap with no causal link cannot borrow another gap's wait witness.
    if any(not _acceptance_gap_causal_todo_ids([gap]) for gap in gaps):
        return None
    causal_todo_ids = _acceptance_gap_causal_todo_ids(gaps)
    if agent_todo_summary:
        for proof in (agent_todo_summary or {}).get("vision_wait_states") or []:
            if (
                isinstance(proof, dict)
                and proof.get("schema_version") == GOAL_VISION_WAIT_STATE_SCHEMA_VERSION
                and proof.get("state") == "waiting"
                and proof.get("agent_id") == agent_id
                and proof.get("causal_todo_ids") == sorted(causal_todo_ids)
            ):
                return dict(proof)
    blocker_items, candidates = _covered_wait_items(
        agent_todo_summary=agent_todo_summary,
        agent_id=agent_id,
        causal_todo_ids=causal_todo_ids,
        source_items=source_items,
        lineage_source_items=lineage_source_items,
    )
    if candidates:
        selected = candidates[0]
        waiting_todo_ids = [
            todo_id
            for todo_id in (
                normalize_todo_id(item.get("todo_id")) for item in candidates[:5]
            )
            if todo_id
        ]
        payload: dict[str, Any] = {
            "schema_version": GOAL_VISION_WAIT_STATE_SCHEMA_VERSION,
            "state": "waiting",
            "reason_code": "exact_blocked_successor",
            "agent_id": agent_id,
            "waiting_todo_count": len(candidates),
            "waiting_todo_ids": waiting_todo_ids,
            "selected_todo_id": normalize_todo_id(selected.get("todo_id")),
            "selected_todo_status": selected.get("status"),
            "selected_todo_priority": selected.get("priority"),
            "selected_todo_claimed_by": selected.get("claimed_by"),
            "resume_when": selected.get("resume_when"),
            "resume_condition": _compact_resume_condition(
                selected.get("resume_condition")
            ),
            "causal_todo_ids": sorted(causal_todo_ids),
            "deferred_acceptance_gap_count": len(gaps),
            "deferred_acceptance_gap_kinds": [VISION_ACCEPTANCE_GAP_KIND],
            "automatic_resume": True,
            "resume_behavior": (
                "when resume_ready becomes true, restore ordinary open-todo or "
                "deferred-successor routing without closing the active vision"
            ),
        }
        return {key: value for key, value in payload.items() if value is not None}

    if not blocker_items:
        return None
    selected = blocker_items[0]
    waiting_todo_ids = [
        todo_id
        for todo_id in (
            normalize_todo_id(item.get("todo_id")) for item in blocker_items[:5]
        )
        if todo_id
    ]
    payload = {
        "schema_version": GOAL_VISION_WAIT_STATE_SCHEMA_VERSION,
        "state": "waiting",
        "reason_code": "current_agent_blocker",
        "agent_id": agent_id,
        "waiting_todo_count": len(blocker_items),
        "waiting_todo_ids": waiting_todo_ids,
        "selected_todo_id": normalize_todo_id(selected.get("todo_id")),
        "selected_todo_status": selected.get("status"),
        "selected_todo_priority": selected.get("priority"),
        "selected_todo_claimed_by": selected.get("claimed_by"),
        "blocker_reason": str(selected.get("reason") or "").strip(),
        "causal_todo_ids": sorted(causal_todo_ids),
        "deferred_acceptance_gap_count": len(gaps),
        "deferred_acceptance_gap_kinds": [VISION_ACCEPTANCE_GAP_KIND],
        "automatic_resume": False,
        "resume_behavior": (
            "re-evaluate the active vision when the blocker is resolved or "
            "superseded, or when runnable scoped advancement appears"
        ),
    }
    return {key: value for key, value in payload.items() if value is not None}
