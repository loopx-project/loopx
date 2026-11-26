"""Runtime Turn lifecycle wiring for opt-in Todo semantic-unit anchor, settlement fold, and prompt swap."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from ...file_lock import exclusive_file_lock
from ..quota.turn_envelope import record_semantic_unit_anchor
from .driver import selected_turn_todo
from .epoch_prefetch import (
    DEFAULT_TOKENIZER_ENCODING,
    _HOST_WIRE_EXCLUDED_PREFETCH_FIELDS,
    HostInputRenderer,
    UNSET_HOST_INPUT_RENDERER,
    _adjudicate_candidate_prompt_gate,
    _apply_prompt_gate_audit_fields,
    _resolve_active_host_renderer,
    _safe_rendered_prompt,
)
from .quiet_turn_store import init_turn_prefetch_sqlite_db
from .semantic_unit_hook import _evaluate_semantic_unit_fold


SEMANTIC_UNIT_STORE_SCHEMA = "loopx_semantic_unit_settlement_store_v0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _as_dict(value: object) -> dict[str, object]:
    return {str(k): v for k, v in value.items()} if isinstance(value, Mapping) else {}


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def extract_semantic_unit_fold_option(
    options: Mapping[str, object] | None,
    decision_capture_payload: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    """Return active opt-in semantic-unit fold configuration, or None when disabled."""
    candidates: list[object] = []
    if isinstance(options, Mapping):
        candidates.append(options.get("semantic_unit_fold"))
        ep = options.get("epoch_prefetch")
        if isinstance(ep, Mapping):
            candidates.append(ep.get("semantic_unit_fold"))
        dc = options.get("decision_capture_payload")
        if isinstance(dc, Mapping):
            candidates.append(dc.get("semantic_unit_fold"))
            dc_ep = dc.get("epoch_prefetch")
            if isinstance(dc_ep, Mapping):
                candidates.append(dc_ep.get("semantic_unit_fold"))
    if isinstance(decision_capture_payload, Mapping):
        candidates.append(decision_capture_payload.get("semantic_unit_fold"))
        dc_ep = decision_capture_payload.get("epoch_prefetch")
        if isinstance(dc_ep, Mapping):
            candidates.append(dc_ep.get("semantic_unit_fold"))
    for raw in candidates:
        if isinstance(raw, Mapping) and raw.get("enabled") is True:
            return _as_dict(raw)
    return None


def _resolve_todo_and_epoch(
    *,
    plan: Mapping[str, object],
    request: Mapping[str, object],
    settlement_lineage: Mapping[str, object] | None,
    goal_id: str,
    turn_key: str,
    fold_cfg: Mapping[str, object],
) -> tuple[str, str, str]:
    envelope = _as_dict(plan.get("turn_envelope") or request.get("turn_envelope"))
    selected = selected_turn_todo(envelope) if envelope else {}
    todo_id = str(
        fold_cfg.get("todo_id")
        or (settlement_lineage or {}).get("todo_id")
        or selected.get("todo_id")
        or ""
    ).strip()
    turn_instance_id = str(
        fold_cfg.get("turn_instance_id")
        or (settlement_lineage or {}).get("turn_instance_id")
        or turn_key
    ).strip()
    session = _as_dict(plan.get("session"))
    context_epoch = str(
        fold_cfg.get("context_epoch")
        or session.get("context_epoch")
        or f"goal:{goal_id}"
    ).strip()
    return todo_id, turn_instance_id, context_epoch


def compute_turn_parent_prefix_digest(
    request: Mapping[str, object],
    *,
    goal_id: str,
    agent_id: str,
    context_epoch: str,
    fold_cfg: Mapping[str, object] | None = None,
    ignore_exploratory_todo_id: str | None = None,
) -> str:
    """Compute a deterministic digest of the clean parent KV prefix Q before a Todo suffix."""
    if isinstance(fold_cfg, Mapping) and isinstance(fold_cfg.get("prefix_digest"), str):
        candidate = str(fold_cfg["prefix_digest"]).strip()
        if len(candidate) == 64:
            return candidate
    parent_prefix = (
        fold_cfg.get("parent_prefix")
        if isinstance(fold_cfg, Mapping) and "parent_prefix" in fold_cfg
        else request.get("parent_prefix")
    )
    retained_items: list[object] = []
    raw_exp = request.get("exploratory_context")
    if isinstance(raw_exp, list):
        for item in raw_exp:
            if isinstance(item, Mapping):
                item_todo = str(item.get("todo_id") or "").strip()
                if ignore_exploratory_todo_id and item_todo == ignore_exploratory_todo_id:
                    break
            retained_items.append(item)
    return _sha256_text(
        _stable_json(
            {
                "goal_id": goal_id,
                "agent_id": agent_id,
                "context_epoch": context_epoch,
                "parent_prefix": parent_prefix,
                "retained_items": retained_items,
            }
        )
    )


def _store_paths(goal_state_root: Path, scope_key: str) -> tuple[Path, Path]:
    store_dir = goal_state_root / "semantic_unit_fold"
    file_path = store_dir / f"{_sha256_text(scope_key)}.json"
    sqlite_path = goal_state_root / "turn_prefetch_state.sqlite"
    return file_path, sqlite_path


def _ensure_sqlite_table(connection: sqlite3.Connection) -> None:
    init_turn_prefetch_sqlite_db(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_unit_fold_records (
            scope_key TEXT PRIMARY KEY,
            state_json TEXT NOT NULL,
            state_sha256 TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _read_store_state(file_path: Path, sqlite_path: Path, scope_key: str) -> tuple[dict[str, object], bool]:
    """Read File & SQLite semantic-unit state; fail closed on conflicting non-equal digests."""
    file_state: dict[str, object] | None = None
    if file_path.exists():
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(raw, Mapping):
            file_state = _as_dict(raw)
    sqlite_state: dict[str, object] | None = None
    if sqlite_path.exists():
        with closing(sqlite3.connect(sqlite_path, timeout=5.0)) as conn:
            _ensure_sqlite_table(conn)
            row = conn.execute(
                "SELECT state_json, state_sha256 FROM semantic_unit_fold_records WHERE scope_key = ?",
                (scope_key,),
            ).fetchone()
        if row is not None:
            parsed = json.loads(row[0])
            if isinstance(parsed, Mapping) and _sha256_text(_stable_json(parsed)) == row[1]:
                sqlite_state = _as_dict(parsed)
            else:
                return {}, True
    if file_state is not None and sqlite_state is not None:
        if _sha256_text(_stable_json(file_state)) != _sha256_text(_stable_json(sqlite_state)):
            return {}, True
        return file_state, False
    if file_state is not None:
        _write_sqlite_state(sqlite_path, scope_key, file_state)
        return file_state, False
    if sqlite_state is not None:
        _write_file_state(file_path, sqlite_state)
        return sqlite_state, False
    return {
        "schema_version": SEMANTIC_UNIT_STORE_SCHEMA,
        "scope_key": scope_key,
        "anchors": {},
        "folds": {},
        "latest_committed_fold": None,
    }, False


def _write_file_state(file_path: Path, state: Mapping[str, object]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_name(f".{file_path.name}.tmp.{os.getpid()}")
    tmp_path.write_text(json.dumps(dict(state), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, file_path)


def _write_sqlite_state(sqlite_path: Path, scope_key: str, state: Mapping[str, object]) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _stable_json(dict(state))
    digest = _sha256_text(encoded)
    with closing(sqlite3.connect(sqlite_path, timeout=5.0, isolation_level=None)) as conn:
        _ensure_sqlite_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                INSERT INTO semantic_unit_fold_records (scope_key, state_json, state_sha256, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    state_json = excluded.state_json,
                    state_sha256 = excluded.state_sha256,
                    updated_at = excluded.updated_at
                """,
                (scope_key, encoded, digest, _now_iso()),
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


