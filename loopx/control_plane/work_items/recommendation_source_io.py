"""Source I/O for the existing TS recommendation owner; no task write authority."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Required, TypedDict, cast

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ...file_lock import exclusive_cross_runtime_file_lock
from ...history import load_index, load_registry
from ...registry import registry_goals
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..runtime.local_state_write_correctness import stable_write_digest
from ..runtime.runtime_projection_route import resolve_goal_source_runtime_route
from ..runtime.time import chronology_key
from ..todos.frontier_revision import FRONTIER_REVISION_FIELDS
from ..todos.projection_document import TodoProjectionDocument


class LaneRecommendationContext(TypedDict, total=False):
    basis: Required[str]
    admitted: bool
    selected_todo: dict[str, object] | None
    resolution: dict[str, object]
    error: str
    error_code: str


class RecommendationWritebackRejected(ValueError):
    def __init__(self, result: Mapping[str, object]) -> None:
        super().__init__(str(result["error"]))
        self.code = result["error_code"]
        self.payload: dict[str, object] = {"recommendation_writeback": dict(result)}


def load_recommendation_source_goal(
    registry_path: Path,
    goal_id: str,
    *,
    registry: Mapping[str, object] | None = None,
) -> tuple[Path, dict[str, object] | None]:
    invoked_registry = registry_path.expanduser().resolve()
    invoked_payload: dict[str, object] = (
        dict(registry) if registry is not None else dict(load_registry(invoked_registry))
    )
    route = resolve_goal_source_runtime_route(
        registry_path=invoked_registry,
        goal_id=goal_id,
        registry=invoked_payload,
    )
    source_registry = Path(str(route["source_registry"])).resolve()
    if source_registry == invoked_registry:
        matching = next(
            (
                dict(goal)
                for goal in registry_goals(invoked_payload)
                if isinstance(goal, Mapping) and str(goal.get("id")) == str(goal_id)
            ),
            None,
        )
        return source_registry, matching
    loaded = load_goal_from_registry(source_registry, goal_id)
    return source_registry, dict(loaded) if loaded is not None else None


def recommendation_source_context(
    goal: Mapping[str, object],
    state_text: str,
    *,
    source_registry: Path | None,
    todo_fields: Mapping[str, object] | None = None,
) -> dict[str, object]:
    # This is a source-facts read fence, never a canonical Goal intent revision.
    # Todo regions and compatibility Next Action are not independent intent.
    if not state_text and todo_fields is not None:
        from ..todos.projection_document import recovered_todo_projection_skeleton
        # Read the same degraded display the canonical projection owner will
        # deliver. Repairing a missing display is not an intent/step transition.
        state_text = recovered_todo_projection_skeleton(str(goal["id"]))
    narrative = TodoProjectionDocument.parse(state_text).narrative
    lines = narrative.splitlines()
    preserved: list[str] = []
    in_next_action = False
    for line in lines:
        if line.startswith("## "):
            in_next_action = line.strip() == "## Next Action"
        if not in_next_action and not line.startswith("updated_at:"):
            preserved.append(line)
    raw_agent_todos = (todo_fields or {}).get("agent_todos")
    agent_todos = dict(raw_agent_todos) if isinstance(raw_agent_todos, Mapping) else {}
    return {
        "goal_id": goal["id"],
        "registered_agents": registered_agent_ids_for_goal(dict(goal)),
        "source_revision": "sha256:" + stable_write_digest({
            "source_registry": str(source_registry.resolve()) if source_registry else None,
            "goal": {
                key: goal.get(key)
                for key in ("id", "goal_instance_id", "status", "repo", "state_file", "authority_sources")
            },
            "narrative": "\n".join(preserved).strip(),
            "acceptance_contract": agent_todos.get("goal_acceptance_contract"),
        }),
    }


def recommendation_runs(runtime_root: Path, goal_id: str) -> list[dict[str, object]]:
    rows, _ = load_index(runtime_root / "goals" / goal_id / "runs" / "index.jsonl")
    return [
        dict(row)
        for _, row in sorted(
            enumerate(rows),
            key=lambda item: (*chronology_key(item[1].get("generated_at")), item[0]),
            reverse=True,
        )
    ]


def recommendation_run_decision(row: Mapping[str, object]) -> dict[str, object] | None:
    # Journal decoding only. The TS owner validates source, actor and task binding.
    from .refresh_recommendation import RECOMMENDED_ACTION_SOURCE_EXPLICIT

    resolution = row.get("recommended_action_resolution")
    if not isinstance(resolution, Mapping):
        return None
    if resolution.get("recommended_action_source") == RECOMMENDED_ACTION_SOURCE_EXPLICIT:
        # Preserve the same committed decision identity in full and retained
        # history. A derived observation does not retire or mint a step.
        return {
            **dict(resolution),
            "agent_id": row.get("agent_id"),
            "goal_id": row.get("goal_id"),
            "journal_source": {key: row.get(key) for key in ("generated_at", "json_path")},
        }
    return dict(resolution) if resolution.get("step_revision") else None


def latest_bound_recommendation(
    runs: list[dict[str, object]],
    agent_id: str,
) -> dict[str, object] | None:
    for row in runs:
        if row.get("agent_id") == agent_id:
            decision = recommendation_run_decision(row)
            if decision is not None:
                return decision
    return None


def lane_recommendation_context(
    source: Mapping[str, object],
    *,
    agent_id: str | None,
    selected_todo: Mapping[str, object] | None,
    task: Mapping[str, object] | None,
    prior_resolution: Mapping[str, object] | None = None,
    write: Mapping[str, object] | None = None,
    settlement_identity: Mapping[str, object] | None = None,
) -> LaneRecommendationContext:
    # Reuse the existing frontier's semantic fact vocabulary. Display indexes,
    # selection reasons and bounded text decorations are not task revisions.
    facts = (
        {
            key: task[key]
            for key in (*FRONTIER_REVISION_FIELDS, "updated_at", "completed_at", "goal_acceptance_guard")
            if task.get(key) is not None
        }
        if task is not None
        else None
    )
    try:
        result = effect_runtime_result("work_item.refresh_recommendation.lane", {
            **dict(source),
            "agent_id": agent_id,
            "selected_todo": dict(selected_todo) if selected_todo is not None else None,
            "task_facts": facts,
            "prior_resolution": dict(prior_resolution) if prior_resolution is not None else None,
            "write": dict(write) if write is not None else None,
            "settlement_identity": dict(settlement_identity) if settlement_identity is not None else None,
        })
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from error
    if not isinstance(result, dict) or not isinstance(result.get("basis"), str):
        raise RuntimeError("invalid TypeScript lane recommendation result")
    if write is not None and result.get("admitted") is not True:
        raise RecommendationWritebackRejected(result)
    return cast(LaneRecommendationContext, result)


def project_agent_next_actions(
    goal: Mapping[str, object],
    todo_fields: Mapping[str, object],
    *,
    source: Mapping[str, object],
    runs: list[dict[str, object]],
) -> list[dict[str, object]]:
    from ..agents.agent_lane_recommendation import build_agent_lane_next_action
    raw_summary = todo_fields.get("agent_todos")
    summary = dict(raw_summary) if isinstance(raw_summary, Mapping) else {}
    raw_items = summary.get("items")
    tasks: dict[str, Mapping[str, object]] = {
        str(item["todo_id"]): item
        for item in (raw_items if isinstance(raw_items, list) else [])
        if isinstance(item, Mapping) and item.get("todo_id")
    }
    routes: list[dict[str, object]] = []
    for agent in registered_agent_ids_for_goal(dict(goal)):
        selected = build_agent_lane_next_action(
            agent_identity={"agent_id": agent},
            agent_todo_summary=summary,
            capability_gate=None,
            active_next_action=[],
        )
        if selected:
            raw_todo_id = selected.get("todo_id")
            context = lane_recommendation_context(
                source,
                agent_id=agent,
                selected_todo=selected,
                task=tasks.get(str(raw_todo_id)) if raw_todo_id is not None else None,
                prior_resolution=latest_bound_recommendation(runs, agent),
            )
            raw_route = context.get("selected_todo")
            route = dict(raw_route) if isinstance(raw_route, Mapping) else {}
            routes.append({
                key: route[key]
                for key in ("agent_id", "todo_id", "text", "next_step", "next_action_basis")
                if key in route
            })
    return routes


@contextmanager
def recommendation_source_guard(
    registry_path: Path,
    state_path: Path,
    goal_id: str,
    *,
    source_registry: Path,
) -> Iterator[tuple[dict[str, object] | None, str]]:
    # Registration/configuration use the same registry lock. Quota's outer
    # index/source guard precedes this short registry -> state critical section.
    # Release before projection/global sync to avoid recursive registry locking.
    # Configuration locks source before its shared projection. Keep that order,
    # and revalidate the projection's route under both locks before using it.
    with ExitStack() as stack:
        stack.enter_context(exclusive_cross_runtime_file_lock(source_registry, operation="recommendation-writeback"))
        if registry_path.resolve() != source_registry.resolve():
            stack.enter_context(exclusive_cross_runtime_file_lock(registry_path, operation="recommendation-writeback"))
        current_source, goal = load_recommendation_source_goal(registry_path, goal_id)
        if current_source != source_registry.resolve():
            raise ValueError("Next Action source registry changed; read current status and rejudge before retrying")
        stack.enter_context(exclusive_cross_runtime_file_lock(state_path, operation="recommendation-writeback"))
        yield goal, state_path.read_text(encoding="utf-8") if state_path.exists() else ""
