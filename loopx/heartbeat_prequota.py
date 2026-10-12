from __future__ import annotations

from pathlib import Path
from typing import Any

from .capabilities.issue_fix.pr_gate_reconcile import (
    reconcile_acknowledged_issue_fix_pr_reviews,
)
from .capabilities.issue_fix.pr_wait_reconcile import reconcile_pr_wait_dependencies


HEARTBEAT_PRE_QUOTA_SCHEMA_VERSION = "heartbeat_pre_quota_v0"


def run_heartbeat_pre_quota(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    agent_id: str,
    fetch_timeout_seconds: int = 10,
) -> dict[str, Any]:
    try:
        review_reconciliation = reconcile_acknowledged_issue_fix_pr_reviews(
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            goal_id=goal_id,
            agent_id=agent_id,
            project=None,
            fetch_metadata=True,
            fetch_timeout_seconds=fetch_timeout_seconds,
            execute=True,
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

    try:
        dependencies = reconcile_pr_wait_dependencies(registry_path=registry_path,
            runtime_root_arg=runtime_root_arg, goal_id=goal_id, agent_id=agent_id,
            fetch_timeout_seconds=fetch_timeout_seconds)
    except Exception as exc:
        dependencies = {"ok": False, "degraded": True, "failure_count": 1,
                        "error_category": type(exc).__name__, "external_read_count": 0}
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
