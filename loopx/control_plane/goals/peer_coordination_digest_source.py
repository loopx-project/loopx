"""Turn-envelope adapter and cursor persistence for M1 peer coordination digest."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...paths import registry_project_root
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_projection_result
from ..quota.decision_summary import quota_decision_agent_id
from ..quota.effective_action import EffectiveAction
from ..quota.effect_program import ReceiptBoundReplayPhase
from ..quota.goal_boundary import registry_goal_by_id
from ..todos.contract import normalize_todo_claimed_by
from .shared_goal_work_source import SharedGoalWorkSource, read_shared_goal_work_source

PEER_COORDINATION_DIGEST_EFFECT_METHOD = "goal.peer_coordination_digest.project"
PEER_COORDINATION_DIGEST_REQUEST_SCHEMA = "peer_coordination_digest_request_v0"
PEER_COORDINATION_DIGEST_PROJECTION_SCHEMA = "peer_coordination_digest_projection_v0"
PEER_COORDINATION_DIGEST_SCHEMA = "peer_digest_v0"
PEER_COORDINATION_CURSOR_SCHEMA = "peer_coordination_cursor_v0"
PEER_COORDINATION_DIGEST_DISABLE_ENV = "LOOPX_PEER_COORDINATION_DIGEST"

_SAFE_SEGMENT_RE = re.compile(r"^[^\\/\s]+$")
_DISABLED_TOKENS = frozenset({"0", "false", "off", "disabled", "no"})
_SKIPPED_EFFECTIVE_ACTIONS = frozenset(
    {
        EffectiveAction.BLOCKED_WAIT.value,
        EffectiveAction.THROTTLED_SKIP.value,
        EffectiveAction.QUOTA_SKIP.value,
        EffectiveAction.HEARTBEAT_SETTLED_SKIP.value,
        EffectiveAction.MONITOR_QUIET_SKIP.value,
    }
)


def _safe_single_segment(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text or not _SAFE_SEGMENT_RE.match(text) or ".." in text:
        raise ValueError(f"{label} must be a safe single segment")
    return text


def peer_coordination_cursor_path(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
) -> Path:
    """Return the runtime-scoped cursor file path for one registered Goal Agent."""

    safe_goal = _safe_single_segment(goal_id, "goal_id")
    normalized_agent = normalize_todo_claimed_by(agent_id)
    if normalized_agent is None:
        raise ValueError("agent_id must be a public-safe agent id")
    safe_agent = _safe_single_segment(normalized_agent, "agent_id")
    return (
        runtime_root.expanduser()
        / "goals"
        / safe_goal
        / "peer-coordination-cursors"
        / f"{safe_agent}.json"
    )


def _normalize_opportunity_ids(values: Sequence[str] | Any) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    cleaned = {
        str(item).strip()
        for item in values
        if isinstance(item, str) and str(item).strip()
    }
    return sorted(cleaned)


def read_peer_coordination_cursor(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
) -> list[str]:
    """Read previously surfaced opportunity IDs; fail open to ``[]`` if missing or corrupt."""

    try:
        path = peer_coordination_cursor_path(
            runtime_root, goal_id=goal_id, agent_id=agent_id
        )
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, ValueError):
        return []
    if not isinstance(payload, Mapping):
        return []
    if (
        payload.get("schema_version") != PEER_COORDINATION_CURSOR_SCHEMA
        or payload.get("goal_id") != goal_id
    ):
        return []
    return _normalize_opportunity_ids(payload.get("seen_opportunity_ids"))


def write_peer_coordination_cursor(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    seen_opportunity_ids: Sequence[str],
) -> bool:
    """Persist normalized seen opportunity IDs, skipping no-op writes and pruning empties."""

    normalized = _normalize_opportunity_ids(seen_opportunity_ids)
    path = peer_coordination_cursor_path(
        runtime_root, goal_id=goal_id, agent_id=agent_id
    )
    if path.exists():
        existing = read_peer_coordination_cursor(
            runtime_root, goal_id=goal_id, agent_id=agent_id
        )
        if existing == normalized:
            return False
    elif not normalized:
        return False

    normalized_agent = normalize_todo_claimed_by(agent_id)
    if normalized_agent is None:
        raise ValueError("agent_id must be a public-safe agent id")
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": PEER_COORDINATION_CURSOR_SCHEMA,
        "goal_id": goal_id,
        "agent_id": normalized_agent,
        "seen_opportunity_ids": normalized,
    }
    serialized = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".peer-cursor-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return True


def peer_coordination_digest_disabled(
    *,
    registry_goal: Mapping[str, Any] | None = None,
) -> bool:
    """Return True when frozen baselines or config explicitly disable Turn digest injection."""

    env_val = os.environ.get(PEER_COORDINATION_DIGEST_DISABLE_ENV, "").strip().lower()
    if env_val in _DISABLED_TOKENS:
        return True
    coordination = (
        registry_goal.get("coordination")
        if isinstance(registry_goal, Mapping)
        else None
    )
    if not isinstance(coordination, Mapping):
        return False
    setting = coordination.get("peer_coordination_digest")
    if setting is False:
        return True
    if isinstance(setting, str) and setting.strip().lower() in _DISABLED_TOKENS:
        return True
    return False


def resolve_goal_project_root(
    *,
    registry_goal: Mapping[str, Any] | None,
    registry_path_value: Any,
) -> Path | None:
    """Resolve the project root directory for reading canonical shared work snapshots."""

    registry_path = (
        Path(str(registry_path_value)).expanduser()
        if isinstance(registry_path_value, str) and registry_path_value.strip()
        else None
    )
    if isinstance(registry_goal, Mapping):
        repo_raw = registry_goal.get("repo")
        if isinstance(repo_raw, str) and repo_raw.strip():
            repo_path = Path(repo_raw.strip()).expanduser()
            if repo_path.is_absolute():
                return repo_path
            if registry_path is not None:
                return (registry_project_root(registry_path) / repo_path).resolve()
    if registry_path is not None:
        return registry_project_root(registry_path)
    return None


def project_turn_peer_coordination_digest(
    *,
    registry_goal: Mapping[str, Any] | None,
    project: Path | None,
    runtime_root: Path | None,
    goal_id: str,
    agent_id: str | None,
    registered_agent_ids: Sequence[str],
    work_source: SharedGoalWorkSource | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Compute bounded M1 peer digest for a Turn; returns ``(None, [])`` when inactive."""

    normalized_goal_id = str(goal_id or "").strip()
    normalized_agent_id = normalize_todo_claimed_by(agent_id)
    normalized_agents = [
        claimed
        for candidate in registered_agent_ids
        if (claimed := normalize_todo_claimed_by(candidate)) is not None
    ]
    if (
        not normalized_goal_id
        or not normalized_agent_id
        or len(set(normalized_agents)) < 2
        or normalized_agent_id not in normalized_agents
        or runtime_root is None
        or project is None
        or not isinstance(registry_goal, Mapping)
        or not registry_goal.get("id")
        or peer_coordination_digest_disabled(registry_goal=registry_goal)
    ):
        return None, []

    try:
        source = work_source or read_shared_goal_work_source(
            goal=dict(registry_goal),
            project=project,
            runtime_root=runtime_root,
        )
    except (OSError, ValueError, FileNotFoundError):
        return None, []
    if source.goal_id != normalized_goal_id:
        return None, []

    seen_ids = read_peer_coordination_cursor(
        runtime_root,
        goal_id=normalized_goal_id,
        agent_id=normalized_agent_id,
    )
    request = {
        "schema_version": PEER_COORDINATION_DIGEST_REQUEST_SCHEMA,
        "goal_id": normalized_goal_id,
        "agent_id": normalized_agent_id,
        "registered_agent_count": len(set(normalized_agents)),
        "seen_opportunity_ids": seen_ids,
        "work_items": source.items,
        "observed_at": source.observed_at,
    }
    try:
        result = effect_runtime_projection_result(
            PEER_COORDINATION_DIGEST_EFFECT_METHOD, request
        )
    except EffectRuntimeRejected:
        return None, []
    if not isinstance(result, Mapping) or (
        result.get("schema_version") != PEER_COORDINATION_DIGEST_PROJECTION_SCHEMA
        or result.get("goal_id") != normalized_goal_id
        or result.get("agent_id") != normalized_agent_id
    ):
        raise RuntimeError("TypeScript peer_coordination_digest shape mismatch")
    active_ids = _normalize_opportunity_ids(result.get("active_opportunity_ids"))
    digest_value = result.get("digest")
    if digest_value is None:
        return None, active_ids
    if not isinstance(digest_value, Mapping) or (
        digest_value.get("schema") != PEER_COORDINATION_DIGEST_SCHEMA
        or digest_value.get("read_only") is not True
        or digest_value.get("grants_authority") is not False
    ):
        raise RuntimeError("TypeScript peer_coordination_digest payload shape mismatch")
    return dict(digest_value), active_ids


