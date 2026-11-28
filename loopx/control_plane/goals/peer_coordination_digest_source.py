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


_MAX_DELIVERED_TURN_IDS = 32


def _normalize_opportunity_ids(values: Sequence[str] | Any) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    cleaned = {
        str(item).strip()
        for item in values
        if isinstance(item, str) and str(item).strip()
    }
    return sorted(cleaned)


def _normalize_turn_ids(values: Sequence[str] | Any) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    deduped: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return deduped[-_MAX_DELIVERED_TURN_IDS:]


def read_peer_coordination_cursor_state(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
) -> tuple[list[str], list[str]]:
    """Read seen opportunity IDs and delivered Turn IDs; fail open if absent/corrupt."""

    try:
        path = peer_coordination_cursor_path(
            runtime_root, goal_id=goal_id, agent_id=agent_id
        )
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, ValueError):
        return [], []
    if not isinstance(payload, Mapping):
        return [], []
    if (
        payload.get("schema_version") != PEER_COORDINATION_CURSOR_SCHEMA
        or payload.get("goal_id") != goal_id
    ):
        return [], []
    return (
        _normalize_opportunity_ids(payload.get("seen_opportunity_ids")),
        _normalize_turn_ids(payload.get("delivered_turn_ids")),
    )


def read_peer_coordination_cursor(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
) -> list[str]:
    """Read previously surfaced opportunity IDs; fail open to ``[]`` if missing or corrupt."""

    seen_ids, _turn_ids = read_peer_coordination_cursor_state(
        runtime_root, goal_id=goal_id, agent_id=agent_id
    )
    return seen_ids


def write_peer_coordination_cursor(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    seen_opportunity_ids: Sequence[str],
    delivered_turn_ids: Sequence[str] | None = None,
) -> bool:
    """Persist normalized seen IDs and delivered Turn IDs atomically, skipping no-op writes."""

    normalized = _normalize_opportunity_ids(seen_opportunity_ids)
    path = peer_coordination_cursor_path(
        runtime_root, goal_id=goal_id, agent_id=agent_id
    )
    existing_seen, existing_turns = read_peer_coordination_cursor_state(
        runtime_root, goal_id=goal_id, agent_id=agent_id
    )
    normalized_turns = (
        existing_turns
        if delivered_turn_ids is None
        else _normalize_turn_ids(delivered_turn_ids)
    )
    if path.exists():
        if existing_seen == normalized and existing_turns == normalized_turns:
            return False
    elif not normalized and not normalized_turns:
        return False

    normalized_agent = normalize_todo_claimed_by(agent_id)
    if normalized_agent is None:
        raise ValueError("agent_id must be a public-safe agent id")
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "schema_version": PEER_COORDINATION_CURSOR_SCHEMA,
        "goal_id": goal_id,
        "agent_id": normalized_agent,
        "seen_opportunity_ids": normalized,
    }
    if normalized_turns:
        record["delivered_turn_ids"] = normalized_turns
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
    turn_instance_id: str | None = None,
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

    seen_ids, delivered_turn_ids = read_peer_coordination_cursor_state(
        runtime_root,
        goal_id=normalized_goal_id,
        agent_id=normalized_agent_id,
    )
    clean_turn_id = str(turn_instance_id or "").strip()
    if clean_turn_id and clean_turn_id in delivered_turn_ids:
        seen_ids = sorted(set(seen_ids))
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
            if isinstance(result, Mapping):
                return None, _normalize_opportunity_ids(result.get("active_opportunity_ids"))
        except EffectRuntimeRejected:
            return None, []
        return None, []
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
    turn_instance_id: str | None = None,
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
    receipt = payload.get("heartbeat_receipt")
    receipt_turn_id = (
        str(receipt.get("turn_instance_id") or "").strip()
        if isinstance(receipt, Mapping)
        else ""
    )
    effective_turn_id = str(turn_instance_id or receipt_turn_id or "").strip() or None
    digest, _active_ids = project_turn_peer_coordination_digest(
        registry_goal=registry_goal,
        project=project,
        runtime_root=runtime_path,
        goal_id=goal_id,
        agent_id=agent_id,
        registered_agent_ids=registered_agent_ids,
        turn_instance_id=effective_turn_id,
    )
    if digest is not None:
        payload["peer_coordination_digest"] = digest


