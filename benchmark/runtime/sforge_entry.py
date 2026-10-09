"""Native SForge planning handoff, inside its timed and isolated execution."""

from __future__ import annotations

import json
import math
import os
import signal
from pathlib import Path
import sys
import time

from .planning import task_plan_packet, validate_plan_readback
from .worker import loopx_command, run_once


def prepare_entry(env: dict[str, str]) -> bool:
    """Reuse a qualified initial plan; execution still requires ordinary quota."""
    if env.get("LOOPX_TASK_ENTRY") != "loopx-planned":
        raise ValueError("SForge planning entry requires loopx-planned")
    remaining = float(env["LOOPX_PHASE_DEADLINE_EPOCH"]) - time.time()
    if not math.isfinite(remaining):
        raise ValueError("Invalid planning budget")
    if remaining <= 160:
        return False
    path = Path(env["LOOPX_PLANNING_RESULT"])
    if path.exists():
        saved = json.loads(path.read_text())
        current = task_plan_packet(env, loopx_command(env))
        for key in ("input_digest", "goal_id", "agent_id"):
            if saved.get(key) != current[key]:
                raise ValueError("Saved planning receipt does not match current task")
        if saved.get("state_readback_verified") is not True:
            raise ValueError("Saved planning receipt is unverified")
        if saved.get("status") == "blocked":
            validate_plan_readback(saved, current, current)
            return False
        ids = saved.get("todo_ids")
        existing = {item["todo_id"] for item in current["existing_todos"]}
        if (saved.get("status") != "ready" or not isinstance(ids, list) or not ids
                or any(not isinstance(item, str) for item in ids)
                or len(set(ids)) != len(ids) or not set(ids).issubset(existing)):
            raise ValueError("Saved planning receipt has no valid Todo lineage")
        # Original Todos may now be completed. Do not replan on every native
        # process resume or treat this historical receipt as new admission.
        return True
    planning_env = env | {"LOOPX_TASK_STAGE": "plan"}
    # Native SForge owns one absolute trial deadline for planning and execution.
    # Do not inherit a separate planning cap from the shared worker environment.
    planning_env.pop("LOOPX_PLANNING_TIMEOUT_SEC", None)
    receipt = run_once(planning_env)
    if receipt.get("ok") is not True:
        raise RuntimeError("Planning failed; execution was not started")
    entry = receipt.get("planning") or {}
    if entry.get("state_readback_verified") is not True:
        raise RuntimeError("Planning returned no verified Todo readback")
    return entry.get("status") == "ready"


def main() -> int:
    if not sys.argv[1:]:
        raise ValueError("Missing execution command")
    def cancelled(signum, frame):
        raise KeyboardInterrupt("planning entry cancelled")

    signal.signal(signal.SIGTERM, cancelled)
    env = dict(os.environ)
    if not prepare_entry(env):
        # A blocked or exhausted planning checkpoint never ran the solver.
        # Native process completion must not qualify it as a finished trial.
        return 1
    # A successful plan may itself consume the remaining execution allowance.
    if float(env["LOOPX_PHASE_DEADLINE_EPOCH"]) - time.time() <= 160:
        return 1
    # Native SForge owns environment filtering and the outer phase deadline;
    # retain its proxy/isolation inputs rather than launching from setup hooks.
    env["LOOPX_TASK_STAGE"] = "execute"
    Path(env["LOOPX_PLANNING_RESULT"]).with_name("execution-started").touch()
    os.execvpe(sys.argv[1], sys.argv[1:], env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
