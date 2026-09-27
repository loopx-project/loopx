"""One read-only Todo/lease source per alignment or amendment decision.

Before promotion, parse the existing display and lease files. After promotion,
read both collections at one provider revision; never consult or repair display.
This is an adapter over the canonical summary, not a second Todo inventory.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...registry import resolve_state_file
from ..coordination.local_authority import canonical_todo_summary_fields, read_canonical_todos_if_promoted
from ..todos.active_state_todo_parser import parse_active_state_todos
from ..todos.contract import normalize_todo_excluded_agents
from ..work_items.local_lease_record import TaskLeaseError, read_lease
from ..work_items.task_lease import task_lease_path


@dataclass(frozen=True)
class SharedGoalWorkSource:
    goal_id: str
    state_path: Path
    state_text: str
    items: list[dict[str, Any]]
    canonical_basis: dict[str, Any] | None
    observed_at: str


def read_shared_goal_work_source(*, goal: dict[str, Any], project: Path,
    runtime_root: Path | None) -> SharedGoalWorkSource:
    goal_id = str(goal["id"])
    state_path = resolve_state_file(project, goal.get("state_file"))
    if state_path is None:
        raise ValueError(f"goal state file is missing for {goal_id}")
    canonical = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id,
        include_leases=True) if runtime_root is not None else None
    if canonical is None:
        from .legacy_event_source import require_no_legacy_todo_events
        require_no_legacy_todo_events(goal, state_path=state_path)
        if not state_path.is_file():
            raise ValueError(f"goal state file is missing for {goal_id}")
        state_text = state_path.read_text(encoding="utf-8")
        fields = parse_active_state_todos(state_text, goal=goal, state_path=state_path, item_limit=None)
        basis = None
        leases = None
    else:
        state_text = ""
        fields = canonical_todo_summary_fields(canonical["todos"])
        basis = {"source_authority": canonical["source_authority"],
            "provider_revision": canonical["provider_revision"],
            "records_sha256": canonical["todo_read_model"]["records_sha256"]}
        leases = {lease["todo_id"]: lease for lease in canonical["leases"]}
    summary = fields.get("agent_todos") or {}
    items = []
    for source in summary.get("items") or []:
        item = {key: source.get(key) for key in ("todo_id", "status", "done", "task_class",
            "archive_state", "claimed_by", "bound_agent", "resume_when", "resume_ready", "action_kind")}
        item["excluded_agents"] = normalize_todo_excluded_agents(source.get("excluded_agents"))
        item["lease"] = None
        todo_id = item["todo_id"]
        if todo_id and source.get("claimed_by"):
            try:
                item["lease"] = leases.get(todo_id) if leases is not None else (
                    read_lease(task_lease_path(runtime_root=runtime_root, goal_id=goal_id, todo_id=todo_id))
                    if runtime_root is not None else None)
            except TaskLeaseError:
                # Let the typed selector reject a corrupt *selected* claim.
                # Unrelated historical/peer lease files must not block this Agent.
                item["lease_read_error"] = "corrupt_lease"
        items.append(item)
    return SharedGoalWorkSource(goal_id, state_path, state_text, items, basis,
        datetime.now(timezone.utc).isoformat())
