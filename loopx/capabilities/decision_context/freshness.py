"""Per-source freshness contract and capture host health projection.

Every Decision Context projection carries one row per enabled source with the
last successful read time and its staleness against the source window. A
healthy control plane or an enabled profile never implies fresh sources.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sources import DecisionSourceSpec

DECISION_SOURCE_FRESHNESS_SCHEMA_VERSION = "decision_source_freshness_v0"
DECISION_CAPTURE_HOST_HEALTH_SCHEMA_VERSION = "decision_capture_host_health_v0"
DECISION_CAPTURE_HOSTS_DIAGNOSTICS_SCHEMA_VERSION = (
    "decision_capture_hosts_diagnostics_v0"
)
CAPTURE_HOST_HEALTH_DIRNAME = Path("decision-context") / "capture-hosts"
FRESHNESS_ALERT_MARKER = "🔴"
_SUCCESS_STATUSES = frozenset({"completed", "no_change"})


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def source_freshness_row(
    *,
    source: DecisionSourceSpec,
    observed_at: datetime,
    last_read_at: str | None,
    last_attempt_status: str | None,
    failure_streak: int = 0,
    scanned: bool = True,
) -> dict[str, Any]:
    """Project one source; a missing or old successful read is always alerted."""

    read_time = _parse_time(last_read_at)
    staleness_seconds = (
        int(max(0.0, (observed_at - read_time).total_seconds()))
        if read_time is not None
        else None
    )
    if not scanned and read_time is None:
        status = "not_scanned"
    elif read_time is None:
        status = "never_read"
    elif staleness_seconds is not None and staleness_seconds > source.freshness_seconds:
        status = "stale"
    else:
        status = "fresh"
    alert_reasons = [] if status == "fresh" else [status]
    if last_attempt_status is not None and last_attempt_status not in _SUCCESS_STATUSES:
        alert_reasons.append(f"last_attempt_{last_attempt_status}")
    if failure_streak > 0:
        alert_reasons.append("consecutive_failures")
    return {
        "source_id": source.source_id,
        "priority": source.priority,
        "scan_mode": source.scan_mode,
        "freshness_seconds": source.freshness_seconds,
        "last_read_at": read_time.isoformat() if read_time is not None else None,
        "staleness_seconds": staleness_seconds,
        "status": status,
        "last_attempt_status": last_attempt_status,
        "failure_streak": failure_streak,
        "alert": bool(alert_reasons),
        "alert_reasons": alert_reasons,
    }


def build_source_freshness_report(
    *,
    observed_at: datetime,
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    ordered = sorted((dict(row) for row in rows), key=lambda row: row["source_id"])
    alerted = [row["source_id"] for row in ordered if row["alert"]]
    return {
        "schema_version": DECISION_SOURCE_FRESHNESS_SCHEMA_VERSION,
        "observed_at": observed_at.isoformat(),
        "window_policy": "per_source_freshness_seconds",
        "source_count": len(ordered),
        "fresh_count": sum(1 for row in ordered if row["status"] == "fresh"),
        "alert_source_ids": alerted,
        "stale_source_ids": [
            row["source_id"] for row in ordered if row["status"] == "stale"
        ],
        "all_fresh": not alerted,
        "enabled_state_implies_freshness": False,
        "sources": ordered,
    }


def render_source_freshness_markdown(report: Mapping[str, Any]) -> list[str]:
    rows = report.get("sources")
    if not isinstance(rows, Sequence):
        return []
    lines = [
        "## Source Freshness",
        "",
        f"- all_fresh: `{report.get('all_fresh')}`",
        f"- alert_sources: `{len(report.get('alert_source_ids') or [])}`"
        f" / `{report.get('source_count')}`",
        "",
    ]
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        marker = f"{FRESHNESS_ALERT_MARKER} " if row.get("alert") else ""
        reasons = ",".join(row.get("alert_reasons") or []) or "-"
        lines.append(
            f"- {marker}`{row.get('source_id')}` ({row.get('priority')}): "
            f"`{row.get('status')}` last_read_at=`{row.get('last_read_at')}` "
            f"staleness_seconds=`{row.get('staleness_seconds')}` "
            f"window=`{row.get('freshness_seconds')}` alerts=`{reasons}`"
        )
    lines.append("")
    return lines


def _host_record_path(runtime_root: Path, spool_path: Path) -> Path:
    digest = hashlib.sha256(str(spool_path.resolve()).encode("utf-8")).hexdigest()
    return runtime_root / CAPTURE_HOST_HEALTH_DIRNAME / f"{digest[:24]}.json"


def write_capture_host_health(
    *,
    runtime_root: Path,
    spool_path: Path,
    goal_id: str,
    agent_id: str,
    interval_seconds: int,
    tick_status: str,
    observed_at: datetime,
    freshness: Mapping[str, Any] | None,
) -> Path:
    """Record one capture tick so ``loopx doctor`` can notice a silent host."""

    path = _host_record_path(runtime_root, spool_path)
    previous: Mapping[str, Any] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, Mapping):
            previous = loaded
    except (OSError, json.JSONDecodeError):
        previous = {}
    succeeded = tick_status == "completed"
    previous_failures = previous.get("consecutive_tick_failures")
    record = {
        "schema_version": DECISION_CAPTURE_HOST_HEALTH_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "spool_path": str(spool_path.resolve()),
        "interval_seconds": interval_seconds,
        "last_tick_at": observed_at.isoformat(),
        "last_tick_status": tick_status,
        "last_successful_tick_at": (
            observed_at.isoformat()
            if succeeded
            else previous.get("last_successful_tick_at")
        ),
        "consecutive_tick_failures": (
            0
            if succeeded
            else (previous_failures if isinstance(previous_failures, int) else 0) + 1
        ),
        "source_freshness": (
            dict(freshness) if freshness is not None else previous.get("source_freshness")
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return path


def _host_diagnosis(
    record: Mapping[str, Any],
    *,
    now: datetime,
    record_path: Path,
) -> dict[str, Any]:
    interval = record.get("interval_seconds")
    interval = interval if isinstance(interval, int) and interval > 0 else 900
    grace_seconds = max(2 * interval, interval + 600)
    last_tick = _parse_time(record.get("last_tick_at"))
    tick_age = (
        int(max(0.0, (now - last_tick).total_seconds())) if last_tick else None
    )
    reasons: list[str] = []
    if tick_age is None or tick_age > grace_seconds:
        reasons.append("host_heartbeat_stale")
    if record.get("last_tick_status") != "completed":
        reasons.append("last_tick_failed")
    spool_path = record.get("spool_path")
    if not isinstance(spool_path, str) or not Path(spool_path).exists():
        reasons.append("spool_missing")

    alert_source_ids: list[str] = []
    freshness = record.get("source_freshness")
    rows = freshness.get("sources") if isinstance(freshness, Mapping) else None
    for row in rows if isinstance(rows, Sequence) else ():
        if not isinstance(row, Mapping):
            continue
        read_time = _parse_time(row.get("last_read_at"))
        window = row.get("freshness_seconds")
        stale_now = (
            read_time is None
            or not isinstance(window, int)
            or (now - read_time).total_seconds() > window
        )
        if stale_now or row.get("failure_streak"):
            alert_source_ids.append(str(row.get("source_id")))
    if alert_source_ids:
        reasons.append("source_freshness_alert")
    return {
        "goal_id": record.get("goal_id"),
        "agent_id": record.get("agent_id"),
        "record": str(record_path),
        "interval_seconds": interval,
        "heartbeat_grace_seconds": grace_seconds,
        "last_tick_at": record.get("last_tick_at"),
        "last_tick_age_seconds": tick_age,
        "last_tick_status": record.get("last_tick_status"),
        "last_successful_tick_at": record.get("last_successful_tick_at"),
        "consecutive_tick_failures": record.get("consecutive_tick_failures", 0),
        "alert_source_ids": sorted(alert_source_ids),
        "healthy": not reasons,
        "alert_reasons": reasons,
    }


def collect_capture_host_diagnostics(
    runtime_root: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read registered capture host records without opening private spools."""

    current = now or datetime.now(timezone.utc)
    directory = runtime_root / CAPTURE_HOST_HEALTH_DIRNAME
    hosts: list[dict[str, Any]] = []
    for record_path in sorted(directory.glob("*.json")) if directory.is_dir() else ():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            record = None
        if (
            not isinstance(record, Mapping)
            or record.get("schema_version") != DECISION_CAPTURE_HOST_HEALTH_SCHEMA_VERSION
        ):
            hosts.append(
                {
                    "record": str(record_path),
                    "healthy": False,
                    "alert_reasons": ["record_invalid"],
                }
            )
            continue
        hosts.append(_host_diagnosis(record, now=current, record_path=record_path))
    unhealthy = [host for host in hosts if not host["healthy"]]
    return {
        "schema_version": DECISION_CAPTURE_HOSTS_DIAGNOSTICS_SCHEMA_VERSION,
        "registry": str(directory),
        "observed_at": current.isoformat(),
        "host_count": len(hosts),
        "unhealthy_count": len(unhealthy),
        "healthy": not unhealthy,
        "hosts": hosts,
        "private_spools_opened": False,
    }


def capture_host_diagnostics_detail(diagnostics: Mapping[str, Any]) -> str:
    hosts = diagnostics.get("hosts") or []
    if not hosts:
        return "no Decision Context capture hosts registered"
    unhealthy = [host for host in hosts if not host.get("healthy")]
    if not unhealthy:
        return f"{len(hosts)} capture host(s) healthy"
    parts = [
        f"{host.get('goal_id')}/{host.get('agent_id')}: "
        + ",".join(host.get("alert_reasons") or [])
        + (
            f" sources={','.join(host['alert_source_ids'])}"
            if host.get("alert_source_ids")
            else ""
        )
        for host in unhealthy
    ]
    return (
        f"{FRESHNESS_ALERT_MARKER} {len(unhealthy)}/{len(hosts)} capture host(s) "
        "unhealthy; " + "; ".join(parts)
        + ". Repair the host binding or remove the retired record."
    )
