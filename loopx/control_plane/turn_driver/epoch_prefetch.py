"""Bounded Stage-then-Swap EpochReset prefetch, File+SQLite persistence, and Quiet-Turn IO.

Semantic admission, anti-whitewashing receipt construction, net-gain gating, and
fail-closed KeepRaw decisions stay owned by TypeScript (`quota/stage_then_swap.ts`).
This module owns bounded subprocess execution and durable File + SQLite replay IO.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import sqlite3
import subprocess
import time
from typing import Any, cast

from ...file_lock import exclusive_file_lock
from ..quota.turn_envelope import (
    evaluate_epoch_prefetch_prompt_gate,
    evaluate_epoch_prefetch_swap,
    plan_stage_then_swap_prefetch,
)
from .quiet_turn_store import (
    QUIET_TURN_SKIP_SCHEMA_VERSION as QUIET_TURN_SKIP_SCHEMA_VERSION,
    QUIET_TURN_STATE_SCHEMA_VERSION as QUIET_TURN_STATE_SCHEMA_VERSION,
    atomic_write_json as _atomic_write_json,
    commit_quiet_turn_state as commit_quiet_turn_state,
    commit_turn_quiet_state_after_success as commit_turn_quiet_state_after_success,
    effective_turn_agent_id as _effective_agent_id,
    init_turn_prefetch_sqlite_db as _init_sqlite_db,
    is_turn_option_enabled as _is_turn_option_enabled,
    maybe_skip_quiet_turn_before_admission as maybe_skip_quiet_turn_before_admission,
    now_iso as _now_iso,
    quiet_turn_target_params as _quiet_turn_target_params,
    resolve_codex_context_quiet_turn_hook as resolve_codex_context_quiet_turn_hook,
    resolve_quiet_turn_metadata as resolve_quiet_turn_metadata,
    sha256_text as _sha256_text,
)

EPOCH_PREFETCH_SCHEMA_VERSION = "loopx_epoch_prefetch_state_v0"
_HARD_WIRE_DRAIN_MULTIPLIER = 4
_MIN_HARD_WIRE_DRAIN_BYTES = 262_144
_MAX_VERIFIED_SOURCE_BYTES = 10 * 1024 * 1024
DEFAULT_TOKENIZER_ENCODING = "o200k_base"


def count_verifiable_tokens(
    text: str,
    *,
    encoding_name: str = DEFAULT_TOKENIZER_ENCODING,
    token_counter: Callable[[str], int] | None = None,
) -> tuple[int, dict[str, Any]] | None:
    """Count tokens using an inspectable tokenizer (`tiktoken` or explicit callable)."""
    if token_counter is not None:
        try:
            count = token_counter(text)
        except Exception:  # noqa: BLE001 - fail closed if configured counter raises
            return None
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return None
        return count, {"name": "configured_tokenizer", "encoding": encoding_name}
    try:
        from importlib.metadata import version
        import tiktoken

        encoding = tiktoken.get_encoding(encoding_name)
        count = len(encoding.encode_ordinary(text))
        try:
            pkg_version = version("tiktoken")
        except Exception:  # noqa: BLE001
            pkg_version = "unknown"
        return count, {"name": "tiktoken", "version": pkg_version, "encoding": encoding_name}
    except Exception:  # noqa: BLE001 - missing/unavailable tokenizer fails closed
        return None


def _measure_verified_source_ref_tokens(
    source_ref: Mapping[str, Any] | None,
    *,
    encoding_name: str = DEFAULT_TOKENIZER_ENCODING,
    token_counter: Callable[[str], int] | None = None,
) -> tuple[int, int, dict[str, Any]] | None:
    """Verify `source_ref` on disk by sha256 and count UTF-8 baseline tokens."""
    if not isinstance(source_ref, Mapping):
        return None
    raw_path = source_ref.get("path")
    expected_sha = source_ref.get("sha256")
    if (
        not isinstance(raw_path, str)
        or not raw_path
        or not isinstance(expected_sha, str)
        or len(expected_sha) != 64
    ):
        return None
    path = Path(raw_path)
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > _MAX_VERIFIED_SOURCE_BYTES:
            return None
        raw_bytes = path.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(raw_bytes).hexdigest() != expected_sha:
        return None
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None
    counted = count_verifiable_tokens(
        text, encoding_name=encoding_name, token_counter=token_counter,
    )
    if counted is None:
        return None
    tokens, tokenizer_meta = counted
    return len(raw_bytes), tokens, tokenizer_meta


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


def _evaluate_swap_with_token_gate(
    *,
    epoch_prefetch: Mapping[str, Any],
    context_epoch: str,
    reclaimed_turns: int,
    dropped_failures: int,
    failure_facts: list[str] | tuple[str, ...],
    open_obligations: list[str] | tuple[str, ...],
    source_ref: Mapping[str, str] | None,
    exit_code: int | None,
    timed_out: bool,
    truncated: bool,
    raw_bytes_read: int,
    prefetched_output: str,
    baseline_bytes: int | None,
    latency_ms: int,
    error_observation: str | None,
    require_token_gate: bool,
    token_counter: Callable[[str], int] | None,
) -> dict[str, Any]:
    evaluated = evaluate_epoch_prefetch_swap(
        epoch_prefetch=epoch_prefetch,
        context_epoch=context_epoch,
        reclaimed_turns=reclaimed_turns,
        dropped_failures=dropped_failures,
        failure_facts=failure_facts,
        open_obligations=open_obligations,
        source_ref=source_ref,
        exit_code=exit_code,
        timed_out=timed_out,
        truncated=truncated,
        raw_bytes_read=raw_bytes_read,
        prefetched_output=prefetched_output,
        baseline_bytes=baseline_bytes,
        latency_ms=latency_ms,
        error_observation=error_observation,
    )
    if evaluated.get("operator") != "EpochReset" or (source_ref is None and not require_token_gate):
        return evaluated
    encoding_name = str(epoch_prefetch.get("tokenizer_encoding") or DEFAULT_TOKENIZER_ENCODING)
    measured_source = _measure_verified_source_ref_tokens(
        source_ref, encoding_name=encoding_name, token_counter=token_counter,
    )
    measured_hydrated = count_verifiable_tokens(
        str(evaluated.get("hydrated_text") or ""),
        encoding_name=encoding_name,
        token_counter=token_counter,
    )
    tokenizer_meta = (
        measured_hydrated[1]
        if measured_hydrated is not None
        else (measured_source[2] if measured_source is not None else None)
    )
    gated = evaluate_epoch_prefetch_swap(
        epoch_prefetch=epoch_prefetch,
        context_epoch=context_epoch,
        reclaimed_turns=reclaimed_turns,
        dropped_failures=dropped_failures,
        failure_facts=failure_facts,
        open_obligations=open_obligations,
        source_ref=source_ref,
        exit_code=exit_code,
        timed_out=timed_out,
        truncated=truncated,
        raw_bytes_read=raw_bytes_read,
        prefetched_output=prefetched_output,
        baseline_bytes=measured_source[0] if measured_source is not None else baseline_bytes,
        baseline_tokens=measured_source[1] if measured_source is not None else None,
        hydrated_tokens=measured_hydrated[0] if measured_hydrated is not None else None,
        tokenizer=tokenizer_meta,
        require_token_gate=True,
        latency_ms=latency_ms,
        error_observation=error_observation,
    )
    if measured_source is not None and gated.get("operator") == "EpochReset":
        return {**gated, "baseline_tokens_verified": True}
    return gated


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
    require_token_gate: bool = False,
    token_counter: Callable[[str], int] | None = None,
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
            evaluated = _evaluate_swap_with_token_gate(
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
                require_token_gate=require_token_gate,
                token_counter=token_counter,
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


HostInputRenderer = Callable[[Mapping[str, Any]], str | None]
UNSET_HOST_INPUT_RENDERER: object = object()


def _render_codex_cli_host_input(request: Mapping[str, Any]) -> str:
    from .codex_cli import _prompt

    return _prompt(request)


def _render_generic_cli_host_input(request: Mapping[str, Any]) -> str:
    from .executor import serialize_generic_cli_host_input

    return serialize_generic_cli_host_input(request)


def _render_dsh_host_input(request: Mapping[str, Any]) -> str | None:
    from .host_candidate import render_dsh_host_prompt

    return render_dsh_host_prompt(request)


def resolve_host_input_renderer(host: str | None) -> HostInputRenderer | None:
    """Return the actual delivered host-input serializer for measurable hosts, or None."""
    normalized = (host or "").strip()
    if normalized in {"codex", "codex-cli"}:
        return _render_codex_cli_host_input
    if normalized == "generic-cli":
        return _render_generic_cli_host_input
    if normalized == "dsh":
        return _render_dsh_host_input
    return None


def _safe_rendered_prompt(
    request: Mapping[str, Any],
    *,
    renderer: HostInputRenderer | None = _render_codex_cli_host_input,
) -> str | None:
    """Render the exact delivered host input when the host adapter is measurable."""
    if renderer is None:
        return None
    try:
        return renderer(request)
    except (ValueError, RuntimeError, TypeError, KeyError):
        return None


_PROMPT_GATE_AUDIT_FIELDS = (
    "net_savings_tokens",
    "cold_prefill_amortization_tokens",
    "amortized_net_savings_tokens",
    "min_net_savings_tokens",
    "rendered_prompt_baseline_bytes",
    "rendered_prompt_delivered_bytes",
    "rendered_prompt_delta_bytes",
    "rendered_prompt_baseline_tokens",
    "rendered_prompt_delivered_tokens",
    "rendered_prompt_delta_tokens",
    "tokenizer",
)

_DELIVERED_PROMPT_TELEMETRY_FIELDS = frozenset({
    "rendered_prompt_delivered_bytes",
    "rendered_prompt_delta_bytes",
    "rendered_prompt_delivered_tokens",
    "rendered_prompt_delta_tokens",
})

_HOST_WIRE_EXCLUDED_PREFETCH_FIELDS = frozenset({
    "potential_bytes_saved",
    "bytes_saved",
    "net_savings_bytes",
    "baseline_tokens",
    "hydrated_tokens",
    "baseline_tokens_verified",
    *_PROMPT_GATE_AUDIT_FIELDS,
})


def strip_internal_epoch_prefetch_audit_fields(
    epoch_prefetch: Mapping[str, Any],
) -> dict[str, Any]:
    """Strip internal prompt-gate audit telemetry before serializing host-wire payloads."""
    return {
        key: value
        for key, value in epoch_prefetch.items()
        if key not in _HOST_WIRE_EXCLUDED_PREFETCH_FIELDS
    }


def _apply_prompt_gate_audit_fields(
    target: dict[str, Any],
    adjudicated: Mapping[str, Any],
    *,
    include_delivered_metrics: bool = True,
) -> None:
    for field in _PROMPT_GATE_AUDIT_FIELDS:
        if not include_delivered_metrics and field in _DELIVERED_PROMPT_TELEMETRY_FIELDS:
            continue
        if field in adjudicated and adjudicated[field] is not None:
            target[field] = adjudicated[field]


def _clear_delivered_prompt_telemetry(payload: dict[str, Any]) -> None:
    for field in _DELIVERED_PROMPT_TELEMETRY_FIELDS:
        payload.pop(field, None)


def _keep_raw_prefetch_payload(
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    fallback_reason: str,
    net_savings_bytes: int | None,
    rendered_baseline_bytes: int | None,
    rendered_delivered_bytes: int | None,
    error_observation: str | None = None,
    adjudicated: Mapping[str, Any] | None = None,
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
    if adjudicated is not None:
        # Preserve threshold-gate audit fields and baseline metrics from adjudication,
        # never rejected-candidate delivered measurements.
        _apply_prompt_gate_audit_fields(payload, adjudicated, include_delivered_metrics=False)
    if rendered_delivered_bytes is not None:
        payload["rendered_prompt_delivered_bytes"] = rendered_delivered_bytes
        if rendered_baseline_bytes is not None:
            payload["rendered_prompt_delta_bytes"] = rendered_delivered_bytes - rendered_baseline_bytes
    updated = dict(request)
    updated["epoch_prefetch"] = payload
    return updated


def _measure_and_finalize_keep_raw_payload(
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    fallback_reason: str,
    net_savings_bytes: int | None,
    error_observation: str | None = None,
    adjudicated: Mapping[str, Any] | None = None,
    rendered_baseline_bytes: int | None = None,
    rendered_baseline_tokens: int | None = None,
    active_renderer: HostInputRenderer | None,
    encoding_name: str,
    token_counter: Callable[[str], int] | None,
) -> dict[str, Any]:
    """Construct the KeepRaw request first, then measure its actual delivered host input."""
    baseline_bytes = rendered_baseline_bytes
    baseline_tokens = rendered_baseline_tokens
    if baseline_bytes is None and adjudicated is not None:
        adj_bytes = adjudicated.get("rendered_prompt_baseline_bytes")
        if isinstance(adj_bytes, int) and not isinstance(adj_bytes, bool):
            baseline_bytes = adj_bytes
    if baseline_tokens is None and adjudicated is not None:
        adj_tokens = adjudicated.get("rendered_prompt_baseline_tokens")
        if isinstance(adj_tokens, int) and not isinstance(adj_tokens, bool):
            baseline_tokens = adj_tokens

    if active_renderer is not None and (baseline_bytes is None or baseline_tokens is None):
        raw_prompt = _safe_rendered_prompt(request, renderer=active_renderer)
        if raw_prompt is not None:
            if baseline_bytes is None:
                baseline_bytes = len(raw_prompt.encode("utf-8"))
            if baseline_tokens is None:
                raw_counted = count_verifiable_tokens(
                    raw_prompt,
                    encoding_name=encoding_name,
                    token_counter=token_counter,
                )
                if raw_counted is not None:
                    baseline_tokens = raw_counted[0]

    updated_keep = _keep_raw_prefetch_payload(
        request,
        receipt,
        fallback_reason=fallback_reason,
        net_savings_bytes=net_savings_bytes,
        rendered_baseline_bytes=baseline_bytes,
        rendered_delivered_bytes=None,
        error_observation=error_observation,
        adjudicated=adjudicated,
    )
    ep = updated_keep["epoch_prefetch"]
    _clear_delivered_prompt_telemetry(ep)
    if baseline_bytes is not None:
        ep["rendered_prompt_baseline_bytes"] = baseline_bytes
    if baseline_tokens is not None:
        ep["rendered_prompt_baseline_tokens"] = baseline_tokens

    keep_prompt = _safe_rendered_prompt(updated_keep, renderer=active_renderer)
    for _ in range(3):
        if keep_prompt is None:
            _clear_delivered_prompt_telemetry(ep)
            return updated_keep
        keep_bytes = len(keep_prompt.encode("utf-8"))
        ep["rendered_prompt_delivered_bytes"] = keep_bytes
        if baseline_bytes is not None:
            ep["rendered_prompt_delta_bytes"] = keep_bytes - baseline_bytes
        else:
            ep.pop("rendered_prompt_delta_bytes", None)
        keep_counted = count_verifiable_tokens(
            keep_prompt,
            encoding_name=encoding_name,
            token_counter=token_counter,
        )
        if keep_counted is not None:
            keep_tokens, keep_tok_meta = keep_counted
            ep["rendered_prompt_delivered_tokens"] = keep_tokens
            if baseline_tokens is not None:
                ep["rendered_prompt_delta_tokens"] = keep_tokens - baseline_tokens
            else:
                ep.pop("rendered_prompt_delta_tokens", None)
            if "tokenizer" not in ep:
                ep["tokenizer"] = keep_tok_meta
        else:
            ep.pop("rendered_prompt_delivered_tokens", None)
            ep.pop("rendered_prompt_delta_tokens", None)

        stabilized_prompt = _safe_rendered_prompt(updated_keep, renderer=active_renderer)
        if stabilized_prompt == keep_prompt:
            return updated_keep
        keep_prompt = stabilized_prompt

    _clear_delivered_prompt_telemetry(ep)
    return updated_keep


def _external_verified_baseline(
    receipt: Mapping[str, Any],
    *,
    encoding_name: str,
    token_counter: Callable[[str], int] | None,
) -> tuple[int | None, int | None]:
    raw_bytes = receipt.get("baseline_bytes")
    fallback_bytes = (
        int(raw_bytes) if isinstance(raw_bytes, int) and not isinstance(raw_bytes, bool) else None
    )
    measured = _measure_verified_source_ref_tokens(
        receipt.get("source_ref") if isinstance(receipt.get("source_ref"), Mapping) else None,
        encoding_name=encoding_name,
        token_counter=token_counter,
    )
    if measured is not None:
        return measured[0], measured[1]
    receipt_tokens = receipt.get("baseline_tokens")
    receipt_tokenizer = receipt.get("tokenizer")
    if (
        receipt.get("baseline_tokens_verified") is True
        and isinstance(receipt_tokens, int)
        and not isinstance(receipt_tokens, bool)
        and isinstance(receipt_tokenizer, Mapping)
        and receipt_tokenizer.get("encoding") == encoding_name
    ):
        return fallback_bytes, int(receipt_tokens)
    return fallback_bytes, None


def _prompt_gate_policy(
    epoch_prefetch: Mapping[str, Any] | None,
    receipt: Mapping[str, Any],
) -> dict[str, Any] | None:
    if isinstance(epoch_prefetch, Mapping):
        return dict(epoch_prefetch)
    policy: dict[str, Any] = {}
    for key in (
        "min_net_savings_bytes",
        "min_net_savings_tokens",
        "cold_prefill_amortization_tokens",
        "tokenizer_encoding",
    ):
        if key in receipt and receipt[key] is not None:
            policy[key] = receipt[key]
    return policy or None


def _resolve_active_host_renderer(
    *,
    host: str | None,
    host_input_renderer: HostInputRenderer | None | object,
) -> HostInputRenderer | None:
    if host_input_renderer is not UNSET_HOST_INPUT_RENDERER:
        return cast(HostInputRenderer | None, host_input_renderer)
    if host is not None:
        return resolve_host_input_renderer(host)
    return _render_codex_cli_host_input


def _adjudicate_candidate_prompt_gate(
    *,
    policy_cfg: Mapping[str, Any] | None,
    encoding_name: str,
    token_counter: Callable[[str], int] | None,
    raw_prompt: str | None,
    epoch_prompt: str | None,
    has_inline_exploratory: bool,
    replace_exploratory_context: bool,
    ext_bytes: int | None,
    ext_tokens: int | None,
    potential_saved: int,
    host_input_measurable: bool,
) -> tuple[dict[str, Any], int | None, int | None]:
    before_bytes = len(raw_prompt.encode("utf-8")) if raw_prompt is not None else None
    after_bytes = len(epoch_prompt.encode("utf-8")) if epoch_prompt is not None else None
    before_counted = (
        count_verifiable_tokens(raw_prompt, encoding_name=encoding_name, token_counter=token_counter)
        if host_input_measurable and raw_prompt is not None
        else None
    )
    after_counted = (
        count_verifiable_tokens(epoch_prompt, encoding_name=encoding_name, token_counter=token_counter)
        if host_input_measurable and epoch_prompt is not None
        else None
    )
    tokenizer_meta = (
        before_counted[1]
        if before_counted is not None and after_counted is not None
        else None
    )
    adjudicated = evaluate_epoch_prefetch_prompt_gate(
        epoch_prefetch=policy_cfg,
        has_inline_exploratory=has_inline_exploratory,
        replace_exploratory_context=replace_exploratory_context,
        host_input_measurable=host_input_measurable,
        rendered_prompt_baseline_bytes=before_bytes,
        rendered_prompt_delivered_bytes=after_bytes,
        rendered_prompt_baseline_tokens=before_counted[0] if before_counted is not None else None,
        rendered_prompt_delivered_tokens=after_counted[0] if after_counted is not None else None,
        external_baseline_bytes=ext_bytes,
        external_baseline_tokens=ext_tokens,
        potential_bytes_saved=potential_saved,
        tokenizer=tokenizer_meta,
    )
    return adjudicated, before_bytes, after_bytes


def apply_turn_epoch_prefetch_to_request(
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    replace_exploratory_context: bool = False,
    epoch_prefetch: Mapping[str, Any] | None = None,
    token_counter: Callable[[str], int] | None = None,
    host: str | None = None,
    host_input_renderer: HostInputRenderer | None | object = UNSET_HOST_INPUT_RENDERER,
) -> dict[str, Any]:
    """Attach Stage-then-Swap observation onto a host request with host-accurate prompt token gating."""
    policy_cfg = _prompt_gate_policy(epoch_prefetch, receipt)
    encoding_name = str((policy_cfg or {}).get("tokenizer_encoding") or DEFAULT_TOKENIZER_ENCODING)
    active_renderer = _resolve_active_host_renderer(
        host=host,
        host_input_renderer=host_input_renderer,
    )
    if receipt.get("operator") != "EpochReset":
        return _measure_and_finalize_keep_raw_payload(
            request,
            receipt,
            fallback_reason=str(receipt.get("fallback_reason") or "command_failed"),
            net_savings_bytes=0,
            active_renderer=active_renderer,
            encoding_name=encoding_name,
            token_counter=token_counter,
        )

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

    raw_prompt = _safe_rendered_prompt(request, renderer=active_renderer)
    epoch_prompt = _safe_rendered_prompt(candidate, renderer=active_renderer)
    ext_bytes, ext_tokens = (
        (None, None)
        if has_inline_exploratory
        else _external_verified_baseline(receipt, encoding_name=encoding_name, token_counter=token_counter)
    )
    host_input_measurable = (
        active_renderer is not None
        and raw_prompt is not None
        and epoch_prompt is not None
        and not (
            not has_inline_exploratory
            and bool(receipt.get("hydrated_text"))
            and epoch_prompt == raw_prompt
        )
    )

    adjudicated: dict[str, Any] | None = None
    before_bytes: int | None = None
    after_bytes: int | None = None
    for _ in range(2):
        adjudicated, before_bytes, after_bytes = _adjudicate_candidate_prompt_gate(
            policy_cfg=policy_cfg,
            encoding_name=encoding_name,
            token_counter=token_counter,
            raw_prompt=raw_prompt,
            epoch_prompt=epoch_prompt,
            has_inline_exploratory=has_inline_exploratory,
            replace_exploratory_context=replace_exploratory_context,
            ext_bytes=ext_bytes,
            ext_tokens=ext_tokens,
            potential_saved=potential_saved,
            host_input_measurable=host_input_measurable,
        )
        if adjudicated.get("operator") != "EpochReset":
            return _measure_and_finalize_keep_raw_payload(
                request,
                receipt,
                fallback_reason=str(adjudicated.get("fallback_reason") or "negative_net_savings"),
                net_savings_bytes=int(adjudicated.get("net_savings_bytes") or 0),
                error_observation=str(adjudicated.get("error_observation") or ""),
                adjudicated=adjudicated,
                rendered_baseline_bytes=before_bytes,
                active_renderer=active_renderer,
                encoding_name=encoding_name,
                token_counter=token_counter,
            )
        epoch_payload["bytes_saved"] = adjudicated["bytes_saved"]
        epoch_payload["net_savings_bytes"] = adjudicated["net_savings_bytes"]
        final_prompt = _safe_rendered_prompt(candidate, renderer=active_renderer)
        if final_prompt is None:
            host_input_measurable = False
            epoch_prompt = None
            continue
        if final_prompt == epoch_prompt:
            _apply_prompt_gate_audit_fields(epoch_payload, adjudicated)
            return candidate
        epoch_prompt = final_prompt

    # Fail closed if rendered host output did not stabilize after updating bytes_saved.
    fallback_adj, before_bytes, _after_bytes = _adjudicate_candidate_prompt_gate(
        policy_cfg=policy_cfg,
        encoding_name=encoding_name,
        token_counter=token_counter,
        raw_prompt=raw_prompt,
        epoch_prompt=epoch_prompt,
        has_inline_exploratory=has_inline_exploratory,
        replace_exploratory_context=replace_exploratory_context,
        ext_bytes=ext_bytes,
        ext_tokens=ext_tokens,
        potential_saved=potential_saved,
        host_input_measurable=False,
    )
    return _measure_and_finalize_keep_raw_payload(
        request,
        receipt,
        fallback_reason=str(fallback_adj.get("fallback_reason") or "host_input_unmeasurable"),
        net_savings_bytes=int(fallback_adj.get("net_savings_bytes") or 0),
        error_observation=str(fallback_adj.get("error_observation") or ""),
        adjudicated=fallback_adj,
        rendered_baseline_bytes=before_bytes,
        active_renderer=active_renderer,
        encoding_name=encoding_name,
        token_counter=token_counter,
    )


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
    host: str | None = None,
    host_input_renderer: HostInputRenderer | None | object = UNSET_HOST_INPUT_RENDERER,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    """Evaluate default-off Quiet-Turn & Stage-then-Swap EpochReset state for a Turn."""
    goal_state_root = journal_path.parent.parent
    sqlite_path = goal_state_root / "turn_prefetch_state.sqlite"
    effective_host = (
        host
        if host is not None
        else (
            str(plan["host"].get("kind") or "")
            if isinstance(plan.get("host"), Mapping)
            else ""
        )
    )

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
        target = _quiet_turn_target_params(
            plan=plan,
            goal_id=goal_id,
            agent_id=agent_id,
            goal_state_root=goal_state_root,
            effective_quiet=effective_quiet,
        )
        # Never commit digest before Turn settlement succeeds; inspect read-only here.
        quiet_meta = resolve_quiet_turn_metadata(
            scope_key=target.scope_key,
            file_path=target.file_path,
            sqlite_path=target.sqlite_path,
            enabled=True,
            quiet_noop_allowed=target.quiet_noop_allowed,
            must_attempt=target.must_attempt,
            state_changed=target.state_changed,
            current_state_digest=target.state_digest,
            record_if_changed=False,
            work_contract=target.work_contract,
            turn_envelope=target.turn_envelope,
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
                epoch_prefetch=effective_epoch_prefetch,
                host=effective_host,
                host_input_renderer=host_input_renderer,
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
                    "error_observation",
                    *_PROMPT_GATE_AUDIT_FIELDS,
                ):
                    if field in adjudicated:
                        prefetch_receipt[field] = adjudicated[field]
                    elif field in _PROMPT_GATE_AUDIT_FIELDS:
                        prefetch_receipt.pop(field, None)
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
        host=options.get("host"),
        host_input_renderer=options.get("host_input_renderer", UNSET_HOST_INPUT_RENDERER),
    )
    if replay_prefetch is not None:
        payload["epoch_prefetch"] = replay_prefetch
    if replay_quiet is not None:
        payload["quiet_turn"] = replay_quiet
    if isinstance(existing_journal.get("semantic_unit_fold"), Mapping):
        payload["semantic_unit_fold"] = dict(existing_journal["semantic_unit_fold"])
    return payload


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
    from .todo_unit_settlement import (
        extract_semantic_unit_fold_option,
        prepare_turn_semantic_unit_context,
    )

    epoch_prefetch = options.get("epoch_prefetch")
    quiet_turn = options.get("quiet_turn", False)
    decision_capture_payload = options.get("decision_capture_payload")
    epoch_opted_in = _is_turn_option_enabled(
        epoch_prefetch, decision_capture_payload, "epoch_prefetch",
    )
    quiet_opted_in = _is_turn_option_enabled(
        quiet_turn, decision_capture_payload, "quiet_turn", allow_bool_true=True,
    )
    semantic_unit_opted_in = (
        extract_semantic_unit_fold_option(options, decision_capture_payload) is not None
    )
    if not (epoch_opted_in or quiet_opted_in or semantic_unit_opted_in):
        return {}, dict(request)

    allow_execute = bool(
        needs_host
        and not (
            isinstance(existing_journal, Mapping)
            and isinstance(existing_journal.get("epoch_prefetch"), Mapping)
        )
    )
    effective_agent_id = _effective_agent_id(plan, settlement_lineage)
    prefetch_receipt: dict[str, Any] | None = None
    quiet_meta: dict[str, Any] | None = None
    updated_request = dict(request)
    if epoch_opted_in or quiet_opted_in:
        prefetch_receipt, quiet_meta, updated_request = prepare_turn_epoch_context(
            plan=plan,
            request=updated_request,
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
            host=options.get("host"),
            host_input_renderer=options.get("host_input_renderer", UNSET_HOST_INPUT_RENDERER),
        )
    semantic_meta: dict[str, Any] | None = None
    if semantic_unit_opted_in:
        semantic_meta, updated_request = prepare_turn_semantic_unit_context(
            plan=plan,
            request=updated_request,
            journal_path=journal_path,
            goal_id=goal_id,
            agent_id=effective_agent_id,
            turn_key=turn_key,
            settlement_lineage=settlement_lineage,
            options=options,
            existing_journal=existing_journal,
            needs_host=needs_host,
        )
    metadata: dict[str, Any] = {}
    if prefetch_receipt is not None or quiet_meta is not None:
        metadata["epoch_prefetch"] = prefetch_receipt
        metadata["quiet_turn"] = quiet_meta
    if isinstance(semantic_meta, Mapping):
        for key, value in semantic_meta.items():
            metadata[str(key)] = value
    return metadata, updated_request


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
    for key in ("semantic_unit_anchor", "semantic_unit_fold"):
        value = epoch_metadata.get(key)
        if isinstance(value, Mapping) and key not in journal:
            journal[key] = dict(value)


def reset_failed_turn_journal_with_epoch_metadata(
    journal: dict[str, Any],
    *,
    validation_reinvokes_host: bool,
    completed_prefix: list[str],
    epoch_metadata: Mapping[str, Any],
) -> None:
    """Reset failed phase fields in-place when retrying an existing Turn journal."""
    receipt = journal.get("receipt") if isinstance(journal.get("receipt"), dict) else {}
    if receipt.get("failed_phase") == "validation":
        if validation_reinvokes_host:
            journal.pop("host_result", None)
        journal.pop("result_kind", None)
        journal["completed_phases"] = (
            list(completed_prefix) if isinstance(journal.get("host_result"), dict) else []
        )
        journal.pop("task_validation", None)
        journal.pop("validation_stage", None)
    for key in ("reason", "receipt", "host_recovery", "host_failure"):
        journal.pop(key, None)
    journal["status"] = "in_progress"
    attach_epoch_metadata_to_journal(journal, epoch_metadata)


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
    source_ref = (
        {"path": str(recovery["path"]), "sha256": str(recovery["sha256"])}
        if isinstance(recovery.get("path"), str) and isinstance(recovery.get("sha256"), str)
        else None
    )
    baseline_bytes = cfg.get("baseline_bytes")
    if baseline_bytes is None and source_ref is not None:
        try:
            baseline_bytes = Path(source_ref["path"]).stat().st_size
        except OSError:
            baseline_bytes = None
    goal_label = str(state.get("goal_id") or event.get("goal_id") or "default")
    agent_label = str(state.get("agent_id") or event.get("agent_id") or "default")
    context_epoch = str(state.get("context_epoch") or "epoch-0")
    with lock_ctx(directory) as root:
        current_state = read_state_fn(root)
        receipt = resolve_or_execute_epoch_prefetch(
            epoch_prefetch=cfg,
            scope_key=f"codex_context:{goal_label}:{agent_label}",
            context_epoch=context_epoch,
            file_store_dir=root / "epoch_prefetch",
            sqlite_path=root / "epoch_prefetch.sqlite",
            reclaimed_turns=int(event.get("reclaimed_turns") or cfg.get("reclaimed_turns") or 0),
            dropped_failures=int(event.get("dropped_failures") or cfg.get("dropped_failures") or 0),
            failure_facts=list(event.get("failure_facts") or cfg.get("failure_facts") or ()),
            open_obligations=list(event.get("open_obligations") or cfg.get("open_obligations") or ()),
            source_ref=source_ref,
            baseline_bytes=baseline_bytes,
            cwd=event.get("cwd"),
            existing_inline_record=current_state.get("epoch_prefetch"),
            require_token_gate=True,
        )
        if receipt is not None:
            current_state["epoch_prefetch"] = receipt
            save_state_fn(root, current_state)
    if receipt is None:
        return None
    epoch_meta = {
        "schema_version": receipt["schema_version"],
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
