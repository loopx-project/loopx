"""Keep Steward observation drafts separate from committed poll snapshots."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
import json
import logging
from pathlib import Path

from ...chat_store import _atomic_write_json
from ...control_plane.goals.first_party_host_admission import capture_first_party_host_goal_ref
from ...control_plane.quota.monitor_poll import QUOTA_MONITOR_POLL_CLASSIFICATION, find_quota_monitor_poll_turn
from ...file_lock import exclusive_file_lock
from .steward_monitor_turns import (
    _MONITOR_TARGET_KEY_RE,
    _coerce_dict,
    read_steward_monitor_scope,
    steward_monitor_scope_path,
    steward_monitor_snapshot_path,
)
from .steward_turn_common import MONITOR_SIGNAL_SCOPE_CHANGED

_LOGGER = logging.getLogger(__name__)
SIGNAL_MONITOR_SNAPSHOT_SCHEMA = "steward_signal_monitor_snapshot_v0"


@contextmanager
def fence_observed_signal_scope(runtime_root: Path, snapshot: Mapping[str, object]) -> Iterator[None]:
    goal_id = str(snapshot["goal_id"])
    target_key = str(snapshot.get("target_key") or "")
    scope_lock = (
        exclusive_file_lock(
            steward_monitor_scope_path(runtime_root, goal_id, target_key), operation="commit_steward_monitor_scope",
        )
        if _MONITOR_TARGET_KEY_RE.fullmatch(target_key) else nullcontext()
    )
    with scope_lock:
        if read_steward_monitor_scope(runtime_root, goal_id, target_key) != snapshot.get("signal_scope"):
            raise ValueError(MONITOR_SIGNAL_SCOPE_CHANGED)
        yield


def _read_snapshot(path: Path) -> dict[str, object]:
    try:
        return _coerce_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _pending_path(latest: Path) -> Path:
    return latest.with_suffix(".pending.json")


def _poll_index_record(runtime_root: Path, snapshot: Mapping[str, object]) -> dict[str, object] | None:
    binding = _coerce_dict(snapshot.get("poll_binding"))
    turn = binding.get("turn_instance_id")
    agent = binding.get("agent_id")
    goal = snapshot.get("goal_id")
    todo = snapshot.get("todo_id")
    if not all(isinstance(value, str) and value for value in (turn, agent, goal, todo)):
        return None
    try:
        return find_quota_monitor_poll_turn(
            runtime_root, goal_id=str(goal), agent_id=str(agent), turn_instance_id=str(turn),
            todo_id=str(todo), goal_ref=_coerce_dict(binding.get("goal_ref")) or None,
        )
    except (OSError, ValueError):
        return None


def _matches_poll(snapshot: Mapping[str, object], poll: Mapping[str, object]) -> bool:
    binding = _coerce_dict(snapshot.get("poll_binding"))
    event = _coerce_dict(poll.get("monitor_event"))
    return (
        poll.get("classification") == QUOTA_MONITOR_POLL_CLASSIFICATION
        and poll.get("goal_id") == snapshot.get("goal_id")
        and poll.get("agent_id") == binding.get("agent_id")
        and poll.get("turn_instance_id") == binding.get("turn_instance_id")
        and poll.get("goal_ref") == binding.get("goal_ref")
        and event.get("todo_id") == snapshot.get("todo_id")
        and event.get("target_key") == snapshot.get("target_key")
        and event.get("result_hash") == snapshot.get("result_hash")
    )


def _has_committed_poll(runtime_root: Path, snapshot: Mapping[str, object]) -> bool:
    record = _poll_index_record(runtime_root, snapshot)
    if record is None:
        return False
    receipt = _coerce_dict(record.get("quota_monitor_poll_commit"))
    if receipt.get("schema_version") != "quota_monitor_poll_commit_receipt_v0" or not receipt.get("effect_id"):
        return False
    artifact = record.get("json_path")
    if not isinstance(artifact, str):
        return False
    runs = runtime_root / "goals" / str(snapshot["goal_id"]) / "runs"
    try:
        poll_path = Path(artifact).resolve()
        if not poll_path.is_relative_to(runs.resolve()):
            return False
    except (OSError, ValueError, RuntimeError):
        return False
    return _matches_poll(snapshot, _read_snapshot(poll_path))


def read_committed_signal_snapshot(
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    todo: Mapping[str, object],
    signal_scope: Mapping[str, object] | None,
) -> dict[str, object] | None:
    latest = steward_monitor_snapshot_path(runtime_root, goal_id, str(todo["todo_id"]))
    pending = _read_snapshot(_pending_path(latest))
    for snapshot, staged in ((pending, True), (_read_snapshot(latest), False)):
        if (
            snapshot.get("schema_version") != SIGNAL_MONITOR_SNAPSHOT_SCHEMA
            or snapshot.get("goal_id") != goal_id
            or snapshot.get("todo_id") != todo["todo_id"]
            or snapshot.get("task_repository") != todo.get("task_repository")
            or snapshot.get("target_key") != todo.get("target_key")
            or snapshot.get("signal_scope") != signal_scope
        ):
            continue
        binding = _coerce_dict(snapshot.get("poll_binding"))
        if binding:
            if todo.get("claimed_by") and binding.get("agent_id") != todo["claimed_by"]:
                continue
            if binding.get("goal_ref") != capture_first_party_host_goal_ref(registry_path=registry_path, goal_id=goal_id):
                continue
            if staged and not _has_committed_poll(runtime_root, snapshot):
                continue
        elif staged or not todo.get("result_hash") or snapshot.get("result_hash") != todo["result_hash"]:
            continue
        return {key: value for key, value in snapshot.items() if key != "poll_binding"}
    return None


def commit_signal_snapshot(
    runtime_root: Path,
    registry_path: Path,
    snapshot: dict[str, object],
    *,
    agent_id: str,
    turn_instance_id: str,
    poll: Callable[[], dict[str, object]],
) -> dict[str, object]:
    """Publish under the caller's monitor commit fence, after the native poll."""
    goal_id = str(snapshot["goal_id"])
    latest = steward_monitor_snapshot_path(runtime_root, goal_id, str(snapshot["todo_id"]))
    pending_path = _pending_path(latest)
    binding = {
        "agent_id": agent_id,
        "turn_instance_id": turn_instance_id,
        "goal_ref": capture_first_party_host_goal_ref(registry_path=registry_path, goal_id=goal_id),
    }
    candidate = {**snapshot, "poll_binding": binding}
    pending = _read_snapshot(pending_path)
    if (
        pending.get("schema_version") != SIGNAL_MONITOR_SNAPSHOT_SCHEMA
        or pending.get("goal_id") != goal_id
        or pending.get("todo_id") != snapshot["todo_id"]
        or _coerce_dict(pending.get("poll_binding")).get("goal_ref") != binding["goal_ref"]
    ):
        pending = {}
    if pending.get("poll_binding") == binding:
        if any(pending.get(key) != candidate.get(key) for key in ("result_hash", "target_key", "task_repository", "signal_scope")):
            raise ValueError("Steward monitor observation changed for a staged poll")
        candidate = pending
    else:
        # A response can be lost after the quota index committed the previous draft.
        # Recover it before replacing the slot for another Turn.
        if pending and _has_committed_poll(runtime_root, pending):
            pending_path.replace(latest)
        if _poll_index_record(runtime_root, candidate) is not None:
            return poll()
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(pending_path, candidate)
    result = poll()
    if result.get("ok") is True and result.get("dry_run") is False and _matches_poll(candidate, result):
        try:
            pending_path.replace(latest)
        except OSError:
            _LOGGER.warning("Committed Steward snapshot remains staged for recovery")
    return result
