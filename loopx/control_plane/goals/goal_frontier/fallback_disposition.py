from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ...todos.contract import (
    normalize_todo_claimed_by,
    normalize_todo_excluded_agents,
    normalize_todo_id,
    normalize_todo_resume_when,
    normalize_todo_status,
)
from ...effect_runtime import effect_runtime_result
from ...todos.resume_planning import build_todo_resume_planning_request
from ...todos.todo_semantics import (
    agent_scoped_selectable_advancement_todo_ids,
    todo_item_has_removed_continuation_policy,
    todo_item_task_class,
)
from ...todos.resume_condition import (
    build_todo_resume_evaluation_request,
    normalize_todo_generation,
)
from ..goal_vision_read_model import (
    VISION_FRONTIER_TODO_DELTA_ACTIONS as VISION_FRONTIER_TODO_DELTA_ACTIONS,
    VISION_TODO_DELTA_ID_LIMIT as VISION_TODO_DELTA_ID_LIMIT,
    parse_vision_todo_delta_entries as parse_vision_todo_delta_entries,
)
from ..goal_vision_state import goal_vision_state_is_closed

# create/reopen entries are bounded successor declarations and resolve the
# fallback disposition on their own; activate/resume/retain entries only link
# the vision to existing Todos and still need a selectable frontier match.
VISION_TODO_DELTA_SUCCESSOR_ACTIONS = frozenset({"create", "reopen"})
VISION_FALLBACK_DECLARATION_ENTRY_LIMIT = 4
VISION_FALLBACK_GAP_TRIGGER = "vision_fallback_unresolved"
VISION_FALLBACK_GAP_REASON_CODE = "declared_fallback_without_runnable_or_terminal"
VISION_FALLBACK_LOOKUP_UNCERTAIN_TRIGGER = "vision_fallback_lookup_uncertain"
VISION_FALLBACK_LOOKUP_UNCERTAIN_REASON_CODE = (
    "declared_fallback_authoritative_lookup_unavailable"
)
VISION_FALLBACK_TERMINAL_PATH_OUTCOME = "stop"
VISION_FALLBACK_RECOMMENDED_ACTION = (
    "resolve the declared fallback direction: link or retain a runnable "
    "successor Todo referencing it, declare a bounded create/reopen "
    "successor, or record an explicit terminal no-follow-up disposition; "
    "do not invent a user gate"
)
VISION_FALLBACK_LOOKUP_UNCERTAIN_ACTION = (
    "retry the fallback disposition from the complete canonical Todo source; "
    "do not infer absence from a bounded presentation lane or invent a user gate"
)


class FallbackTodoReadState(Enum):
    UNAVAILABLE = "unavailable"


# None is an omitted source from a legacy caller; UNAVAILABLE is a failed
# authority read, which compact display evidence must not override.
FallbackTodoSource = list[dict[str, Any]] | FallbackTodoReadState | None


@dataclass(frozen=True)
class FallbackDeclaration:
    """Structured declaration of a fallback direction and its associated work."""

    declaration_id: str
    target_todo_id: str | None = None
    successor_todo_id: str | None = None

    @property
    def candidate_todo_ids(self) -> set[str]:
        return {
            todo_id
            for todo_id in (
                self.target_todo_id,
                self.successor_todo_id,
            )
            if todo_id
        }

    @property
    def unresolved_todo_id(self) -> str:
        return self.target_todo_id or self.successor_todo_id or self.declaration_id


