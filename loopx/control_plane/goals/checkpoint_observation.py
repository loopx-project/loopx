"""Project checkpoint history without loading the checkpoint writer."""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from ..coordination.local_authority import read_canonical_todos_if_promoted
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..todos.active_state_todo_parser import parse_todo_source
from ..todos.path_resolution import resolve_todo_state_path


class CheckpointReadContextRejected(ValueError):
    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(result["error"])
        self.code = result["error_code"]
        self.payload = {"checkpoint_read_context": result}


def delivery_observation_todos(root: Path, goal_id: str, registry_path: Path | None) -> list[dict[str, Any]]:
    canonical = read_canonical_todos_if_promoted(runtime_root=root, goal_id=goal_id)
    if canonical is not None:
        return cast(list[dict[str, Any]], canonical.get("todos", []))
    if registry_path is None:
        return []
    _, state = resolve_todo_state_path(registry_path=registry_path, goal_id=goal_id)
    active, archived, _ = parse_todo_source(state.read_text(encoding="utf-8"))
    return [*active["user"], *active["agent"], *archived]


def pending_first_delivery_progress(
    root: Path, goal_id: str, agent_id: str | None = None, *, registry_path: Path | None = None,
) -> dict[str, Any] | None:
    """Read historical Turn facts without granting admission or repair authority."""
    try:
        todos = [{key: todo.get(key) for key in (
            "todo_id", "status", "no_followup", "completion_continuation",
            "completion_requested_continuation", "completion_turn_key",
        )} for todo in delivery_observation_todos(root, goal_id, registry_path)]
    except (ValueError, OSError):
        todos = None
    try:
        return cast(dict[str, Any] | None, effect_runtime_result("turn.first_delivery.observe", {
            "runtime_root": str(root.resolve()), "goal_id": goal_id,
            "agent_id": agent_id, "todos": todos,
            # Preserve the historical scan order across Python and Node hosts.
            "receipt_names": [path.name for path in (root / "goals" / goal_id / "checkpoint-contexts").glob("*.json")],
        }))
    except EffectRuntimeRejected as error:
        raise CheckpointReadContextRejected({
            "ok": False, "error": str(error), "error_code": error.diagnostic_code, "reread_required": False,
        }) from error
