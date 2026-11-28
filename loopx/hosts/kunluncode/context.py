"""KunlunCode-specific LoopX goal and agent binding."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loopx.file_lock import exclusive_file_lock
from loopx.goal_mode_context import find_registry, resolve_goal_context
from loopx.registry import atomic_write_json


BINDING_SCHEMA_VERSION = "loopx_kunluncode_binding_v0"
BINDING_FILENAME = "kunluncode.json"


def binding_path(project_root: str | Path) -> Path:
    return Path(project_root) / ".loopx" / BINDING_FILENAME


def read_binding(project_root: str | Path) -> dict[str, str] | None:
    path = binding_path(project_root)
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != BINDING_SCHEMA_VERSION:
        return None
    goal_id = str(payload.get("goal_id") or "").strip()
    agent_id = str(payload.get("agent_id") or "").strip()
    if not goal_id or not agent_id:
        return None
    return {"goal_id": goal_id, "agent_id": agent_id}


def write_binding(
    project_root: str | Path,
    *,
    goal_id: str,
    agent_id: str,
) -> Path:
    path = binding_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "host": "kunluncode",
        "goal_id": goal_id,
        "agent_id": agent_id,
    }
    with exclusive_file_lock(path, operation="kunluncode_binding_write"):
        atomic_write_json(path, payload)
    return path


def goal_context(cwd: str | Path) -> dict[str, Any] | None:
    registry = find_registry(cwd)
    if registry is None:
        return None
    root = registry.parent.parent
    binding = read_binding(root)
    if binding is None:
        return None
    return resolve_goal_context(
        cwd,
        preferred_goal_id=binding["goal_id"],
        preferred_agent_id=binding["agent_id"],
        require_preferred_binding=True,
    )
