"""Consume the product task-planning checkpoint and verify its state readback."""

from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path


def render_host_window(env: dict[str, str], *, execution_reserve_seconds: int,
                       wake_timeout_seconds: float | None = None) -> str:
    """Project the caller's enforced clock; never create or extend an allowance."""
    key = "LOOPX_PHASE_DEADLINE_EPOCH"
    if key not in env:
        return ""
    value = float(env[key])
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be finite and positive")
    observed = time.time()
    window = [
        f"Absolute phase deadline: {value} Unix UTC seconds.",
        f"Remaining phase time at observation {observed}: {max(0, value - observed):.1f} seconds.",
        f"Existing execution reserve: {execution_reserve_seconds} seconds. "
        f"Observed work window before that reserve: {max(0, value - observed - execution_reserve_seconds):.1f} seconds.",
        "This shared window includes planning and subsequent execution; "
        "runtime startup and settlement reserves also consume it.",
    ]
    if wake_timeout_seconds is not None:
        window.append(f"Current wake execution limit: {wake_timeout_seconds:.1f} seconds. "
                      "A wake limit is not the remaining phase budget.")
    return "Host execution window:\n" + "\n".join(window) + "\n"


def render_planning_prompt(packet: dict, env: dict[str, str], *, execution_reserve_seconds: int) -> str:
    """Project caller-owned clocks without changing the product planning packet."""
    if "LOOPX_PLANNING_TIMEOUT_SEC" in env:
        raise ValueError(
            "Remove LOOPX_PLANNING_TIMEOUT_SEC; planning uses LOOPX_PHASE_DEADLINE_EPOCH"
        )
    window = render_host_window(env, execution_reserve_seconds=execution_reserve_seconds)
    prefix = "$loopx\n\n"
    if window:
        prefix += window
        prefix += (
            "Use these observations to size the plan and its validation. They age "
            "as work proceeds; the runtime still enforces its original limits. "
            "They grant no extra time or authority and do not relax task acceptance.\n\n"
        )
    return prefix + "Host-supplied planning checkpoint:\n" + json.dumps(packet, ensure_ascii=False)


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
