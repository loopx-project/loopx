#!/usr/bin/env python3
"""Smoke test for worker lifecycle state projection.

Verifies that the agent management projection correctly derives lifecycle
states from existing facts (registry, todo, session binding, execution facts).

Run from the repository root:
    uv run --extra test python examples/worker-lifecycle-state-smoke.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add the repository root to the path for direct execution
sys.path.insert(0, str(Path(__file__).parent.parent))


def _recent_activity() -> str:
    """A fresh Todo update: activity, which on its own never means execution."""
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


# Execution facts are what make a worker `executing`: here the worker's Turn
# lane is live. The projection reads them; it never infers them from age.
EXECUTION_FACTS = {"worker-executing": {"lane": "live"}}

from loopx.control_plane.agents.management_projection import (  # noqa: E402
    WORKER_LIFECYCLE_STATE_ADDRESSABLE,
    WORKER_LIFECYCLE_STATE_BLOCKED,
    WORKER_LIFECYCLE_STATE_BOUND,
    WORKER_LIFECYCLE_STATE_EXECUTING,
    WORKER_LIFECYCLE_STATE_LAUNCHABLE,
    WORKER_LIFECYCLE_STATE_REGISTERED,
    build_agent_management_projection,
)


def build_status_payload() -> dict:
    """Build a minimal status payload with known facts."""
    return {
        "goal_filter": "smoke-goal",
        "run_history": {
            "goals": [
                {
                    "id": "smoke-goal",
                    "coordination": {
                        "registered_agents": [
                            "worker-registered",
                            "worker-addressable",
                            "worker-bound",
                            "worker-launchable",
                            "worker-executing",
                            "worker-blocked",
                        ],
                        "thread_agent_bindings": [
                            {
                                "agent_id": "worker-addressable",
                                "thread_id": "thread-1",
                                "host_surface": "codex-app",
                            },
                            {
                                "agent_id": "worker-bound",
                                "thread_id": "thread-2",
                                "host_surface": "codex-app",
                            },
                            {
                                "agent_id": "worker-executing",
                                "thread_id": "thread-3",
                                "host_surface": "codex-app",
                            },
                        ],
                    },
                }
            ]
        },
        "attention_queue": {
            "items": [
                {
                    "goal_id": "smoke-goal",
                    "agent_todos": {
                        "items": [
                            {
                                "todo_id": "todo-bound",
                                "claimed_by": "worker-bound",
                                "status": "open",
                                "updated_at": "2026-09-15T12:00:00+00:00",
                            },
                            {
                                "todo_id": "todo-launchable",
                                "claimed_by": "worker-launchable",
                                "status": "open",
                                "updated_at": "2026-09-15T12:00:00+00:00",
                            },
                            {
                                "todo_id": "todo-executing",
                                "claimed_by": "worker-executing",
                                "status": "open",
                                "updated_at": _recent_activity(),
                            },
                            {
                                "todo_id": "todo-blocked",
                                "claimed_by": "worker-blocked",
                                "status": "blocked",
                                "task_class": "blocker",
                                "updated_at": "2026-09-17T12:00:00+00:00",
                            },
                        ]
                    },
                }
            ]
        },
    }


def main() -> int:
    payload = build_status_payload()
    projection = build_agent_management_projection(
        payload, execution_facts=EXECUTION_FACTS
    )
    without_facts = build_agent_management_projection(payload)
    timestamp_only = {
        a["agent_id"]: a["state"] for a in without_facts.get("agents", [])
    }.get("worker-executing")
    if timestamp_only == WORKER_LIFECYCLE_STATE_EXECUTING:
        print(
            "FAIL: a fresh Todo timestamp without execution facts must not "
            "read as executing"
        )
        return 1

    agents = {a["agent_id"]: a for a in projection.get("agents", [])}

    # Verify each worker's lifecycle state
    expected = {
        "worker-registered": WORKER_LIFECYCLE_STATE_REGISTERED,
        "worker-addressable": WORKER_LIFECYCLE_STATE_ADDRESSABLE,
        "worker-bound": WORKER_LIFECYCLE_STATE_BOUND,
        "worker-launchable": WORKER_LIFECYCLE_STATE_LAUNCHABLE,
        "worker-executing": WORKER_LIFECYCLE_STATE_EXECUTING,
        "worker-blocked": WORKER_LIFECYCLE_STATE_BLOCKED,
    }

    failures = []
    for agent_id, expected_state in expected.items():
        agent = agents.get(agent_id)
        if agent is None:
            failures.append(f"{agent_id}: missing from projection")
            continue
        actual_state = agent.get("state")
        if actual_state != expected_state:
            failures.append(
                f"{agent_id}: expected {expected_state}, got {actual_state}"
            )
        else:
            print(f"  {agent_id}: {actual_state} ✓")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print(f"\nAll {len(expected)} lifecycle states verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
