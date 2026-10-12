"""Bracket local registry fact projection before sending it to TS authority.

The witness remains unchanged across a validation effect and its commit retry.
The TS transaction rechecks it; this adapter grants no authority and holds no
registry lock while user validation runs.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from .local_authority_shadow_projection import source_effect_runtime_result as effect_runtime_result


@contextmanager
def authority_registry_source(registry_path: Path) -> Iterator[dict[str, str]]:
    path = registry_path.resolve()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    yield {"path": str(path), "sha256": digest}
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("Todo authority registration changed while reading; retry")


def build_todo_runtime_shadow_projection(
    *,
    goal_id: str,
    todos: object,
    leases: object = None,
    handoff_mode: str = "hard_lease",
) -> dict[str, object]:
    """Build the complete source projection using the capture partition rules."""

    from .local_authority_shadow_projection import project_coordination_source

    return project_coordination_source({
        "kind": "snapshot", "goal_id": goal_id, "handoff_mode": handoff_mode,
        "read_model_schema": "loopx_todo_canonical_read_record_v0",
        "todos": todos, "leases": [] if leases is None else leases,
    })


def capture_todo_archive_dependencies(todos: list[dict[str, Any]], state_text: str) -> list[dict[str, Any]]:
    """Use the same bounded capture for bootstrap and subsequent writer outbox."""
    from ..todos.active_state_todo_parser import parse_todo_source
    from ..todos.contract import normalize_todo_task_class
    from ..todos.todo_summary import structured_todo_item, canonical_todo_read_record

    _, archived, _ = parse_todo_source(state_text)
    # No prose or wide diagnostics cross the selection transport budget.
    capture_fields = ("todo_id", "role", "task_class", "status", "done", "archive_state", "resume_when",
        "decision_scope", "decision_outcome", "global_gate", "blocks_agent", "bound_agent", "goal_bound",
        "successor_todo_ids", "superseded_by", "unblocks_todo_id")
    archive_facts = []
    for item in archived:
        facts = {key: item[key] for key in capture_fields if key in item}
        # Keep the compatibility read class separate from recorded authority.
        # Do not transport private prose or infer a missing role from it.
        if item.get("role") == "agent" and item.get("task_class") is None:
            facts["legacy_task_class"] = normalize_todo_task_class(None,
                text=str(item.get("text") or ""), action_kind=item.get("action_kind"))
        archive_facts.append(facts)
    capture = effect_runtime_result("todo.archive.capture_dependencies", {
        "schema_version": "todo_archive_dependency_capture_request_v2",
        "active": [{key: item[key] for key in capture_fields if key in item} for item in todos],
        "archived": archive_facts,
    })
    if not isinstance(capture, dict) or capture.get("schema_version") != "todo_archive_dependency_capture_result_v1":
        raise ValueError("invalid archived dependency capture result")
    result = list(todos)
    for selected in capture["records"]:
        item = archived[selected["index"]]
        result.append(canonical_todo_read_record(structured_todo_item(
            {**item, "task_class": selected["task_class"]}, role=selected["role"],
            source_section=item["source_section"], archive_state="archive")))
    return result


def todo_partition_projector(
    goal: Mapping[str, Any] | None,
    *,
    state_path: Path,
    rollout_events: list[dict[str, Any]] | None = None,
) -> Callable[[str], dict[str, Any]]:
    """Production projector: parse active-state text into the todos partition."""

    from ..todos.handoff_mode import goal_handoff_mode
    from .local_authority_shadow_projection import todo_partition_projection
    from ..todos.goal_todo_projection import project_goal_todo_items

    goal_record = dict(goal) if isinstance(goal, Mapping) else None
    events = list(rollout_events or [])

    def project(state_text: str) -> dict[str, Any]:
        return todo_partition_projection(
            handoff_mode=goal_handoff_mode(state_text),
            todos=capture_todo_archive_dependencies(project_goal_todo_items(
                goal_record,
                state_text=state_text,
                state_path=state_path,
                rollout_events=events,
            ), state_text),
        )

    return project


def build_runtime_shadow_source_snapshot(
    *, goal: Mapping[str, Any], runtime_root: Path, state_path: Path,
    registry_path: Path,
    include_all_archived_todos: bool = False,
) -> tuple[dict[str, object], dict[str, object]]:
    """Bind the supplied Goal and every derived fact to one registry observation."""
    from ...agent_registry import registered_agent_ids_for_goal
    from ...registry import find_registry_goal
    from ..projects.registry_codec import load_project_registry
    from .shadow_management import ShadowManagementError

    with authority_registry_source(registry_path) as witness:
        registry = load_project_registry(registry_path)
        current = find_registry_goal(registry, str(goal["id"]))
        if current is None or current != dict(goal):
            raise ShadowManagementError("source_registry_changed_retry")
        projection, snapshot = _build_runtime_shadow_source_snapshot(
            goal=current, runtime_root=runtime_root, state_path=state_path,
            registry_path=registry_path, registry=registry,
            include_all_archived_todos=include_all_archived_todos,
        )
        snapshot["registry_source"] = {
            **witness, "registered_agents": registered_agent_ids_for_goal(current),
        }
    return projection, snapshot


def _source_lease_paths(runtime_root: Path, goal_id: str) -> list[Path]:
    """Keep unsafe filesystem sources out of the transport; TS rechecks admission."""
    from .shadow_management import ShadowManagementError

    directory = runtime_root
    for segment in ("goals", goal_id, "task-leases"):
        directory /= segment
        try:
            info = directory.lstat()
        except FileNotFoundError:
            return []
        if not stat.S_ISDIR(info.st_mode):
            raise ShadowManagementError("source_lease_inventory_invalid")
    paths = sorted(path for path in directory.iterdir() if path.name.endswith(".json"))
    for path in paths:
        if re.fullmatch(r"[A-Za-z0-9_.-]+\.json", path.name) is None:
            raise ShadowManagementError("source_lease_inventory_invalid")
    return paths


def _source_lease_bytes(path: Path) -> bytes:
    from .shadow_management import ShadowManagementError

    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ShadowManagementError("source_lease_inventory_invalid")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ShadowManagementError("source_changed_retry")
        data = stream.read()
        after = os.fstat(stream.fileno())
        current = path.lstat()
        if ((current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                or not stat.S_ISREG(current.st_mode) or before.st_size != len(data)
                or after.st_size != len(data) or current.st_size != len(data)
                or before.st_mtime_ns != after.st_mtime_ns or before.st_ctime_ns != after.st_ctime_ns
                or after.st_mtime_ns != current.st_mtime_ns or after.st_ctime_ns != current.st_ctime_ns):
            raise ShadowManagementError("source_changed_retry")
    return data


def _build_runtime_shadow_source_snapshot(
    *, goal: Mapping[str, Any], runtime_root: Path, state_path: Path,
    registry_path: Path, registry: dict[str, Any],
    include_all_archived_todos: bool = False,
) -> tuple[dict[str, object], dict[str, object]]:
    """Project exactly the bytes carried by one ephemeral source precondition.

    TS takes the shared source locks and verifies every byte/inventory before
    publishing a baseline or a bounded qualification result.
    """
    from ...rollout_event_log import ROLLOUT_EVENT_SCHEMA_VERSION, rollout_event_log_path
    from ...paths import resolve_runtime_root
    from ..goals.state_resolution import resolve_goal_state
    from ..goals.legacy_event_source import state_event_log_candidates
    from ..todos.active_state_todo_parser import parse_active_state_todos
    from ..todos.goal_todo_projection import todo_summaries_from_fields
    from ..todos.handoff_mode import goal_handoff_mode
    from .local_authority_shadow_projection import canonical_bytes, compact_lease
    from .shadow_management import ShadowManagementError

    goal_id = str(goal["id"])
    state_path = state_path.expanduser().resolve()
    state_bytes = state_path.read_bytes()
    state_text = state_bytes.decode("utf-8")
    evidence: list[dict[str, object]] = []

    def read_evidence(path: Path) -> bytes | None:
        path = path.expanduser().resolve()
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            data = None
        evidence.append({"path": str(path), "bytes_sha256": None if data is None else "sha256:" + hashlib.sha256(data).hexdigest()})
        return data

    rollout_bytes = read_evidence(rollout_event_log_path(runtime_root, goal_id))
    rollout_events: list[dict[str, Any]] = []
    for line in (rollout_bytes or b"").decode("utf-8").split("\n"):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("schema_version") == ROLLOUT_EVENT_SCHEMA_VERSION:
            rollout_events.append(value)

    # Preserve absent/empty source witnesses; reject retired sources before publishing.
    for path in state_event_log_candidates(dict(goal), state_path=state_path):
        data = read_evidence(path)
        if not data:
            continue
        raise ShadowManagementError("legacy_todo_event_source_retired",
            "legacy_todo_event_source_retired: preserve and export legacy Todo events with a compatible older release before migration")

    if include_all_archived_todos:
        from ..todos.active_state_todo_parser import parse_todo_source
        from ..todos.todo_summary import structured_todo_item, canonical_todo_read_record
        active, archived, _ = parse_todo_source(state_text)
        for item in archived:
            if item.get("role") not in {"agent", "user"}:
                raise ShadowManagementError("cold_source_archive_role_unproved",
                    "Archived Todo role must be explicit; cold inspection cannot infer its owner from prose")
        # A cold inventory reads persisted records, not attention summaries or
        # the live graph's dependency-only archive. Reuse the full record codec
        # for both sections, retaining their original heading and full text.
        todos = [canonical_todo_read_record(structured_todo_item(
            item, role=item["role"], source_section=item["source_section"],
            archive_state=item["archive_state"], text_limit=None))
            for item in [*active["user"], *active["agent"], *archived]]
    else:
        fields = parse_active_state_todos(state_text, goal=dict(goal), state_path=state_path, item_limit=None, rollout_events=rollout_events)
        todos = todo_summaries_from_fields(fields=fields, source="markdown_active_state", rollout_events=rollout_events, roles=["user", "agent"], status=None,
            todo_id=None, agent_id=None, limit=None).todos
        todos = capture_todo_archive_dependencies(todos, state_text)
    leases: list[dict[str, Any]] = []
    inventory: list[dict[str, object]] = []
    for path in _source_lease_paths(runtime_root, goal_id):
        data = _source_lease_bytes(path)
        leases.append(compact_lease(json.loads(data), goal_id=goal_id, file_stem=path.stem))
        inventory.append({"name": path.name, "bytes_sha256": "sha256:" + hashlib.sha256(data).hexdigest()})
    projection = build_todo_runtime_shadow_projection(goal_id=goal_id, todos=todos, leases=leases,
        handoff_mode=goal_handoff_mode(state_text))
    registered_root = resolve_runtime_root(registry, None, registry_path=registry_path)
    _, _, registered_state = resolve_goal_state(registry=registry, goal_id=goal_id,
        project_override=None, state_file_override=None)
    return projection, {"state_path": str(state_path), "registered_runtime_root": str(registered_root.expanduser().absolute()),
        "registered_state_path": str(registered_state.expanduser().resolve()),
        "state_bytes_sha256": "sha256:" + hashlib.sha256(state_bytes).hexdigest(),
        "lease_inventory": inventory, "projection_sha256": hashlib.sha256(canonical_bytes(projection)).hexdigest(),
        "evidence_files": evidence}
