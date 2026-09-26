"""Opt-in source-reference capture; never semantic review or lifecycle authority.

The private spool holds bounded scan receipts and replay cursors, not bodies.
A host supplies provider bindings and invokes one tick with its own scheduler.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .assembler import (
    DecisionContextAssembly,
    DecisionEvidenceCollection,
    DecisionEvidenceRebaser,
    assemble_decision_evidence,
)
from .freshness import (
    build_source_freshness_report,
    source_freshness_row,
    write_capture_host_health,
)
from .private_state import load_private_decision_cursors, private_file_digest
from .profile import DecisionContextProfile, resolve_decision_context_activation
from .runtime import _build_source_providers
from .sources import DecisionSourceProvider, DecisionSourceSpec

_READ_SUCCESS_STATUSES = frozenset({"completed", "no_change"})
_READ_FAILURE_STATUSES = frozenset({"provider_failed", "failed", "unavailable"})


class CaptureReplayError(ValueError):
    """Typed recovery diagnosis; never classify provider exception prose."""

    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


def _open_spool(path: Path, *, goal_id: str, agent_id: str) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    # Windows has no fchmod; os.open already applied the mode above.
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    db = sqlite3.connect(path, timeout=1)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS identity (goal TEXT, agent TEXT);
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY, binding_digest TEXT NOT NULL,
                cursor TEXT, checked_at TEXT, status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL,
                cursor_before TEXT, cursor_after TEXT NOT NULL,
                before_time TEXT NOT NULL, receipt TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS review_observations (
                source_id TEXT PRIMARY KEY, cursor TEXT);
        """)
        db.execute("BEGIN IMMEDIATE")
        columns = _source_columns(db)
        if "last_success_at" not in columns:
            # Legacy spools only knew the last attempt; a successful last
            # attempt is the best available evidence of the last read.
            db.execute("ALTER TABLE sources ADD COLUMN last_success_at TEXT")
            db.execute(
                "UPDATE sources SET last_success_at=checked_at "
                "WHERE status IN ('completed','no_change')"
            )
        if "failure_streak" not in columns:
            db.execute(
                "ALTER TABLE sources ADD COLUMN failure_streak INTEGER NOT NULL DEFAULT 0"
            )
        identity = db.execute("SELECT goal, agent FROM identity").fetchone()
        if identity is None:
            db.execute("INSERT INTO identity VALUES (?, ?)", (goal_id, agent_id))
        elif tuple(identity) != (goal_id, agent_id):
            raise ValueError("capture spool goal/agent mismatch")
        return db
    except BaseException:
        db.close()
        raise


