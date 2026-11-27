"""Durable File + SQLite Quiet-Turn state, skip receipts, and pre-host guards.

Work-contract and Quiet-Turn skip semantics remain TypeScript-owned
(`quota/stage_then_swap.ts` via `quota/turn_envelope.py`). This module owns
once-only File + SQLite persistence, crash-healing, and terminal skip replay.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from ...file_lock import exclusive_file_lock
from ..quota.turn_envelope import compute_quiet_turn_state_digest, evaluate_quiet_turn
from .journal_store import TURN_KEY_RE

QUIET_TURN_STATE_SCHEMA_VERSION = "loopx_quiet_turn_state_v0"
QUIET_TURN_SKIP_SCHEMA_VERSION = "loopx_quiet_turn_skip_receipt_v0"


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
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


def init_turn_prefetch_sqlite_db(connection: sqlite3.Connection) -> None:
    """Initialize shared Stage-then-Swap EpochReset & Quiet-Turn SQLite schema."""
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


def is_turn_option_enabled(
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


def effective_turn_agent_id(
    plan: Mapping[str, Any], settlement_lineage: Mapping[str, Any] | None,
) -> str:
    if settlement_lineage is not None:
        return str(settlement_lineage["agent_id"])
    raw_contract = plan.get("work_contract")
    raw_envelope = plan.get("turn_envelope")
    container: Any = (
        (raw_contract if isinstance(raw_contract, Mapping) else {}).get("agent_identity", {})
        if plan.get("schema_version") == "loopx_turn_plan_v1"
        else (raw_envelope if isinstance(raw_envelope, Mapping) else {})
    )
    return str(container.get("agent_id") or "") if isinstance(container, Mapping) else ""


def _resolve_effective_quiet(
    quiet_turn: Any, decision_capture_payload: Mapping[str, Any] | None,
) -> Any:
    if quiet_turn:
        return quiet_turn
    if isinstance(decision_capture_payload, Mapping):
        return decision_capture_payload.get("quiet_turn")
    return False


@dataclass(frozen=True)
class QuietTurnTarget:
    scope_key: str
    file_path: Path
    sqlite_path: Path
    config: dict[str, Any]
    quiet_noop_allowed: bool
    must_attempt: bool
    state_changed: Any
    state_digest: str
    work_contract: dict[str, Any] | None
    turn_envelope: dict[str, Any] | None


def _compact_contract_slice(contract: Any) -> dict[str, Any] | None:
    if not isinstance(contract, Mapping):
        return None
    compact: dict[str, Any] = {}
    for flag in ("must_attempt", "quiet_noop_allowed"):
        if flag in contract:
            compact[flag] = contract[flag]
    interaction = contract.get("interaction_contract")
    if isinstance(interaction, Mapping):
        agent_channel = interaction.get("agent_channel")
        if isinstance(agent_channel, Mapping):
            compact["interaction_contract"] = {
                "agent_channel": {
                    key: agent_channel[key]
                    for key in ("must_attempt", "quiet_noop_allowed", "delivery_allowed")
                    if key in agent_channel
                }
            }
    action = contract.get("action")
    if isinstance(action, Mapping):
        compact_action = {
            key: action[key]
            for key in ("must_attempt", "quiet_noop_allowed", "effective_action")
            if key in action
        }
        if compact_action:
            compact["action"] = compact_action
    decision = contract.get("decision")
    if isinstance(decision, Mapping):
        compact_decision = _compact_contract_slice(decision)
        if compact_decision:
            compact["decision"] = compact_decision
    return compact


def _plan_quiet_contract_inputs(
    plan: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    work_contract_slice = _compact_contract_slice(plan.get("work_contract"))
    turn_envelope_slice = _compact_contract_slice(plan.get("turn_envelope"))
    if work_contract_slice is None and turn_envelope_slice is None:
        # Fail closed via TS resolvePlanQuietNoopEligibility when both contracts are absent.
        return {}, None
    return work_contract_slice, turn_envelope_slice


def _hook_quiet_contract_inputs(
    *,
    event: Mapping[str, Any],
    state: Mapping[str, Any],
    qt_cfg: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool, bool]:
    raw_work = event.get("work_contract") if "work_contract" in event else state.get("work_contract")
    raw_env = event.get("turn_envelope") if "turn_envelope" in event else state.get("turn_envelope")
    work_contract_slice = _compact_contract_slice(raw_work)
    turn_envelope_slice = _compact_contract_slice(raw_env)
    must_attempt_opt = qt_cfg.get("must_attempt") is True
    raw_quiet = qt_cfg.get("quiet_noop_allowed", True)
    quiet_cap = raw_quiet if isinstance(raw_quiet, bool) else bool(raw_quiet)
    return work_contract_slice, turn_envelope_slice, must_attempt_opt, quiet_cap


def build_cli_quiet_turn_option(
    plan: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str,
) -> dict[str, Any]:
    """Build structured Quiet-Turn opt-in config from the authoritative Turn plan."""
    authoritative = compute_quiet_turn_state_digest(
        goal_id=goal_id,
        agent_id=agent_id,
        route=plan.get("route") if isinstance(plan.get("route"), Mapping) else None,
        work_contract=(
            plan.get("work_contract") if isinstance(plan.get("work_contract"), Mapping) else None
        ),
        turn_envelope=(
            plan.get("turn_envelope") if isinstance(plan.get("turn_envelope"), Mapping) else None
        ),
    )
    return {
        "enabled": True,
        "quiet_noop_allowed": bool(authoritative["quiet_noop_allowed"]),
        "must_attempt": bool(authoritative["must_attempt"]),
        "current_state_digest": str(authoritative["state_digest"]),
        "skip_host_when_unchanged": True,
        "record_wait_baseline_when_changed": True,
    }


def quiet_turn_target_params(
    *,
    plan: Mapping[str, Any],
    goal_id: str,
    agent_id: str,
    goal_state_root: Path,
    effective_quiet: Any,
) -> QuietTurnTarget:
    qt_cfg = dict(effective_quiet) if isinstance(effective_quiet, Mapping) else {}
    work_contract_slice, turn_envelope_slice = _plan_quiet_contract_inputs(plan)
    raw_quiet = qt_cfg.get("quiet_noop_allowed", True)
    quiet_cap = raw_quiet if isinstance(raw_quiet, bool) else bool(raw_quiet)
    must_attempt_opt = qt_cfg.get("must_attempt") is True
    scope_key = f"turn_quiet:{goal_id}:{agent_id}"
    raw_envelope = plan.get("turn_envelope")
    if isinstance(qt_cfg.get("current_state_digest"), str):
        state_digest = str(qt_cfg["current_state_digest"])
    else:
        computed = compute_quiet_turn_state_digest(
            goal_id=goal_id,
            agent_id=agent_id,
            route=plan.get("route") if isinstance(plan.get("route"), Mapping) else None,
            work_contract=(
                plan.get("work_contract") if isinstance(plan.get("work_contract"), Mapping) else None
            ),
            turn_envelope=(
                raw_envelope if isinstance(raw_envelope, Mapping) else None
            ),
        )
        state_digest = str(computed["state_digest"])
    return QuietTurnTarget(
        scope_key=scope_key,
        file_path=goal_state_root / "quiet_turn" / f"{sha256_text(scope_key)}.json",
        sqlite_path=goal_state_root / "turn_prefetch_state.sqlite",
        config=qt_cfg,
        quiet_noop_allowed=quiet_cap,
        must_attempt=must_attempt_opt,
        state_changed=qt_cfg.get("state_changed"),
        state_digest=state_digest,
        work_contract=work_contract_slice,
        turn_envelope=turn_envelope_slice,
    )


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
        "updated_at": now_iso(),
    }
    if file_path is not None:
        atomic_write_json(file_path, state_payload)
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(sqlite_path, timeout=5.0, isolation_level=None)) as connection:
            init_turn_prefetch_sqlite_db(connection)
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
                        now_iso(),
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
    must_attempt: bool = False,
    state_changed: bool | None = None,
    current_state_digest: str | None = None,
    record_if_changed: bool = True,
    work_contract: Mapping[str, Any] | None = None,
    turn_envelope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute and optionally persist Quiet-Turn zero-tax skip metadata in File & SQLite."""
    file_digest: str | None = None
    sqlite_digest: str | None = None
    corrupt_stores: list[str] = []
    if current_state_digest is not None:
        if file_path is not None and file_path.exists():
            if not file_path.is_file():
                corrupt_stores.append("file")
            else:
                try:
                    saved = json.loads(file_path.read_text(encoding="utf-8"))
                    if (
                        isinstance(saved, dict)
                        and saved.get("scope_key") == scope_key
                        and isinstance(saved.get("state_digest"), str)
                        and saved.get("state_digest")
                    ):
                        file_digest = str(saved["state_digest"])
                    else:
                        corrupt_stores.append("file")
                except (OSError, ValueError):
                    corrupt_stores.append("file")
        if sqlite_path is not None and sqlite_path.exists():
            try:
                with closing(sqlite3.connect(sqlite_path, timeout=5.0)) as connection:
                    has_table = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quiet_turn_state'"
                    ).fetchone()
                    if has_table is not None:
                        row = connection.execute(
                            "SELECT state_digest FROM quiet_turn_state WHERE scope_key = ?",
                            (scope_key,),
                        ).fetchone()
                        if row is not None:
                            if isinstance(row[0], str) and row[0]:
                                sqlite_digest = row[0]
                            else:
                                corrupt_stores.append("sqlite")
            except sqlite3.Error:
                corrupt_stores.append("sqlite")

    if corrupt_stores:
        corrupt_meta = evaluate_quiet_turn(
            enabled=enabled,
            quiet_noop_allowed=quiet_noop_allowed,
            must_attempt=must_attempt,
            state_changed=True,
            current_state_digest=current_state_digest,
            previous_state_digest=None,
            work_contract=work_contract,
            turn_envelope=turn_envelope,
        )
        return {
            **corrupt_meta,
            "reason": "state_corrupt",
            "state_corrupt": True,
            "corrupt_stores": corrupt_stores,
        }

    if file_digest is not None and sqlite_digest is not None and file_digest != sqlite_digest:
        conflict_meta = evaluate_quiet_turn(
            enabled=enabled,
            quiet_noop_allowed=quiet_noop_allowed,
            must_attempt=must_attempt,
            state_changed=True,
            current_state_digest=current_state_digest,
            previous_state_digest=None,
            work_contract=work_contract,
            turn_envelope=turn_envelope,
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
        must_attempt=must_attempt,
        state_changed=state_changed,
        current_state_digest=current_state_digest,
        previous_state_digest=previous_digest,
        work_contract=work_contract,
        turn_envelope=turn_envelope,
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


def _quiet_turn_skip_paths(journal_path: Path, turn_key: str) -> tuple[Path, Path, Path]:
    goal_state_root = journal_path.parent.parent
    skip_file = goal_state_root / "quiet_turn" / "skips" / f"{sha256_text(turn_key)}.json"
    sqlite_path = goal_state_root / "turn_prefetch_state.sqlite"
    return goal_state_root, skip_file, sqlite_path


def quiet_turn_skip_requested(
    quiet_turn: Any,
    decision_capture_payload: Mapping[str, Any] | None,
) -> bool:
    if not is_turn_option_enabled(
        quiet_turn, decision_capture_payload, "quiet_turn", allow_bool_true=True,
    ):
        return False
    effective_quiet = _resolve_effective_quiet(quiet_turn, decision_capture_payload)
    return isinstance(effective_quiet, Mapping) and effective_quiet.get("skip_host_when_unchanged") is True


def has_quiet_turn_skip_store(journal_path: Path, turn_key: str) -> bool:
    """Cheap non-creating probe for File or SQLite Quiet-Turn skip persistence."""
    _goal_state_root, skip_file, sqlite_path = _quiet_turn_skip_paths(journal_path, turn_key)
    return skip_file.exists() or sqlite_path.exists()


def resolve_turn_request_or_quiet_wait(
    *,
    plan: Mapping[str, Any],
    transaction_plan: Mapping[str, Any],
    execute: bool,
    runtime_root: Path,
    goal_id: str,
    quiet_turn: Any,
    decision_capture_payload: Mapping[str, Any] | None,
    journal_path_fn: Callable[..., Path],
    request_builder: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    """Build executable Host request upfront or defer only for quiet-noop wait Turns."""
    raw_route = plan.get("route")
    route = raw_route if isinstance(raw_route, Mapping) else {}
    raw_turn_key = transaction_plan.get("turn_key")
    if (
        execute
        and route.get("would_invoke_host") is not True
        and route.get("kind") == "wait"
        and isinstance(raw_turn_key, str)
        and TURN_KEY_RE.fullmatch(raw_turn_key)
    ):
        probe_journal_path = journal_path_fn(
            runtime_root, goal_id=goal_id, turn_key=raw_turn_key,
        )
        if quiet_turn_skip_requested(quiet_turn, decision_capture_payload) or has_quiet_turn_skip_store(
            probe_journal_path, raw_turn_key,
        ):
            return None, raw_turn_key
    request = request_builder(plan)
    return request, str(request["turn_key"])


def _validate_skip_receipt_mapping(
    raw: Any,
    *,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    scope_key: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("Quiet-Turn skip receipt must be a JSON object")
    receipt = dict(raw)
    if receipt.get("schema_version") != QUIET_TURN_SKIP_SCHEMA_VERSION:
        raise ValueError("Quiet-Turn skip receipt schema_version mismatch")
    if receipt.get("turn_key") != turn_key or receipt.get("scope_key") != scope_key:
        raise ValueError("Quiet-Turn skip receipt identity mismatch")
    if "goal_id" in receipt and receipt.get("goal_id") != goal_id:
        raise ValueError("Quiet-Turn skip receipt goal_id mismatch")
    if "agent_id" in receipt and receipt.get("agent_id") != agent_id:
        raise ValueError("Quiet-Turn skip receipt agent_id mismatch")
    quiet_meta = receipt.get("quiet_turn")
    if not isinstance(quiet_meta, Mapping) or quiet_meta.get("skipped") is not True:
        raise ValueError("Quiet-Turn skip receipt must have skipped=true")
    return receipt


def _read_file_skip_receipt(
    skip_file: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    scope_key: str,
) -> dict[str, Any] | None:
    if not skip_file.exists():
        return None
    if not skip_file.is_file():
        raise ValueError(f"Quiet-Turn skip receipt path is not a file: {skip_file}")
    try:
        raw = json.loads(skip_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Corrupt File Quiet-Turn skip receipt") from exc
    return _validate_skip_receipt_mapping(
        raw,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_key=turn_key,
        scope_key=scope_key,
    )


def _read_sqlite_skip_receipt(
    sqlite_path: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    scope_key: str,
) -> dict[str, Any] | None:
    if not sqlite_path.exists():
        return None
    try:
        with closing(sqlite3.connect(sqlite_path, timeout=5.0)) as connection:
            has_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quiet_turn_skips'"
            ).fetchone()
            if has_table is None:
                return None
            row = connection.execute(
                """
                SELECT scope_key, state_digest, receipt_json, recorded_at
                FROM quiet_turn_skips
                WHERE turn_key = ?
                """,
                (turn_key,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("Corrupt SQLite Quiet-Turn skip store") from exc
    if row is None:
        return None
    row_scope_key, row_state_digest, row_json, row_recorded_at = row
    if row_scope_key != scope_key:
        raise ValueError("SQLite Quiet-Turn skip receipt scope_key mismatch")
    try:
        raw = json.loads(row_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("Corrupt SQLite Quiet-Turn skip receipt") from exc
    receipt = _validate_skip_receipt_mapping(
        raw,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_key=turn_key,
        scope_key=scope_key,
    )
    if (row_state_digest or None) != (receipt.get("state_digest") or None) or (
        row_recorded_at != receipt.get("recorded_at")
    ):
        raise ValueError("SQLite Quiet-Turn skip receipt metadata mismatch")
    return receipt


def _write_sqlite_skip_receipt_once(
    sqlite_path: Path,
    receipt: Mapping[str, Any],
) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(receipt), ensure_ascii=False, sort_keys=True)
    with closing(sqlite3.connect(sqlite_path, timeout=5.0, isolation_level=None)) as connection:
        init_turn_prefetch_sqlite_db(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT scope_key FROM quiet_turn_skips WHERE turn_key = ?",
                (str(receipt["turn_key"]),),
            ).fetchone()
            if existing is not None:
                raise ValueError("Quiet-Turn skip receipt already exists in SQLite")
            connection.execute(
                """
                INSERT INTO quiet_turn_skips (turn_key, scope_key, state_digest, receipt_json, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(receipt["turn_key"]),
                    str(receipt["scope_key"]),
                    str(receipt.get("state_digest") or "") or None,
                    encoded,
                    str(receipt["recorded_at"]),
                ),
            )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise


def _load_or_heal_quiet_turn_skip_receipt(
    *,
    skip_file: Path,
    sqlite_path: Path,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    scope_key: str,
) -> dict[str, Any] | None:
    if not skip_file.exists():
        peek_sqlite = _read_sqlite_skip_receipt(
            sqlite_path,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_key=turn_key,
            scope_key=scope_key,
        )
        if peek_sqlite is None:
            return None
    with exclusive_file_lock(skip_file):
        file_receipt = _read_file_skip_receipt(
            skip_file,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_key=turn_key,
            scope_key=scope_key,
        )
        sqlite_receipt = _read_sqlite_skip_receipt(
            sqlite_path,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_key=turn_key,
            scope_key=scope_key,
        )
        if file_receipt is None and sqlite_receipt is None:
            return None
        if file_receipt is not None and sqlite_receipt is not None:
            if file_receipt != sqlite_receipt:
                raise ValueError("Conflicting File and SQLite Quiet-Turn skip receipts")
            return file_receipt
        if file_receipt is not None:
            _write_sqlite_skip_receipt_once(sqlite_path, file_receipt)
            return file_receipt
        assert sqlite_receipt is not None
        atomic_write_json(skip_file, sqlite_receipt)
        return sqlite_receipt


def recover_quiet_turn_skip_receipt(
    *,
    plan: Mapping[str, Any],
    journal_path: Path,
    goal_id: str,
    turn_key: str,
    settlement_lineage: Mapping[str, Any] | None,
    host_projection: Mapping[str, Any],
    empty_effects: Mapping[str, bool],
    payload_builder: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    """Replay an already-committed Quiet-Turn skip terminally without rewriting receipts or invoking Host."""
    _goal_state_root, skip_file, sqlite_path = _quiet_turn_skip_paths(journal_path, turn_key)
    if not skip_file.exists() and not sqlite_path.exists():
        return None
    agent_id = effective_turn_agent_id(plan, settlement_lineage)
    scope_key = f"turn_quiet:{goal_id}:{agent_id}"
    receipt = _load_or_heal_quiet_turn_skip_receipt(
        skip_file=skip_file,
        sqlite_path=sqlite_path,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_key=turn_key,
        scope_key=scope_key,
    )
    if receipt is None:
        return None
    replayed_quiet = dict(receipt["quiet_turn"])
    replayed_quiet["replayed"] = True
    return {
        **payload_builder(
            plan,
            {"status": "stopped", "host": host_projection, "reason": "quiet_turn_unchanged"},
            execute=True,
            replayed=True,
            effects=empty_effects,
        ),
        "status": "skipped",
        "quiet_turn": replayed_quiet,
    }


def assert_no_conflicting_quiet_turn_skip(
    *,
    journal_path: Path,
    goal_id: str,
    agent_id: str,
    turn_key: str,
) -> None:
    """Fail closed if a Turn journal coexists with a committed Quiet-Turn skip receipt."""
    _goal_state_root, skip_file, sqlite_path = _quiet_turn_skip_paths(journal_path, turn_key)
    if not skip_file.exists() and not sqlite_path.exists():
        return
    scope_key = f"turn_quiet:{goal_id}:{agent_id}"
    if skip_file.exists():
        _read_file_skip_receipt(
            skip_file,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_key=turn_key,
            scope_key=scope_key,
        )
        raise ValueError("Conflicting Turn journal and File Quiet-Turn skip receipt for turn_key")
    sqlite_receipt = _read_sqlite_skip_receipt(
        sqlite_path,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_key=turn_key,
        scope_key=scope_key,
    )
    if sqlite_receipt is not None:
        raise ValueError("Conflicting Turn journal and SQLite Quiet-Turn skip receipt for turn_key")


def _record_quiet_turn_skip_receipt(
    *,
    goal_state_root: Path,
    sqlite_path: Path,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    scope_key: str,
    quiet_meta: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    skip_file = goal_state_root / "quiet_turn" / "skips" / f"{sha256_text(turn_key)}.json"
    incoming_digest = str(quiet_meta.get("state_digest") or "") or None
    with exclusive_file_lock(skip_file):
        file_receipt = _read_file_skip_receipt(
            skip_file,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_key=turn_key,
            scope_key=scope_key,
        )
        sqlite_receipt = _read_sqlite_skip_receipt(
            sqlite_path,
            goal_id=goal_id,
            agent_id=agent_id,
            turn_key=turn_key,
            scope_key=scope_key,
        )
        if file_receipt is not None or sqlite_receipt is not None:
            if file_receipt is not None and sqlite_receipt is not None:
                if file_receipt != sqlite_receipt:
                    raise ValueError("Conflicting File and SQLite Quiet-Turn skip receipts")
                existing = file_receipt
            elif file_receipt is not None:
                existing = file_receipt
            else:
                assert sqlite_receipt is not None
                existing = sqlite_receipt
            if (existing.get("state_digest") or None) != incoming_digest:
                raise ValueError("Conflicting Quiet-Turn skip state_digest for committed turn_key")
            if file_receipt is None:
                atomic_write_json(skip_file, existing)
            elif sqlite_receipt is None:
                _write_sqlite_skip_receipt_once(sqlite_path, existing)
            return existing, True

        skip_payload = {
            "schema_version": QUIET_TURN_SKIP_SCHEMA_VERSION,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "turn_key": turn_key,
            "scope_key": scope_key,
            "state_digest": incoming_digest,
            "quiet_turn": dict(quiet_meta),
            "recorded_at": now_iso(),
        }
        atomic_write_json(skip_file, skip_payload)
        _write_sqlite_skip_receipt_once(sqlite_path, skip_payload)
        return skip_payload, False


def _evaluate_quiet_skip_candidate(
    *,
    plan: Mapping[str, Any],
    journal_path: Path,
    goal_id: str,
    settlement_lineage: Mapping[str, Any] | None,
    options: Mapping[str, Any],
) -> tuple[Path, str, QuietTurnTarget, dict[str, Any], Mapping[str, Any]] | None:
    quiet_turn = options.get("quiet_turn", False)
    decision_capture_payload = options.get("decision_capture_payload")
    if not is_turn_option_enabled(
        quiet_turn, decision_capture_payload, "quiet_turn", allow_bool_true=True,
    ):
        return None
    effective_quiet = _resolve_effective_quiet(quiet_turn, decision_capture_payload)
    skip_host_cfg = effective_quiet if isinstance(effective_quiet, Mapping) else {}
    if not skip_host_cfg.get("skip_host_when_unchanged"):
        return None
    goal_state_root = journal_path.parent.parent
    agent_id = effective_turn_agent_id(plan, settlement_lineage)
    target = quiet_turn_target_params(
        plan=plan,
        goal_id=goal_id,
        agent_id=agent_id,
        goal_state_root=goal_state_root,
        effective_quiet=effective_quiet,
    )
    with exclusive_file_lock(target.file_path):
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
    return goal_state_root, agent_id, target, quiet_meta, skip_host_cfg


def _skip_payload_from_meta(
    *,
    plan: Mapping[str, Any],
    goal_state_root: Path,
    agent_id: str,
    goal_id: str,
    turn_key: str,
    target: QuietTurnTarget,
    quiet_meta: Mapping[str, Any],
    host_projection: Mapping[str, Any],
    empty_effects: Mapping[str, bool],
    payload_builder: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    stored_receipt, replayed_existing = _record_quiet_turn_skip_receipt(
        goal_state_root=goal_state_root,
        sqlite_path=target.sqlite_path,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_key=turn_key,
        scope_key=target.scope_key,
        quiet_meta=quiet_meta,
    )
    returned_quiet = dict(stored_receipt["quiet_turn"])
    if replayed_existing:
        returned_quiet["replayed"] = True
    return {
        **payload_builder(
            plan,
            {"status": "stopped", "host": host_projection, "reason": "quiet_turn_unchanged"},
            execute=True,
            replayed=replayed_existing,
            effects=empty_effects,
        ),
        "status": "skipped",
        "quiet_turn": returned_quiet,
    }


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
    replayed_payload = recover_quiet_turn_skip_receipt(
        plan=plan,
        journal_path=journal_path,
        goal_id=goal_id,
        turn_key=turn_key,
        settlement_lineage=settlement_lineage,
        host_projection=host_projection,
        empty_effects=empty_effects,
        payload_builder=payload_builder,
    )
    if replayed_payload is not None:
        return replayed_payload

    evaluated = _evaluate_quiet_skip_candidate(
        plan=plan,
        journal_path=journal_path,
        goal_id=goal_id,
        settlement_lineage=settlement_lineage,
        options=options,
    )
    if evaluated is None:
        return None
    goal_state_root, agent_id, target, quiet_meta, _skip_host_cfg = evaluated
    if not quiet_meta.get("skipped"):
        return None
    return _skip_payload_from_meta(
        plan=plan,
        goal_state_root=goal_state_root,
        agent_id=agent_id,
        goal_id=goal_id,
        turn_key=turn_key,
        target=target,
        quiet_meta=quiet_meta,
        host_projection=host_projection,
        empty_effects=empty_effects,
        payload_builder=payload_builder,
    )


def resolve_quiet_wait_or_replay_before_admission(
    *,
    plan: Mapping[str, Any],
    request: dict[str, Any] | None,
    journal_path: Path,
    goal_id: str,
    turn_key: str,
    settlement_lineage: Mapping[str, Any] | None,
    host_projection: Mapping[str, Any],
    empty_effects: Mapping[str, bool],
    needs_host: bool,
    goal_admission: Any,
    options: Mapping[str, Any],
    payload_builder: Callable[..., dict[str, Any]],
    request_builder: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Enforce terminal Quiet-Turn skip replay or resolve deferred quiet-noop wait before admission."""
    replayed_payload = recover_quiet_turn_skip_receipt(
        plan=plan,
        journal_path=journal_path,
        goal_id=goal_id,
        turn_key=turn_key,
        settlement_lineage=settlement_lineage,
        host_projection=host_projection,
        empty_effects=empty_effects,
        payload_builder=payload_builder,
    )
    if replayed_payload is not None:
        return replayed_payload, (request if request is not None else {})
    if request is not None:
        return None, request
    if needs_host and goal_admission is not None:
        goal_admission.require_current()
    evaluated = _evaluate_quiet_skip_candidate(
        plan=plan,
        journal_path=journal_path,
        goal_id=goal_id,
        settlement_lineage=settlement_lineage,
        options=options,
    )
    if evaluated is not None:
        goal_state_root, agent_id, target, quiet_meta, skip_host_cfg = evaluated
        if quiet_meta.get("state_conflict") or quiet_meta.get("state_corrupt"):
            raise ValueError(f"Quiet-Turn state store error: {quiet_meta.get('reason')}")
        if quiet_meta.get("skipped"):
            return (
                _skip_payload_from_meta(
                    plan=plan,
                    goal_state_root=goal_state_root,
                    agent_id=agent_id,
                    goal_id=goal_id,
                    turn_key=turn_key,
                    target=target,
                    quiet_meta=quiet_meta,
                    host_projection=host_projection,
                    empty_effects=empty_effects,
                    payload_builder=payload_builder,
                ),
                {},
            )
        if (
            quiet_meta.get("quiet_noop_allowed") is True
            and skip_host_cfg.get("record_wait_baseline_when_changed") is True
            and skip_host_cfg.get("state_changed") is None
            and target.state_digest
        ):
            with exclusive_file_lock(target.file_path):
                commit_quiet_turn_state(
                    scope_key=target.scope_key,
                    file_path=target.file_path,
                    sqlite_path=target.sqlite_path,
                    state_digest=target.state_digest,
                    metadata=quiet_meta,
                )
            return (
                {
                    **payload_builder(
                        plan,
                        {"status": "stopped", "host": host_projection, "reason": "quiet_turn_state_changed"},
                        execute=True,
                        replayed=False,
                        effects=empty_effects,
                    ),
                    "quiet_turn": dict(quiet_meta),
                },
                {},
            )
    return None, request_builder(plan)


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
    effective_quiet = _resolve_effective_quiet(
        options.get("quiet_turn", False),
        options.get("decision_capture_payload"),
    )
    agent_id = effective_turn_agent_id(plan, settlement_lineage)
    goal_state_root = journal_path.parent.parent
    target = quiet_turn_target_params(
        plan=plan,
        goal_id=goal_id,
        agent_id=agent_id,
        goal_state_root=goal_state_root,
        effective_quiet=effective_quiet,
    )
    commit_quiet_turn_state(
        scope_key=target.scope_key,
        file_path=target.file_path,
        sqlite_path=target.sqlite_path,
        state_digest=target.state_digest,
        metadata=quiet_meta,
    )


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
    work_contract, turn_envelope, must_attempt, quiet_cap = _hook_quiet_contract_inputs(
        event=event,
        state=state,
        qt_cfg=qt_cfg,
    )
    quiet_meta = resolve_quiet_turn_metadata(
        scope_key=f"codex_context:{goal_label}:{agent_label}",
        file_path=root_dir / "quiet_turn_state.json",
        sqlite_path=root_dir / "epoch_prefetch.sqlite",
        enabled=True,
        quiet_noop_allowed=quiet_cap,
        must_attempt=must_attempt,
        state_changed=qt_cfg.get("state_changed"),
        current_state_digest=(
            str(qt_cfg["current_state_digest"])
            if isinstance(qt_cfg.get("current_state_digest"), str)
            else str((recovery or {}).get("sha256") or "") or None
        ),
        work_contract=work_contract,
        turn_envelope=turn_envelope,
    )
    if quiet_meta.get("skipped"):
        return {"hookSpecificOutput": {"hookEventName": hook_name, "quietTurn": quiet_meta}}
    return None
