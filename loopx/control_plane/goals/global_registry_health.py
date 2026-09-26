from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


def global_registry_finding(
    *,
    kind: str,
    severity: str,
    message: str,
    recommended_action: str,
    goal_id: str | None = None,
    path: Path | None = None,
    goal_ids: list[str] | None = None,
) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "kind": kind,
        "severity": severity,
        "message": message,
        "recommended_action": recommended_action,
    }
    if goal_id:
        finding["goal_id"] = goal_id
    if path:
        finding["path"] = str(path)
    if goal_ids:
        finding["goal_ids"] = goal_ids
    return finding


def collect_global_registry_health(
    *,
    registry_path: Path,
    runtime_root: Path,
    current_registry: dict[str, Any],
    global_registry_path: Callable[[Path], Path],
    load_registry: Callable[[Path], dict[str, Any]],
    registry_goals: Callable[[dict[str, Any]], list[dict[str, Any]]],
    same_path: Callable[[Path, Path], bool],
    resolve_goal_local_path: Callable[..., Path | None],
    parse_timestamp: Callable[[Any], datetime | None],
) -> dict[str, Any]:
    global_path = global_registry_path(runtime_root)
    if not global_path.exists():
        return {
            "available": False,
            "read_status": "missing",
            "ok": True,
            "registry": str(global_path),
            "current_registry": str(registry_path),
            "current_registry_is_global": False,
            "summary": {"high": 0, "action": 0, "info": 0, "checks": 0, "findings": 0},
            "findings": [],
            "checks": [],
        }

    try:
        global_registry = load_registry(global_path)
    except (OSError, ValueError):
        return {
            "available": False,
            "read_status": "unreadable",
            "ok": False,
            "registry": str(global_path),
            "current_registry": str(registry_path),
            "current_registry_is_global": False,
            "summary": {"high": 1, "action": 0, "info": 0, "checks": 1, "findings": 1},
            "findings": [global_registry_finding(
                kind="global_registry_unreadable", severity="high",
                message="global membership could not be read",
                recommended_action="repair the global registry source and retry the projection",
            )],
            "checks": ["global registry readability"],
        }
    global_goals = registry_goals(global_registry)
    current_goals = registry_goals(current_registry)
    current_ids = {str(goal.get("id")) for goal in current_goals if goal.get("id")}
    global_ids = [str(goal.get("id")) for goal in global_goals if goal.get("id")]
    global_id_set = set(global_ids)
    source_registries: set[str] = set()
    findings: list[dict[str, Any]] = []
    checks: list[str] = []

    current_is_global = same_path(registry_path, global_path)
    id_counts = Counter(global_ids)
    for goal_id, count in sorted(id_counts.items()):
        if count <= 1:
            continue
        findings.append(
            global_registry_finding(
                kind="duplicate_goal_id",
                severity="high",
                goal_id=goal_id,
                message=f"global registry contains {count} entries for `{goal_id}`",
                recommended_action="deduplicate the global registry before trusting multi-project routing",
            )
        )

    for goal in global_goals:
        goal_id = str(goal.get("id") or "unknown-goal")
        source_path = resolve_goal_local_path(
            goal.get("source_registry"),
            goal,
            fallback_base=global_path.parent,
        )
        state_path = resolve_goal_local_path(
            goal.get("state_file"),
            goal,
            fallback_base=global_path.parent,
        )
        source_missing = source_path is None or not source_path.exists()
        state_missing = state_path is None or not state_path.exists()
        orphan_retirement_action = (
            f"preview `loopx retire-global-goal --goal-id {goal_id}`; execute only "
            "after confirming both the source registry and active state are obsolete"
        )
        if source_path:
            source_registries.add(str(source_path))
            if not source_path.exists():
                findings.append(
                    global_registry_finding(
                        kind="source_registry_missing",
                        severity="action",
                        goal_id=goal_id,
                        path=source_path,
                        message=f"`{goal_id}` source registry is missing",
                        recommended_action=(
                            orphan_retirement_action
                            if state_missing
                            else f"reconnect `{goal_id}` from its project"
                        ),
                    )
                )
            else:
                synced_at = parse_timestamp(goal.get("synced_at"))
                if synced_at:
                    source_mtime = datetime.fromtimestamp(source_path.stat().st_mtime, tz=timezone.utc)
                    if source_mtime > synced_at.astimezone(timezone.utc) + timedelta(seconds=5):
                        findings.append(
                            global_registry_finding(
                                kind="stale_source_registry",
                                severity="action",
                                goal_id=goal_id,
                                path=source_path,
                                message=f"`{goal_id}` source registry changed after its last global sync",
                                recommended_action=(
                                    f"run `loopx sync-global --goal-id {goal_id}` from the source project"
                                ),
                            )
                        )
        if state_path and not state_path.exists():
            findings.append(
                global_registry_finding(
                    kind="state_file_missing",
                    severity="action",
                    goal_id=goal_id,
                    path=state_path,
                    message=f"`{goal_id}` active state file is missing",
                    recommended_action=(
                        orphan_retirement_action
                        if source_missing
                        else f"repair `{goal_id}` state_file or reconnect the project"
                    ),
                )
            )
        if not state_path:
            findings.append(
                global_registry_finding(
                    kind="state_file_not_declared",
                    severity="action",
                    goal_id=goal_id,
                    message=f"`{goal_id}` does not declare a state_file",
                    recommended_action=f"reconnect `{goal_id}` with a durable active goal state file",
                )
            )

    missing_from_current = sorted(global_id_set - current_ids)
    if not current_is_global and missing_from_current:
        shown = missing_from_current[:8]
        findings.append(
            global_registry_finding(
                kind="current_registry_scope_excludes_global_goals",
                severity="info",
                message=f"current registry excludes {len(missing_from_current)} global goal(s)",
                recommended_action=(
                    "for multi-project dashboard/controller status, run `loopx status` "
                    "without `--registry`, pass the global registry, or start `serve-status --global-registry`"
                ),
                goal_ids=shown,
            )
        )

    checks.append(f"global registry goals checked: {len(global_goals)}")
    checks.append(f"global source registries checked: {len(source_registries)}")
    severity_counts = Counter(str(finding.get("severity") or "info") for finding in findings)
    return {
        "available": True,
        "ok": severity_counts.get("high", 0) == 0,
        "registry": str(global_path),
        "current_registry": str(registry_path),
        "current_registry_is_global": current_is_global,
        "global_goal_count": len(global_goals),
        "current_goal_count": len(current_goals),
        "current_registry_excluded_goal_count": 0 if current_is_global else len(missing_from_current),
        "current_registry_excluded_goal_ids": [] if current_is_global else missing_from_current[:8],
        "source_registry_count": len(source_registries),
        "summary": {
            "high": severity_counts.get("high", 0),
            "action": severity_counts.get("action", 0),
            "info": severity_counts.get("info", 0),
            "checks": len(checks),
            "findings": len(findings),
        },
        "findings": findings,
        "checks": checks,
    }
