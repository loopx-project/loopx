from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ...history import load_registry
from ...paths import resolve_runtime_root
from ..todos.contract import normalize_required_capabilities
from .time import now_utc as runtime_now_utc
from .time import now_utc_iso as runtime_now_utc_iso
from .time import parse_timestamp


STATUS_PROJECTION_CACHE_SCHEMA_VERSION = "status_projection_cache_v0"


def _normalized_available_capabilities(value: Any) -> list[str]:
    return sorted(normalize_required_capabilities(value))


def now_utc() -> datetime:
    return runtime_now_utc()


def now_utc_iso() -> str:
    return runtime_now_utc_iso()


def resolve_status_projection_cache_runtime_root(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
) -> Path:
    registry = load_registry(registry_path)
    return resolve_runtime_root(
        registry,
        runtime_root_override,
        registry_path=registry_path,
    )


def status_projection_cache_dir(runtime_root: Path) -> Path:
    return runtime_root / "status-projection-cache"


def status_projection_cache_key(
    *,
    registry_path: Path,
    runtime_root: Path,
    scan_roots: list[Path],
    limit: int,
    include_task_graph: bool,
    goal_id: str | None,
    available_capabilities: Any = None,
    agent_lane_id: str | None = None,
) -> str:
    request = {
        "schema_version": STATUS_PROJECTION_CACHE_SCHEMA_VERSION,
        "collector": "collect_status",
        "registry_path": str(registry_path.expanduser().resolve()),
        "runtime_root": str(runtime_root.expanduser()),
        "scan_roots": [str(path.expanduser().resolve()) for path in scan_roots],
        "limit": max(0, int(limit)),
        "include_task_graph": bool(include_task_graph),
        "goal_id": str(goal_id or "").strip() or None,
        "agent_lane_id": str(agent_lane_id or "").strip() or None,
        "available_capabilities": _normalized_available_capabilities(
            available_capabilities
        ),
    }
    encoded = json.dumps(
        request,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def status_projection_cache_path(runtime_root: Path, key: str) -> Path:
    return status_projection_cache_dir(runtime_root) / f"{key}.json"


def cached_goal_run_index_is_current(
    payload: dict[str, Any], *, runtime_root: Path, goal_id: str
) -> bool:
    """Fence scheduler cache reads against the durable Goal Run index.

    The status projection stores the index digest computed over raw index
    bytes. Hashing those bytes avoids reparsing run artifacts on a cache hit,
    while any new blocked-settlement or other Run forces a fresh projection.
    """

    if not goal_id or goal_id in {".", ".."} or Path(goal_id).name != goal_id:
        return False
    history = payload.get("run_history")
    goals = history.get("goals") if isinstance(history, dict) else None
    goal = next(
        (
            item
            for item in goals
            if isinstance(item, dict) and item.get("id") == goal_id
        ),
        None,
    ) if isinstance(goals, list) else None
    if not isinstance(goal, dict) or "index_digest" not in goal:
        return False
    expected = goal["index_digest"]
    if expected is not None and not isinstance(expected, str):
        return False
    index_path = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    digest = hashlib.sha256()
    try:
        with index_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError:
        return expected is None
    except OSError:
        return False
    return digest.hexdigest() == expected


def status_projection_cache_metadata(
    *,
    registry_path: Path,
    runtime_root: Path,
    scan_roots: list[Path],
    limit: int,
    include_task_graph: bool,
    goal_id: str | None,
    max_age_seconds: int,
    available_capabilities: Any = None,
    agent_lane_id: str | None = None,
) -> dict[str, Any]:
    key = status_projection_cache_key(
        registry_path=registry_path,
        runtime_root=runtime_root,
        scan_roots=scan_roots,
        limit=limit,
        include_task_graph=include_task_graph,
        goal_id=goal_id,
        available_capabilities=available_capabilities,
        agent_lane_id=agent_lane_id,
    )
    normalized_capabilities = _normalized_available_capabilities(
        available_capabilities
    )
    return {
        "schema_version": STATUS_PROJECTION_CACHE_SCHEMA_VERSION,
        "key": key,
        "path": str(status_projection_cache_path(runtime_root, key)),
        "max_age_seconds": max(0, int(max_age_seconds)),
        "goal_id": str(goal_id or "").strip() or None,
        "agent_lane_id": str(agent_lane_id or "").strip() or None,
        "limit": max(0, int(limit)),
        "include_task_graph": bool(include_task_graph),
        "scan_roots": [str(path.expanduser()) for path in scan_roots],
        "available_capabilities": normalized_capabilities,
    }


def load_status_projection_cache(
    *,
    registry_path: Path,
    runtime_root: Path,
    scan_roots: list[Path],
    limit: int,
    include_task_graph: bool,
    goal_id: str | None,
    max_age_seconds: int,
    available_capabilities: Any = None,
    agent_lane_id: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    metadata = status_projection_cache_metadata(
        registry_path=registry_path,
        runtime_root=runtime_root,
        scan_roots=scan_roots,
        limit=limit,
        include_task_graph=include_task_graph,
        goal_id=goal_id,
        max_age_seconds=max_age_seconds,
        available_capabilities=available_capabilities,
        agent_lane_id=agent_lane_id,
    )
    path = Path(str(metadata["path"]))
    metadata["hit"] = False
    if max_age_seconds < 0:
        metadata["miss_reason"] = "disabled"
        return None, metadata
    if not path.exists():
        metadata["miss_reason"] = "missing"
        return None, metadata
    try:
        cache_record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        metadata["miss_reason"] = "unreadable"
        metadata["error"] = str(exc)
        return None, metadata
    if not isinstance(cache_record, dict):
        metadata["miss_reason"] = "invalid_record"
        return None, metadata
    if cache_record.get("schema_version") != STATUS_PROJECTION_CACHE_SCHEMA_VERSION:
        metadata["miss_reason"] = "schema_mismatch"
        return None, metadata
    generated_at = parse_timestamp(cache_record.get("generated_at"))
    if generated_at is None:
        metadata["miss_reason"] = "missing_generated_at"
        return None, metadata
    age_seconds = max(0.0, (now_utc() - generated_at).total_seconds())
    metadata["generated_at"] = cache_record.get("generated_at")
    metadata["age_seconds"] = age_seconds
    if age_seconds > max(0, int(max_age_seconds)):
        metadata["miss_reason"] = "expired"
        return None, metadata
    payload = cache_record.get("payload")
    if not isinstance(payload, dict):
        metadata["miss_reason"] = "missing_payload"
        return None, metadata
    metadata["hit"] = True
    metadata["miss_reason"] = None
    payload = dict(payload)
    payload["projection_cache"] = dict(metadata)
    return payload, metadata


def write_status_projection_cache(
    *,
    registry_path: Path,
    runtime_root: Path,
    scan_roots: list[Path],
    limit: int,
    include_task_graph: bool,
    goal_id: str | None,
    payload: dict[str, Any],
    max_age_seconds: int,
    available_capabilities: Any = None,
    agent_lane_id: str | None = None,
) -> dict[str, Any]:
    metadata = status_projection_cache_metadata(
        registry_path=registry_path,
        runtime_root=runtime_root,
        scan_roots=scan_roots,
        limit=limit,
        include_task_graph=include_task_graph,
        goal_id=goal_id,
        max_age_seconds=max_age_seconds,
        available_capabilities=available_capabilities,
        agent_lane_id=agent_lane_id,
    )
    path = Path(str(metadata["path"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    stored_payload = dict(payload)
    stored_payload.pop("projection_cache", None)
    record = {
        "schema_version": STATUS_PROJECTION_CACHE_SCHEMA_VERSION,
        "generated_at": now_utc_iso(),
        "payload": stored_payload,
    }
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    metadata.update(
        {
            "hit": False,
            "written": True,
            "generated_at": record["generated_at"],
        }
    )
    return metadata