def _compact_text(value: Any, *, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def parse_fallback_declarations(
    agent_vision: dict[str, Any] | None,
) -> list[FallbackDeclaration]:
    """Parse typed fallback declarations written by the TS Vision contract.

    The only supported authoring path is ``agent_vision.fallback_declarations``
    as validated and persisted by the TS-owned ``goal.vision_checkpoint``
    prepare (and mirrored through the status/shared-runtime compact read
    model). Prose mentions, generic ``todo_delta`` actions, and legacy alias
    shapes are not declarations.
    """

    declarations: list[FallbackDeclaration] = []
    if not isinstance(agent_vision, dict):
        return declarations
    source = agent_vision.get("fallback_declarations")
    if not isinstance(source, list):
        return declarations

    seen: set[tuple[str, str | None, str | None]] = set()
    for raw in source[:VISION_FALLBACK_DECLARATION_ENTRY_LIMIT]:
        if not isinstance(raw, dict):
            continue
        declaration_id = _compact_text(
            raw.get("declaration_id"),
            limit=VISION_TODO_DELTA_ID_LIMIT,
        )
        if not declaration_id:
            continue
        target_todo_id = normalize_todo_id(raw.get("target_todo_id"))
        successor_todo_id = normalize_todo_id(raw.get("successor_todo_id"))
        key = (declaration_id, target_todo_id, successor_todo_id)
        if key in seen:
            continue
        seen.add(key)
        declarations.append(
            FallbackDeclaration(
                declaration_id=declaration_id,
                target_todo_id=target_todo_id,
                successor_todo_id=successor_todo_id,
            )
        )
    return declarations


def select_fallback_source_items(
    source: list[dict[str, Any]], requested: set[str],
) -> FallbackTodoSource:
    """Bound transport for every caller, including already-loaded writeback sources."""
    if not isinstance(source, list) or any(not isinstance(item, dict) for item in source):
        return FallbackTodoReadState.UNAVAILABLE
    by_id: dict[str, list[dict[str, Any]]] = {}
    for item in source:
        todo_id = normalize_todo_id(item.get("todo_id"))
        if todo_id:
            by_id.setdefault(todo_id, []).append(item)
    selected = set(requested)
    for todo_id in requested:
        for item in by_id.get(todo_id, []):
            resume = normalize_todo_resume_when(item.get("resume_when"))
            kind, _, target = (resume or "").partition(":")
            if kind in {"todo_done", "monitor_changed"}:
                dependency = normalize_todo_id(target)
                if dependency:
                    selected.add(dependency)
    # Four declarations name at most eight alternatives and eight direct
    # dependencies. Do not follow dependency chains or re-read another revision.
    if any(len(by_id.get(todo_id, [])) > 1 for todo_id in selected):
        return FallbackTodoReadState.UNAVAILABLE
    return [by_id[todo_id][0] for todo_id in sorted(selected) if todo_id in by_id]


def declared_fallback_gap_from_agent_vision(
    agent_vision: dict[str, Any] | None,
    *,
    agent_todo_summary: dict[str, Any] | None,
    agent_id: str | None,
    agent_todo_source_items: FallbackTodoSource = None,
    rollout_events: list[dict[str, Any]] | None = None,
    available_capabilities: Any = None,
) -> dict[str, Any] | None:
    """Encode persisted facts; TypeScript owns the advisory disposition."""
    declarations = parse_fallback_declarations(agent_vision)
    if not declarations:
        return None
    assert isinstance(agent_vision, dict)
    summary = agent_todo_summary if isinstance(agent_todo_summary, dict) else {}
    source = agent_todo_source_items
    if isinstance(source, list):
        source = select_fallback_source_items(source, {
            todo_id for entry in declarations for todo_id in entry.candidate_todo_ids
        })
    items = [
        {
            **item,
            "todo_id": normalize_todo_id(item.get("todo_id")),
            "status": normalize_todo_status(item.get("status")) or "open",
            "task_class": todo_item_task_class(item),
            "claimed_by": normalize_todo_claimed_by(item.get("claimed_by")),
            "excluded_agents": normalize_todo_excluded_agents(item.get("excluded_agents")),
            "done": item.get("done") is True,
            "removed_continuation": todo_item_has_removed_continuation_policy(item),
            "resume_when": normalize_todo_resume_when(item.get("resume_when")),
            "resume_monitor_generation": normalize_todo_generation(item.get("resume_monitor_generation")),
        }
        for item in source if isinstance(item, dict) and normalize_todo_id(item.get("todo_id"))
    ] if isinstance(source, list) else []
    resume_request = build_todo_resume_evaluation_request(
        items, source_items=items, rollout_events=rollout_events,
        available_capabilities=available_capabilities,
    )
    # Reuse the resume codec's bounded evidence, never send arbitrary Todo prose
    # or raw rollout events into this read-only projection.
    facts = [
        {**row, **{key: item[key] for key in
                   ("excluded_agents", "done", "removed_continuation")}}
        for row, item in zip(resume_request["source_items"], items, strict=True)
    ]
    path = agent_vision.get("path_delta")
    path = path if isinstance(path, dict) else {}
    result = effect_runtime_result("goal.fallback_disposition.project", {
        "schema_version": "goal_fallback_disposition_request_v0",
        "terminal": goal_vision_state_is_closed(agent_vision.get("state"))
        or str(path.get("outcome") or "").strip().lower() == VISION_FALLBACK_TERMINAL_PATH_OUTCOME,
        "agent_id": normalize_todo_claimed_by(agent_id),
        "blocker_present": bool(summary.get("current_agent_blocker_items")),
        "resume_planning": build_todo_resume_planning_request(summary, agent_id=agent_id),
        "source_state": "complete" if isinstance(source, list) else
            "unavailable" if source is FallbackTodoReadState.UNAVAILABLE else "omitted",
        "items": facts,
        "resume_evaluation": {
            key: value for key, value in resume_request.items()
            if key not in {"items", "source_items"}
        },
        "legacy_selectable_ids": sorted(agent_scoped_selectable_advancement_todo_ids(
            summary, agent_id=agent_id,
        )) if source is None else [],
        "declarations": [
            {"candidate_ids": sorted(entry.candidate_todo_ids),
             "unresolved_id": entry.unresolved_todo_id}
            for entry in declarations
        ],
        "created_or_reopened_ids": sorted({
            todo_id for action, todo_id in parse_vision_todo_delta_entries(agent_vision.get("todo_delta"))
            if action in VISION_TODO_DELTA_SUCCESSOR_ACTIONS
        }),
    })
    if not isinstance(result, dict) or result.get("schema_version") != "goal_fallback_disposition_v0":
        raise RuntimeError("TypeScript fallback disposition shape mismatch")
    if result["kind"] == "resolved":
        return None
    uncertain = result["kind"] == VISION_FALLBACK_LOOKUP_UNCERTAIN_TRIGGER
    gap = {
        "kind": result["kind"], "source": "latest_agent_vision",
        "agent_id": agent_vision.get("agent_id"), "state": agent_vision.get("state"),
        "reason_code": VISION_FALLBACK_LOOKUP_UNCERTAIN_REASON_CODE if uncertain else VISION_FALLBACK_GAP_REASON_CODE,
        "recommended_action": VISION_FALLBACK_LOOKUP_UNCERTAIN_ACTION if uncertain else VISION_FALLBACK_RECOMMENDED_ACTION,
    }
    for key in ("unresolved_todo_ids", "lookup_uncertain_todo_ids"):
        if result[key]:
            gap[key] = result[key]
    generated_at = _compact_text(agent_vision.get("generated_at"), limit=80)
    if generated_at:
        gap["generated_at"] = generated_at
    return {key: value for key, value in gap.items() if value is not None}