def attach_turn_peer_coordination_digest(
    payload: dict[str, Any],
    *,
    prepared: Any,
    runtime_root: str | Path | None,
) -> None:
    """Attach bounded M1 digest to active should-run payload when eligible."""

    registered_agent_ids = prepared.registered_agent_ids
    settled_replay = (
        prepared.receipt_bound_replay_phase is ReceiptBoundReplayPhase.SETTLED
    )
    if (
        len(registered_agent_ids) < 2
        or settled_replay
        or payload.get("ok") is not True
        or str(payload.get("effective_action") or "").strip() in _SKIPPED_EFFECTIVE_ACTIONS
    ):
        return
    status_payload = prepared.status_payload
    goal_id = prepared.safe_goal_id
    agent_id = quota_decision_agent_id(payload) or prepared.requested_agent_id
    runtime_path = Path(str(runtime_root)).expanduser() if runtime_root else None
    registry_goal = registry_goal_by_id(status_payload).get(goal_id)
    project = resolve_goal_project_root(
        registry_goal=registry_goal,
        registry_path_value=status_payload.get("registry"),
    )
    digest, _active_ids = project_turn_peer_coordination_digest(
        registry_goal=registry_goal,
        project=project,
        runtime_root=runtime_path,
        goal_id=goal_id,
        agent_id=agent_id,
        registered_agent_ids=registered_agent_ids,
    )
    if digest is not None:
        payload["peer_coordination_digest"] = digest