def _save_store_state(
    file_path: Path, sqlite_path: Path, scope_key: str, state: Mapping[str, object],
) -> None:
    _write_file_state(file_path, state)
    _write_sqlite_state(sqlite_path, scope_key, state)


def _sync_codex_context_if_configured(
    *,
    plan: Mapping[str, object],
    fold_cfg: Mapping[str, object],
    event_fields: Mapping[str, object],
) -> None:
    codex_dir = fold_cfg.get("codex_context_dir") or plan.get("codex_context_dir")
    session_id = fold_cfg.get("session_id") or _as_dict(plan.get("session")).get("session_id")
    cwd = fold_cfg.get("cwd") or _as_dict(plan.get("session")).get("cwd")
    if not (isinstance(codex_dir, str) and codex_dir and isinstance(session_id, str) and session_id):
        return
    if not (isinstance(cwd, str) and cwd):
        return
    from .codex_context import codex_context_hook

    codex_context_hook(
        codex_dir,
        {
            "session_id": session_id,
            "cwd": cwd,
            "hook_event_name": "PostToolUse",
            **dict(event_fields),
        },
    )


_HOST_WIRE_EXCLUDED_FOLD_TEXT_FIELDS = frozenset({
    "replacement_text",
    "receipt_mu",
    "obs_next_cmd",
    "preserved_failure_facts",
    "preserved_open_obligations",
    "error_observation",
})


