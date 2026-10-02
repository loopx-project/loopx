from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ...file_lock import (
    cross_runtime_lock_witness,
    exclusive_cross_runtime_file_lock,
    exclusive_file_lock,
    exclusive_run_index_lock,
)
from ..projects.registry_codec import SOURCE_SESSION_PROFILE_ID


QUOTA_SOURCE_ADMISSION_SCHEMA = "loopx_quota_source_admission_v0"


def _planned_goal_ref(
    value: Mapping[str, Any] | None,
    *,
    goal_id: str,
) -> dict[str, str] | None:
    if value is None:
        return None
    from ..goals.source_session_registry_state import exact_goal_ref

    planned = exact_goal_ref(
        str(value.get("goal_id") or ""),
        str(value.get("goal_instance_id") or ""),
    )
    if planned["goal_id"] != goal_id:
        raise ValueError("quota GoalRef does not match goal_id")
    return planned


def _uses_source_profile(
    registry_path: Path | None,
    goal_id: str,
) -> bool:
    if registry_path is None or not registry_path.is_file():
        return False
    from ..goals.first_party_host_admission import source_goal_authority

    return source_goal_authority(registry_path, goal_id).get("kind") in {
        "present",
        "absent",
    }


@contextmanager
def quota_accounting_admission(
    *,
    runtime_root: Path,
    registry_path: Path | None,
    goal_id: str,
    goal_ref: Mapping[str, Any] | None,
    operation: str,
    lock_legacy_index: bool = True,
    handoff_legacy_index: bool = False,
) -> Iterator[dict[str, Any] | None]:
    """Hold quota's index/source locks until the TypeScript owner adopts them."""

    root = runtime_root.expanduser().resolve()
    registry = registry_path.expanduser().resolve() if registry_path else None
    planned = _planned_goal_ref(goal_ref, goal_id=goal_id)
    source_profile = _uses_source_profile(registry, goal_id)
    if not source_profile:
        if planned is not None:
            raise ValueError("quota GoalRef requires a source-session registry")
        if not lock_legacy_index:
            yield None
            return
        index_path = root / "goals" / goal_id / "runs" / "index.jsonl"
        lock = (
            exclusive_run_index_lock
            if handoff_legacy_index
            else exclusive_file_lock
        )
        with lock(index_path, operation=operation):
            yield None
        return
    if planned is None or registry is None:
        raise ValueError("source-session quota commit requires an exact GoalRef")

    from ..goals.first_party_host_admission import source_goal_authority
    from ..goals.source_session_registry_state import guard_path

    index_path = root / "goals" / goal_id / "runs" / "index.jsonl"
    source_target = guard_path(registry, goal_id)
    with exclusive_run_index_lock(index_path, operation=operation):
        with exclusive_cross_runtime_file_lock(
            source_target,
            operation=operation,
        ):
            yield {
                "schema_version": QUOTA_SOURCE_ADMISSION_SCHEMA,
                "profile_id": SOURCE_SESSION_PROFILE_ID,
                "registry_path": str(registry),
                "planned_goal_ref": planned,
                "authority": source_goal_authority(registry, goal_id),
                "locks": [
                    {
                        "role": "run_index",
                        **cross_runtime_lock_witness(index_path),
                    },
                    {
                        "role": "source_guard",
                        **cross_runtime_lock_witness(source_target),
                    },
                ],
            }
