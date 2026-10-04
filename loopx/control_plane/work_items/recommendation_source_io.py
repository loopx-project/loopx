"""Source I/O for the existing TS recommendation owner; no task write authority."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ...file_lock import exclusive_cross_runtime_file_lock
from ...history import load_index
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..runtime.local_state_write_correctness import stable_write_digest
from ..runtime.runtime_projection_route import resolve_goal_source_runtime_route
from ..runtime.time import chronology_key
from ..todos.frontier_revision import FRONTIER_REVISION_FIELDS
from ..todos.projection_document import TodoProjectionDocument


class RecommendationWritebackRejected(ValueError):
    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(result["error"])
        self.code = result["error_code"]
        self.payload = {"recommendation_writeback": result}


def load_recommendation_source_goal(registry_path: Path, goal_id: str) -> tuple[Path, dict[str, Any] | None]:
    route = resolve_goal_source_runtime_route(registry_path=registry_path, goal_id=goal_id)
    source_registry = Path(route["source_registry"]).resolve()
    return source_registry, load_goal_from_registry(source_registry, goal_id)


def recommendation_source_context(goal: dict[str, Any], state_text: str, *, source_registry: Path | None, todo_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    # This is a source-facts read fence, never a canonical Goal intent revision.
    # Todo regions and compatibility Next Action are not independent intent.
    if not state_text and todo_fields is not None:
        from ..todos.projection_document import recovered_todo_projection_skeleton
        # Read the same degraded display the canonical projection owner will
        # deliver. Repairing a missing display is not an intent/step transition.
        state_text = recovered_todo_projection_skeleton(goal["id"])
    narrative = TodoProjectionDocument.parse(state_text).narrative
    lines = narrative.splitlines()
    preserved = []
    in_next_action = False
    for line in lines:
        if line.startswith("## "):
            in_next_action = line.strip() == "## Next Action"
        if not in_next_action and not line.startswith("updated_at:"):
            preserved.append(line)
    return {
        "goal_id": goal["id"], "registered_agents": registered_agent_ids_for_goal(goal),
        "source_revision": "sha256:" + stable_write_digest({
            "source_registry": str(source_registry.resolve()) if source_registry else None,
            "goal": {key: goal.get(key) for key in ("id", "goal_instance_id", "status", "repo", "state_file", "authority_sources")},
            "narrative": "\n".join(preserved).strip(),
            "acceptance_contract": ((todo_fields or {}).get("agent_todos") or {}).get("goal_acceptance_contract"),
        }),
    }


def recommendation_runs(runtime_root: Path, goal_id: str) -> list[dict[str, Any]]:
    rows, _ = load_index(runtime_root / "goals" / goal_id / "runs" / "index.jsonl")
    return [row for _, row in sorted(enumerate(rows),
        key=lambda item: (*chronology_key(item[1].get("generated_at")), item[0]), reverse=True)]


def latest_bound_recommendation(runs: list[dict[str, Any]], agent_id: str) -> dict[str, Any] | None:
    # Journal decoding only. The TS owner validates source, actor and task binding.
    for row in runs:
        resolution = row.get("recommended_action_resolution")
        if row.get("agent_id") == agent_id and isinstance(resolution, dict) and resolution.get("step_revision"):
            return resolution
    return None


def lane_recommendation_context(source: dict[str, Any], *, agent_id: str | None,
    selected_todo: dict[str, Any] | None, task: dict[str, Any] | None,
    prior_resolution: dict[str, Any] | None = None, write: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Reuse the existing frontier's semantic fact vocabulary. Display indexes,
    # selection reasons and bounded text decorations are not task revisions.
    facts = {key: task[key] for key in (*FRONTIER_REVISION_FIELDS, "updated_at", "completed_at", "goal_acceptance_guard")
             if task.get(key) is not None} if task is not None else None
    try:
        result = effect_runtime_result("work_item.refresh_recommendation.lane", {
            **source, "agent_id": agent_id, "selected_todo": selected_todo,
            "task_facts": facts, "prior_resolution": prior_resolution, "write": write,
        })
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from error
    if not isinstance(result, dict) or not isinstance(result.get("basis"), str):
        raise RuntimeError("invalid TypeScript lane recommendation result")
    if write is not None and result.get("admitted") is not True:
        raise RecommendationWritebackRejected(result)
    return result


def project_agent_next_actions(goal: dict[str, Any], todo_fields: dict[str, Any],
    *, source: dict[str, Any], runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from ..agents.agent_lane_recommendation import build_agent_lane_next_action
    summary = todo_fields.get("agent_todos") or {}
    tasks = {item["todo_id"]: item for item in summary.get("items") or [] if item.get("todo_id")}
    routes = []
    for agent in registered_agent_ids_for_goal(goal):
        selected = build_agent_lane_next_action(
            agent_identity={"agent_id": agent}, agent_todo_summary=summary,
            capability_gate=None, active_next_action=[],
        )
        if selected:
            context = lane_recommendation_context(source, agent_id=agent,
                selected_todo=selected, task=tasks.get(selected.get("todo_id")),
                prior_resolution=latest_bound_recommendation(runs, agent))
            route = context["selected_todo"]
            routes.append({key: route[key] for key in ("agent_id", "todo_id", "text", "next_step", "next_action_basis") if key in route})
    return routes


@contextmanager
def recommendation_source_guard(
    registry_path: Path, state_path: Path, goal_id: str,
    *, source_registry: Path,
) -> Iterator[tuple[dict[str, Any] | None, str]]:
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