def extract_delivered_peer_coordination_digest(
    delivered_payload: Mapping[str, Any] | Any,
) -> Mapping[str, Any] | None:
    """Extract an inline peer_coordination_digest from a delivered CLI payload, if present."""

    if not isinstance(delivered_payload, Mapping):
        return None
    direct = delivered_payload.get("peer_coordination_digest")
    if isinstance(direct, Mapping):
        return direct
    work_opening = delivered_payload.get("work_opening")
    if isinstance(work_opening, Mapping):
        nested = work_opening.get("peer_coordination_digest")
        if isinstance(nested, Mapping):
            return nested
    if delivered_payload.get("schema_version") == "context_delivery_v1":
        inner = delivered_payload.get("payload")
        if isinstance(inner, Mapping):
            nested = inner.get("peer_coordination_digest")
            if isinstance(nested, Mapping):
                return nested
    return None


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
    turn_instance_id: str | None = None,
) -> bool:
    """Advance cursor using only actually delivered IDs + still-active previously seen IDs."""

    clean_turn_id = str(turn_instance_id or "").strip()
    existing_seen_list, existing_turns = read_peer_coordination_cursor_state(
        runtime_root, goal_id=goal_id, agent_id=agent_id
    )
    if clean_turn_id and clean_turn_id in existing_turns:
        return False
    active_set = set(_normalize_opportunity_ids(active_opportunity_ids))
    displayed_set = set(_normalize_opportunity_ids(displayed_opportunity_ids)) & active_set
    existing_seen = set(existing_seen_list) & active_set
    next_seen = sorted(existing_seen | displayed_set)
    try:
        cursor_exists = peer_coordination_cursor_path(
            runtime_root, goal_id=goal_id, agent_id=agent_id
        ).exists()
    except ValueError:
        return False
    next_turns = existing_turns
    if clean_turn_id and (displayed_set or cursor_exists):
        next_turns = _normalize_turn_ids([*existing_turns, clean_turn_id])
    return write_peer_coordination_cursor(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        seen_opportunity_ids=next_seen,
        delivered_turn_ids=next_turns,
    )


def commit_recovered_peer_coordination_cursor(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    displayed_opportunity_ids: Sequence[str],
    turn_instance_id: str | None = None,
) -> bool:
    """Commit cursor idempotently after digest-verified recovery or readback."""

    clean_turn_id = str(turn_instance_id or "").strip()
    existing_seen_list, existing_turns = read_peer_coordination_cursor_state(
        runtime_root, goal_id=goal_id, agent_id=agent_id
    )
    displayed = _normalize_opportunity_ids(displayed_opportunity_ids)
    next_seen = sorted(set(existing_seen_list) | set(displayed))
    next_turns = (
        _normalize_turn_ids([*existing_turns, clean_turn_id])
        if clean_turn_id and (next_seen or existing_turns)
        else existing_turns
    )
    if next_seen == existing_seen_list and next_turns == existing_turns:
        return False
    return write_peer_coordination_cursor(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        seen_opportunity_ids=next_seen,
        delivered_turn_ids=next_turns,
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
    delivered_payload: Mapping[str, Any] | None = None,
    turn_instance_id: str | None = None,
) -> bool:
    """Commit or prune the live non-preview Turn cursor only after channel delivery."""

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

    delivered_view: Mapping[str, Any] = (
        delivered_payload if delivered_payload is not None else payload
    )
    if delivered_view.get("ok") is not True:
        return False

    delivered_digest = extract_delivered_peer_coordination_digest(delivered_view)
    original_had_digest = isinstance(payload.get("peer_coordination_digest"), Mapping)
    is_deferred_wrapper = (
        delivered_view.get("schema_version")
        in {"context_capture_navigation_v1", "context_delivery_v1"}
        or (
            "capture_sha256" in delivered_view
            and "interaction_contract" not in delivered_view
            and "mode" not in delivered_view
        )
    )
    if delivered_digest is None and (original_had_digest or is_deferred_wrapper):
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

    displayed_ids = displayed_opportunity_ids_from_digest(delivered_digest)
    digest_emitted = delivered_digest is not None
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
    receipt = payload.get("heartbeat_receipt")
    receipt_turn_id = (
        str(receipt.get("turn_instance_id") or "").strip()
        if isinstance(receipt, Mapping)
        else ""
    )
    effective_turn_id = str(turn_instance_id or receipt_turn_id or "").strip() or None
    _digest, active_ids = project_turn_peer_coordination_digest(
        registry_goal=registry_goal,
        project=project,
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=normalized_agent,
        registered_agent_ids=normalized_agents,
        turn_instance_id=effective_turn_id,
    )
    return commit_displayed_peer_coordination_cursor(
        runtime_root,
        goal_id=goal_id,
        agent_id=normalized_agent,
        active_opportunity_ids=active_ids,
        displayed_opportunity_ids=displayed_ids,
        turn_instance_id=effective_turn_id,
    )


