from __future__ import annotations

from pathlib import Path
from typing import Any

from .capabilities.issue_fix.pr_gate_reconcile import (
    reconcile_acknowledged_issue_fix_pr_reviews,
)
from .capabilities.turn_start import extend_turn_start_dispatch
from .control_plane.effect_runtime import effect_runtime_request_scope
from .control_plane.todos.list_readback import list_goal_todos
from .history import load_registry
from .paths import resolve_runtime_root


HEARTBEAT_PRE_QUOTA_SCHEMA_VERSION = "heartbeat_pre_quota_v0"


def run_heartbeat_pre_quota(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    agent_id: str,
    fetch_timeout_seconds: int = 10,
) -> dict[str, Any]:
    with effect_runtime_request_scope():
        runtime = resolve_runtime_root(
            load_registry(registry_path), runtime_root_arg, registry_path=registry_path
        )
        shared_todo_cache: dict[str, Any] = {"todos": None, "dirty": False}

        def load_shared_todos() -> list[dict[str, Any]]:
            cached = shared_todo_cache["todos"]
            if cached is None or shared_todo_cache["dirty"]:
                cached = list_goal_todos(
                    registry_path=registry_path,
                    runtime_root_arg=str(runtime),
                    goal_id=goal_id,
                    role=None,
                )["todos"]
                shared_todo_cache["todos"] = cached
                shared_todo_cache["dirty"] = False
            return list(cached)

        def invalidate_shared_todos() -> None:
            shared_todo_cache["todos"] = None
            shared_todo_cache["dirty"] = True

        def load_shared_agent_todos() -> list[dict[str, Any]] | None:
            cached = shared_todo_cache["todos"]
            if cached is None or shared_todo_cache["dirty"]:
                return None
            return [
                dict(todo)
                for todo in cached
                if isinstance(todo, dict) and todo.get("role") == "agent"
            ]

        try:
            review_reconciliation = reconcile_acknowledged_issue_fix_pr_reviews(
                registry_path=registry_path,
                runtime_root_arg=str(runtime),
                goal_id=goal_id,
                agent_id=agent_id,
                project=None,
                fetch_metadata=True,
                fetch_timeout_seconds=fetch_timeout_seconds,
                execute=True,
                todo_loader=load_shared_todos,
                on_todo_mutation=invalidate_shared_todos,
            )
            degraded = bool(review_reconciliation.get("degraded"))
            failure_count = int(review_reconciliation.get("failure_count") or 0)
        except Exception as exc:
            degraded = True
            failure_count = 1
            review_reconciliation = {
                "ok": True,
                "degraded": True,
                "failure_count": 1,
                "failure_categories": [type(exc).__name__],
                "external_read_count": 0,
                "write_count": 0,
            }

        dispatch = extend_turn_start_dispatch(
            {},
            registry_path=registry_path,
            runtime_root=runtime,
            goal_id=goal_id,
            agent_id=agent_id,
            available=None,
            provider_options={
                "issue-fix": {
                    "fetch_timeout_seconds": fetch_timeout_seconds,
                    "shared_agent_todos_loader": load_shared_agent_todos,
                }
            },
        )
        # Keep the released command's compatibility projection. Observation itself
        # is registered and permission-resolved by the same lifecycle as execution.
        failed = next((row for row in dispatch.get("failures", [])
            if row.get("capability_id") == "issue-fix"), None)
        dependencies = next((row["readback"] for row in dispatch.get("results", [])
            if row.get("capability_id") == "issue-fix" and isinstance(row.get("readback"), dict)),
            {"ok": not bool(failed), "degraded": bool(failed), "failure_count": 1 if failed else 0,
             "external_read_count": None if failed else 0, "state_written": None if failed else False,
             "status": "observation_unavailable" if failed else "not_configured_or_unavailable"})
        degraded = degraded or bool(dependencies.get("degraded"))
        failure_count += int(dependencies.get("failure_count") or 0)

        return {
            "ok": True,
            "schema_version": HEARTBEAT_PRE_QUOTA_SCHEMA_VERSION,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "degraded": degraded,
            "failure_count": failure_count,
            "checks": {
                "acknowledged_pr_reviews": review_reconciliation,
                "pr_dependencies": dependencies,
            },
            "turn_start_capability_hook_dispatch": dispatch,
            "quota_spend_required": False,
            "continue_to_quota": True,
        }


def render_heartbeat_pre_quota_markdown(payload: dict[str, Any]) -> str:
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    review = (
        checks.get("acknowledged_pr_reviews")
        if isinstance(checks.get("acknowledged_pr_reviews"), dict)
        else {}
    )
    return "\n".join(
        [
            "# LoopX Heartbeat Pre-Quota",
            "",
            f"- ok: `{payload.get('ok')}`",
            f"- degraded: `{payload.get('degraded')}`",
            f"- reconciled_count: `{review.get('reconciled_count', 0)}`",
            f"- failure_count: `{payload.get('failure_count')}`",
            f"- PR dependency reads: `{checks.get('pr_dependencies', {}).get('external_read_count', 0)}`",
            f"- continue_to_quota: `{payload.get('continue_to_quota')}`",
            "- quota_spend_required: `False`",
        ]
    )