def strip_internal_semantic_unit_fold_audit_fields(
    fold_receipt: Mapping[str, object],
) -> dict[str, object]:
    """Keep host model-input semantic_unit_fold free of internal persistence audit blobs."""
    excluded = {
        "persistence",
        "anchor_recorded",
        "covered_exploratory_digests",
        *_HOST_WIRE_EXCLUDED_PREFETCH_FIELDS,
    }
    return {
        str(k): v
        for k, v in fold_receipt.items()
        if str(k) not in excluded
    }


def compact_semantic_unit_fold_wire_fields(
    fold_receipt: Mapping[str, object],
) -> dict[str, object]:
    """Strip internal audit telemetry and large text blobs before JSON wire rendering."""
    return {
        key: value
        for key, value in strip_internal_semantic_unit_fold_audit_fields(fold_receipt).items()
        if key not in _HOST_WIRE_EXCLUDED_FOLD_TEXT_FIELDS
    }


def _is_bound_exploratory_item(item: object, folded_todo_id: str) -> bool:
    return (
        bool(folded_todo_id)
        and isinstance(item, Mapping)
        and item.get("replaced_by_semantic_unit_fold") is not True
        and str(item.get("todo_id") or "").strip() == folded_todo_id
    )


def _partition_exploratory_context_for_fold(
    request: Mapping[str, object],
    folded_todo_id: str,
) -> tuple[bool, object, str]:
    """Return `(has_folded_inline, replaced_exploratory_context, baseline_inline_text)`."""
    if "exploratory_context" not in request:
        return False, None, ""
    raw_exp = request["exploratory_context"]
    if not folded_todo_id:
        return False, raw_exp, ""
    if isinstance(raw_exp, list):
        folded_items = [item for item in raw_exp if _is_bound_exploratory_item(item, folded_todo_id)]
        if not folded_items:
            return False, raw_exp, ""
        retained_items = [item for item in raw_exp if not _is_bound_exploratory_item(item, folded_todo_id)]
        return True, retained_items, _stable_json(folded_items)
    if isinstance(raw_exp, Mapping):
        if _is_bound_exploratory_item(raw_exp, folded_todo_id):
            return True, None, _stable_json(raw_exp)
        return False, raw_exp, ""
    return False, raw_exp, ""


def _covered_exploratory_digests(
    *,
    recorded_baseline: object,
    request: Mapping[str, object],
    todo_id: str,
) -> list[str]:
    """Return ordered SHA-256 digests for Turn 1 exploratory items bound to todo_id."""
    source_json = recorded_baseline if isinstance(recorded_baseline, str) and recorded_baseline else ""
    if not source_json:
        has_inline, _, inline_text = _partition_exploratory_context_for_fold(request, todo_id)
        if not has_inline or not inline_text:
            return []
        source_json = inline_text
    try:
        parsed = json.loads(source_json)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [_sha256_text(_stable_json(item)) for item in parsed]
    if isinstance(parsed, Mapping):
        return [_sha256_text(_stable_json(parsed))]
    return []


def _partition_for_covered_fold_span(
    request: Mapping[str, object],
    folded_todo_id: str,
    covered_digests_raw: object,
) -> tuple[bool, object, str, bool]:
    """Replace only the exact ordered span covered by Turn 1's receipt; flag unclear/late mismatches."""
    if "exploratory_context" not in request:
        return False, None, "", False
    raw_exp = request["exploratory_context"]
    if not folded_todo_id:
        return False, raw_exp, "", False
    covered_digests: list[str] | None = (
        [
            digest
            for digest in covered_digests_raw
            if isinstance(digest, str) and len(digest) == 64
        ]
        if isinstance(covered_digests_raw, list)
        else None
    )
    if (
        covered_digests is not None
        and isinstance(covered_digests_raw, list)
        and len(covered_digests) != len(covered_digests_raw)
    ):
        covered_digests = None
    if isinstance(raw_exp, list):
        bound_positions = [
            idx for idx, item in enumerate(raw_exp) if _is_bound_exploratory_item(item, folded_todo_id)
        ]
        if not bound_positions:
            return False, raw_exp, "", False
        if not covered_digests:
            return True, raw_exp, "", True
        span_len = len(covered_digests)
        start = bound_positions[0]
        end = start + span_len
        if end > len(raw_exp) or any(
            not _is_bound_exploratory_item(raw_exp[i], folded_todo_id) for i in range(start, end)
        ):
            return True, raw_exp, "", True
        span_items = raw_exp[start:end]
        if [_sha256_text(_stable_json(item)) for item in span_items] != covered_digests:
            return True, raw_exp, "", True
        retained_items = [*raw_exp[:start], *raw_exp[end:]]
        return True, retained_items, _stable_json(span_items), False
    if isinstance(raw_exp, Mapping):
        if not _is_bound_exploratory_item(raw_exp, folded_todo_id):
            return False, raw_exp, "", False
        if (
            covered_digests is not None
            and len(covered_digests) == 1
            and _sha256_text(_stable_json(raw_exp)) == covered_digests[0]
        ):
            return True, None, _stable_json(raw_exp), False
        return True, raw_exp, "", True
    return False, raw_exp, "", False