def _binding_digest(profile: DecisionContextProfile, source: DecisionSourceSpec) -> str:
    import hashlib

    payload = {
        "source": source.public_record(),
        "locator": source.private_locator,
        "binding": profile.provider_binding_map()[source.provider_id],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _source_columns(db: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in db.execute("PRAGMA table_info(sources)")}


def _status(
    db: sqlite3.Connection,
    sources: tuple[DecisionSourceSpec, ...],
    *,
    now: datetime,
) -> dict[str, Any]:
    has_recovery = (
        db.execute("SELECT 1 FROM sqlite_master WHERE name='capture_holds'").fetchone()
        is not None
    )
    columns = _source_columns(db)
    rows = []
    freshness_rows = []
    for spec in sources:
        source_id = spec.source_id
        source = db.execute(
            "SELECT * FROM sources WHERE source_id=?", (source_id,)
        ).fetchone()
        pending = db.execute(
            "SELECT count(*), min(id) FROM batches WHERE source_id=?", (source_id,)
        ).fetchone()
        if source is None:
            last_read_at = None
        elif "last_success_at" in columns:
            last_read_at = source["last_success_at"]
        else:
            last_read_at = (
                source["checked_at"]
                if source["status"] in _READ_SUCCESS_STATUSES
                else None
            )
        failure_streak = (
            int(source["failure_streak"] or 0)
            if source is not None and "failure_streak" in columns
            else 0
        )
        freshness = source_freshness_row(
            source=spec,
            observed_at=now,
            last_read_at=last_read_at,
            last_attempt_status=source["status"] if source else None,
            failure_streak=failure_streak,
        )
        freshness_rows.append(freshness)
        rows.append(
            {
                "source_id": source_id,
                "last_checked_at": source["checked_at"] if source else None,
                "last_read_at": freshness["last_read_at"],
                "staleness_seconds": freshness["staleness_seconds"],
                "freshness": freshness["status"],
                "failure_streak": failure_streak,
                "status": source["status"] if source else "never_checked",
                "pending_batch_count": pending[0],
                "next_batch_id": pending[1],
                "held_batch_count": db.execute(
                    "SELECT count(*) FROM held_batches WHERE source_id=?", (source_id,)
                ).fetchone()[0]
                if has_recovery
                else 0,
                "acquisition_held": bool(
                    db.execute(
                        "SELECT 1 FROM capture_holds WHERE source_id=?", (source_id,)
                    ).fetchone()
                )
                if has_recovery
                else False,
            }
        )
    return {
        "schema_version": "decision_context_capture_status_v0",
        "sources": rows,
        "source_freshness": build_source_freshness_report(
            observed_at=now, rows=freshness_rows
        ),
        "pending_batch_count": db.execute("SELECT count(*) FROM batches").fetchone()[0],
        "held_batch_count": db.execute("SELECT count(*) FROM held_batches").fetchone()[
            0
        ]
        if has_recovery
        else 0,
        "semantic_review_completion": "not_inferred_from_capture",
        "raw_content_captured": False,
        "decision_cursors_mutated": False,
        "external_writes_performed": False,
        "model_calls_performed": False,
    }


def capture_profile_sources(
    *,
    goal_id: str,
    agent_id: str,
    profile_path: Path,
    spool_path: Path,
    source_provider_overrides: Mapping[str, DecisionSourceProvider] | None = None,
    cursor_path: Path | None = None,
    execute: bool = False,
    timeout_seconds: float = 20.0,
    health_runtime_root: Path | None = None,
) -> dict[str, Any]:
    """Run a bounded tick. Disabled profiles and preview never create a spool.

    Reviewed cursors, if supplied, must be the existing settlement-owned file.
    A newly observed review transition may retire only the oldest matching batch.
    Ambiguous or unobserved transitions retain batches; capture never writes review.
    Provider deadlines are cooperative, so hosts must also bound process runtime.
    With ``health_runtime_root``, every executed tick (including a failed one)
    refreshes the host health record that ``loopx doctor`` inspects; a host that
    stops ticking is then reported by heartbeat age rather than going silent.
    """
    if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 60:
        raise ValueError("capture timeout must be between 0 and 60 seconds")
    overrides = source_provider_overrides or {}
    profile_path = profile_path.expanduser()
    digest_before = private_file_digest(profile_path) if profile_path.exists() else None
    activation, profile = resolve_decision_context_activation(
        goal_id=goal_id,
        agent_id=agent_id,
        profile_path=profile_path,
        available_source_provider_ids=overrides,
    )
    if (
        profile is None
        or not profile.enabled
        or not activation["configured_for_agent"]
        or not profile.automatic_capture
    ):
        return {
            "activation": activation,
            "status": "capture_disabled",
            "executed": False,
        }
    if private_file_digest(profile_path) != digest_before:
        raise ValueError("capture profile changed during activation")
    if spool_path.resolve() == profile_path.resolve() or (
        cursor_path and spool_path.resolve() == cursor_path.resolve()
    ):
        raise ValueError(
            "capture spool must be separate from profile and reviewed cursors"
        )
    if not execute and not spool_path.exists():
        return {"activation": activation, "status": "not_started", "executed": False}
    now = datetime.now(timezone.utc)
    sources = tuple(
        source
        for source in profile.sources
        if source.source_id in profile.capture_source_ids
    )
    if not execute:
        # Read-only diagnostics do not create tables, change permissions or retire rows.
        with closing(
            sqlite3.connect(spool_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            db.row_factory = sqlite3.Row
            identity = db.execute("SELECT goal, agent FROM identity").fetchone()
            if identity is None or tuple(identity) != (goal_id, agent_id):
                raise ValueError("capture spool goal/agent mismatch")
            return {
                "activation": activation,
                "executed": False,
                **_status(db, sources, now=now),
            }

    def record_health(tick_status: str, freshness: Mapping[str, Any] | None) -> None:
        if health_runtime_root is None:
            return
        write_capture_host_health(
            runtime_root=health_runtime_root,
            spool_path=spool_path,
            goal_id=goal_id,
            agent_id=agent_id,
            interval_seconds=profile.capture_interval_seconds,
            tick_status=tick_status,
            observed_at=now,
            freshness=freshness,
        )

    try:
        result = _execute_capture_tick(
            activation=activation,
            profile=profile,
            profile_path=profile_path,
            digest_before=digest_before,
            spool_path=spool_path,
            cursor_path=cursor_path,
            goal_id=goal_id,
            agent_id=agent_id,
            sources=sources,
            overrides=overrides,
            timeout_seconds=timeout_seconds,
            now=now,
        )
    except BaseException:
        record_health("failed", None)
        raise
    record_health("completed", result["source_freshness"])
    return result


def _execute_capture_tick(
    *,
    activation: Mapping[str, Any],
    profile: DecisionContextProfile,
    profile_path: Path,
    digest_before: str | None,
    spool_path: Path,
    cursor_path: Path | None,
    goal_id: str,
    agent_id: str,
    sources: tuple[DecisionSourceSpec, ...],
    overrides: Mapping[str, DecisionSourceProvider],
    timeout_seconds: float,
    now: datetime,
) -> dict[str, Any]:
    observed_at = now.isoformat()
    providers = _build_source_providers(
        profile, sources=sources, source_provider_overrides=overrides
    )
    db = _open_spool(spool_path, goal_id=goal_id, agent_id=agent_id)
    try:
        reviewed = load_private_decision_cursors(cursor_path, profile=profile)
        for source in sources:
            if (
                db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='capture_holds'"
                ).fetchone()
                and db.execute(
                    "SELECT 1 FROM capture_holds WHERE source_id=?", (source.source_id,)
                ).fetchone()
            ):
                continue
            binding = _binding_digest(profile, source)
            row = db.execute(
                "SELECT * FROM sources WHERE source_id=?", (source.source_id,)
            ).fetchone()
            if row and row["binding_digest"] != binding:
                db.execute(
                    "UPDATE sources SET status='binding_changed' WHERE source_id=?",
                    (source.source_id,),
                )
                continue
            # Cursor equality alone is not an acknowledgement (A -> B -> A).
            # Baseline legacy spools without deleting anything; only an observed
            # transition matching the oldest pending batch can retire that batch.
            previous = db.execute(
                "SELECT cursor FROM review_observations WHERE source_id=?",
                (source.source_id,),
            ).fetchone()
            current_review = reviewed.get(source.source_id)
            if previous is not None and previous["cursor"] != current_review:
                oldest = db.execute(
                    "SELECT id, cursor_before, cursor_after FROM batches "
                    "WHERE source_id=? ORDER BY id LIMIT 1",
                    (source.source_id,),
                ).fetchone()
                if (
                    oldest is not None
                    and oldest["cursor_before"] == previous["cursor"]
                    and oldest["cursor_after"] == current_review
                ):
                    db.execute("DELETE FROM batches WHERE id=?", (oldest["id"],))
            db.execute(
                "INSERT OR REPLACE INTO review_observations VALUES (?, ?)",
                (source.source_id, current_review),
            )
            if (
                row
                and row["checked_at"]
                and row["status"] != "backpressure"
                and (now - datetime.fromisoformat(row["checked_at"])).total_seconds()
                < profile.capture_interval_seconds
            ):
                continue
            cursor = row["cursor"] if row else reviewed.get(source.source_id)
            status = "backpressure"
            if (
                db.execute("SELECT count(*) FROM batches").fetchone()[0]
                < profile.capture_max_pending_batches
            ):
                try:
                    scan = providers[source.provider_id].scan(
                        source=source,
                        after_cursor=cursor,
                        before=observed_at,
                        limit=source.max_changes,
                        timeout_seconds=timeout_seconds,
                        observed_at=observed_at,
                    )
                    receipt = scan.public_receipt()
                    if (
                        scan.source_id != source.source_id
                        or scan.provider_id != source.provider_id
                        or scan.cursor_before != cursor
                        or scan.requested_limit != source.max_changes
                    ):
                        raise ValueError("capture scan scope mismatch")
                    if scan.items and (
                        not scan.cursor_after or scan.cursor_after == cursor
                    ):
                        raise ValueError("changed capture requires a new cursor")
                except Exception:
                    # Provider exception messages can contain bodies or credentials.
                    status = "provider_failed"
                else:
                    status = scan.status
                    if (
                        status in {"completed", "no_change"}
                        and scan.cursor_after
                        and scan.cursor_after != cursor
                    ):
                        db.execute(
                            "INSERT INTO batches(source_id,cursor_before,cursor_after,before_time,receipt) VALUES(?,?,?,?,?)",
                            (
                                source.source_id,
                                cursor,
                                scan.cursor_after,
                                observed_at,
                                json.dumps(receipt),
                            ),
                        )
                        cursor = scan.cursor_after
            # checked_at records the attempt; only a successful read may
            # advance last_success_at, so repeated failures cannot look fresh.
            last_success_at = row["last_success_at"] if row else None
            failure_streak = int(row["failure_streak"] or 0) if row else 0
            if status in _READ_SUCCESS_STATUSES:
                last_success_at, failure_streak = observed_at, 0
            elif status in _READ_FAILURE_STATUSES:
                failure_streak += 1
            db.execute(
                "INSERT INTO sources(source_id,binding_digest,cursor,checked_at,status,"
                "last_success_at,failure_streak) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(source_id) DO UPDATE SET cursor=excluded.cursor, "
                "checked_at=excluded.checked_at, status=excluded.status, "
                "last_success_at=excluded.last_success_at, "
                "failure_streak=excluded.failure_streak",
                (
                    source.source_id,
                    binding,
                    cursor,
                    observed_at,
                    status,
                    last_success_at,
                    failure_streak,
                ),
            )
        if private_file_digest(profile_path) != digest_before:
            raise ValueError("capture profile changed during tick")
        result = {
            "activation": activation,
            "executed": True,
            **_status(db, sources, now=now),
        }
        db.commit()
        return result
    finally:
        db.close()


def assemble_captured_decision_evidence(
    *,
    goal_id: str,
    agent_id: str,
    profile_path: Path,
    spool_path: Path,
    cursor_path: Path | None,
    batch_id: int,
    decision_id: str,
    rebase: DecisionEvidenceRebaser,
    source_provider_overrides: Mapping[str, DecisionSourceProvider] | None = None,
    timeout_seconds: float = 20.0,
) -> DecisionContextAssembly:
    """Re-read one captured batch; return an ordinary reviewable assembly.

    No queue acknowledgement is implied. Changed/deleted historical material or
    a provider unable to replay its bounded scan produces a visible hold.
    """
    overrides = source_provider_overrides or {}
    digest = private_file_digest(profile_path)
    activation, profile = resolve_decision_context_activation(
        goal_id=goal_id,
        agent_id=agent_id,
        profile_path=profile_path,
        available_source_provider_ids=overrides,
    )
    if profile is None or not profile.enabled or not activation["configured_for_agent"]:
        raise ValueError("decision context is not enabled for this agent")
    with closing(
        sqlite3.connect(spool_path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.row_factory = sqlite3.Row
        identity = db.execute("SELECT goal, agent FROM identity").fetchone()
        if identity is None or tuple(identity) != (goal_id, agent_id):
            raise ValueError("capture spool goal/agent mismatch")
        batch = db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if batch is None:
            raise ValueError("capture batch unavailable")
        binding = db.execute(
            "SELECT binding_digest FROM sources WHERE source_id=?",
            (batch["source_id"],),
        ).fetchone()[0]
    source = next(
        (s for s in profile.sources if s.source_id == batch["source_id"] and s.enabled),
        None,
    )
    if source is None or binding != _binding_digest(profile, source):
        raise CaptureReplayError("binding_changed", "capture source binding changed")
    reviewed = load_private_decision_cursors(cursor_path, profile=profile)
    if reviewed.get(source.source_id) != batch["cursor_before"]:
        raise CaptureReplayError(
            "cursor_diverged", "capture batch must follow the reviewed cursor"
        )
    expected = json.loads(batch["receipt"])

    def checked_rebase(collection: DecisionEvidenceCollection):
        receipt = collection.source_scan_receipts[0]

        def changes(value):
            return sorted(
                (r["change_ref"], r["revision_ref"]) for r in value["changes"]
            )

        if (
            changes(receipt) != changes(expected)
            or receipt["cursor_after_ref"] != expected["cursor_after_ref"]
            or receipt["status"] != expected["status"]
            or receipt["exact_read_count"] != expected["changed_count"]
        ):
            raise CaptureReplayError(
                "revision_unavailable",
                "captured revision unavailable; explicit source rebase required",
            )
        return rebase(collection)

    assembly = assemble_decision_evidence(
        goal_id=goal_id,
        decision_id=decision_id,
        observed_at=datetime.now(timezone.utc).isoformat(),
        before=batch["before_time"],
        sources=(source,),
        source_providers=_build_source_providers(
            profile, sources=(source,), source_provider_overrides=overrides
        ),
        cursors={source.source_id: batch["cursor_before"]},
        rebase=checked_rebase,
        timeout_seconds=timeout_seconds,
    )
    if private_file_digest(profile_path) != digest:
        raise ValueError("capture profile changed during exact read")
    return replace(
        assembly,
        profile_digest=digest,
        runtime_bound_provider_ids=tuple(sorted(overrides)),
    )
