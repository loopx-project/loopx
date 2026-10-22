"""Register PR dependency observation with the shared pre-admission lifecycle."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...agent_registry import load_goal_from_registry
from ...control_plane.agents.capability_memory import (
    agent_capability_memory, resolve_agent_capabilities,
)
from ...control_plane.capability_hooks import (
    TURN_START_HOOK_RESULT_SCHEMA_VERSION,
    TurnStartHookRegistration,
)
from .pr_wait_reconcile import reconcile_pr_wait_dependencies


PR_WAIT_OBSERVATION_HOOK_ID = "issue_fix.pr_wait_observation"


def _bounded_readback(readback: dict[str, Any]) -> dict[str, Any]:
    """Bound diagnostics only, after the full-source plan and effects finish."""
    def row(value: dict[str, Any]) -> dict[str, Any]:
        ids = value.get("todo_ids")
        return {**value, "todo_ids": ids[:16], "todo_ids_omitted": max(0, len(ids) - 16)} if isinstance(ids, list) else value

    projected = dict(readback)
    projected["results"] = [row(value) for value in readback.get("results", [])]
    if isinstance(readback.get("plan"), dict):
        plan = dict(readback["plan"])
        coverage = {}
        for field in ("targets", "waiting_targets", "unresolved"):
            values = plan.get(field, [])
            plan[field] = [row(value) for value in values[:16]]
            coverage[field] = {"total": len(values), "included": len(plan[field]),
                               "omitted": max(0, len(values) - 16)}
        plan["diagnostic_coverage"] = coverage
        projected["plan"] = plan
    return projected


def _availability(registry_path: Path, runtime_root: Path, goal_id: str,
                  agent_id: str, available: Any) -> dict[str, Any]:
    goal = load_goal_from_registry(registry_path, goal_id)
    state = agent_capability_memory(registry_path=registry_path, runtime_root=runtime_root,
        goal_id=goal_id, agent_id=agent_id)
    # Resolve from the same scoped owner as quota, with the successfully read
    # state supplied explicitly; an unavailable permission source cannot fall
    # back to an inherited grant for provider IO.
    return resolve_agent_capabilities(status_payload={"registry": str(registry_path),
        "runtime_root": str(runtime_root)}, goal_id=goal_id,
        agent_identity={"agent_id": agent_id}, item=goal, project_asset={},
        available=available, state=state)


def turn_start_hook(*, registry_path: Path, runtime_root: Path, goal_id: str,
                    agent_id: str, available: Any,
                    fetch_timeout_seconds: int = 10) -> TurnStartHookRegistration | None:
    # The existing typed owner resolves declarations, remembered host observations
    # and revocation. A Todo requirement is never a capability grant.
    try:
        if "external_evidence_poll" not in _availability(
            registry_path, runtime_root, goal_id, agent_id, available,
        )["effective"]:
            return None
    except (ValueError, RuntimeError, OSError, TypeError):
        # Register a failure readback, never permission to contact the provider.
        pass

    def produce() -> dict[str, Any]:
        base = {
            "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": PR_WAIT_OBSERVATION_HOOK_ID, "capability_id": "issue-fix", "phase": "turn_start",
            "status": "not_applicable", "observation_count": 0,
            "agent_read_required": False, "external_reads_performed": False,
            "external_writes_performed": False, "local_private_state_mutated": False,
            "private_content_returned": False, "provider_payload_returned": False,
            "error_code": None,
        }
        started = time.monotonic_ns()
        try:
            # Recheck at invocation, so revocation after registration is effective.
            if "external_evidence_poll" not in _availability(
                registry_path, runtime_root, goal_id, agent_id, available,
            )["effective"]:
                return base
        except (ValueError, RuntimeError, OSError, TypeError):
            return {**base, "status": "unavailable", "error_code": "capability_source_unavailable",
                    "readback": {"ok": False, "degraded": True, "failure_count": 1,
                        "external_read_count": 0, "state_written": False,
                        "summary_lines": ["Observation permission source is unavailable; provider not contacted."]}}
        try:
            readback = reconcile_pr_wait_dependencies(registry_path=registry_path,
                runtime_root_arg=str(runtime_root), goal_id=goal_id, agent_id=agent_id,
                fetch_timeout_seconds=fetch_timeout_seconds)
            count = readback["external_read_count"]
            written = count > 0
            status = "partial" if readback.get("degraded") else "observed" if count else "empty"
            error = "dependency_observation_unavailable" if readback.get("degraded") else None
        except Exception as exc:  # noqa: BLE001 - optional observation is fail-open
            # Provider/log response loss cannot certify zero reads or zero writes.
            readback = {"ok": False, "degraded": True, "failure_count": 1,
                        "external_read_count": None, "error_category": type(exc).__name__}
            count, written, status, error = None, None, "failed", "dependency_observation_failed"
        readback["state_written"] = written
        readback["duration_ms"] = (time.monotonic_ns() - started) // 1_000_000
        readback["summary_lines"] = [
            f"PR dependency reads: {count}",
            f"PR observation duration_ms: {readback['duration_ms']}",
            f"PR dependency observation degraded: {readback.get('degraded')}",
            f"PR unavailable dependencies: {readback.get('unavailable_dependency_count')}",
            f"PR observation state_written: {written}",
        ]
        if readback.get("error_category"):
            readback["summary_lines"].append(f"PR observation error: {readback['error_category']}")
        for item in readback.get("results", []):
            readback["summary_lines"].append(f"PR dependency {item['pr_ref']}: {item['state']}")
        plan = readback.get("plan")
        if isinstance(plan, dict):
            readback["summary_lines"].append(f"PR retry interval_seconds: {plan['poll_interval_seconds']}")
            observed = {item["pr_ref"] for item in readback.get("results", [])}
            for target in plan.get("waiting_targets", []):
                if target.get("last_observed_state") == "FAILED" and target["pr_ref"] not in observed:
                    readback["summary_lines"].append(
                        f"PR dependency {target['pr_ref']}: FAILED; next_check_at: {target['next_due_at']}")
                    if len(readback["summary_lines"]) >= 16:
                        break
        return {**base, "status": status, "observation_count": count or 0,
                "external_reads_performed": None if count is None else count > 0,
                "local_private_state_mutated": written, "error_code": error,
                "readback": _bounded_readback(readback)}

    return TurnStartHookRegistration(hook_id=PR_WAIT_OBSERVATION_HOOK_ID, capability_id="issue-fix",
        requested_read_scope=("goal_agent_registration", "canonical_todos", "github_pr_metadata", "goal_event_log"),
        requested_write_scope=("goal_event_log",), producer=produce, max_result_bytes=64 * 1024)