def displayed_opportunity_ids_from_digest(digest: Mapping[str, Any] | Any) -> list[str]:
    """Return the exact opportunity IDs actually shown inline inside a rendered digest."""

    if not isinstance(digest, Mapping):
        return []
    items = digest.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return []
    return _normalize_opportunity_ids(
        [
            str(item.get("id") or "")
            for item in items
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        ]
    )


def commit_displayed_peer_coordination_cursor(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    active_opportunity_ids: Sequence[str],
    displayed_opportunity_ids: Sequence[str],
) -> bool:
    """Advance cursor using only actually displayed IDs + still-active previously seen IDs."""

    active_set = set(_normalize_opportunity_ids(active_opportunity_ids))
    displayed_set = set(_normalize_opportunity_ids(displayed_opportunity_ids)) & active_set
    existing_seen = set(
        read_peer_coordination_cursor(
            runtime_root, goal_id=goal_id, agent_id=agent_id
        )
    ) & active_set
    next_seen = sorted(existing_seen | displayed_set)
    return write_peer_coordination_cursor(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        seen_opportunity_ids=next_seen,
    )


def maybe_commit_turn_peer_coordination_cursor(
    payload: Mapping[str, Any],
    *,
    quota_command: str,
    preview: bool,
    scheduler_observation: bool,
    preflight_failed: bool,
    closeout_unavailable: bool,
    status_payload: Mapping[str, Any] | None,
    registry_path: Path,
    runtime_root: Path | None,
    goal_id: str | None,
    agent_id: str | None,
) -> bool:
    """Commit or prune the live non-preview Turn cursor after emission checks."""

    if (
        quota_command != "should-run"
        or preview
        or scheduler_observation
        or preflight_failed
        or closeout_unavailable
        or payload.get("ok") is not True
        or status_payload is None
        or runtime_root is None
        or not goal_id
        or not agent_id
        or str(payload.get("effective_action") or "").strip() in _SKIPPED_EFFECTIVE_ACTIONS
    ):
        return False

    registry_goal = registry_goal_by_id(status_payload).get(goal_id)
    identity = payload.get("agent_identity")
    registered_agents = (
        list(identity.get("registered_agents") or [])
        if isinstance(identity, Mapping)
        else []
    )
    if not registered_agents and isinstance(registry_goal, Mapping):
        coordination = registry_goal.get("coordination")
        if isinstance(coordination, Mapping):
            registered_agents = list(coordination.get("registered_agents") or [])
    normalized_agent = normalize_todo_claimed_by(agent_id)
    normalized_agents = [
        claimed
        for candidate in registered_agents
        if (claimed := normalize_todo_claimed_by(candidate)) is not None
    ]
    if (
        normalized_agent is None
        or len(set(normalized_agents)) < 2
        or normalized_agent not in normalized_agents
        or not isinstance(registry_goal, Mapping)
        or not registry_goal.get("id")
        or peer_coordination_digest_disabled(registry_goal=registry_goal)
    ):
        return False
    displayed_ids = displayed_opportunity_ids_from_digest(
        payload.get("peer_coordination_digest")
    )
    digest_emitted = isinstance(payload.get("peer_coordination_digest"), Mapping)
    try:
        cursor_file = peer_coordination_cursor_path(
            runtime_root, goal_id=goal_id, agent_id=normalized_agent
        )
    except ValueError:
        return False
    if not digest_emitted and not cursor_file.exists():
        return False
    project = resolve_goal_project_root(
        registry_goal=registry_goal,
        registry_path_value=str(registry_path),
    )
    if project is None:
        return False
    _digest, active_ids = project_turn_peer_coordination_digest(
        registry_goal=registry_goal,
        project=project,
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=normalized_agent,
        registered_agent_ids=normalized_agents,
    )
    return commit_displayed_peer_coordination_cursor(
        runtime_root,
        goal_id=goal_id,
        agent_id=normalized_agent,
        active_opportunity_ids=active_ids,
        displayed_opportunity_ids=displayed_ids,
    )
