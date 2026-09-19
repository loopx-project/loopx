from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...control_plane.scheduler.monitor_todo import (
    monitor_next_due_at,
)
from ...control_plane.todos.monitor_metadata import MonitorPollObservation
from ...control_plane.coordination.local_authority import LOCAL_AUTHORITY_SOURCES
from ...control_plane.todos.provider_projection import settle_canonical_todo_projection
from ...control_plane.work_items.task_lease import runtime_root_from_registry
from ...todos import (
    add_goal_todo,
    complete_goal_todo,
    list_goal_todos,
    update_goal_todo,
)

ISSUE_FIX_GROUPED_MONITOR_WRITEBACK_SCHEMA_VERSION = (
    "issue_fix_grouped_monitor_writeback_v0"
)
DEFAULT_ISSUE_FIX_MONITOR_CADENCE = "30m"


def _load_lifecycle_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"issue-fix PR lifecycle ledger line {line_number} is invalid JSON"
            ) from exc
        if not isinstance(row, dict):
            raise TypeError(
                f"issue-fix PR lifecycle ledger line {line_number} must be an object"
            )
        rows.append(row)
    return rows


def _active_grouped_monitors(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        projection = row.get("grouped_monitor_projection")
        if not isinstance(projection, Mapping):
            continue
        if projection.get("materialize_nonempty_bucket_monitor") is not True:
            continue
        target_key = str(projection.get("target_key") or "").strip()
        member_key = str(projection.get("member_key") or "").strip()
        action_kind = str(projection.get("action_kind") or "").strip()
        state_bucket = str(projection.get("state_bucket") or "").strip()
        repository = str(projection.get("repository") or "").strip()
        if (
            not target_key
            or not member_key
            or not action_kind
            or not state_bucket
            or not repository
        ):
            continue
        group = grouped.setdefault(
            target_key,
            {
                "target_key": target_key,
                "action_kind": action_kind,
                "state_bucket": state_bucket,
                "repository": repository,
                "member_keys": set(),
            },
        )
        group["member_keys"].add(member_key)
    return grouped


def _existing_issue_fix_monitors(
    *, registry_path: Path, goal_id: str, project: Path
) -> tuple[dict[str, dict[str, Any]], str | None]:
    payload = list_goal_todos(
        registry_path=registry_path,
        goal_id=goal_id,
        role="agent",
        project=project,
    )
    monitors: dict[str, dict[str, Any]] = {}
    for item in payload.get("todos") or []:
        if not isinstance(item, dict):
            continue
        target_key = str(item.get("target_key") or "").strip()
        action_kind = str(item.get("action_kind") or "").strip()
        if (
            item.get("task_class") == "continuous_monitor"
            and target_key.startswith("github-pr-state-")
            and action_kind.startswith("issue_fix_pr_state_")
        ):
            monitors[target_key] = item
    return monitors, (payload.get("authority_read") or {}).get("source_authority")


def _group_fingerprint(member_keys: set[str]) -> str:
    encoded = json.dumps(sorted(member_keys), separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def materialize_issue_fix_grouped_monitors(
    *,
    registry_path: Path,
    goal_id: str,
    project: Path,
    ledger_path: Path,
    claimed_by: str,
    cadence: str,
    generated_at: str,
) -> dict[str, Any]:
    """Reconcile issue-fix PR lifecycle buckets into generic monitor todos."""

    groups = _active_grouped_monitors(_load_lifecycle_rows(ledger_path))
    existing, source_authority = _existing_issue_fix_monitors(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
    )
    next_due_at = monitor_next_due_at(
        generated_at=generated_at,
        cadence=cadence,
    )
    if next_due_at is None:
        raise ValueError("issue-fix grouped monitor cadence must be parseable")

    writes: list[dict[str, Any]] = []
    for target_key, group in sorted(groups.items()):
        member_keys = set(group["member_keys"])
        result_hash = _group_fingerprint(member_keys)
        previous = existing.get(target_key)
        previous_hash = str((previous or {}).get("result_hash") or "")
        reopening = bool((previous or {}).get("done"))
        material_change = reopening or previous_hash != result_hash
        monitor_metadata = {
            "target_key": target_key,
            "cadence": cadence,
            "next_due_at": next_due_at,
            "last_checked_at": generated_at,
            "result_hash": result_hash,
            "consecutive_no_change": "0",
            "material_change": "true" if material_change else "false",
            "watch_only": "true",
        }
        reason = (
            f"Issue-fix PR lifecycle bucket {group['state_bucket']} contains "
            f"{len(member_keys)} active member(s)."
        )
        if previous:
            schedule_complete = bool(
                str(previous.get("cadence") or "").strip() == cadence
                and str(previous.get("next_due_at") or "").strip()
            )
            if not reopening and not material_change and schedule_complete:
                writes.append(
                    {
                        "operation": "unchanged",
                        "target_key": target_key,
                        "write_performed": False,
                    }
                )
                continue
            result = update_goal_todo(
                registry_path=registry_path,
                goal_id=goal_id,
                todo_id=str(previous["todo_id"]),
                role="agent",
                status="open" if reopening else None,
                reason=reason,
                # Membership is an observation, not precomputed Todo state.
                # The writer derives counters/generation against its locked
                # snapshot, just like quota monitor-poll.
                monitor_metadata=MonitorPollObservation(
                    generated_at=generated_at, result_hash=result_hash,
                    material_change=material_change, target_key=target_key,
                    cadence=cadence, next_due_at=next_due_at,
                ),
                no_followup=False if reopening else None,
                agent_id=claimed_by,
                project=project,
            )
            writes.append(
                {
                    "operation": "update",
                    "target_key": target_key,
                    "write_performed": bool(result.get("changed")),
                }
            )
            continue
        result = add_goal_todo(
            registry_path=registry_path,
            goal_id=goal_id,
            role="agent",
            text=(
                f"[P2] Monitor {group['repository']} issue-fix PR lifecycle "
                f"bucket {group['state_bucket']} for material changes."
            ),
            task_class="continuous_monitor",
            action_kind=str(group["action_kind"]),
            claimed_by=claimed_by,
            monitor_metadata=monitor_metadata,
            project=project,
        )
        writes.append(
            {
                "operation": "add",
                "target_key": target_key,
                "write_performed": bool(
                    result.get("added") or result.get("metadata_updated")
                ),
            }
        )

    for target_key, item in sorted(existing.items()):
        if target_key in groups or item.get("done") is True:
            continue
        result = complete_goal_todo(
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=str(item["todo_id"]),
            role="agent",
            evidence=f"Issue-fix PR lifecycle bucket {target_key} is empty.",
            no_followup=True,
            claimed_by=str(item.get("claimed_by") or claimed_by),
            agent_id=claimed_by,
            project=project,
        )
        writes.append(
            {
                "operation": "complete",
                "target_key": target_key,
                "write_performed": bool(result.get("changed")),
            }
        )

    result = {
        "schema_version": ISSUE_FIX_GROUPED_MONITOR_WRITEBACK_SCHEMA_VERSION,
        "write_performed": any(item["write_performed"] for item in writes),
        "path_recorded": False,
        "active_bucket_count": len(groups),
        "active_member_count": sum(
            len(group["member_keys"]) for group in groups.values()
        ),
        "next_due_at": next_due_at if groups else None,
        "writes": writes,
    }
    if source_authority in LOCAL_AUTHORITY_SOURCES:
        # An unchanged observation may follow a committed write whose display
        # delivery failed. Drain the current head without repeating that write.
        result = settle_canonical_todo_projection(
            payload={**result, "source_authority": source_authority},
            registry_path=registry_path,
            runtime_root=runtime_root_from_registry(registry_path, None),
            goal_id=goal_id, project=project,
        )
    return result
