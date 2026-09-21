from __future__ import annotations

from pathlib import Path
from typing import Any

from ...history import load_registry
from ...paths import resolve_runtime_root
from ...rollout_event_log import (
    append_rollout_event,
    build_rollout_event,
    load_rollout_events,
    rollout_event_log_path,
)


def append_pr_merge_rollout_event(
    *,
    payload: dict[str, Any],
    goal_id: str,
    registry_path: Path | None,
    runtime_root_arg: str | None,
) -> dict[str, Any]:
    observation = payload.get("observation")
    if (
        not isinstance(observation, dict)
        or str(observation.get("state") or "").upper() != "MERGED"
    ):
        raise ValueError("PR merge rollout event requires a merged lifecycle observation")
    repo = str(observation.get("repo") or "").strip().lower()
    number = observation.get("number")
    if not repo or not isinstance(number, int) or number <= 0:
        raise ValueError("merged lifecycle observation must include a repository and PR number")

    if runtime_root_arg:
        runtime_root = Path(runtime_root_arg).expanduser()
    elif registry_path is not None and registry_path.expanduser().exists():
        runtime_root = resolve_runtime_root(
            load_registry(registry_path.expanduser()),
            None,
        )
    else:
        return {
            "schema_version": "issue_fix_pr_merge_rollout_event_receipt_v0",
            "recorded": False,
            "appended": False,
            "skip_reason": "connected_runtime_unavailable",
        }

    pr_ref = f"{repo}#{number}"
    repository_aliases = sorted(
        {
            str(alias).strip().lower()
            for alias in observation.get("repository_aliases") or []
            if isinstance(alias, str)
            and str(alias).strip()
            and str(alias).strip().lower() != repo
        }
    )
    event = build_rollout_event(
        goal_id=goal_id,
        event_kind="pr_merge",
        pr_ref=pr_ref,
        source_refs=[
            {"kind": "pull_request", "ref": f"{alias}#{number}"}
            for alias in repository_aliases
        ],
        status="merged",
        summary=f"PR {pr_ref} merged; dependent resume conditions may proceed.",
        recorded_at=str(
            observation.get("merged_at")
            or observation.get("closed_at")
            or payload.get("generated_at")
            or ""
        ).strip()
        or None,
    )
    log_path = rollout_event_log_path(runtime_root, goal_id)
    existing_event = next(
        (
            item
            for item in load_rollout_events(log_path)
            if item.get("event_id") == event["event_id"]
        ),
        None,
    )
    appended = existing_event is None
    recorded_event = append_rollout_event(log_path, event) if appended else existing_event
    return {
        "schema_version": "issue_fix_pr_merge_rollout_event_receipt_v0",
        "recorded": True,
        "appended": appended,
        "already_recorded": not appended,
        "event_id": recorded_event["event_id"],
        "event_kind": recorded_event["event_kind"],
        "recorded_at": recorded_event["recorded_at"],
        "status": recorded_event.get("status"),
        "pr_ref": pr_ref,
        "repository_aliases": repository_aliases,
    }
