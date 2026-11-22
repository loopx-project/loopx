"""Bounded Stage-then-Swap EpochReset prefetch, File+SQLite persistence, and Quiet-Turn IO.

Semantic admission, anti-whitewashing receipt construction, net-gain gating, and
fail-closed KeepRaw decisions stay owned by TypeScript (`quota/stage_then_swap.ts`).
This module owns bounded subprocess execution and durable File + SQLite replay IO.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import selectors
import shlex
import signal
import sqlite3
import subprocess
import time
from typing import Any

from ...file_lock import exclusive_file_lock
from ..quota.turn_envelope import (
    evaluate_epoch_prefetch_swap,
    evaluate_quiet_turn,
    plan_stage_then_swap_prefetch,
)

EPOCH_PREFETCH_SCHEMA_VERSION = "loopx_epoch_prefetch_state_v0"
QUIET_TURN_STATE_SCHEMA_VERSION = "loopx_quiet_turn_state_v0"
_HARD_WIRE_DRAIN_MULTIPLIER = 4
_MIN_HARD_WIRE_DRAIN_BYTES = 262_144


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _utf8_prefix(data: bytes, max_bytes: int) -> tuple[str, bool]:
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="replace"), False
    clipped = data[:max_bytes]
    # Back off past any split UTF-8 continuation bytes so output stays valid text.
    while clipped and (clipped[-1] & 0xC0) == 0x80:
        clipped = clipped[:-1]
    return clipped.decode("utf-8", errors="replace"), True


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


def run_bounded_prefetch_subprocess(
    argv: list[str],
    *,
    timeout_ms: int,
    max_output_bytes: int,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Execute one bounded single-shot read-only command under timeout and byte caps."""
    start = time.monotonic()
    hard_cap = max(max_output_bytes * _HARD_WIRE_DRAIN_MULTIPLIER, _MIN_HARD_WIRE_DRAIN_BYTES)
    deadline = start + (timeout_ms / 1000.0)
    stdout_kept = bytearray()
    stderr_kept = bytearray()
    raw_stdout_bytes = 0
    truncated = False
    timed_out = False
    hard_overflow = False

    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        shell=False,
        cwd=str(cwd) if cwd is not None else None,
        start_new_session=(os.name == "posix"),
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    try:
        for stream, kind in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, data=kind)

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate_process_group(process)
                break
            events = selector.select(timeout=min(remaining, 0.1))
            if not events and process.poll() is not None and time.monotonic() >= deadline:
                timed_out = True
                _terminate_process_group(process)
                break
            for key, _ in events:
                stream = key.fileobj
                assert hasattr(stream, "fileno")
                try:
                    chunk = os.read(stream.fileno(), 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                if key.data == "stdout":
                    raw_stdout_bytes += len(chunk)
                    if len(stdout_kept) < max_output_bytes:
                        take = max_output_bytes - len(stdout_kept)
                        stdout_kept.extend(chunk[:take])
                    if raw_stdout_bytes > max_output_bytes:
                        truncated = True
                    if raw_stdout_bytes > hard_cap:
                        hard_overflow = True
                        _terminate_process_group(process)
                        selector.unregister(stream)
                        break
                else:
                    if len(stderr_kept) < 1024:
                        stderr_kept.extend(chunk[: 1024 - len(stderr_kept)])
            if hard_overflow:
                break

        if not timed_out and not hard_overflow:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                exit_code: int | None = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_group(process)
                process.wait()
                exit_code = None
        else:
            process.wait()
            exit_code = None if timed_out else -1
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()

    latency_ms = max(0, int(round((time.monotonic() - start) * 1000)))
    output_text, utf8_truncated = _utf8_prefix(bytes(stdout_kept), max_output_bytes)
    truncated = truncated or utf8_truncated
    stderr_text = stderr_kept.decode("utf-8", errors="replace").strip()
    error_observation = None
    if timed_out:
        error_observation = f"prefetch_command timed out after {timeout_ms}ms"
    elif hard_overflow:
        error_observation = f"prefetch_command exceeded hard output ceiling ({hard_cap} bytes)"
    elif exit_code != 0:
        detail = f": {stderr_text[:256]}" if stderr_text else ""
        error_observation = f"prefetch_command failed with exit_code={exit_code}{detail}"
    return {
        "exit_code": None if (timed_out or hard_overflow) else exit_code,
        "timed_out": timed_out,
        "truncated": truncated,
        "raw_bytes_read": raw_stdout_bytes,
        "prefetched_output": "" if hard_overflow else output_text,
        "latency_ms": latency_ms,
        "error_observation": error_observation,
    }


def _record_identity_key(
    *,
    scope_key: str,
    context_epoch: str,
    command_sha256: str,
    source_sha256: str,
) -> str:
    payload = json.dumps(
        {
            "scope_key": scope_key,
            "context_epoch": context_epoch,
            "command_sha256": command_sha256,
            "source_sha256": source_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(payload)


def _init_sqlite_db(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS epoch_prefetch_records (
          scope_key TEXT NOT NULL,
          context_epoch TEXT NOT NULL,
          command_sha256 TEXT NOT NULL,
          source_sha256 TEXT NOT NULL,
          receipt_sha256 TEXT NOT NULL,
          operator TEXT NOT NULL,
          record_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY (scope_key, context_epoch, command_sha256, source_sha256)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS quiet_turn_state (
          scope_key TEXT PRIMARY KEY,
          state_digest TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS quiet_turn_skips (
          turn_key TEXT PRIMARY KEY,
          scope_key TEXT NOT NULL,
          state_digest TEXT,
          receipt_json TEXT NOT NULL,
          recorded_at TEXT NOT NULL
        )
        """
    )


def _read_sqlite_record(
    sqlite_path: Path,
    *,
    scope_key: str,
    context_epoch: str,
    command_sha256: str,
    source_sha256: str,
) -> dict[str, Any] | None:
    if not sqlite_path.exists():
        return None
    with closing(sqlite3.connect(sqlite_path, timeout=5.0)) as connection:
        _init_sqlite_db(connection)
        row = connection.execute(
            """
            SELECT record_json, receipt_sha256
            FROM epoch_prefetch_records
            WHERE scope_key = ? AND context_epoch = ? AND command_sha256 = ? AND source_sha256 = ?
            """,
            (scope_key, context_epoch, command_sha256, source_sha256),
        ).fetchone()
    if row is None:
        return None
    record = json.loads(row[0])
    if not isinstance(record, dict) or record.get("receipt_sha256") != row[1]:
        return None
    return record


def _write_sqlite_record(
    sqlite_path: Path,
    *,
    scope_key: str,
    context_epoch: str,
    command_sha256: str,
    source_sha256: str,
    record: Mapping[str, Any],
) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_sha256 = str(record.get("receipt_sha256") or "")
    operator = str(record.get("operator") or "KeepRaw")
    encoded = json.dumps(dict(record), ensure_ascii=False, sort_keys=True)
    with closing(sqlite3.connect(sqlite_path, timeout=5.0, isolation_level=None)) as connection:
        _init_sqlite_db(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                """
                SELECT receipt_sha256
                FROM epoch_prefetch_records
                WHERE scope_key = ? AND context_epoch = ? AND command_sha256 = ? AND source_sha256 = ?
                """,
                (scope_key, context_epoch, command_sha256, source_sha256),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO epoch_prefetch_records (
                      scope_key, context_epoch, command_sha256, source_sha256,
                      receipt_sha256, operator, record_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scope_key,
                        context_epoch,
                        command_sha256,
                        source_sha256,
                        receipt_sha256,
                        operator,
                        encoded,
                        _now_iso(),
                    ),
                )
            elif existing[0] != receipt_sha256:
                raise ValueError("Conflicting SQLite epoch_prefetch receipt")
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise


def _read_file_record(
    file_path: Path,
    *,
    scope_key: str,
    context_epoch: str,
    command_sha256: str,
    source_sha256: str,
) -> dict[str, Any] | None:
    if not file_path.is_file():
        return None
    try:
        envelope = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(envelope, dict) or envelope.get("schema_version") != EPOCH_PREFETCH_SCHEMA_VERSION:
        return None
    identity = envelope.get("identity")
    record = envelope.get("record")
    if (
        not isinstance(identity, dict)
        or identity.get("scope_key") != scope_key
        or identity.get("context_epoch") != context_epoch
        or identity.get("command_sha256") != command_sha256
        or identity.get("source_sha256") != source_sha256
        or not isinstance(record, dict)
    ):
        return None
    return record


def _write_file_record(
    file_path: Path,
    *,
    scope_key: str,
    context_epoch: str,
    command_sha256: str,
    source_sha256: str,
    record: Mapping[str, Any],
) -> None:
    _atomic_write_json(
        file_path,
        {
            "schema_version": EPOCH_PREFETCH_SCHEMA_VERSION,
            "identity": {
                "scope_key": scope_key,
                "context_epoch": context_epoch,
                "command_sha256": command_sha256,
                "source_sha256": source_sha256,
            },
            "record": dict(record),
            "recorded_at": _now_iso(),
        },
    )


def _matching_inline_record(
    candidate: Any,
    *,
    context_epoch: str,
    command_sha256: str,
    source_sha256: str,
) -> dict[str, Any] | None:
    if not isinstance(candidate, Mapping):
        return None
    if candidate.get("context_epoch") != context_epoch:
        return None
    if candidate.get("command_sha256") != command_sha256:
        return None
    ref = candidate.get("source_ref")
    candidate_source_sha = str(ref.get("sha256") or "") if isinstance(ref, Mapping) else ""
    if candidate_source_sha != source_sha256:
        return None
    if not isinstance(candidate.get("receipt_sha256"), str):
        return None
    return dict(candidate)


def _conflict_keep_raw_receipt(
    *,
    epoch_prefetch: Mapping[str, Any],
    context_epoch: str,
    failure_facts: list[str] | tuple[str, ...],
    open_obligations: list[str] | tuple[str, ...],
    source_ref: Mapping[str, str] | None,
    baseline_bytes: int | None,
) -> dict[str, Any]:
    return evaluate_epoch_prefetch_swap(
        epoch_prefetch=epoch_prefetch,
        context_epoch=context_epoch,
        reclaimed_turns=0,
        dropped_failures=0,
        failure_facts=failure_facts,
        open_obligations=open_obligations,
        source_ref=source_ref,
        exit_code=None,
        timed_out=False,
        truncated=False,
        raw_bytes_read=0,
        prefetched_output="",
        baseline_bytes=baseline_bytes,
        latency_ms=0,
        error_observation="Prefetch aborted atomically to KeepRaw (file_sqlite_state_conflict).",
    )


def resolve_or_execute_epoch_prefetch(
    *,
    epoch_prefetch: Mapping[str, Any] | None,
    scope_key: str,
    context_epoch: str,
    file_store_dir: Path,
    sqlite_path: Path,
    reclaimed_turns: int = 0,
    dropped_failures: int = 0,
    failure_facts: list[str] | tuple[str, ...] = (),
    open_obligations: list[str] | tuple[str, ...] = (),
    source_ref: Mapping[str, str] | None = None,
    baseline_bytes: int | None = None,
    cwd: str | Path | None = None,
    existing_inline_record: Mapping[str, Any] | None = None,
    runner: Callable[..., dict[str, Any]] | None = None,
    allow_execute: bool = True,
) -> dict[str, Any] | None:
    """Resolve once-only Stage-then-Swap EpochReset prefetch or fail closed to KeepRaw.

    Persists completion/output across both File (`file_store_dir`) and SQLite
    (`sqlite_path`) keyed by `(scope_key, context_epoch, command_sha256, source_sha256)`
    so replay never re-executes `prefetch_command`.
    """
    plan = plan_stage_then_swap_prefetch(epoch_prefetch=epoch_prefetch)
    if not plan.get("enabled"):
        return None
    assert isinstance(epoch_prefetch, Mapping)

    safety = plan.get("safety") if isinstance(plan.get("safety"), Mapping) else {}
    command_sha256 = str(
        safety.get("command_sha256")
        or _sha256_text(json.dumps(epoch_prefetch.get("prefetch_command"), sort_keys=True, default=str))
    )
    source_sha256 = str(source_ref.get("sha256") or "") if isinstance(source_ref, Mapping) else ""
    key_hash = _record_identity_key(
        scope_key=scope_key,
        context_epoch=context_epoch,
        command_sha256=command_sha256,
        source_sha256=source_sha256,
    )
    file_path = file_store_dir / f"{key_hash}.json"

    with exclusive_file_lock(file_path, operation="epoch_prefetch_resolve"):
        inline_record = _matching_inline_record(
            existing_inline_record,
            context_epoch=context_epoch,
            command_sha256=command_sha256,
            source_sha256=source_sha256,
        )
        file_record = _read_file_record(
            file_path,
            scope_key=scope_key,
            context_epoch=context_epoch,
            command_sha256=command_sha256,
            source_sha256=source_sha256,
        )
        sqlite_record = _read_sqlite_record(
            sqlite_path,
            scope_key=scope_key,
            context_epoch=context_epoch,
            command_sha256=command_sha256,
            source_sha256=source_sha256,
        )

        observed = [rec for rec in (inline_record, file_record, sqlite_record) if rec is not None]
        if observed:
            receipts = {str(rec.get("receipt_sha256") or "") for rec in observed}
            if len(receipts) != 1:
                conflict = _conflict_keep_raw_receipt(
                    epoch_prefetch=epoch_prefetch,
                    context_epoch=context_epoch,
                    failure_facts=failure_facts,
                    open_obligations=open_obligations,
                    source_ref=source_ref,
                    baseline_bytes=baseline_bytes,
                )
                return {
                    **conflict,
                    "fallback_reason": "state_conflict",
                    "replayed": True,
                    "executed": False,
                    "persistence": {
                        "file_path": str(file_path),
                        "sqlite_path": str(sqlite_path),
                        "state_conflict": True,
                    },
                }
            reused = dict(observed[0])
            if file_record is None:
                _write_file_record(
                    file_path,
                    scope_key=scope_key,
                    context_epoch=context_epoch,
                    command_sha256=command_sha256,
                    source_sha256=source_sha256,
                    record=reused,
                )
            if sqlite_record is None:
                _write_sqlite_record(
                    sqlite_path,
                    scope_key=scope_key,
                    context_epoch=context_epoch,
                    command_sha256=command_sha256,
                    source_sha256=source_sha256,
                    record=reused,
                )
            return {
                **reused,
                "replayed": True,
                "executed": False,
                "persistence": {
                    "file_path": str(file_path),
                    "sqlite_path": str(sqlite_path),
                    "healed_file": file_record is None,
                    "healed_sqlite": sqlite_record is None,
                },
            }

        if not allow_execute:
            return None

        if not plan.get("eligible"):
            evaluated = evaluate_epoch_prefetch_swap(
                epoch_prefetch=epoch_prefetch,
                context_epoch=context_epoch,
                reclaimed_turns=reclaimed_turns,
                dropped_failures=dropped_failures,
                failure_facts=failure_facts,
                open_obligations=open_obligations,
                source_ref=source_ref,
                exit_code=None,
                timed_out=False,
                truncated=False,
                raw_bytes_read=0,
                prefetched_output="",
                baseline_bytes=baseline_bytes,
                latency_ms=0,
            )
            executed = False
        else:
            argv = [str(part) for part in safety.get("argv", [])]
            exec_runner = runner or run_bounded_prefetch_subprocess
            probe = exec_runner(
                argv,
                timeout_ms=int(plan["timeout_ms"]),
                max_output_bytes=int(plan["max_output_bytes"]),
                cwd=cwd,
            )
            evaluated = evaluate_epoch_prefetch_swap(
                epoch_prefetch=epoch_prefetch,
                context_epoch=context_epoch,
                reclaimed_turns=reclaimed_turns,
                dropped_failures=dropped_failures,
                failure_facts=failure_facts,
                open_obligations=open_obligations,
                source_ref=source_ref,
                exit_code=probe.get("exit_code"),
                timed_out=bool(probe.get("timed_out")),
                truncated=bool(probe.get("truncated")),
                raw_bytes_read=int(probe.get("raw_bytes_read") or 0),
                prefetched_output=str(probe.get("prefetched_output") or ""),
                baseline_bytes=baseline_bytes,
                latency_ms=int(probe.get("latency_ms") or 0),
                error_observation=probe.get("error_observation"),
            )
            executed = True

        # Persist in BOTH File and SQLite before returning so crash/replay is strictly once-only.
        _write_sqlite_record(
            sqlite_path,
            scope_key=scope_key,
            context_epoch=context_epoch,
            command_sha256=command_sha256,
            source_sha256=source_sha256,
            record=evaluated,
        )
        _write_file_record(
            file_path,
            scope_key=scope_key,
            context_epoch=context_epoch,
            command_sha256=command_sha256,
            source_sha256=source_sha256,
            record=evaluated,
        )
        return {
            **evaluated,
            "replayed": False,
            "executed": executed,
            "persistence": {
                "file_path": str(file_path),
                "sqlite_path": str(sqlite_path),
                "healed_file": False,
                "healed_sqlite": False,
            },
        }


def _persist_quiet_turn_state(
    *,
    scope_key: str,
    file_path: Path | None,
    sqlite_path: Path | None,
    state_digest: str,
    metadata: Mapping[str, Any],
) -> None:
    state_payload = {
        "schema_version": QUIET_TURN_STATE_SCHEMA_VERSION,
        "scope_key": scope_key,
        "state_digest": state_digest,
        "metadata": dict(metadata),
        "updated_at": _now_iso(),
    }
    if file_path is not None:
        _atomic_write_json(file_path, state_payload)
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(sqlite_path, timeout=5.0, isolation_level=None)) as connection:
            _init_sqlite_db(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO quiet_turn_state (scope_key, state_digest, metadata_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(scope_key) DO UPDATE SET
                      state_digest = excluded.state_digest,
                      metadata_json = excluded.metadata_json,
                      updated_at = excluded.updated_at
                    """,
                    (
                        scope_key,
                        state_digest,
                        json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True),
                        _now_iso(),
                    ),
                )
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise


def commit_quiet_turn_state(
    *,
    scope_key: str,
    file_path: Path | None = None,
    sqlite_path: Path | None = None,
    state_digest: str,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Persist Quiet-Turn state digest in BOTH File and SQLite after Turn settlement."""
    if not state_digest:
        return
    meta = dict(metadata) if isinstance(metadata, Mapping) else evaluate_quiet_turn(
        enabled=True,
        quiet_noop_allowed=True,
        state_changed=True,
        current_state_digest=state_digest,
        previous_state_digest=None,
    )
    _persist_quiet_turn_state(
        scope_key=scope_key,
        file_path=file_path,
        sqlite_path=sqlite_path,
        state_digest=state_digest,
        metadata=meta,
    )


def resolve_quiet_turn_metadata(
    *,
    scope_key: str,
    file_path: Path | None = None,
    sqlite_path: Path | None = None,
    enabled: bool = True,
    quiet_noop_allowed: bool = True,
    state_changed: bool | None = None,
    current_state_digest: str | None = None,
    record_if_changed: bool = True,
) -> dict[str, Any]:
    """Compute and optionally persist Quiet-Turn zero-tax skip metadata in File & SQLite."""
    file_digest: str | None = None
    sqlite_digest: str | None = None
    if current_state_digest is not None:
        if file_path is not None and file_path.is_file():
            try:
                saved = json.loads(file_path.read_text(encoding="utf-8"))
                if isinstance(saved, dict) and saved.get("scope_key") == scope_key:
                    file_digest = str(saved.get("state_digest") or "") or None
            except (OSError, ValueError):
                pass
        if sqlite_path is not None and sqlite_path.exists():
            with closing(sqlite3.connect(sqlite_path, timeout=5.0)) as connection:
                _init_sqlite_db(connection)
                row = connection.execute(
                    "SELECT state_digest FROM quiet_turn_state WHERE scope_key = ?",
                    (scope_key,),
                ).fetchone()
                if row is not None and isinstance(row[0], str) and row[0]:
                    sqlite_digest = row[0]

    if file_digest is not None and sqlite_digest is not None and file_digest != sqlite_digest:
        conflict_meta = evaluate_quiet_turn(
            enabled=enabled,
            quiet_noop_allowed=quiet_noop_allowed,
            state_changed=True,
            current_state_digest=current_state_digest,
            previous_state_digest=None,
        )
        return {
            **conflict_meta,
            "reason": "state_conflict",
            "state_conflict": True,
            "file_digest": file_digest,
            "sqlite_digest": sqlite_digest,
        }

    previous_digest = file_digest if file_digest is not None else sqlite_digest
    metadata = evaluate_quiet_turn(
        enabled=enabled,
        quiet_noop_allowed=quiet_noop_allowed,
        state_changed=state_changed,
        current_state_digest=current_state_digest,
        previous_state_digest=previous_digest,
    )
    if record_if_changed and enabled and current_state_digest and not metadata.get("skipped"):
        _persist_quiet_turn_state(
            scope_key=scope_key,
            file_path=file_path,
            sqlite_path=sqlite_path,
            state_digest=current_state_digest,
            metadata=metadata,
        )
    return metadata


def _turn_epoch_inputs(
    *,
    plan: Mapping[str, Any],
    request: Mapping[str, Any],
    epoch_prefetch: Mapping[str, Any],
    capture: Any = None,
) -> dict[str, Any]:
    work_contract = (
        plan.get("work_contract")
        if isinstance(plan.get("work_contract"), Mapping)
        else request.get("work_contract")
        if isinstance(request.get("work_contract"), Mapping)
        else {}
    )
    envelope = (
        plan.get("turn_envelope")
        if isinstance(plan.get("turn_envelope"), Mapping)
        else request.get("turn_envelope")
        if isinstance(request.get("turn_envelope"), Mapping)
        else {}
    )
    session = plan.get("session") if isinstance(plan.get("session"), Mapping) else {}
    context_epoch = str(
        epoch_prefetch.get("context_epoch")
        or session.get("context_epoch")
        or f"turn:{request.get('turn_key') or 'default'}"
    )
    source_ref = epoch_prefetch.get("source_ref")
    if not isinstance(source_ref, Mapping) and capture is not None:
        capture_path = getattr(capture, "path", None)
        capture_sha256 = getattr(capture, "sha256", None)
        if isinstance(capture_path, (str, Path)) and isinstance(capture_sha256, str) and len(capture_sha256) == 64:
            source_ref = {"path": str(capture_path), "sha256": capture_sha256}
    open_obligations: list[str] = list(epoch_prefetch.get("open_obligations") or ())
    route = plan.get("route") if isinstance(plan.get("route"), Mapping) else {}
    selected_todo = route.get("selected_todo") if isinstance(route.get("selected_todo"), Mapping) else {}
    if isinstance(selected_todo.get("todo_id"), str) and selected_todo["todo_id"]:
        label = f"todo:{selected_todo['todo_id']}"
        if isinstance(selected_todo.get("text"), str) and selected_todo["text"].strip():
            label = f"{selected_todo['todo_id']}: {selected_todo['text'].strip()[:160]}"
        if label not in open_obligations:
            open_obligations.append(label)
    replan_id = envelope.get("replan_obligation_id") or work_contract.get("replan_obligation_id")
    if isinstance(replan_id, str) and replan_id and replan_id not in open_obligations:
        open_obligations.append(f"replan_obligation:{replan_id}")
    failure_facts = list(epoch_prefetch.get("failure_facts") or ())
    baseline_bytes = epoch_prefetch.get("baseline_bytes")
    if baseline_bytes is None and isinstance(request.get("exploratory_context"), (str, Mapping, list)):
        raw_exploratory = (
            request["exploratory_context"]
            if isinstance(request["exploratory_context"], str)
            else json.dumps(request["exploratory_context"], ensure_ascii=False, sort_keys=True)
        )
        baseline_bytes = len(raw_exploratory.encode("utf-8"))
    return {
        "context_epoch": context_epoch,
        "reclaimed_turns": int(epoch_prefetch.get("reclaimed_turns") or 0),
        "dropped_failures": int(epoch_prefetch.get("dropped_failures") or len(failure_facts)),
        "failure_facts": failure_facts,
        "open_obligations": open_obligations,
        "source_ref": dict(source_ref) if isinstance(source_ref, Mapping) else None,
        "baseline_bytes": baseline_bytes,
    }


def _safe_rendered_prompt_bytes(request: Mapping[str, Any]) -> int | None:
    """Measure the exact UTF-8 byte length rendered for Codex CLI, when renderable."""
    from .codex_cli import _prompt

    try:
        rendered = _prompt(request)
    except (ValueError, RuntimeError, TypeError, KeyError):
        return None
    return len(rendered.encode("utf-8"))


def _keep_raw_prefetch_payload(
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    fallback_reason: str,
    net_savings_bytes: int | None,
    rendered_baseline_bytes: int | None,
    rendered_delivered_bytes: int | None,
    error_observation: str | None = None,
) -> dict[str, Any]:
    header_fields = receipt.get("header_fields") if isinstance(receipt.get("header_fields"), Mapping) else {}
    facts = list(receipt.get("preserved_failure_facts") or header_fields.get("failure_facts") or ())
    obligations = list(receipt.get("preserved_open_obligations") or header_fields.get("open_obligations") or ())
    payload: dict[str, Any] = {
        "schema_version": receipt["schema_version"],
        "operator": "KeepRaw",
        "status": "fallback_keep_raw",
        "fallback_reason": fallback_reason,
        "trust_level": receipt.get("trust_level", "tool"),
        "context_epoch": receipt.get("context_epoch"),
        "preserves_raw_state": True,
        "preserved_failure_facts": facts,
        "preserved_open_obligations": obligations,
        "bytes_saved": 0,
        "net_savings_bytes": net_savings_bytes if net_savings_bytes is not None else 0,
        "round_trips_saved": 0,
        "error_observation": error_observation or receipt.get("error_observation"),
    }
    if rendered_baseline_bytes is not None:
        payload["rendered_prompt_baseline_bytes"] = rendered_baseline_bytes
    if rendered_delivered_bytes is not None:
        payload["rendered_prompt_delivered_bytes"] = rendered_delivered_bytes
        if rendered_baseline_bytes is not None:
            payload["rendered_prompt_delta_bytes"] = rendered_delivered_bytes - rendered_baseline_bytes
    updated = dict(request)
    updated["epoch_prefetch"] = payload
    return updated


def apply_turn_epoch_prefetch_to_request(
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    replace_exploratory_context: bool = False,
) -> dict[str, Any]:
    """Attach bounded Stage-then-Swap observation onto a Turn host request with real prompt-byte accounting."""
    if receipt.get("operator") != "EpochReset":
        before_bytes = _safe_rendered_prompt_bytes(request)
        updated_keep = _keep_raw_prefetch_payload(
            request,
            receipt,
            fallback_reason=str(receipt.get("fallback_reason") or "command_failed"),
            net_savings_bytes=0,
            rendered_baseline_bytes=before_bytes,
            rendered_delivered_bytes=None,
        )
        after_bytes = _safe_rendered_prompt_bytes(updated_keep)
        if after_bytes is not None:
            updated_keep["epoch_prefetch"]["rendered_prompt_delivered_bytes"] = after_bytes
            if before_bytes is not None:
                updated_keep["epoch_prefetch"]["rendered_prompt_delta_bytes"] = after_bytes - before_bytes
        return updated_keep

    potential_saved = int(receipt.get("bytes_saved") or 0)
    epoch_payload: dict[str, Any] = {
        "schema_version": receipt["schema_version"],
        "operator": "EpochReset",
        "trust_level": receipt["trust_level"],
        "context_epoch": receipt["context_epoch"],
        "source_ref": receipt.get("source_ref"),
        "header_fields": receipt.get("header_fields"),
        "hydrated_text": receipt.get("hydrated_text"),
        "potential_bytes_saved": potential_saved,
        "bytes_saved": potential_saved,
        "net_savings_bytes": receipt.get("net_savings_bytes"),
        "round_trips_saved": receipt.get("round_trips_saved", 1),
        "replayed": bool(receipt.get("replayed")),
    }
    candidate = dict(request)
    candidate["epoch_prefetch"] = epoch_payload
    has_inline_exploratory = "exploratory_context" in request
    if replace_exploratory_context and has_inline_exploratory:
        candidate["exploratory_context"] = {
            "replaced_by_epoch_reset": True,
            "source_ref": receipt.get("source_ref"),
        }

    before_bytes = _safe_rendered_prompt_bytes(request)
    after_bytes = _safe_rendered_prompt_bytes(candidate)
    if before_bytes is not None and after_bytes is not None:
        prompt_delta = after_bytes - before_bytes
        epoch_payload["rendered_prompt_baseline_bytes"] = before_bytes
        epoch_payload["rendered_prompt_delivered_bytes"] = after_bytes
        epoch_payload["rendered_prompt_delta_bytes"] = prompt_delta
        if has_inline_exploratory:
            actual_saved = before_bytes - after_bytes
            if actual_saved <= 0:
                return _keep_raw_prefetch_payload(
                    request,
                    receipt,
                    fallback_reason="negative_net_savings",
                    net_savings_bytes=actual_saved,
                    rendered_baseline_bytes=before_bytes,
                    rendered_delivered_bytes=after_bytes,
                    error_observation=(
                        f"Prefetch aborted atomically to KeepRaw (rendered prompt net change {actual_saved}B <= 0)."
                    ),
                )
            honest_saved = min(potential_saved, actual_saved) if potential_saved > 0 else actual_saved
            epoch_payload["bytes_saved"] = honest_saved
            epoch_payload["net_savings_bytes"] = actual_saved
        else:
            receipt_baseline = receipt.get("baseline_bytes")
            added_bytes = max(0, prompt_delta)
            if isinstance(receipt_baseline, int) and not isinstance(receipt_baseline, bool):
                external_net = int(receipt_baseline) - added_bytes
                if external_net <= 0:
                    return _keep_raw_prefetch_payload(
                        request,
                        receipt,
                        fallback_reason="negative_net_savings",
                        net_savings_bytes=external_net,
                        rendered_baseline_bytes=before_bytes,
                        rendered_delivered_bytes=after_bytes,
                        error_observation=(
                            f"Prefetch aborted atomically to KeepRaw (rendered prompt overhead {added_bytes}B >= baseline {receipt_baseline}B)."
                        ),
                    )
                honest_saved = min(potential_saved, external_net) if potential_saved > 0 else external_net
                epoch_payload["bytes_saved"] = honest_saved
                epoch_payload["net_savings_bytes"] = external_net
        return candidate

    if has_inline_exploratory and not replace_exploratory_context:
        return _keep_raw_prefetch_payload(
            request,
            receipt,
            fallback_reason="negative_net_savings",
            net_savings_bytes=0,
            rendered_baseline_bytes=None,
            rendered_delivered_bytes=None,
            error_observation="Prefetch aborted atomically to KeepRaw (append mode does not replace exploratory_context).",
        )
    return candidate


def _quiet_turn_target_params(
    *,
    plan: Mapping[str, Any],
    goal_id: str,
    agent_id: str,
    goal_state_root: Path,
    effective_quiet: Any,
) -> tuple[str, Path, Path, dict[str, Any], bool, Any, str]:
    qt_cfg = effective_quiet if isinstance(effective_quiet, Mapping) else {}
    envelope = plan.get("turn_envelope") if isinstance(plan.get("turn_envelope"), Mapping) else {}
    interaction = (
        envelope.get("interaction_contract")
        if isinstance(envelope.get("interaction_contract"), Mapping)
        else {}
    )
    agent_channel = (
        interaction.get("agent_channel")
        if isinstance(interaction.get("agent_channel"), Mapping)
        else {}
    )
    quiet_allowed = bool(
        qt_cfg.get("quiet_noop_allowed", agent_channel.get("quiet_noop_allowed", True))
    )
    scope_key = f"turn_quiet:{goal_id}:{agent_id}"
    state_digest = (
        str(qt_cfg["current_state_digest"])
        if isinstance(qt_cfg.get("current_state_digest"), str)
        else _sha256_text(
            json.dumps(
                {
                    "goal_id": goal_id,
                    "agent_id": agent_id,
                    "route": plan.get("route"),
                    "work_contract": plan.get("work_contract") or envelope,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    )
    return (
        scope_key,
        goal_state_root / "quiet_turn" / f"{_sha256_text(scope_key)}.json",
        goal_state_root / "turn_prefetch_state.sqlite",
        qt_cfg,
        quiet_allowed,
        qt_cfg.get("state_changed"),
        state_digest,
    )


def _record_quiet_turn_skip_receipt(
    *,
    goal_state_root: Path,
    sqlite_path: Path,
    turn_key: str,
    scope_key: str,
    quiet_meta: Mapping[str, Any],
) -> None:
    skip_payload = {
        "schema_version": "loopx_quiet_turn_skip_receipt_v0",
        "turn_key": turn_key,
        "scope_key": scope_key,
        "state_digest": quiet_meta.get("state_digest"),
        "quiet_turn": dict(quiet_meta),
        "recorded_at": _now_iso(),
    }
    turn_digest = _sha256_text(turn_key)
    skip_file = goal_state_root / "quiet_turn" / "skips" / f"{turn_digest}.json"
    _atomic_write_json(skip_file, skip_payload)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(sqlite_path, timeout=5.0, isolation_level=None)) as connection:
        _init_sqlite_db(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                INSERT INTO quiet_turn_skips (turn_key, scope_key, state_digest, receipt_json, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(turn_key) DO UPDATE SET
                  scope_key = excluded.scope_key,
                  state_digest = excluded.state_digest,
                  receipt_json = excluded.receipt_json,
                  recorded_at = excluded.recorded_at
                """,
                (
                    turn_key,
                    scope_key,
                    str(quiet_meta.get("state_digest") or "") or None,
                    json.dumps(skip_payload, ensure_ascii=False, sort_keys=True),
                    skip_payload["recorded_at"],
                ),
            )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise


def prepare_turn_epoch_context(
    *,
    plan: Mapping[str, Any],
    request: Mapping[str, Any],
    journal_path: Path,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    project: Path,
    epoch_prefetch: Mapping[str, Any] | None = None,
    quiet_turn: bool | Mapping[str, Any] = False,
    decision_capture_payload: Mapping[str, Any] | None = None,
    existing_journal: Mapping[str, Any] | None = None,
    capture: Any = None,
    allow_execute: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    """Evaluate default-off Quiet-Turn & Stage-then-Swap EpochReset state for a Turn."""
    goal_state_root = journal_path.parent.parent
    sqlite_path = goal_state_root / "turn_prefetch_state.sqlite"

    effective_epoch_prefetch = (
        dict(epoch_prefetch)
        if isinstance(epoch_prefetch, Mapping)
        else dict(decision_capture_payload["epoch_prefetch"])
        if isinstance(decision_capture_payload, Mapping)
        and isinstance(decision_capture_payload.get("epoch_prefetch"), Mapping)
        else None
    )
    effective_quiet = (
        quiet_turn
        if quiet_turn
        else decision_capture_payload.get("quiet_turn")
        if isinstance(decision_capture_payload, Mapping)
        else False
    )

    quiet_meta: dict[str, Any] | None = None
    if not allow_execute and isinstance(existing_journal, Mapping) and isinstance(existing_journal.get("quiet_turn"), Mapping):
        quiet_meta = dict(existing_journal["quiet_turn"])
    elif effective_quiet is True or (
        isinstance(effective_quiet, Mapping) and effective_quiet.get("enabled") is True
    ):
        scope_key, q_file, q_sqlite, _qt_cfg, quiet_allowed, state_changed_opt, state_digest = (
            _quiet_turn_target_params(
                plan=plan,
                goal_id=goal_id,
                agent_id=agent_id,
                goal_state_root=goal_state_root,
                effective_quiet=effective_quiet,
            )
        )
        # Never commit digest before Turn settlement succeeds; inspect read-only here.
        quiet_meta = resolve_quiet_turn_metadata(
            scope_key=scope_key,
            file_path=q_file,
            sqlite_path=q_sqlite,
            enabled=True,
            quiet_noop_allowed=quiet_allowed,
            state_changed=state_changed_opt,
            current_state_digest=state_digest,
            record_if_changed=False,
        )

    prefetch_receipt: dict[str, Any] | None = None
    updated_request = dict(request)
    existing_inline = (
        existing_journal.get("epoch_prefetch")
        if isinstance(existing_journal, Mapping)
        else None
    )
    if not allow_execute:
        if isinstance(existing_inline, Mapping):
            prefetch_receipt = {
                **dict(existing_inline),
                "replayed": True,
                "executed": False,
            }
        elif isinstance(effective_epoch_prefetch, Mapping) and effective_epoch_prefetch.get("enabled") is True:
            inputs = _turn_epoch_inputs(
                plan=plan,
                request=updated_request,
                epoch_prefetch=effective_epoch_prefetch,
                capture=capture,
            )
            prefetch_receipt = resolve_or_execute_epoch_prefetch(
                epoch_prefetch=effective_epoch_prefetch,
                scope_key=f"turn_driver:{goal_id}:{turn_key}",
                context_epoch=inputs["context_epoch"],
                file_store_dir=goal_state_root / "turn_prefetch",
                sqlite_path=sqlite_path,
                reclaimed_turns=inputs["reclaimed_turns"],
                dropped_failures=inputs["dropped_failures"],
                failure_facts=inputs["failure_facts"],
                open_obligations=inputs["open_obligations"],
                source_ref=inputs["source_ref"],
                baseline_bytes=inputs["baseline_bytes"],
                cwd=effective_epoch_prefetch.get("cwd") or project,
                existing_inline_record=None,
                allow_execute=False,
            )
        return prefetch_receipt, quiet_meta, updated_request

    if (
        isinstance(effective_epoch_prefetch, Mapping)
        and effective_epoch_prefetch.get("enabled") is True
        and not (quiet_meta and quiet_meta.get("skipped"))
    ):
        inputs = _turn_epoch_inputs(
            plan=plan,
            request=updated_request,
            epoch_prefetch=effective_epoch_prefetch,
            capture=capture,
        )
        prefetch_receipt = resolve_or_execute_epoch_prefetch(
            epoch_prefetch=effective_epoch_prefetch,
            scope_key=f"turn_driver:{goal_id}:{turn_key}",
            context_epoch=inputs["context_epoch"],
            file_store_dir=goal_state_root / "turn_prefetch",
            sqlite_path=sqlite_path,
            reclaimed_turns=inputs["reclaimed_turns"],
            dropped_failures=inputs["dropped_failures"],
            failure_facts=inputs["failure_facts"],
            open_obligations=inputs["open_obligations"],
            source_ref=inputs["source_ref"],
            baseline_bytes=inputs["baseline_bytes"],
            cwd=effective_epoch_prefetch.get("cwd") or project,
            existing_inline_record=existing_inline,
            allow_execute=True,
        )
        if prefetch_receipt is not None:
            updated_request = apply_turn_epoch_prefetch_to_request(
                updated_request,
                prefetch_receipt,
                replace_exploratory_context=bool(
                    effective_epoch_prefetch.get("replace_exploratory_context")
                ),
            )
            adjudicated = updated_request.get("epoch_prefetch")
            if isinstance(adjudicated, Mapping):
                prefetch_receipt = dict(prefetch_receipt)
                for field in (
                    "operator",
                    "status",
                    "fallback_reason",
                    "preserves_raw_state",
                    "bytes_saved",
                    "net_savings_bytes",
                    "potential_bytes_saved",
                    "round_trips_saved",
                    "rendered_prompt_baseline_bytes",
                    "rendered_prompt_delivered_bytes",
                    "rendered_prompt_delta_bytes",
                    "error_observation",
                ):
                    if field in adjudicated:
                        prefetch_receipt[field] = adjudicated[field]
    return prefetch_receipt, quiet_meta, updated_request


def attach_turn_replay_epoch_metadata(
    payload: dict[str, Any],
    *,
    plan: Mapping[str, Any],
    request: Mapping[str, Any],
    journal_path: Path,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    project: Path,
    options: Mapping[str, Any],
    existing_journal: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach persisted Stage-then-Swap EpochReset and Quiet-Turn metadata on replay."""
    replay_prefetch, replay_quiet, _ = prepare_turn_epoch_context(
        plan=plan,
        request=request,
        journal_path=journal_path,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_key=turn_key,
        project=project,
        epoch_prefetch=options.get("epoch_prefetch"),
        quiet_turn=options.get("quiet_turn", False),
        decision_capture_payload=options.get("decision_capture_payload"),
        existing_journal=existing_journal,
        allow_execute=False,
    )
    if replay_prefetch is not None:
        payload["epoch_prefetch"] = replay_prefetch
    if replay_quiet is not None:
        payload["quiet_turn"] = replay_quiet
    return payload


def _is_turn_option_enabled(
    explicit: Any,
    decision_capture_payload: Mapping[str, Any] | None,
    key: str,
    *,
    allow_bool_true: bool = False,
) -> bool:
    if (allow_bool_true and explicit is True) or (
        isinstance(explicit, Mapping) and explicit.get("enabled") is True
    ):
        return True
    if not isinstance(decision_capture_payload, Mapping):
        return False
    captured = decision_capture_payload.get(key)
    return (allow_bool_true and captured is True) or (
        isinstance(captured, Mapping) and captured.get("enabled") is True
    )


def _effective_agent_id(
    plan: Mapping[str, Any], settlement_lineage: Mapping[str, Any] | None,
) -> str:
    if settlement_lineage is not None:
        return str(settlement_lineage["agent_id"])
    container = (
        (plan.get("work_contract") if isinstance(plan.get("work_contract"), Mapping) else {}).get(
            "agent_identity", {}
        )
        if plan.get("schema_version") == "loopx_turn_plan_v1"
        else (plan.get("turn_envelope") if isinstance(plan.get("turn_envelope"), Mapping) else {})
    )
    return str(container.get("agent_id") or "") if isinstance(container, Mapping) else ""


def maybe_skip_quiet_turn_before_admission(
    *,
    plan: Mapping[str, Any],
    journal_path: Path,
    goal_id: str,
    turn_key: str,
    settlement_lineage: Mapping[str, Any] | None,
    host_projection: Mapping[str, Any],
    empty_effects: Mapping[str, bool],
    options: Mapping[str, Any],
    payload_builder: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    """Evaluate Quiet-Turn BEFORE quota admission/capture publish and record durable skip receipt."""
    quiet_turn = options.get("quiet_turn", False)
    decision_capture_payload = options.get("decision_capture_payload")
    if not _is_turn_option_enabled(
        quiet_turn, decision_capture_payload, "quiet_turn", allow_bool_true=True,
    ):
        return None
    effective_quiet = (
        quiet_turn
        if quiet_turn
        else decision_capture_payload.get("quiet_turn")
        if isinstance(decision_capture_payload, Mapping)
        else False
    )
    skip_host_cfg = effective_quiet if isinstance(effective_quiet, Mapping) else {}
    if not skip_host_cfg.get("skip_host_when_unchanged"):
        return None
    goal_state_root = journal_path.parent.parent
    agent_id = _effective_agent_id(plan, settlement_lineage)
    scope_key, q_file, q_sqlite, _qt_cfg, quiet_allowed, state_changed_opt, state_digest = (
        _quiet_turn_target_params(
            plan=plan,
            goal_id=goal_id,
            agent_id=agent_id,
            goal_state_root=goal_state_root,
            effective_quiet=effective_quiet,
        )
    )
    quiet_meta = resolve_quiet_turn_metadata(
        scope_key=scope_key,
        file_path=q_file,
        sqlite_path=q_sqlite,
        enabled=True,
        quiet_noop_allowed=quiet_allowed,
        state_changed=state_changed_opt,
        current_state_digest=state_digest,
        record_if_changed=False,
    )
    if not quiet_meta.get("skipped"):
        return None
    _record_quiet_turn_skip_receipt(
        goal_state_root=goal_state_root,
        sqlite_path=q_sqlite,
        turn_key=turn_key,
        scope_key=scope_key,
        quiet_meta=quiet_meta,
    )
    return {
        **payload_builder(
            plan,
            {"status": "stopped", "host": host_projection, "reason": "quiet_turn_unchanged"},
            execute=True,
            replayed=False,
            effects=empty_effects,
        ),
        "status": "skipped",
        "quiet_turn": quiet_meta,
    }


def prepare_turn_execution_epoch_context(
    *,
    plan: Mapping[str, Any],
    request: Mapping[str, Any],
    journal_path: Path,
    goal_id: str,
    turn_key: str,
    project: Path,
    settlement_lineage: Mapping[str, Any] | None,
    options: Mapping[str, Any],
    existing_journal: Mapping[str, Any] | None,
    capture: Any,
    needs_host: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prepare opt-in Stage-then-Swap prefetch & Quiet-Turn state before Turn launch."""
    epoch_prefetch = options.get("epoch_prefetch")
    quiet_turn = options.get("quiet_turn", False)
    decision_capture_payload = options.get("decision_capture_payload")
    epoch_opted_in = _is_turn_option_enabled(
        epoch_prefetch, decision_capture_payload, "epoch_prefetch",
    )
    quiet_opted_in = _is_turn_option_enabled(
        quiet_turn, decision_capture_payload, "quiet_turn", allow_bool_true=True,
    )
    if not (epoch_opted_in or quiet_opted_in):
        return {}, dict(request)

    allow_execute = bool(
        needs_host
        and not (
            isinstance(existing_journal, Mapping)
            and isinstance(existing_journal.get("epoch_prefetch"), Mapping)
        )
    )
    effective_agent_id = _effective_agent_id(plan, settlement_lineage)
    prefetch_receipt, quiet_meta, updated_request = prepare_turn_epoch_context(
        plan=plan,
        request=request,
        journal_path=journal_path,
        goal_id=goal_id,
        agent_id=effective_agent_id,
        turn_key=turn_key,
        project=project,
        epoch_prefetch=epoch_prefetch,
        quiet_turn=quiet_turn,
        decision_capture_payload=decision_capture_payload,
        existing_journal=existing_journal,
        capture=capture,
        allow_execute=allow_execute,
    )
    return {"epoch_prefetch": prefetch_receipt, "quiet_turn": quiet_meta}, updated_request


def commit_turn_quiet_state_after_success(
    *,
    settled: Mapping[str, Any],
    epoch_metadata: Mapping[str, Any] | None,
    plan: Mapping[str, Any],
    journal_path: Path,
    goal_id: str,
    settlement_lineage: Mapping[str, Any] | None,
    options: Mapping[str, Any],
) -> None:
    """Commit Quiet-Turn state_digest ONLY after the Turn has settled cleanly."""
    if not epoch_metadata:
        return
    if settled.get("ok") is not True or settled.get("status") not in {
        "committed",
        "stopped",
        "scheduler_action_required",
    }:
        return
    quiet_meta = epoch_metadata.get("quiet_turn")
    if not isinstance(quiet_meta, Mapping) or quiet_meta.get("enabled") is not True:
        return
    if quiet_meta.get("skipped") is True:
        return
    quiet_turn = options.get("quiet_turn", False)
    decision_capture_payload = options.get("decision_capture_payload")
    effective_quiet = (
        quiet_turn
        if quiet_turn
        else decision_capture_payload.get("quiet_turn")
        if isinstance(decision_capture_payload, Mapping)
        else False
    )
    agent_id = _effective_agent_id(plan, settlement_lineage)
    goal_state_root = journal_path.parent.parent
    scope_key, q_file, q_sqlite, _qt_cfg, _quiet_allowed, _state_changed, state_digest = (
        _quiet_turn_target_params(
            plan=plan,
            goal_id=goal_id,
            agent_id=agent_id,
            goal_state_root=goal_state_root,
            effective_quiet=effective_quiet,
        )
    )
    commit_quiet_turn_state(
        scope_key=scope_key,
        file_path=q_file,
        sqlite_path=q_sqlite,
        state_digest=state_digest,
        metadata=quiet_meta,
    )


def attach_epoch_metadata_to_journal(
    journal: dict[str, Any],
    epoch_metadata: Mapping[str, Any] | None,
) -> None:
    """Persist Stage-then-Swap prefetch & Quiet-Turn metadata into a Turn journal."""
    if not epoch_metadata:
        return
    prefetch_receipt = epoch_metadata.get("epoch_prefetch")
    quiet_meta = epoch_metadata.get("quiet_turn")
    if isinstance(prefetch_receipt, Mapping) and "epoch_prefetch" not in journal:
        journal["epoch_prefetch"] = dict(prefetch_receipt)
    if isinstance(quiet_meta, Mapping):
        journal["quiet_turn"] = dict(quiet_meta)


_WORKSPACE_ONLY_PROBE_EXES = frozenset({"git", "ls", "pwd", "wc", "stat", "find"})
_LOOPX_STATE_PROBE_SUBCOMMANDS = frozenset({"status", "doctor", "version"})


def _parse_prefetch_argv(command: Any) -> list[str]:
    if isinstance(command, str):
        stripped = command.strip()
        if not stripped:
            return []
        try:
            return shlex.split(stripped)
        except ValueError:
            return stripped.split()
    if isinstance(command, (list, tuple)):
        return [str(part) for part in command]
    return []


def _loopx_subcommand_tokens(argv: list[str], *, python_mode: bool) -> list[str]:
    args = argv[1:]
    if python_mode:
        if len(args) < 2 or args[0] != "-m":
            return []
        args = args[2:]
    cursor = 0
    while cursor < len(args) and args[cursor].startswith("-"):
        flag = args[cursor]
        if flag in {"--registry", "--runtime-root", "--format"}:
            cursor += 2
            continue
        cursor += 1
    return args[cursor:]


def _command_binds_capture_source(command: Any, capture_path: str) -> bool:
    """Return True when prefetch_command targets the compact capture or reads files directly."""
    argv = _parse_prefetch_argv(command)
    if not argv:
        return True
    capture_name = Path(capture_path).name if capture_path else ""
    if capture_path and any(capture_path in tok or (capture_name and capture_name in tok) for tok in argv):
        return True
    for idx in range(len(argv) - 1):
        if argv[idx] == "quota" and argv[idx + 1] == "context-read":
            return True
    exe = Path(argv[0]).name
    if exe in _WORKSPACE_ONLY_PROBE_EXES:
        return False
    if exe == "loopx" or exe.startswith("python"):
        sub = _loopx_subcommand_tokens(argv, python_mode=exe.startswith("python"))
        if not sub:
            return True
        if sub[:2] == ["quota", "context-read"]:
            return True
        if sub[0] in _LOOPX_STATE_PROBE_SUBCOMMANDS:
            return False
        if len(sub) >= 2 and (sub[0], sub[1]) in {("turn", "inspect"), ("todo", "list"), ("task-lease", "inspect")}:
            return False
    return True


def resolve_codex_context_epoch_prefetch_hook(
    *,
    directory: str,
    hook_name: str,
    state: Mapping[str, Any],
    event: Mapping[str, Any],
    recovery: Mapping[str, Any],
    lock_ctx: Callable[[str], Any],
    read_state_fn: Callable[[Path], dict[str, Any]],
    save_state_fn: Callable[[Path, dict[str, Any]], None],
    continuation_text: str,
    raw_guidance_text: str,
) -> dict[str, Any] | None:
    """Resolve Stage-then-Swap EpochReset or KeepRaw output for SessionStart hooks."""
    cfg = (
        event.get("epoch_prefetch")
        if isinstance(event.get("epoch_prefetch"), Mapping)
        else state.get("epoch_prefetch_policy")
        if isinstance(state.get("epoch_prefetch_policy"), Mapping)
        else None
    )
    if not (isinstance(cfg, Mapping) and cfg.get("enabled") is True):
        return None
    recovery_ref = (
        {"path": str(recovery["path"]), "sha256": str(recovery["sha256"])}
        if isinstance(recovery.get("path"), str) and isinstance(recovery.get("sha256"), str)
        else None
    )
    baseline_bytes = cfg.get("baseline_bytes")
    if baseline_bytes is None and recovery_ref is not None:
        try:
            baseline_bytes = Path(recovery_ref["path"]).stat().st_size
        except OSError:
            baseline_bytes = None
    with lock_ctx(directory) as root:
        current_state = read_state_fn(root)
        locked_cfg = (
            event.get("epoch_prefetch")
            if isinstance(event.get("epoch_prefetch"), Mapping)
            else current_state.get("epoch_prefetch_policy")
            if isinstance(current_state.get("epoch_prefetch_policy"), Mapping)
            else cfg
        )
        if not (isinstance(locked_cfg, Mapping) and locked_cfg.get("enabled") is True):
            return None
        if baseline_bytes is None and isinstance(locked_cfg.get("baseline_bytes"), int):
            baseline_bytes = locked_cfg.get("baseline_bytes")
        explicit_source_ref = locked_cfg.get("source_ref")
        if isinstance(explicit_source_ref, Mapping):
            source_ref: dict[str, str] | None = {
                "path": str(explicit_source_ref.get("path") or ""),
                "sha256": str(explicit_source_ref.get("sha256") or ""),
            }
        elif recovery_ref is not None and _command_binds_capture_source(
            locked_cfg.get("prefetch_command"), recovery_ref["path"],
        ):
            source_ref = recovery_ref
        else:
            source_ref = None
        goal_label = str(current_state.get("goal_id") or state.get("goal_id") or event.get("goal_id") or "default")
        agent_label = str(current_state.get("agent_id") or state.get("agent_id") or event.get("agent_id") or "default")
        context_epoch = str(current_state.get("context_epoch") or state.get("context_epoch") or "epoch-0")
        receipt = resolve_or_execute_epoch_prefetch(
            epoch_prefetch=locked_cfg,
            scope_key=f"codex_context:{goal_label}:{agent_label}",
            context_epoch=context_epoch,
            file_store_dir=root / "epoch_prefetch",
            sqlite_path=root / "epoch_prefetch.sqlite",
            reclaimed_turns=int(event.get("reclaimed_turns") or locked_cfg.get("reclaimed_turns") or 0),
            dropped_failures=int(event.get("dropped_failures") or locked_cfg.get("dropped_failures") or 0),
            failure_facts=list(event.get("failure_facts") or locked_cfg.get("failure_facts") or ()),
            open_obligations=list(event.get("open_obligations") or locked_cfg.get("open_obligations") or ()),
            source_ref=source_ref,
            baseline_bytes=baseline_bytes,
            cwd=event.get("cwd"),
            existing_inline_record=current_state.get("epoch_prefetch"),
        )
        if receipt is not None:
            current_state["epoch_prefetch"] = receipt
            save_state_fn(root, current_state)
    if receipt is None:
        return None
    epoch_meta = {
        "schema_version": receipt["schema_version"],
        "configured": True,
        "superseded": False,
        "operator": receipt["operator"],
        "status": receipt["status"],
        "fallback_reason": receipt.get("fallback_reason"),
        "trust_level": receipt["trust_level"],
        "context_epoch": receipt["context_epoch"],
        "replayed": receipt["replayed"],
        "executed": receipt["executed"],
        "round_trips_saved": receipt["round_trips_saved"],
        "bytes_saved": receipt["bytes_saved"],
        "receipt_sha256": receipt["receipt_sha256"],
    }
    if receipt["operator"] == "EpochReset" and isinstance(receipt.get("hydrated_text"), str):
        additional_context = (
            "LoopX model-context baseline swapped via Stage-then-Swap EpochReset "
            f"(trust={receipt['trust_level']}).\n"
            + receipt["hydrated_text"]
            + "\nOriginal saved capture remains available at: "
            + continuation_text
        )
    else:
        facts = json.dumps(receipt.get("preserved_failure_facts", []), ensure_ascii=False)
        obligations = json.dumps(receipt.get("preserved_open_obligations", []), ensure_ascii=False)
        additional_context = (
            "LoopX model-context baseline invalidated. Saved files remain available. "
            + continuation_text
            + raw_guidance_text
            + f"\nPreserved failure facts: {facts}"
            + f"\nPreserved open obligations: {obligations}"
            + f"\nPrefetch fallback notice (KeepRaw): {receipt.get('error_observation')}"
        )
    return {
        "hookSpecificOutput": {
            "hookEventName": hook_name,
            "additionalContext": additional_context,
            "epochPrefetch": epoch_meta,
        }
    }


def resolve_codex_context_quiet_turn_hook(
    *,
    hook_name: Any,
    state: Mapping[str, Any],
    event: Mapping[str, Any],
    recovery: Mapping[str, Any] | None,
    root_dir: Path,
) -> dict[str, Any] | None:
    """Resolve opt-in Quiet-Turn zero-tax skip metadata for codex_context_hook."""
    quiet_cfg = event.get("quiet_turn")
    if not (quiet_cfg is True or (isinstance(quiet_cfg, Mapping) and quiet_cfg.get("enabled") is True)):
        return None
    qt_cfg = quiet_cfg if isinstance(quiet_cfg, Mapping) else {}
    goal_label = str(state.get("goal_id") or event.get("goal_id") or "default")
    agent_label = str(state.get("agent_id") or event.get("agent_id") or "default")
    quiet_meta = resolve_quiet_turn_metadata(
        scope_key=f"codex_context:{goal_label}:{agent_label}",
        file_path=root_dir / "quiet_turn_state.json",
        sqlite_path=root_dir / "epoch_prefetch.sqlite",
        enabled=True,
        quiet_noop_allowed=bool(qt_cfg.get("quiet_noop_allowed", True)),
        state_changed=qt_cfg.get("state_changed"),
        current_state_digest=(
            str(qt_cfg["current_state_digest"])
            if isinstance(qt_cfg.get("current_state_digest"), str)
            else str((recovery or {}).get("sha256") or "") or None
        ),
    )
    if quiet_meta.get("skipped"):
        return {"hookSpecificOutput": {"hookEventName": hook_name, "quietTurn": quiet_meta}}
    return None