def _skip_json_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in " \t\r\n":
        pos += 1
    return pos


def _top_level_digest_byte_spans(
    raw_text: str, expected_digest: Mapping[str, Any]
) -> list[tuple[int, int]]:
    decoder = json.JSONDecoder()
    pos = _skip_json_ws(raw_text, 0)
    if pos >= len(raw_text) or raw_text[pos] != "{":
        return []
    pos += 1
    spans: list[tuple[int, int]] = []
    expected = dict(expected_digest)
    while pos < len(raw_text):
        pos = _skip_json_ws(raw_text, pos)
        if pos >= len(raw_text) or raw_text[pos] == "}":
            break
        if raw_text[pos] == ",":
            pos = _skip_json_ws(raw_text, pos + 1)
            if pos >= len(raw_text) or raw_text[pos] == "}":
                break
        key_start = pos
        key, pos = decoder.raw_decode(raw_text, pos)
        pos = _skip_json_ws(raw_text, pos)
        if pos >= len(raw_text) or raw_text[pos] != ":":
            break
        pos = _skip_json_ws(raw_text, pos + 1)
        val, val_end = decoder.raw_decode(raw_text, pos)
        if key == "peer_coordination_digest" and isinstance(val, Mapping) and dict(val) == expected:
            spans.append(
                (
                    len(raw_text[:key_start].encode("utf-8")),
                    len(raw_text[:val_end].encode("utf-8")),
                )
            )
        pos = val_end
    return spans


