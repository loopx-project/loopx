"""Locked compatibility inputs for the typed handoff transition planner.

The caller holds the active-state mutex; local leases share their existing
mutex. Retired Todo event sources are rejected before quiescence is projected.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any


@contextmanager
def handoff_mode_source(
    *, registry_path: Path, goal_id: str, state_path: Path, state_text: str,
    runtime_root: Path,
) -> Iterator[dict[str, Any]]:
    from ..goals.legacy_event_source import require_no_legacy_todo_events, RetiredTodoEventSourceError
    from ...file_lock import exclusive_cross_runtime_file_lock
    from ..runtime.time import now_local_iso
    from ..work_items.task_lease import read_lease, task_lease_dir, task_lease_lock_path
    from .goal_todo_projection import project_goal_todo_items
    from .handoff_mode import HandoffModeError

    from ...history import load_registry
    from ...registry import find_registry_goal
    goal = find_registry_goal(load_registry(registry_path), goal_id) or {"id": goal_id}
    with ExitStack() as stack:
        stack.enter_context(exclusive_cross_runtime_file_lock(
            task_lease_lock_path(runtime_root=runtime_root, goal_id=goal_id), operation="handoff_mode_set"))
        try:
            require_no_legacy_todo_events(goal, state_path=state_path)
            todos = project_goal_todo_items(goal, state_text=state_text,
                state_path=state_path, rollout_events=[])
        except (OSError, RetiredTodoEventSourceError) as error:
            raise HandoffModeError("cannot establish handoff quiescence from the legacy source",
                code="handoff_mode_source_unavailable") from error
        fields = ("todo_id", "done", "status", "claimed_by", "archive_state")
        leases = []
        for path in sorted(task_lease_dir(runtime_root=runtime_root, goal_id=goal_id).glob("todo_*.json")):
            lease = read_lease(path)
            if lease is not None:
                leases.append({**lease, "lease_path": str(path)})
        yield {"todos": [{key: item[key] for key in fields if key in item} for item in todos],
            "leases": leases, "observed_at": now_local_iso()}
