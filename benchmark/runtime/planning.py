"""Consume the product task-planning checkpoint and verify its state readback."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def task_plan_packet(env: dict[str, str], cli: list[str]) -> dict:
    command = cli + [
        "todo",
        "plan",
        "--goal-id",
        env["LOOPX_GOAL_ID"],
        "--agent-id",
        env["LOOPX_AGENT_ID"],
        "--project",
        env["LOOPX_PROJECT"],
        "--text",
        Path(env["LOOPX_TASK_DOC"]).read_text(encoding="utf-8"),
    ]
    response = subprocess.run(
        command,
        cwd=env["LOOPX_PROJECT"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
    )
    packet = json.loads(response.stdout)
    if (
        packet.get("ok") is not True
        or packet.get("schema_version") != "loopx_task_planning_v0"
        or packet.get("goal_id") != env["LOOPX_GOAL_ID"]
        or packet.get("agent_id") != env["LOOPX_AGENT_ID"]
        or packet.get("execution_handoff", {}).get("owner") != "caller"
    ):
        raise ValueError("product planning packet does not match the caller binding")
    return packet


def validate_plan_readback(result: dict, before: dict, after: dict) -> dict:
    if (
        result.get("input_digest") != before["input_digest"]
        or after["input_digest"] != before["input_digest"]
    ):
        raise ValueError("planning input changed before readback")
    status = result.get("status")
    ids = result.get("todo_ids")
    if (
        status not in {"ready", "blocked"}
        or not isinstance(ids, list)
        or not ids
        or any(not isinstance(item, str) for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError(
            "planning result requires unique actual Todo ids and a typed status"
        )
    todos = {item["todo_id"]: item for item in after["existing_todos"]}
    if any(todo_id not in todos for todo_id in ids):
        raise ValueError("planning referenced a missing or unrelated Todo")
    if status == "ready" and not set(ids).issubset(after["runnable_todo_ids"]):
        raise ValueError("planning referenced non-runnable or unclaimed work")
    if status == "blocked" and not set(ids).issubset(after["blocking_todo_ids"]):
        raise ValueError("blocked planning result requires unresolved blocking Todos")
    return {
        "input_digest": before["input_digest"],
        "status": status,
        "todo_ids": ids,
        "goal_id": after["goal_id"],
        "agent_id": after["agent_id"],
        "state_readback_verified": True,
        "execution_owner": "caller",
    }