def _selection_digest_byte_spans(
    raw_text: str, expected_digest: Mapping[str, Any]
) -> list[tuple[int, int]]:
    decoder = json.JSONDecoder()
    pos = _skip_json_ws(raw_text, 0)
    if pos >= len(raw_text) or raw_text[pos] != "{":
        return []
    pos += 1
    spans: list[tuple[int, int]] = []
    expected = dict(expected_digest)
    while pos < len(raw_text):
        pos = _skip_json_ws(raw_text, pos)
        if pos >= len(raw_text) or raw_text[pos] == "}":
            break
        if raw_text[pos] == ",":
            pos = _skip_json_ws(raw_text, pos + 1)
            if pos >= len(raw_text) or raw_text[pos] == "}":
                break
        key, pos = decoder.raw_decode(raw_text, pos)
        pos = _skip_json_ws(raw_text, pos)
        if pos >= len(raw_text) or raw_text[pos] != ":":
            break
        pos = _skip_json_ws(raw_text, pos + 1)
        if key != "selections":
            _val, pos = decoder.raw_decode(raw_text, pos)
            continue
        if pos >= len(raw_text) or raw_text[pos] != "[":
            break
        arr_pos = pos + 1
        while arr_pos < len(raw_text):
            arr_pos = _skip_json_ws(raw_text, arr_pos)
            if arr_pos >= len(raw_text) or raw_text[arr_pos] == "]":
                arr_pos += 1
                break
            if raw_text[arr_pos] == ",":
                arr_pos = _skip_json_ws(raw_text, arr_pos + 1)
                if arr_pos >= len(raw_text) or raw_text[arr_pos] == "]":
                    arr_pos += 1
                    break
            elem_start = arr_pos
            elem, elem_end = decoder.raw_decode(raw_text, elem_start)
            if isinstance(elem, Mapping):
                ptr = elem.get("pointer")
                val = elem.get("value")
                if ptr in ("/peer_coordination_digest", "/work_opening/peer_coordination_digest"):
                    if isinstance(val, Mapping) and dict(val) == expected:
                        spans.append(
                            (
                                len(raw_text[:elem_start].encode("utf-8")),
                                len(raw_text[:elem_end].encode("utf-8")),
                            )
                        )
                elif ptr in ("", "/work_opening") and isinstance(val, Mapping):
                    nested_spans = _top_level_digest_byte_spans(
                        raw_text[elem_start:elem_end], expected
                    )
                    if not nested_spans:
                        # Handle wrapper object {"pointer":..., "value":{...}} by walking into "value"
                        val_match_pos = _skip_json_ws(raw_text, elem_start + 1)
                        while val_match_pos < elem_end:
                            val_match_pos = _skip_json_ws(raw_text, val_match_pos)
                            if raw_text[val_match_pos] == ",":
                                val_match_pos = _skip_json_ws(raw_text, val_match_pos + 1)
                            if raw_text[val_match_pos] == "}":
                                break
                            ekey, val_match_pos = decoder.raw_decode(raw_text, val_match_pos)
                            val_match_pos = _skip_json_ws(raw_text, val_match_pos)
                            if raw_text[val_match_pos] != ":":
                                break
                            val_match_pos = _skip_json_ws(raw_text, val_match_pos + 1)
                            if ekey == "value":
                                for s_start, s_end in _top_level_digest_byte_spans(
                                    raw_text[val_match_pos:elem_end], expected
                                ):
                                    spans.append(
                                        (
                                            len(raw_text[: val_match_pos + s_start].encode("utf-8")),
                                            len(raw_text[: val_match_pos + s_end].encode("utf-8")),
                                        )
                                    )
                                break
                            _ev, val_match_pos = decoder.raw_decode(raw_text, val_match_pos)
            arr_pos = elem_end
        pos = arr_pos
    return spans


def prove_page_delivered_peer_digest(
    source_payload: Mapping[str, Any] | Any,
    *,
    raw: bytes,
    content: str,
    offset: int,
    list_fields: bool = False,
    is_selection: bool = False,
) -> Mapping[str, Any] | None:
    """Return expected digest iff this page's actual bytes cover the full digest object."""

    if list_fields or not isinstance(source_payload, Mapping):
        return None
    expected_digest = extract_delivered_peer_coordination_digest(source_payload)
    if expected_digest is None:
        return None
    content_bytes = content.encode("utf-8")
    page_start = offset
    page_end = offset + len(content_bytes)
    if page_start < 0 or page_end > len(raw) or raw[page_start:page_end] != content_bytes:
        return None
    try:
        raw_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        spans = (
            _selection_digest_byte_spans(raw_text, expected_digest)
            if is_selection
            else _top_level_digest_byte_spans(raw_text, expected_digest)
        )
    except (ValueError, json.JSONDecodeError):
        return None
    if any(page_start <= span_start and span_end <= page_end for span_start, span_end in spans):
        return expected_digest
    return None


