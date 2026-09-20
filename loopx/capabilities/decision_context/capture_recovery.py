"""Explicit, reference-preserving capture recovery, not semantic settlement.

The private spool is the existing owner. Holds free active queue capacity but
retain unresolved references; restart uses the *unchanged* reviewed cursor.
Neither action acknowledges historical evidence or creates review authority.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing, nullcontext
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...file_lock import exclusive_file_lock
from .assembler import DecisionEvidenceRecords
from .capture import (
    CaptureReplayError,
    _binding_digest,
    _open_spool,
    assemble_captured_decision_evidence,
)
from .private_state import load_private_decision_cursors, private_file_digest
from .profile import DecisionContextProfile, resolve_decision_context_activation
from .sources import DecisionSourceProvider


class RecoveryAction(str, Enum):
    HOLD = "hold"
    RESTART = "restart"
    ROLLBACK = "rollback"


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _file_fence(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    path = path.expanduser().resolve()
    if not path.exists():
        return {"path": _hash(str(path)), "exists": False}
    stat = path.stat()
    return {
        "path": _hash(str(path)),
        "identity": [stat.st_dev, stat.st_ino],
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
        "digest": private_file_digest(path),
    }


def _rows(
    db: sqlite3.Connection, table: str, source_id: str | None = None
) -> list[dict[str, Any]]:
    # All table names are module-owned literals, never caller input.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
        return []
    query = f"SELECT * FROM {table}"
    parameters: tuple[str, ...] = ()
    if source_id is not None:
        query += " WHERE source_id=?"
        parameters = (source_id,)
    return [dict(row) for row in db.execute(query + " ORDER BY rowid", parameters)]


def _scope(db: sqlite3.Connection, source_id: str) -> dict[str, Any]:
    return {
        name: _rows(db, name, source_id)
        for name in (
            "sources",
            "batches",
            "review_observations",
            "capture_holds",
            "held_batches",
        )
    }


def _fence(
    db: sqlite3.Connection,
    spool_path: Path,
    profile_path: Path,
    cursor_path: Path | None,
) -> str:
    stat = spool_path.stat()
    return _hash(
        {
            "spool_identity": [str(spool_path.resolve()), stat.st_dev, stat.st_ino],
            "profile": _file_fence(profile_path),
            "reviewed": _file_fence(cursor_path),
            "tables": {
                name: _rows(db, name)
                for name in (
                    "identity",
                    "sources",
                    "batches",
                    "review_observations",
                    "capture_holds",
                    "held_batches",
                    "capture_recoveries",
                    "sqlite_sequence",
                )
            },
        }
    )


def _schema(db: sqlite3.Connection) -> None:
    # Do not executescript here: it would commit the caller's fence transaction.
    db.execute(
        "CREATE TABLE IF NOT EXISTS capture_holds "
        "(source_id TEXT PRIMARY KEY, recovery_id TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS held_batches "
        "(id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, cursor_before TEXT, "
        "cursor_after TEXT NOT NULL, before_time TEXT NOT NULL, receipt TEXT NOT NULL, "
        "recovery_id TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS capture_recoveries "
        "(id TEXT PRIMARY KEY, source_id TEXT NOT NULL, action TEXT NOT NULL, "
        "recorded_at TEXT NOT NULL, before_state TEXT NOT NULL, "
        "after_digest TEXT NOT NULL, file_fence TEXT NOT NULL, "
        "rollback_of TEXT, rolled_back INTEGER NOT NULL DEFAULT 0)"
    )


def _resolve(
    goal_id: str,
    agent_id: str,
    profile_path: Path,
    overrides: Mapping[str, DecisionSourceProvider],
) -> tuple[DecisionContextProfile, dict[str, Any] | None]:
    before = _file_fence(profile_path)
    activation, profile = resolve_decision_context_activation(
        goal_id=goal_id,
        agent_id=agent_id,
        profile_path=profile_path,
        available_source_provider_ids=overrides,
    )
    if (
        profile is None
        or not profile.enabled
        or not profile.automatic_capture
        or not activation["configured_for_agent"]
    ):
        raise ValueError("capture recovery requires an enabled capture profile")
    if _file_fence(profile_path) != before:
        raise ValueError("capture profile changed during recovery activation")
    return profile, before


def _read_db(spool_path: Path, goal_id: str, agent_id: str) -> sqlite3.Connection:
    db = sqlite3.connect(spool_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN")
        row = db.execute("SELECT goal, agent FROM identity").fetchone()
        if row is None or tuple(row) != (goal_id, agent_id):
            raise ValueError("capture spool goal/agent mismatch")
        return db
    except BaseException:
        db.close()
        raise


def diagnose_capture_source(
    *,
    goal_id: str,
    agent_id: str,
    profile_path: Path,
    spool_path: Path,
    cursor_path: Path | None,
    source_id: str,
    probe: bool = False,
    source_provider_overrides: Mapping[str, DecisionSourceProvider] | None = None,
) -> dict[str, Any]:
    """Metadata by default; explicit probe exact-reads transiently, never settles."""
    overrides = source_provider_overrides or {}
    profile, profile_fence = _resolve(goal_id, agent_id, profile_path, overrides)
    cursor_fence = _file_fence(cursor_path)
    source = next((s for s in profile.sources if s.source_id == source_id), None)
    if source is None or source_id not in profile.capture_source_ids:
        raise ValueError("source is not enrolled for capture")
    reviewed = load_private_decision_cursors(cursor_path, profile=profile)
    with closing(_read_db(spool_path, goal_id, agent_id)) as db:
        state = _scope(db, source_id)
        fence = _fence(db, spool_path, profile_path, cursor_path)
    oldest = state["batches"][0] if state["batches"] else None
    reason = "empty"
    if state["capture_holds"]:
        reason = "acquisition_held"
    elif oldest:
        reason = "replay_not_checked"
        if state["sources"][0]["binding_digest"] != _binding_digest(profile, source):
            reason = "binding_changed"
        elif reviewed.get(source_id) != oldest["cursor_before"]:
            reason = "cursor_diverged"
        elif probe:
            try:
                assemble_captured_decision_evidence(
                    goal_id=goal_id,
                    agent_id=agent_id,
                    profile_path=profile_path,
                    spool_path=spool_path,
                    cursor_path=cursor_path,
                    batch_id=oldest["id"],
                    decision_id="capture-recovery-probe",
                    rebase=lambda _: DecisionEvidenceRecords(),
                    source_provider_overrides=overrides,
                )
                reason = "replayable"
            except CaptureReplayError as exc:
                reason = exc.reason
            except Exception:
                reason = "probe_unavailable"  # No private provider exception text.
    with closing(_read_db(spool_path, goal_id, agent_id)) as db:
        if (
            _fence(db, spool_path, profile_path, cursor_path) != fence
            or _file_fence(profile_path) != profile_fence
            or _file_fence(cursor_path) != cursor_fence
        ):
            reason = "state_changed"
    return {
        "schema_version": "decision_capture_diagnosis_v0",
        "source_id": source_id,
        "diagnosis": reason,
        "probe_requested": probe,
        "next_batch_id": oldest["id"] if oldest else None,
        "pending_batch_count": len(state["batches"]),
        "held_batch_count": len(state["held_batches"]),
        "decision_cursors_mutated": False,
        "raw_content_captured": False,
    }


def recover_capture_source(
    *,
    goal_id: str,
    agent_id: str,
    profile_path: Path,
    spool_path: Path,
    cursor_path: Path | None,
    source_id: str,
    action: RecoveryAction | str,
    execute: bool = False,
    expected_token: str | None = None,
    recovery_id: str | None = None,
    source_provider_overrides: Mapping[str, DecisionSourceProvider] | None = None,
) -> dict[str, Any]:
    """Preview then explicitly apply one source operation with exact CAS fencing.

    Rollback is permitted only while the affected scope and review/profile files
    still match the applied receipt. Other sources can progress independently.
    This trusted local-host API is not a remote authorization endpoint.
    """
    action = RecoveryAction(action)
    if (action == RecoveryAction.ROLLBACK) != (recovery_id is not None):
        raise ValueError("recovery-id is required only for rollback")
    overrides = source_provider_overrides or {}
    profile_path, spool_path = profile_path.expanduser(), spool_path.expanduser()
    if not spool_path.is_file():
        raise ValueError("capture recovery requires an existing spool")
    if cursor_path is not None:
        cursor_path = cursor_path.expanduser()
    paths = [profile_path.resolve(), spool_path.resolve()]
    if cursor_path is not None:
        paths.append(cursor_path.resolve())
    if len(set(paths)) != len(paths):
        raise ValueError(
            "recovery profile, spool and reviewed cursors must be separate"
        )
    profile, profile_fence = _resolve(goal_id, agent_id, profile_path, overrides)
    source = next((s for s in profile.sources if s.source_id == source_id), None)
    if source is None or source_id not in profile.capture_source_ids:
        raise ValueError("source is not enrolled for capture")
    if execute and not expected_token:
        raise ValueError("recovery execute requires an exact preview token")
    # Same lock as settlement. SQLite serializes capture/recovery writes. A
    # preview is fully read-only: no lock files, migrations or permission writes.
    lock = (
        exclusive_file_lock(cursor_path) if execute and cursor_path else nullcontext()
    )
    with lock:
        db = (
            _open_spool(spool_path, goal_id=goal_id, agent_id=agent_id)
            if execute
            else _read_db(spool_path, goal_id, agent_id)
        )
        with closing(db):
            spool_stat = spool_path.stat()
            spool_identity = (spool_stat.st_dev, spool_stat.st_ino)
            files = {
                "profile": _file_fence(profile_path),
                "reviewed": _file_fence(cursor_path),
            }
            if files["profile"] != profile_fence:
                raise ValueError("capture profile changed during recovery")
            before = _scope(db, source_id)
            reviewed = load_private_decision_cursors(cursor_path, profile=profile)
            token = _hash(
                {
                    "action": action.value,
                    "source_id": source_id,
                    "recovery_id": recovery_id,
                    "state": _fence(db, spool_path, profile_path, cursor_path),
                }
            )
            if execute and expected_token != token:
                raise ValueError("capture recovery preview is stale; preview again")
            old_receipt = None
            pending_total = len(_rows(db, "batches"))
            held_total = len(_rows(db, "held_batches"))
            cap = profile.capture_max_pending_batches
            if action == RecoveryAction.HOLD:
                if not before["batches"] or before["capture_holds"]:
                    raise ValueError(
                        "hold requires pending batches on a non-held source"
                    )
                if held_total + len(before["batches"]) > cap:
                    raise ValueError(
                        "retained-history capacity reached; preserve/export spool before recovery"
                    )
            elif action == RecoveryAction.RESTART:
                if not before["capture_holds"] or before["batches"]:
                    raise ValueError(
                        "restart requires a held source without active batches"
                    )
            else:
                old_receipt = next(
                    (
                        r
                        for r in _rows(db, "capture_recoveries")
                        if r["id"] == recovery_id and r["source_id"] == source_id
                    ),
                    None,
                )
                if (
                    not old_receipt
                    or old_receipt["rolled_back"]
                    or old_receipt["action"] == "rollback"
                ):
                    raise ValueError("recovery receipt is not rollbackable")
                if (
                    old_receipt["after_digest"] != _hash(before)
                    or json.loads(old_receipt["file_fence"]) != files
                ):
                    raise ValueError(
                        "source or reviewed/profile state changed since recovery"
                    )
                restored = json.loads(old_receipt["before_state"])
                if pending_total + len(restored["batch_ids"]) > cap:
                    raise ValueError("rollback would exceed active queue capacity")
            # Bound audit metadata as well as active and unresolved references.
            if (
                len(_rows(db, "capture_recoveries")) >= 2 * cap
                and action != RecoveryAction.ROLLBACK
            ):
                raise ValueError(
                    "recovery audit capacity reached; preserve/export spool"
                )
            result = {
                "schema_version": "decision_capture_recovery_v0",
                "source_id": source_id,
                "action": action.value,
                "executed": False,
                "preview_token": token,
                "pending_batch_count": len(before["batches"]),
                "held_batch_count": len(before["held_batches"]),
                "decision_cursors_mutated": False,
                "historical_batches_reviewed": False,
                "raw_content_captured": False,
                "external_writes_performed": False,
            }
            if not execute:
                return result
            _schema(db)
            new_id = str(uuid4())
            saved = {
                key: before[key]
                for key in ("sources", "review_observations", "capture_holds")
            }
            saved["batch_ids"] = [r["id"] for r in before["batches"]]
            if action == RecoveryAction.HOLD:
                db.execute(
                    "INSERT INTO held_batches SELECT id,source_id,cursor_before,cursor_after,before_time,receipt,? FROM batches WHERE source_id=?",
                    (new_id, source_id),
                )
                db.execute("DELETE FROM batches WHERE source_id=?", (source_id,))
                db.execute(
                    "INSERT INTO capture_holds VALUES (?, ?)", (source_id, new_id)
                )
                db.execute(
                    "UPDATE sources SET status='recovery_held' WHERE source_id=?",
                    (source_id,),
                )
            elif action == RecoveryAction.RESTART:
                db.execute("DELETE FROM capture_holds WHERE source_id=?", (source_id,))
                db.execute(
                    "UPDATE sources SET binding_digest=?, cursor=?, checked_at=NULL, status='recovery_restarted' WHERE source_id=?",
                    (
                        _binding_digest(profile, source),
                        reviewed.get(source_id),
                        source_id,
                    ),
                )
                db.execute(
                    "INSERT OR REPLACE INTO review_observations VALUES (?, ?)",
                    (source_id, reviewed.get(source_id)),
                )
            else:
                assert old_receipt is not None
                restored = json.loads(old_receipt["before_state"])
                for table in ("sources", "review_observations", "capture_holds"):
                    db.execute(f"DELETE FROM {table} WHERE source_id=?", (source_id,))
                    for row in restored[table]:
                        db.execute(
                            f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                            tuple(row.values()),
                        )
                if restored["batch_ids"]:
                    db.execute(
                        "INSERT INTO batches SELECT id,source_id,cursor_before,cursor_after,before_time,receipt FROM held_batches WHERE recovery_id=?",
                        (recovery_id,),
                    )
                    db.execute(
                        "DELETE FROM held_batches WHERE recovery_id=?", (recovery_id,)
                    )
                db.execute(
                    "UPDATE capture_recoveries SET rolled_back=1 WHERE id=?",
                    (recovery_id,),
                )
            db.execute(
                "INSERT INTO capture_recoveries VALUES (?,?,?,?,?,?,?,?,0)",
                (
                    new_id,
                    source_id,
                    action.value,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(saved),
                    _hash(_scope(db, source_id)),
                    json.dumps(files),
                    recovery_id,
                ),
            )
            if {
                "profile": _file_fence(profile_path),
                "reviewed": _file_fence(cursor_path),
            } != files:
                raise ValueError(
                    "capture recovery profile/reviewed state changed during apply"
                )
            spool_stat = spool_path.stat()
            if (spool_stat.st_dev, spool_stat.st_ino) != spool_identity:
                raise ValueError("capture spool replaced during recovery")
            db.commit()
            return {
                **result,
                "executed": True,
                "recovery_id": new_id,
                "pending_batch_count": len(_rows(db, "batches", source_id)),
                "held_batch_count": len(_rows(db, "held_batches", source_id)),
                "acquisition_held": bool(_rows(db, "capture_holds", source_id)),
            }