def _render_candidate_prompt(
    request: Mapping[str, object],
    *,
    renderer: HostInputRenderer | None,
) -> str | None:
    return _safe_rendered_prompt(request, renderer=renderer)


def _semantic_unit_prompt_gate_policy(
    fold_cfg: Mapping[str, object],
    latest_fold: Mapping[str, object],
    *,
    encoding_name: str,
    replace_inline: bool,
) -> dict[str, object]:
    """Build Stage-then-Swap prompt gate policy using current turn overrides and settled floor."""
    policy: dict[str, object] = {
        "enabled": True,
        "replace_exploratory_context": replace_inline,
        "tokenizer_encoding": encoding_name,
    }
    for key in ("min_net_savings_bytes", "min_net_savings_tokens", "cold_prefill_amortization_tokens"):
        if key in fold_cfg and fold_cfg.get(key) is not None:
            policy[key] = fold_cfg[key]
        elif key == "min_net_savings_tokens" and latest_fold.get(key) is not None:
            policy[key] = latest_fold[key]
    return policy


def _apply_fold_to_next_request(
    *,
    request: Mapping[str, object],
    latest_fold: Mapping[str, object],
    fold_cfg: Mapping[str, object],
    goal_id: str,
    agent_id: str,
    context_epoch: str,
    conflict_reason: str | None,
    renderer: HostInputRenderer | None,
    token_counter: Callable[[str], int] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Project verified FoldToReceipt into Turn 2's request or fail closed to KeepRaw."""
    folded_todo = str(latest_fold.get("todo_id") or "")
    has_inline, retained_exp, inline_text, scope_mismatch = _partition_for_covered_fold_span(
        request,
        folded_todo,
        latest_fold.get("covered_exploratory_digests"),
    )
    failure_facts = _as_str_list(latest_fold.get("preserved_failure_facts"))
    open_obligations = _as_str_list(latest_fold.get("preserved_open_obligations"))

    def keep_raw(
        reason: str,
        *,
        gate_audit: Mapping[str, object] | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        receipt_mu = _as_dict(latest_fold.get("receipt_mu"))
        keep_payload: dict[str, object] = {
            "schema_version": str(latest_fold.get("schema_version") or "loopx_semantic_unit_fold_swap_v0"),
            "operator": "KeepRaw",
            "status": "fallback_keep_raw",
            "fallback_reason": reason,
            "preserves_raw_state": True,
            "preserves_prefix_kv": True,
            "todo_id": folded_todo,
            "turn_instance_id": str(latest_fold.get("turn_instance_id") or ""),
            "context_epoch": context_epoch,
            "prefix_digest": latest_fold.get("prefix_digest"),
            "preserved_failure_facts": failure_facts or _as_str_list(receipt_mu.get("failure_facts")),
            "preserved_open_obligations": open_obligations or _as_str_list(receipt_mu.get("open_obligations")),
            "error_observation": (
                str(gate_audit["error_observation"])
                if isinstance(gate_audit, Mapping) and isinstance(gate_audit.get("error_observation"), str) and gate_audit.get("error_observation")
                else f"Todo semantic-unit fold kept raw context ({reason})."
            ),
        }
        if isinstance(gate_audit, Mapping):
            _apply_prompt_gate_audit_fields(keep_payload, gate_audit, include_delivered_metrics=False)
        updated = dict(request)
        updated["semantic_unit_fold"] = keep_payload
        return keep_payload, updated

    if conflict_reason is not None:
        return keep_raw(conflict_reason)
    if latest_fold.get("operator") != "FoldToReceipt":
        return keep_raw(str(latest_fold.get("fallback_reason") or "fold_not_committed"))
    if latest_fold.get("context_epoch") != context_epoch:
        return keep_raw("context_epoch_mismatch")
    live_prefix = compute_turn_parent_prefix_digest(
        request,
        goal_id=goal_id,
        agent_id=agent_id,
        context_epoch=context_epoch,
        fold_cfg=fold_cfg,
        ignore_exploratory_todo_id=folded_todo,
    )
    if live_prefix != latest_fold.get("prefix_digest"):
        return keep_raw("prefix_digest_mismatch")
    if scope_mismatch:
        return keep_raw("source_digest_mismatch")

    replace_inline = fold_cfg.get("replace_exploratory_context", True) is not False
    if has_inline and not replace_inline:
        return keep_raw("negative_net_savings")

    receipt_mu = _as_dict(latest_fold.get("receipt_mu"))
    fold_marker = {
        "replaced_by_semantic_unit_fold": True,
        "todo_id": folded_todo,
        "completion_receipt_id": receipt_mu.get("completion_receipt_id"),
        "source_ref": receipt_mu.get("source_ref"),
    }
    candidate = dict(request)
    if has_inline:
        if isinstance(retained_exp, list):
            candidate["exploratory_context"] = [*retained_exp, fold_marker]
        else:
            candidate["exploratory_context"] = fold_marker
    projected_fold: dict[str, object] = {
        "schema_version": latest_fold.get("schema_version"),
        "operator": "FoldToReceipt",
        "status": "committed",
        "trust_level": "tool",
        "preserves_raw_state": False,
        "preserves_prefix_kv": True,
        "todo_id": folded_todo,
        "turn_instance_id": latest_fold.get("turn_instance_id"),
        "context_epoch": context_epoch,
        "prefix_digest": live_prefix,
        "reclaim_range": latest_fold.get("reclaim_range"),
        "receipt_mu": receipt_mu,
        "obs_next_cmd": latest_fold.get("obs_next_cmd"),
        "replacement_text": latest_fold.get("replacement_text"),
        "preserved_failure_facts": failure_facts,
        "preserved_open_obligations": open_obligations,
        "receipt_sha256": latest_fold.get("receipt_sha256"),
        "replayed": True,
    }
    candidate["semantic_unit_fold"] = projected_fold

    encoding_name = str(fold_cfg.get("tokenizer_encoding") or DEFAULT_TOKENIZER_ENCODING)
    raw_prompt = _render_candidate_prompt(request, renderer=renderer)
    folded_prompt = _render_candidate_prompt(candidate, renderer=renderer)
    if has_inline:
        gate_policy = _semantic_unit_prompt_gate_policy(
            fold_cfg,
            latest_fold,
            encoding_name=encoding_name,
            replace_inline=replace_inline,
        )
        adjudicated: dict[str, object] | None = None
        stabilized = False
        for _ in range(3):
            adjudicated_raw, _, _ = _adjudicate_candidate_prompt_gate(
                policy_cfg=gate_policy,
                encoding_name=encoding_name,
                token_counter=token_counter,
                raw_prompt=raw_prompt,
                epoch_prompt=folded_prompt,
                has_inline_exploratory=True,
                replace_exploratory_context=replace_inline,
                ext_bytes=None,
                ext_tokens=None,
                potential_saved=0,
                host_input_measurable=raw_prompt is not None and folded_prompt is not None,
            )
            adjudicated = _as_dict(adjudicated_raw)
            if adjudicated.get("operator") != "EpochReset":
                return keep_raw(
                    str(adjudicated.get("fallback_reason") or "negative_net_savings"),
                    gate_audit=adjudicated,
                )
            _apply_prompt_gate_audit_fields(projected_fold, adjudicated)
            final_prompt = _render_candidate_prompt(candidate, renderer=renderer)
            if final_prompt is None:
                folded_prompt = None
                continue
            if final_prompt == folded_prompt:
                stabilized = True
                break
            folded_prompt = final_prompt
        if not stabilized:
            return keep_raw("host_input_unmeasurable", gate_audit=adjudicated)
    elif inline_text:
        projected_fold["net_savings_tokens"] = latest_fold.get("net_savings_tokens")
    return projected_fold, candidate


def prepare_turn_semantic_unit_context(
    *,
    plan: Mapping[str, object],
    request: Mapping[str, object],
    journal_path: Path,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    settlement_lineage: Mapping[str, object] | None,
    options: Mapping[str, object],
    existing_journal: Mapping[str, object] | None,
    needs_host: bool,
) -> tuple[dict[str, object] | None, dict[str, object]]:
    """Opt-in Turn-launch seam: apply prior committed Todo fold cleanly & anchor current Todo."""
    fold_cfg = extract_semantic_unit_fold_option(
        options,
        _as_dict(options.get("decision_capture_payload")) or None,
    )
    if fold_cfg is None:
        return None, dict(request)

    todo_id, turn_instance_id, context_epoch = _resolve_todo_and_epoch(
        plan=plan,
        request=request,
        settlement_lineage=settlement_lineage,
        goal_id=goal_id,
        turn_key=turn_key,
        fold_cfg=fold_cfg,
    )
    scope_key = f"turn_driver:{goal_id}:{agent_id}:{context_epoch}"
    goal_state_root = journal_path.parent.parent
    file_path, sqlite_path = _store_paths(goal_state_root, scope_key)

    conflict_reason: str | None = None
    if isinstance(existing_journal, Mapping) and existing_journal.get("status") in {"failed", "scheduler_action_required"}:
        conflict_reason = "recovery_conflict"
    elif isinstance(plan.get("unsettled_host_turn_recovery"), Mapping):
        conflict_reason = "recovery_conflict"
    elif fold_cfg.get("quiescent_boundary") is False or fold_cfg.get("tool_in_flight") or fold_cfg.get("unpaired_tool_calls"):
        conflict_reason = "non_quiescent_boundary"

    host = str(options.get("host") or _as_dict(plan.get("host")).get("kind") or "")
    host_renderer_opt = options.get("host_input_renderer", UNSET_HOST_INPUT_RENDERER)
    renderer = _resolve_active_host_renderer(
        host=host,
        host_input_renderer=host_renderer_opt,
    )
    updated_request = dict(request)
    if "parent_prefix" not in updated_request:
        if "parent_prefix" in fold_cfg:
            updated_request["parent_prefix"] = fold_cfg["parent_prefix"]
        elif "parent_prefix" in plan:
            updated_request["parent_prefix"] = plan["parent_prefix"]
    if "exploratory_context" not in updated_request:
        if "exploratory_context" in fold_cfg:
            updated_request["exploratory_context"] = fold_cfg["exploratory_context"]
        elif "exploratory_context" in plan:
            updated_request["exploratory_context"] = plan["exploratory_context"]
    metadata: dict[str, object] = {}

    with exclusive_file_lock(file_path, operation="semantic_unit_prepare"):
        state, store_conflict = _read_store_state(file_path, sqlite_path, scope_key)
        if store_conflict and conflict_reason is None:
            conflict_reason = "store_digest_conflict"
        existing_fold = (
            _as_dict(existing_journal.get("semantic_unit_fold"))
            if isinstance(existing_journal, Mapping) and isinstance(existing_journal.get("semantic_unit_fold"), Mapping)
            else None
        )
        latest_fold = existing_fold or (
            _as_dict(state.get("latest_committed_fold"))
            if isinstance(state.get("latest_committed_fold"), Mapping)
            else None
        )
        if latest_fold is not None and (
            not todo_id or str(latest_fold.get("todo_id") or "") != todo_id
        ):
            projected_fold, updated_request = _apply_fold_to_next_request(
                request=updated_request,
                latest_fold=latest_fold,
                fold_cfg=fold_cfg,
                goal_id=goal_id,
                agent_id=agent_id,
                context_epoch=context_epoch,
                conflict_reason=conflict_reason,
                renderer=renderer,
            )
            metadata["semantic_unit_fold"] = projected_fold

        if needs_host and todo_id and conflict_reason is None:
            anchor_step_raw = fold_cfg.get("anchor_step_index")
            anchors_map = _as_dict(state.get("anchors"))
            baselines_map = _as_dict(state.get("anchor_baselines"))
            next_step_guess = (
                int(anchor_step_raw)
                if isinstance(anchor_step_raw, int) and not isinstance(anchor_step_raw, bool) and anchor_step_raw >= 0
                else len(anchors_map) + 1
            )
            anchor_digest = compute_turn_parent_prefix_digest(
                updated_request,
                goal_id=goal_id,
                agent_id=agent_id,
                context_epoch=context_epoch,
                fold_cfg=fold_cfg,
                ignore_exploratory_todo_id=todo_id,
            )
            anchor = record_semantic_unit_anchor(
                todo_id=todo_id,
                turn_instance_id=turn_instance_id,
                anchor_step_index=next_step_guess,
                prefix_digest=anchor_digest,
                context_epoch=context_epoch,
                goal_id=goal_id,
                agent_id=agent_id,
            )
            _, _, inline_exp_text = _partition_exploratory_context_for_fold(updated_request, todo_id)
            if inline_exp_text:
                baselines_map[todo_id] = inline_exp_text
                state["anchor_baselines"] = baselines_map
            anchors_map[todo_id] = anchor
            state["anchors"] = anchors_map
            _save_store_state(file_path, sqlite_path, scope_key, state)
            metadata["semantic_unit_anchor"] = anchor
            _sync_codex_context_if_configured(
                plan=plan,
                fold_cfg=fold_cfg,
                event_fields={"semantic_unit_anchor": anchor},
            )
    return metadata or None, updated_request


def _write_immutable_completion_source_file(
    goal_state_root: Path,
    *,
    turn_key: str,
    completion_record: Mapping[str, object],
) -> dict[str, str]:
    receipts_dir = goal_state_root / "semantic_unit_fold" / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipts_dir / f"{_sha256_text(turn_key)}.json"
    if not receipt_path.exists():
        tmp = receipt_path.with_name(f".{receipt_path.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(dict(completion_record), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, receipt_path)
    return {"path": str(receipt_path), "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest()}


def _build_settlement_fold_request(
    *,
    fold_cfg: Mapping[str, object],
    settled: Mapping[str, object],
    result: Mapping[str, object],
    journal: Mapping[str, object],
    goal_state_root: Path,
    goal_id: str,
    agent_id: str,
    todo_id: str,
    turn_instance_id: str,
    turn_key: str,
    context_epoch: str,
    anchor: Mapping[str, object] | None,
    recorded_baseline: object,
) -> tuple[dict[str, object], str, str]:
    writeback = _as_dict(journal.get("writeback"))
    completion = _as_dict(writeback.get("completion"))
    receipt_id = str(
        completion.get("event_id")
        or _as_dict(journal.get("settlement_result")).get("effect_id")
        or turn_key
    ).strip()
    completion_record = {
        "goal_id": goal_id,
        "agent_id": agent_id,
        "todo_id": todo_id,
        "turn_instance_id": turn_instance_id,
        "turn_key": turn_key,
        "completion_receipt_id": receipt_id,
        "summary": result.get("summary"),
        "work_evidence": result.get("work_evidence"),
        "validation_evidence": result.get("validation_evidence"),
    }
    explicit_source = fold_cfg.get("source_ref")
    source_ref = (
        {str(k): str(v) for k, v in explicit_source.items()}
        if isinstance(explicit_source, Mapping)
        and isinstance(explicit_source.get("path"), str)
        and isinstance(explicit_source.get("sha256"), str)
        else _write_immutable_completion_source_file(
            goal_state_root, turn_key=turn_key, completion_record=completion_record,
        )
    )
    failure_facts = _as_str_list(fold_cfg.get("failure_facts"))
    open_obligations = _as_str_list(fold_cfg.get("open_obligations"))
    completion_receipt: dict[str, object] | None = (
        None
        if fold_cfg.get("completion_receipt") is False
        else _as_dict(fold_cfg["completion_receipt"])
        if isinstance(fold_cfg.get("completion_receipt"), Mapping)
        else {
            "todo_id": todo_id,
            "completion_receipt_id": receipt_id,
            "exit_code": 0,
            "sha256": _sha256_text(_stable_json(completion_record)),
            "failure_facts": failure_facts,
            "open_obligations": open_obligations,
            "source_ref": source_ref,
        }
    )
    baseline_text = fold_cfg.get("baseline_text")
    if not isinstance(baseline_text, str):
        baseline_text = _stable_json(
            {
                "anchored_baseline": recorded_baseline if isinstance(recorded_baseline, str) else "",
                "summary": result.get("summary") or "",
                "work_evidence": result.get("work_evidence") or [],
                "validation_evidence": result.get("validation_evidence") or [],
                "exploratory_suffix": fold_cfg.get("exploratory_suffix") or "",
            }
        )
    anchor_step_raw = anchor.get("anchor_step_index") if anchor else None
    anchor_step = (
        anchor_step_raw
        if isinstance(anchor_step_raw, int) and not isinstance(anchor_step_raw, bool)
        else 0
    )
    now_step_raw = fold_cfg.get("now_step_index")
    now_step = (
        int(now_step_raw)
        if isinstance(now_step_raw, int) and not isinstance(now_step_raw, bool)
        else anchor_step + 1
    )
    prefix_digest = (
        str(anchor["prefix_digest"])
        if anchor and isinstance(anchor.get("prefix_digest"), str)
        else compute_turn_parent_prefix_digest(
            _as_dict(journal.get("request")),
            goal_id=goal_id,
            agent_id=agent_id,
            context_epoch=context_epoch,
            fold_cfg=fold_cfg,
            ignore_exploratory_todo_id=todo_id,
        )
    )
    fold_eval_req: dict[str, object] = {
        **dict(fold_cfg),
        "todo_id": todo_id,
        "turn_instance_id": turn_instance_id,
        "prefix_digest": prefix_digest,
        "now_step_index": now_step,
        "baseline_text": baseline_text,
        "completion_receipt": completion_receipt,
    }
    return fold_eval_req, receipt_id, f"{todo_id}:{turn_instance_id}:{receipt_id}:{context_epoch}"


def commit_turn_semantic_unit_fold_after_settlement(
    *,
    settled: Mapping[str, object],
    result: Mapping[str, object] | None,
    journal: dict[str, object] | None,
    plan: Mapping[str, object],
    journal_path: Path,
    goal_id: str,
    agent_id: str,
    turn_key: str,
    settlement_lineage: Mapping[str, object] | None,
    options: Mapping[str, object],
) -> dict[str, object] | None:
    """Commit Todo FoldToReceipt only after real Todo completion settlement succeeds cleanly."""
    fold_cfg = extract_semantic_unit_fold_option(
        options,
        _as_dict(options.get("decision_capture_payload")) or None,
    )
    if fold_cfg is None:
        return None
    if not (
        settled.get("ok") is True
        and settled.get("status") in {"committed", "scheduler_action_required"}
        and isinstance(result, Mapping)
        and result.get("result_kind") == "validated_completion"
        and isinstance(journal, dict)
        and isinstance(journal.get("writeback"), Mapping)
        and _as_dict(journal.get("writeback")).get("ok") is True
        and _as_dict(settled.get("effects")).get("quota_spent") is True
        and not journal.get("host_recovery")
        and not journal.get("host_failure")
    ):
        return None

    todo_id, turn_instance_id, context_epoch = _resolve_todo_and_epoch(
        plan=plan,
        request=_as_dict(journal.get("request")),
        settlement_lineage=settlement_lineage,
        goal_id=goal_id,
        turn_key=turn_key,
        fold_cfg=fold_cfg,
    )
    if not todo_id:
        return None

    scope_key = f"turn_driver:{goal_id}:{agent_id}:{context_epoch}"
    goal_state_root = journal_path.parent.parent
    file_path, sqlite_path = _store_paths(goal_state_root, scope_key)

    with exclusive_file_lock(file_path, operation="semantic_unit_settle"):
        state, store_conflict = _read_store_state(file_path, sqlite_path, scope_key)
        if store_conflict:
            return None
        anchors_map = _as_dict(state.get("anchors"))
        baselines_map = _as_dict(state.get("anchor_baselines"))
        anchor = _as_dict(anchors_map.get(todo_id)) if isinstance(anchors_map.get(todo_id), Mapping) else None
        recorded_baseline = baselines_map.get(todo_id)
        fold_eval_req, _, replay_key = _build_settlement_fold_request(
            fold_cfg=fold_cfg,
            settled=settled,
            result=result,
            journal=journal,
            goal_state_root=goal_state_root,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            turn_instance_id=turn_instance_id,
            turn_key=turn_key,
            context_epoch=context_epoch,
            anchor=anchor,
            recorded_baseline=recorded_baseline,
        )
        covered_digests = _covered_exploratory_digests(
            recorded_baseline=recorded_baseline,
            request=_as_dict(journal.get("request")),
            todo_id=todo_id,
        )
        folds_map = _as_dict(state.get("folds"))
        if isinstance(folds_map.get(replay_key), Mapping):
            outcome = {**_as_dict(folds_map[replay_key]), "replayed": True, "executed": False}
            return outcome

        quiescent = (
            fold_cfg.get("quiescent_boundary", True) is not False
            and not bool(fold_cfg.get("tool_in_flight"))
            and not bool(fold_cfg.get("unpaired_tool_calls"))
        )
        outcome = _evaluate_semantic_unit_fold(
            fold_req=fold_eval_req,
            anchor=anchor,
            todo_id=todo_id,
            turn_instance_id=turn_instance_id,
            context_epoch=context_epoch,
            quiescent=quiescent,
            cwd=fold_cfg.get("cwd") or _as_dict(plan.get("session")).get("cwd"),
            token_counter=None,
        )
        if outcome.get("operator") == "FoldToReceipt":
            outcome = {**outcome, "covered_exploratory_digests": covered_digests}
        state["latest_committed_fold"] = outcome
        if outcome.get("operator") == "FoldToReceipt":
            folds_map[replay_key] = outcome
            state["folds"] = folds_map
            anchors_map.pop(todo_id, None)
            baselines_map.pop(todo_id, None)
            state["anchors"] = anchors_map
            state["anchor_baselines"] = baselines_map
            _sync_codex_context_if_configured(
                plan=plan,
                fold_cfg=fold_cfg,
                event_fields={
                    "semantic_unit_fold": {
                        **fold_eval_req,
                        "anchor": anchor,
                        "obs_next_cmd": outcome.get("obs_next_cmd"),
                    }
                },
            )
        _save_store_state(file_path, sqlite_path, scope_key, state)
        return outcome