def _resolve_captured_decision_for_readback(
    args: Any,
    *,
    emitted_payload: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Resolve the digest-verified source capture for context-read / context-project."""

    from ..turn_driver.codex_context import codex_context_directory, read_codex_context_reference
    from ..turn_driver.decision_capture import (
        read_context_capture_file,
        read_context_read_reference,
    )

    try:
        reference_file = getattr(args, "reference_file", None)
        value = getattr(args, "capture_file", None)
        digest = getattr(args, "capture_sha256", None)
        if reference_file:
            _ref_path, ref_payload = read_context_read_reference(str(reference_file))
            value, digest = ref_payload.get("path"), ref_payload.get("sha256")
        if not value and not digest:
            context_dir = codex_context_directory(
                directory=getattr(args, "codex_context_dir", None),
                root=getattr(args, "codex_context_root", None),
            )
            if context_dir:
                value, digest = read_codex_context_reference(
                    context_dir,
                    goal_id=getattr(args, "goal_id", None),
                    agent_id=getattr(args, "agent_id", None),
                )
        if isinstance(value, str) and value and isinstance(digest, str) and digest:
            _path, _raw, captured = read_context_capture_file(value, digest)
            return captured
    except (OSError, ValueError):
        return None
    if extract_delivered_peer_coordination_digest(emitted_payload) is not None:
        return emitted_payload
    return None


def maybe_commit_captured_peer_coordination_cursor(
    args: Any,
    *,
    emitted_payload: Mapping[str, Any],
    captured_payload: Mapping[str, Any] | None = None,
    delivery_proof: Mapping[str, Any] | None = None,
    registry_path: Path | None = None,
    runtime_root_arg: str | None = None,
) -> bool:
    """Advance cursor idempotently only when output actually delivered the complete digest."""

    if emitted_payload.get("ok") is not True or emitted_payload.get("list_fields") is True:
        return False
    inline_digest = extract_delivered_peer_coordination_digest(emitted_payload)
    proved_digest = (
        delivery_proof.get("delivered_digest")
        if isinstance(delivery_proof, Mapping)
        and isinstance(delivery_proof.get("delivered_digest"), Mapping)
        else None
    )
    if inline_digest is None and proved_digest is None:
        return False
    source = (
        captured_payload
        if isinstance(captured_payload, Mapping)
        else (
            delivery_proof.get("captured_payload")
            if isinstance(delivery_proof, Mapping)
            and isinstance(delivery_proof.get("captured_payload"), Mapping)
            else _resolve_captured_decision_for_readback(args, emitted_payload=emitted_payload)
        )
    )
    if not isinstance(source, Mapping):
        return False
    if (
        source.get("ok") is not True
        or source.get("dry_run") is True
        or str(source.get("effective_action") or "").strip() in _SKIPPED_EFFECTIVE_ACTIONS
    ):
        return False
    receipt = source.get("heartbeat_receipt")
    if not isinstance(receipt, Mapping):
        return False
    event_id = str(receipt.get("event_id") or "").strip()
    turn_id = str(receipt.get("turn_instance_id") or "").strip()
    if not event_id or not turn_id:
        return False
    digest = extract_delivered_peer_coordination_digest(source)
    if digest is None:
        return False
    goal_id = str(source.get("goal_id") or "").strip()
    agent_id = quota_decision_agent_id(source)
    if not goal_id or not agent_id:
        return False
    raw_runtime = source.get("runtime_root") or runtime_root_arg
    runtime_path: Path | None = (
        Path(str(raw_runtime)).expanduser()
        if isinstance(raw_runtime, (str, Path)) and str(raw_runtime).strip()
        else None
    )
    if runtime_path is None and registry_path is not None:
        from ...history import load_registry
        from ...paths import resolve_runtime_root

        try:
            runtime_path = resolve_runtime_root(load_registry(registry_path), runtime_root_arg)
        except (OSError, ValueError):
            return False
    if runtime_path is None:
        return False
    return commit_recovered_peer_coordination_cursor(
        runtime_path,
        goal_id=goal_id,
        agent_id=agent_id,
        displayed_opportunity_ids=displayed_opportunity_ids_from_digest(digest),
        turn_instance_id=turn_id,
    )
