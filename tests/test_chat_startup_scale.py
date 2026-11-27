"""Scale and equivalence regression tests for ChatSessionStore startup maintenance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest import mock

import loopx.chat_store as chat_store_mod
from loopx.chat.event_cache import peek_turn_status as _peek_turn_status
from loopx.chat_store import (
    CHAT_TURN_SCHEMA_VERSION,
    ChatSessionStore,
    _atomic_write_json,
)


def _old_iso(hours: float = 48.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def test_startup_maintenance_skips_full_reads_on_compacted_terminal_turns(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="loopx-manager",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-scale",
        channel_id="manager",
    )
    sid = session["session_id"]
    old_completed_at = _old_iso(48.0)
    large_blob = "x" * 16384

    compacted_ids: list[str] = []
    for idx in range(60):
        turn, _ = store.create_turn(sid, client_turn_id=f"c-{idx}", message=large_blob)
        tid = turn["turn_id"]
        compacted_ids.append(tid)
        store.append_event(sid, tid, kind="assistant.delta", payload={"text": "chunk"})
        store.append_event(sid, tid, kind="turn.completed", payload={"ok": True})
        store.release_active_turn(sid, tid, last_activity_at=old_completed_at, last_error_code=None)
        turn_path = store.sessions_root / sid / "turns" / f"{tid}.json"
        doc = json.loads(turn_path.read_text(encoding="utf-8"))
        doc["status"] = "completed"
        doc["completed_at"] = old_completed_at
        _atomic_write_json(turn_path, doc, preserve_mode=True)

    assert store.compact_completed_events(older_than_hours=24.0) == 60

    # Add 1) an uncompacted old turn, 2) a nested-dict lookalike tail,
    # 3) a standard completing turn, 4) a custom-key-order completing turn,
    # and 5) a unicode-escaped completing turn.
    uncompacted, _ = store.create_turn(sid, client_turn_id="uncomp", message=large_blob)
    uncomp_id = uncompacted["turn_id"]
    store.append_event(sid, uncomp_id, kind="assistant.delta", payload={"text": "delta"})
    store.append_event(sid, uncomp_id, kind="turn.completed", payload={"ok": True})
    store.release_active_turn(sid, uncomp_id, last_activity_at=old_completed_at, last_error_code=None)
    uncomp_path = store.sessions_root / sid / "turns" / f"{uncomp_id}.json"
    uncomp_doc = json.loads(uncomp_path.read_text(encoding="utf-8"))
    uncomp_doc["status"] = "completed"
    uncomp_doc["completed_at"] = old_completed_at
    _atomic_write_json(uncomp_path, uncomp_doc, preserve_mode=True)

    nested, _ = store.create_turn(sid, client_turn_id="nested", message=large_blob)
    nested_id = nested["turn_id"]
    store.append_event(sid, nested_id, kind="assistant.delta", payload={"text": "delta"})
    store.append_event(sid, nested_id, kind="turn.completed", payload={"ok": True})
    store.release_active_turn(sid, nested_id, last_activity_at=old_completed_at, last_error_code=None)
    nested_path = store.sessions_root / sid / "turns" / f"{nested_id}.json"
    nested_event_stat = nested_path.with_name(f"{nested_id}.events.jsonl").stat()
    nested_doc = json.loads(nested_path.read_text(encoding="utf-8"))
    nested_doc["status"] = "completed"
    nested_doc["completed_at"] = old_completed_at
    nested_doc.pop("event_compaction_revision", None)
    nested_doc["nested_lookalike"] = {
        "x": 1,
        "event_compaction_revision": [
            nested_event_stat.st_ino,
            nested_event_stat.st_size,
            nested_event_stat.st_mtime_ns,
        ],
    }
    _atomic_write_json(nested_path, nested_doc, preserve_mode=True)

    completing_std, _ = store.create_turn(sid, client_turn_id="comp-std", message="hi")
    comp_std_id = completing_std["turn_id"]
    store.release_active_turn(sid, comp_std_id, last_activity_at=old_completed_at, last_error_code=None)
    comp_std_path = store.sessions_root / sid / "turns" / f"{comp_std_id}.json"
    comp_std_doc = json.loads(comp_std_path.read_text(encoding="utf-8"))
    comp_std_doc["status"] = "completing"
    comp_std_doc["response"] = {"message": "recovered-standard"}
    _atomic_write_json(comp_std_path, comp_std_doc, preserve_mode=True)

    completing_reordered, _ = store.create_turn(sid, client_turn_id="comp-reord", message="hi")
    comp_reord_id = completing_reordered["turn_id"]
    store.release_active_turn(sid, comp_reord_id, last_activity_at=old_completed_at, last_error_code=None)
    comp_reord_path = store.sessions_root / sid / "turns" / f"{comp_reord_id}.json"
    reord_doc = {
        "attachments": [{"data": large_blob}],
        "schema_version": CHAT_TURN_SCHEMA_VERSION,
        "turn_id": comp_reord_id,
        "session_id": sid,
        "status": "completing",
        "response": {"message": "recovered-reordered"},
        "completed_at": old_completed_at,
    }
    _atomic_write_json(comp_reord_path, reord_doc, preserve_mode=True)
    assert _peek_turn_status(comp_reord_path) is None

    completing_escaped, _ = store.create_turn(sid, client_turn_id="comp-esc", message="hi")
    comp_esc_id = completing_escaped["turn_id"]
    store.release_active_turn(sid, comp_esc_id, last_activity_at=old_completed_at, last_error_code=None)
    comp_esc_path = store.sessions_root / sid / "turns" / f"{comp_esc_id}.json"
    esc_doc = json.loads(comp_esc_path.read_text(encoding="utf-8"))
    esc_doc["status"] = "completing"
    esc_doc["response"] = {"message": "recovered-escaped"}
    raw_esc = json.dumps(esc_doc, separators=(",", ":")).replace('"completing"', '"\\u0063ompleting"')
    comp_esc_path.write_text(raw_esc, encoding="utf-8")
    assert _peek_turn_status(comp_esc_path) == "completing"

    read_paths: list[str] = []
    original_read_json = chat_store_mod._read_json

    def tracking_read_json(path: Path) -> dict[str, object]:
        read_paths.append(path.stem)
        return original_read_json(path)

    with mock.patch.object(chat_store_mod, "_read_json", side_effect=tracking_read_json):
        reopened = ChatSessionStore(tmp_path)

    compacted_set = set(compacted_ids)
    assert compacted_set.isdisjoint(read_paths), f"expected 0 full JSON reads on compacted turns, got {compacted_set & set(read_paths)}"
    assert (reopened.load_turn(sid, comp_std_id) or {}).get("status") == "completed"
    assert (reopened.load_turn(sid, comp_reord_id) or {}).get("status") == "completed"
    assert (reopened.load_turn(sid, comp_esc_id) or {}).get("status") == "completed"
    messages_text = [m["text"] for m in reopened.messages(sid) if m["role"] == "agent"]
    assert {"recovered-standard", "recovered-reordered", "recovered-escaped"} <= set(messages_text)
    assert all(
        row["kind"] != "assistant.delta"
        for tid in (uncomp_id, nested_id)
        for row in reopened.events_after(sid, tid, None)
    )


def test_compact_on_init_false_recovers_completions_without_blocking_on_compaction(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="loopx-manager",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-defer",
        channel_id="manager",
    )
    sid = session["session_id"]
    turn, _ = store.create_turn(sid, client_turn_id="c-1", message="hello")
    tid = turn["turn_id"]
    old_completed_at = _old_iso(48.0)
    store.release_active_turn(sid, tid, last_activity_at=old_completed_at, last_error_code=None)
    turn_path = store.sessions_root / sid / "turns" / f"{tid}.json"
    doc = json.loads(turn_path.read_text(encoding="utf-8"))
    doc["status"] = "completing"
    doc["response"] = {"message": "done-fast"}
    _atomic_write_json(turn_path, doc, preserve_mode=True)

    with mock.patch.object(ChatSessionStore, "compact_completed_events", side_effect=AssertionError("must not run synchronously")):
        fast_store = ChatSessionStore(tmp_path, compact_on_init=False)

    assert (fast_store.load_turn(sid, tid) or {}).get("status") == "completed"
