"""Bounded, read-only Goal evidence for the manager; no lifecycle authority."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .agent_registry import registered_agent_ids_for_goal
from .control_plane.goals.activation import goal_activation_state
from .control_plane.quota.should_run import build_quota_should_run
from .global_todos import _classify_goal_todos
from .paths import resolve_runtime_root
from .status import collect_status

# A caller that asked for Goal lifecycle evidence always gets an answer. Either
# the projection the status collector already derived (carrying its own
# `goal_artifact_lifecycle_projection_v0` schema) or this typed gap record. The
# status owner deliberately omits the key when it cannot derive a projection;
# forwarding that omission would make "not derived" and "nobody looked" read
# the same to the manager, which is the silent-gap failure this contract names.
GOAL_LIFECYCLE_READBACK_SCHEMA_VERSION = "goal_lifecycle_readback_v0"


class SourceQuality(str, Enum):
    VERIFIED = "verified"
    STALE = "stale"
    CONFLICTING = "conflicting"
    UNREADABLE = "unreadable"
    PARTIAL = "partial"
    OMITTED = "omitted"


def _digest(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
    )


def _time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _identity(value: object) -> str | None:
    return (
        value
        if isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", value)
        else None
    )


def lifecycle_readback_unavailable(goal_id: str, reason: str) -> dict[str, Any]:
    """The typed gap a lifecycle-aware reader returns instead of silence."""

    return {
        "schema_version": GOAL_LIFECYCLE_READBACK_SCHEMA_VERSION,
        "goal_id": goal_id,
        "status": "unavailable",
        "reason": reason,
    }


def _lifecycle_readback(history: dict[str, Any], *, goal_id: str) -> dict[str, Any]:
    """Pass through the collected projection, or name why there is none.

    This never re-derives a projection, so the lifecycle read has one owner and
    no second collection path.
    """

    projection = history.get("artifact_lifecycle")
    if isinstance(projection, dict) and projection.get("milestones") is not None:
        return projection
    return lifecycle_readback_unavailable(goal_id, "projection_not_derived")


def _source_versions(goal: dict[str, Any], runtime_root: Path) -> dict[str, str | None]:
    paths = {"run_index": runtime_root / "goals" / goal["id"] / "runs" / "index.jsonl"}
    if goal.get("repo") and goal.get("state_file"):
        paths["declared_state"] = Path(goal["repo"]).expanduser() / goal["state_file"]
    versions = {}
    for label, path in paths.items():
        try:
            stat = path.stat()
            versions[label] = _digest(
                (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            )
        except OSError:
            versions[label] = None
    return versions


def _delivery(context: dict[str, Any], *, goal_id: str) -> dict[str, Any] | None:
    run = context.get("latest_evidence_delivery_run") or {}
    observation = run.get("progress_observation") or {}
    if run.get("goal_id") != goal_id or run.get("agent_id") != context.get("agent_id"):
        return None
    # The Core semantic slot already qualified the typed outcome and evidence
    # relation. This projection identifies the receipt, not artifact validity.
    return {
        "source_ref": _digest(run),
        "run_id": _identity(run.get("run_id")),
        "recorded_at": run.get("generated_at"),
        "agent_id": _identity(run.get("agent_id")),
        "todo_id": _identity(run.get("todo_id")),
        "outcome": run.get("delivery_outcome"),
        "evidence_refs": [
            _digest(ref) for ref in observation.get("evidence_ids", [])[:8]
        ],
        "evidence_count": len(observation.get("evidence_ids", [])),
        "omitted_evidence_count": max(0, len(observation.get("evidence_ids", [])) - 8),
        "verification": "core_recorded_evidence_refs",
    }


def _read_goal(
    goal: dict[str, Any],
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    runtime_root: Path,
    now: datetime,
    max_age_hours: float,
    include_goal_lifecycle: bool = False,
    shared_status: dict[str, Any] | None = None,
    source_versions_before: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    goal_id = goal["id"]
    agents = registered_agent_ids_for_goal(goal)
    row: dict[str, Any] = {
        "goal_id": goal_id,
        "host_id": _identity(goal.get("host_id")),
        "project_id": _identity(goal.get("project_id")),
        "quality": SourceQuality.UNREADABLE.value,
        "progress": "unknown",
        "warnings": [],
        "agents": [],
        "deliveries": [],
        "source": {
            "kind": "core_status",
            "revision": None,
            "observed_at": now.isoformat(),
        },
    }
    if include_goal_lifecycle:
        # Every Goal this reader attempted answers the lifecycle question, so a
        # failed or ambiguous source is named here instead of surfacing as
        # missing evidence the manager would have to interpret.
        row["goal_lifecycle"] = lifecycle_readback_unavailable(
            goal_id, "goal_read_incomplete"
        )
    try:
        versions_before = source_versions_before or _source_versions(goal, runtime_root)
        status = (
            shared_status
            if shared_status is not None
            else collect_status(
                registry_path=registry_path,
                runtime_root_override=runtime_root_override,
                scan_roots=[],
                limit=8,
                goal_id=goal_id,
                include_public_boundary_scan=False,
            )
        )
        row["source"]["revision"] = _digest(status)
        versions_after = _source_versions(goal, runtime_root)
        row["source"]["file_metadata_revisions"] = versions_after
        histories = [
            g
            for g in status.get("run_history", {}).get("goals", [])
            if g.get("id") == goal_id
        ]
        if len(histories) != 1:
            if len(histories) > 1:
                row["quality"] = SourceQuality.CONFLICTING.value
            row["warnings"].append("goal_source_missing_or_ambiguous")
            return row
        history = histories[0]
        if include_goal_lifecycle:
            row["goal_lifecycle"] = _lifecycle_readback(history, goal_id=goal_id)
        contexts = {
            c["agent_id"]: c
            for c in (history.get("semantic_history") or {}).get("agents", [])
        }
        latest = history.get("latest_status_run") or {}
        row["source"]["latest_recorded_at"] = latest.get("generated_at")
        recorded_at = _time(latest.get("generated_at"))
        stale = (
            recorded_at is None
            or (now - recorded_at).total_seconds() > max_age_hours * 3600
        )
        conflict = recorded_at is not None and recorded_at > now
        if versions_before != versions_after:
            conflict = True
            row["warnings"].append("source_changed_during_collection")
        if recorded_at is None:
            row["warnings"].append("progress_timestamp_unknown")
        elif stale:
            row["warnings"].append("recorded_progress_expired")
        partial = not agents or len(agents) > 8
        source_ok = status.get("ok") is True
        for agent_id in agents[:8]:
            quota = build_quota_should_run(status, goal_id=goal_id, agent_id=agent_id)
            warnings: list[dict[str, Any]] = []
            todos = _classify_goal_todos(quota, goal_id=goal_id, warnings=warnings)
            row["warnings"].extend(w["reason_code"] for w in warnings)
            conflict |= any(
                w["reason_code"] == "conflicting_readiness_projection" for w in warnings
            )
            stale |= bool(quota.get("stale_latest_run_warning"))
            if quota.get("stale_latest_run_warning"):
                row["warnings"].append("latest_run_projection_stale")
            agent_ok = (
                quota.get("ok") is True
                and (quota.get("agent_identity") or {}).get("agent_id") == agent_id
            )
            partial |= not agent_ok
            gates = (quota.get("user_todo_summary") or {}).get("gate_open_items") or []
            row["agents"].append(
                {
                    "agent_id": agent_id,
                    "source_verified": agent_ok,
                    "waiting_on": _identity(quota.get("waiting_on"))
                    if agent_ok
                    else None,
                    "todos": [
                        {
                            k: t.get(k)
                            for k in (
                                "todo_id",
                                "role",
                                "status",
                                "priority",
                                "title",
                                "next_safe_action",
                                "claimed_by",
                                "readiness",
                            )
                        }
                        for t in todos
                    ]
                    if agent_ok
                    else [],
                    "owner_gate_ids": [
                        _identity(g.get("todo_id"))
                        for g in gates
                        if _identity(g.get("todo_id"))
                    ]
                    if agent_ok
                    else [],
                }
            )
            delivery = _delivery(contexts.get(agent_id, {}), goal_id=goal_id)
            if delivery is not None:
                row["deliveries"].append(delivery)
            elif contexts.get(agent_id, {}).get("latest_evidence_delivery_run"):
                conflict = True
                row["warnings"].append("delivery_identity_conflict")
        row["agent_coverage"] = {
            "discovered": len(agents),
            "attempted": min(len(agents), 8),
            "omitted": max(len(agents) - 8, 0),
        }
        quality = (
            SourceQuality.UNREADABLE
            if not source_ok
            else SourceQuality.CONFLICTING
            if conflict
            else SourceQuality.STALE
            if stale
            else SourceQuality.PARTIAL
            if partial
            else SourceQuality.VERIFIED
        )
        row["quality"] = quality.value
        row["progress"] = (
            "recorded_delivery_available"
            if quality is SourceQuality.VERIFIED and row["deliveries"]
            else "unknown"
        )
        if stale:
            row["warnings"].append("current_progress_unknown_stale_source")
        if partial:
            row["warnings"].append("agent_coverage_incomplete")
        if not source_ok:
            row["warnings"].append("core_status_health_unverified")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        row["quality"] = SourceQuality.UNREADABLE.value
        row["progress"] = "unknown"
        row["warnings"].append("core_source_unreadable")
    return row


def build_goal_portfolio(
    *,
    registry_path: Path,
    runtime_root_override: str | None = None,
    goal_ids: list[str] | None = None,
    limit: int = 8,
    max_age_hours: float = 24,
    now: datetime | None = None,
    include_stopped: bool = True,
    include_goal_lifecycle: bool = False,
) -> dict[str, Any]:
    """Read only the requested registry scope; never derive inventory from chat."""
    if not 1 <= limit <= 128 or not 0 < max_age_hours <= 8760:
        raise ValueError(
            "limit must be 1..128 and max_age_hours must be positive and at most 8760"
        )
    if type(include_goal_lifecycle) is not bool:
        raise ValueError("include_goal_lifecycle must be a boolean")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("collection time must include a timezone")
    try:
        registry_bytes = registry_path.read_bytes()
        registry = json.loads(registry_bytes)
        raw_goals = registry["goals"]
        if not isinstance(raw_goals, list):
            raise ValueError("registry goals must be a list")
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "ok": False,
            "schema_version": "goal_portfolio_v0",
            "coverage": {"discovered": None, "verified": 0, "complete": False},
            "warnings": ["registry_unreadable"],
            "goals": [],
        }
    valid = [g for g in raw_goals if isinstance(g, dict) and _identity(g.get("id"))]
    counts = Counter(g["id"] for g in valid)
    inventory = {g["id"]: g for g in valid}
    requested = set(goal_ids) if goal_ids is not None else set(inventory)
    if any(_identity(g) is None for g in requested):
        raise ValueError("requested Goal IDs must be safe exact identifiers")
    selected = sorted(requested & inventory.keys())
    activation = {}
    for goal_id in selected:
        try:
            activation[goal_id] = goal_activation_state(inventory[goal_id]).value
        except ValueError:
            activation[goal_id] = "unknown"
    stopped = {g for g in selected if not include_stopped
               and activation[g] == "stopped" and counts[g] == 1}
    inspected = [g for g in selected if g not in stopped]
    positions = {g: i for i, g in enumerate(inspected)}
    runtime_root = resolve_runtime_root(
        registry, runtime_root_override, registry_path=registry_path
    )
    # One authoritative collection for a fully authorized global inventory.
    # Restricted/limited reads stay scoped before collection, never read-all/filter-later.
    shared_status = None
    before = {}
    if (
        goal_ids is None
        and 1 < len(inspected) <= limit
        and all(counts[g] == 1 for g in inspected)
    ):
        before = {g: _source_versions(inventory[g], runtime_root) for g in inspected}
        try:
            shared_status = collect_status(
                registry_path=registry_path,
                runtime_root_override=runtime_root_override,
                scan_roots=[],
                limit=max(8, len(inspected)),
                include_public_boundary_scan=False,
                **({"activation_state_filter": "active"} if not include_stopped else {}),
            )
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            shared_status = {"ok": False}
    rows = []
    for goal_id in selected:
        if goal_id in stopped:
            rows.append({"goal_id": goal_id, "quality": "omitted", "progress": "unknown",
                         "warnings": ["stopped_goal_excluded"]})
        elif counts[goal_id] > 1:
            rows.append(
                {
                    "goal_id": goal_id,
                    "quality": "conflicting",
                    "progress": "unknown",
                    "warnings": ["duplicate_registry_identity"],
                }
            )
        elif positions[goal_id] >= limit:
            rows.append(
                {
                    "goal_id": goal_id,
                    "quality": "omitted",
                    "progress": "unknown",
                    "warnings": ["collection_limit"],
                }
            )
        else:
            rows.append(
                _read_goal(
                    inventory[goal_id],
                    registry_path=registry_path,
                    runtime_root_override=runtime_root_override,
                    runtime_root=runtime_root,
                    now=now,
                    max_age_hours=max_age_hours,
                    include_goal_lifecycle=include_goal_lifecycle,
                    shared_status=shared_status,
                    source_versions_before=before.get(goal_id),
                )
            )
        rows[-1]["activation_state"] = activation[goal_id]
        if include_goal_lifecycle and "goal_lifecycle" not in rows[-1]:
            # Omitted, duplicate-identity and stopped rows are excluded before
            # any source read, so they carry no Goal evidence at all. Their
            # lifecycle answer says exactly that rather than leaving the field
            # absent for the consumer to mistake for a derivation failure.
            rows[-1]["goal_lifecycle"] = lifecycle_readback_unavailable(
                goal_id, "goal_not_read"
            )
    try:
        changed = registry_path.read_bytes() != registry_bytes
    except OSError:
        changed = True
    if changed:
        for row in rows:
            if row["quality"] != "omitted" or row["goal_id"] in stopped:
                row.update(quality="conflicting", progress="unknown", activation_state="unknown")
                row["warnings"].append("inventory_changed_during_collection")
                if row.get("goal_lifecycle", {}).get("status") != "unavailable":
                    # The projection may have been derived from a source that
                    # changed while it was read; the row is already downgraded,
                    # so the lifecycle readback is downgraded with it.
                    row["goal_lifecycle"] = lifecycle_readback_unavailable(
                        row["goal_id"], "inventory_changed_during_collection"
                    )
    qualities = Counter(r["quality"] for r in rows)
    missing = sorted(requested - inventory.keys())
    invalid = len(raw_goals) - len(valid)
    warnings = (["empty_inventory"] if not selected else []) + (
        ["invalid_registry_entries"] if invalid else []
    )
    if missing:
        warnings.append("requested_goals_not_registered")
    limitations = [
        "Local registry projection; unavailable remote sources are not fetched.",
        "Evidence refs identify Core records, not an independent artifact audit.",
        "Latest evidence-bearing delivery per Agent; not a complete delivery history.",
        "Todo readiness reuses the Core summary; the returned task list is not exhaustive.",
        "No absence-of-progress or all-healthy conclusion; external sharing needs its own scope authorization.",
    ]
    if include_goal_lifecycle:
        limitations.append(
            "Goal lifecycle readback is the status collection's own projection or a typed "
            "unavailable gap; this reader never derives a second one."
        )
    snapshot = {
        "ok": True,
        "schema_version": "goal_portfolio_v0",
        "collected_at": now.isoformat(),
        "collection_completed_at": datetime.now(timezone.utc).isoformat(),
        "inventory_revision": "sha256:" + hashlib.sha256(registry_bytes).hexdigest(),
        "scope": {
            "kind": "caller_authorized_registry",
            "requested_goal_ids": sorted(requested),
            "missing_goal_ids": missing,
        },
        "coverage": {
            "discovered": len(selected),
            "attempted": sum("source" in r for r in rows),
            "stopped_excluded": len(stopped) if not changed else 0,
            **{q.value: qualities[q.value] for q in SourceQuality},
            "invalid_registry_entries": invalid,
            "complete": bool(selected)
            and qualities["verified"] == len(selected)
            and not missing
            and not invalid,
        },
        "warnings": warnings,
        "goals": rows,
        "limitations": limitations,
    }
    snapshot["snapshot_id"] = _digest(snapshot)
    return snapshot


def render_goal_portfolio(payload: dict[str, Any]) -> str:
    coverage = payload["coverage"]
    lines = [
        f"Goal coverage: {coverage.get('verified', 0)}/{coverage.get('discovered')} sources verified; complete={coverage['complete']}"
    ]
    for row in payload["goals"]:
        lines.append(
            f"- {row['goal_id']}: {row['quality']}; progress={row['progress']}; recorded deliveries={len(row.get('deliveries', []))}"
        )
    return "\n".join(lines)
