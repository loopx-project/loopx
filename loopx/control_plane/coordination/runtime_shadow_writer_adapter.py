"""Transaction-capture orchestration for legacy Todo and lease writers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...history import load_registry
from ...registry import find_registry_goal
from . import local_authority_shadow_outbox as outbox
from .local_authority_shadow_projection import LEASE_PARTITION
from .runtime_shadow import resolve_coordination_runtime_shadow_config
from .shadow_management import ShadowManagementError, read_shadow_capture_binding, require_shadow_primary_write_allowed


class ActiveStateAuthorityMutationError(ValueError):
    """A prose-only writer attempted to change canonical coordination state."""

    code = "active_state_authority_mutation_forbidden"
    payload = {"primary_writeback_preserved": True}


def require_prose_state_write_allowed(
    *, registry_path: Path, runtime_root: Path, goal_id: str, state_path: Path,
    original_text: str, planned_text: str,
) -> None:
    """Under S, enforce source maintenance and the owned prose-only invariant."""

    from .legacy_writer_fence import require_registry_source_write_allowed
    require_registry_source_write_allowed(
        registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
        state_file=state_path, canonical_mutation=False,
    )

    from ...rollout_event_log import load_rollout_events, rollout_event_log_path
    from ..todos.todo_index import MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL
    from .local_authority_shadow_adapter import todo_partition_projector
    from .local_authority_shadow_projection import partition_comparison_view

    try:
        goal = find_registry_goal(load_registry(registry_path), goal_id)
        events = load_rollout_events(
            rollout_event_log_path(runtime_root, goal_id),
            limit=MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL,
        )
        projector = todo_partition_projector(
            goal, state_path=state_path, rollout_events=events,
        )
        # Todo projections include the read-time resume-condition evaluation
        # clock. Compare the same semantic view used by shadow continuity
        # digests so an owned prose update is not rejected merely because the
        # two projections were evaluated milliseconds apart.
        original_projection = partition_comparison_view(projector(original_text))
        planned_projection = partition_comparison_view(projector(planned_text))
        if original_projection != planned_projection:
            raise ActiveStateAuthorityMutationError(
                "prose update would change canonical Todo or handoff state"
            )
    except ActiveStateAuthorityMutationError:
        raise
    except Exception as error:
        raise ActiveStateAuthorityMutationError(
            "prose update cannot prove that canonical coordination state is unchanged"
        ) from error


def begin_todo_runtime_shadow_capture(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    state_path: Path,
    write_class: str,
    original_text: str,
) -> outbox.TodoPartitionCapture:
    """Create the default-off transaction capture while the Todo lock is held."""

    active_binding = read_shadow_capture_binding(runtime_root, goal_id)["status"] == "active"
    try:
        registry = load_registry(registry_path)
        goal = find_registry_goal(registry, goal_id)
        enabled = active_binding or resolve_coordination_runtime_shadow_config(goal).enabled
        from ...rollout_event_log import load_rollout_events, rollout_event_log_path
        from ..todos.todo_index import MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL
        from .local_authority_shadow_adapter import todo_partition_projector

        events = load_rollout_events(
            rollout_event_log_path(runtime_root, goal_id),
            limit=MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL,
        )
        projector = todo_partition_projector(
            goal,
            state_path=state_path,
            rollout_events=events,
        )
    except Exception:
        enabled = active_binding
        projector = None
    return outbox.TodoPartitionCapture.begin(
        enabled=enabled,
        runtime_root=runtime_root,
        goal_id=goal_id,
        state_path=state_path,
        write_class=write_class,
        original_text=original_text,
        projector=projector,
    )



def require_runtime_shadow_capture_prepared(
    capture: outbox.TodoPartitionCapture, *, runtime_root: Path, goal_id: str,
) -> None:
    """Active lineage cannot admit a primary transition without durable preparation."""

    if capture.outcome.failure is not None and require_shadow_primary_write_allowed(runtime_root, goal_id) is not None:
        raise ShadowManagementError(
            "shadow_capture_prepare_failed",
            "durable shadow preparation failed; the primary state was not changed",
        )


def write_captured_todo_state(
    capture: outbox.TodoPartitionCapture, *, runtime_root: Path, goal_id: str,
    state_path: Path, text: str,
) -> None:
    """Under the primary lock, prepare before replacement and mark only after durability."""

    from ..todos.active_state_editing import atomic_write_state_text

    capture.prepare(text)
    require_runtime_shadow_capture_prepared(capture, runtime_root=runtime_root, goal_id=goal_id)
    atomic_write_state_text(state_path, text)
    capture.committed()


def settle_todo_runtime_shadow_capture(
    payload: dict[str, Any],
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    capture: outbox.TodoPartitionCapture,
    emit_disabled: bool = True,
) -> dict[str, Any]:
    """Boundedly drain one transaction capture after releasing the Todo lock."""

    from .local_authority_shadow_adapter import capture_evidence, drain_local_authority_shadow_outbox

    drain = (
        drain_local_authority_shadow_outbox(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
        )
        if capture.outcome.entry_id is not None
        else None
    )
    if emit_disabled or capture.enabled:
        payload["coordination_runtime_shadow"] = capture_evidence(
            goal_id=goal_id, capture=capture.outcome, drain=drain,
        )
    return payload


def settle_lease_runtime_shadow_capture(
    payload: dict[str, Any],
    *,
    registry_path: Path | None,
    runtime_root: Path,
    goal_id: str,
) -> dict[str, Any]:
    """Turn the TS writer capture receipt into bounded runtime-shadow evidence."""

    raw = payload.pop("coordination_runtime_shadow_capture", None)
    if not isinstance(raw, Mapping) or registry_path is None:
        return payload
    capture = outbox.CaptureOutcome(
        entry_id=str(raw["entry_id"]) if isinstance(raw.get("entry_id"), str) else None,
        partition=LEASE_PARTITION,
        seq=int(raw["seq"]) if isinstance(raw.get("seq"), int) else None,
        source_bytes_digest=(
            str(raw["source_bytes_digest"])
            if isinstance(raw.get("source_bytes_digest"), str)
            else None
        ),
        failure=dict(raw["failure"]) if isinstance(raw.get("failure"), Mapping) else None,
        skipped_reason=str(raw["skipped_reason"]) if raw.get("skipped_reason") else None,
    )
    from .local_authority_shadow_adapter import (
        capture_evidence,
        drain_local_authority_shadow_outbox,
    )

    drain = (
        drain_local_authority_shadow_outbox(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
        )
        if capture.entry_id is not None
        else None
    )
    payload["coordination_runtime_shadow"] = capture_evidence(
        goal_id=goal_id,
        capture=capture,
        drain=drain,
    )
    return payload


__all__ = [
    "begin_todo_runtime_shadow_capture",
    "settle_lease_runtime_shadow_capture",
    "settle_todo_runtime_shadow_capture",
]
