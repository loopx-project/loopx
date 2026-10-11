"""Transport the typed dependency poll plan into existing public lifecycle facts.

This internal issue-fix adapter changes neither Todo requirements nor claims,
leases, status, settlement or review authority. It does not consult an open PR
queue: dependencies are selected from the complete current canonical Todo head.
"""
from __future__ import annotations

from pathlib import Path
from subprocess import TimeoutExpired
from typing import Any

from ...agent_registry import registered_agent_ids_from_registry
from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.runtime.time import now_utc_iso
from ...control_plane.todos.resume_condition import compact_todo_resume_items
from ...history import load_registry
from ...paths import resolve_runtime_root
from ...rollout_event_log import (
    append_rollout_event, build_rollout_event, rollout_event_log_path,
)
from ...todos import list_goal_todos
from . import pr_lifecycle
from .pr_lifecycle_rollout import append_pr_merge_rollout_event


def reconcile_pr_wait_dependencies(
    *, registry_path: Path, runtime_root_arg: str | None, goal_id: str,
    agent_id: str, fetch_timeout_seconds: int = 10,
) -> dict[str, Any]:
    if agent_id not in registered_agent_ids_from_registry(registry_path, goal_id):
        raise ValueError("PR dependency observation requires a registered agent")
    runtime = (Path(runtime_root_arg).expanduser() if runtime_root_arg
               else resolve_runtime_root(load_registry(registry_path), None))
    # Deliberately omit list --agent-id and display limits. The typed owner must
    # see archived/deferred/claimed facts before choosing this actor's subjects.
    todos = list_goal_todos(registry_path=registry_path, runtime_root_arg=str(runtime),
        goal_id=goal_id, role="agent")["todos"]
    compact = compact_todo_resume_items(todos)
    for row, original in zip(compact, todos, strict=True):
        if "excluded_agents" in original:
            row["excluded_agents"] = original["excluded_agents"]
    log = rollout_event_log_path(runtime, goal_id)
    plan = effect_runtime_result("todo.pr_wait_observation.plan", {
        "schema_version": "todo_pr_wait_observation_request_v0", "agent_id": agent_id,
        "generated_at": now_utc_iso(), "items": compact,
        "rollout_events": [],
        "rollout_event_source": {"runtime_root": str(runtime), "goal_id": goal_id},
    })
    results: list[dict[str, Any]] = []
    for target in plan["targets"]:
        reference = {"repo": target["repo"], "number": target["number"]}
        result = {"pr_ref": target["pr_ref"], "todo_ids": target["todo_ids"],
                  "state": "FAILED", "merge_event_recorded": False}
        try:
            metadata = pr_lifecycle.fetch_github_pr_lifecycle_payload(reference,
                timeout_seconds=fetch_timeout_seconds, compact=True)
            # A provider redirect or malformed success must never attest another
            # repository's identically numbered dependency. No default OPEN.
            if str(metadata.get("url") or "").lower().rstrip("/") != target["url"].lower():
                raise ValueError("PR dependency provider identity mismatch")
            state = str(metadata.get("state") or "").upper()
            if state not in {"OPEN", "CLOSED", "MERGED"}:
                raise ValueError("PR dependency provider state unavailable")
            if state == "MERGED" and not metadata.get("mergedAt"):
                raise ValueError("Merged PR observation lacks its terminal timestamp")
            if state == "MERGED":
                lifecycle = pr_lifecycle.build_issue_fix_pr_lifecycle_monitor_packet(
                    repo=target["repo"], pr_ref=target["pr_ref"], url=target["url"],
                    provider_payload=metadata)
                result["merge_event_recorded"] = append_pr_merge_rollout_event(payload=lifecycle,
                    goal_id=goal_id, registry_path=registry_path, runtime_root_arg=str(runtime))["recorded"]
            result["state"] = state
        except (ValueError, RuntimeError, OSError, TimeoutExpired) as error:
            result["error_category"] = type(error).__name__
        # Persist failed attempts too: restarts and repeated heartbeats respect
        # the same cadence. This validation is not delivery/settlement evidence.
        append_rollout_event(log, build_rollout_event(goal_id=goal_id,
            event_kind="validation", agent_id=agent_id, pr_ref=target["pr_ref"],
            classification=plan["observation_classification"], status=result["state"],
            recorded_at=now_utc_iso(), summary="Observed exact PR wait dependency; no Todo transition."))
        results.append(result)
    failures = sum(row["state"] == "FAILED" for row in results)
    observed = {row["pr_ref"]: row["state"] for row in results}
    retained_failures = sum(observed.get(row["pr_ref"], row["last_observed_state"]) == "FAILED"
                            for row in plan["waiting_targets"])
    return {"ok": True, "degraded": retained_failures > 0 or bool(plan["unresolved"]),
        "failure_count": failures, "external_read_count": len(results),
        "unavailable_dependency_count": retained_failures,
        "merged_count": sum(row["merge_event_recorded"] for row in results),
        "closed_without_merge_count": sum(row["state"] == "CLOSED" for row in results),
        "results": results, "plan": plan, "todo_write_performed": False}
