from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import gc
import inspect
import json
import os
from pathlib import Path
import time

import pytest

import loopx.chat_store as chat_store
from loopx.chat_store import CHAT_TURN_SCHEMA_VERSION, ChatSessionStore


def _write_completed_turn(root: Path, *, with_events: bool = True) -> tuple[Path, Path]:
    turn_path = root / "chat" / "sessions" / "session" / "turns" / "turn.json"
    turn_path.parent.mkdir(parents=True, exist_ok=True)
    turn_path.write_text(
        json.dumps(
            {
                "schema_version": CHAT_TURN_SCHEMA_VERSION,
                "session_id": "session",
                "turn_id": "turn",
                "status": "completed",
                "completed_at": (
                    datetime.now(timezone.utc) - timedelta(days=2)
                ).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    event_path = turn_path.with_name("turn.events.jsonl")
    if with_events:
        rows = [
            {"kind": "answer.delta", "sequence": 1, "event_id": "1"},
            {"kind": "turn.completed", "sequence": 2, "event_id": "2"},
        ]
        event_path.write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )
    return turn_path, event_path


def _rewrite_preserving_mtime(path: Path, updated: bytes) -> None:
    before = path.stat()
    assert len(updated) == before.st_size
    path.write_bytes(updated)
    timestamps = (before.st_atime_ns, before.st_mtime_ns)
    os.utime(path, ns=timestamps)
    after = path.stat()
    if after.st_ctime_ns == before.st_ctime_ns:
        time.sleep(0.01)
        os.utime(path, ns=timestamps)
        after = path.stat()
    if after.st_ctime_ns == before.st_ctime_ns:
        pytest.skip("filesystem does not report a different change time for the rewrite")
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino, before.st_size, before.st_mtime_ns,
    )


def test_completed_replay_reuses_log_until_an_external_append(tmp_path: Path, monkeypatch) -> None:
    store = ChatSessionStore(tmp_path)
    key = ("session", "turn")
    store.append_event(*key, kind="assistant.delta", payload={"text": "visible"}, buffered=True)
    store.append_event(*key, kind="turn.completed", payload={})
    assert key not in store._event_cache._entries
    reads = []
    original_open = Path.open
    event_path = store._event_path(*key)

    def counted(path: Path, mode: str = "r", *args, **kwargs):
        if path == event_path and mode == "rb":
            reads.append(path)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted)
    for _ in range(5):
        assert [row["sequence"] for row in store.events_after(*key, "0")] == [1, 2]
    assert len(reads) == 1
    other = ChatSessionStore(tmp_path)
    other.append_event(*key, kind="turn.completed", payload={"recovered": True})
    assert [row["sequence"] for row in store.events_after(*key, "2")] == [3]
    assert len(reads) == 3  # independent writer plus invalidated reader


def test_completed_replay_refreshes_after_same_size_rewrite_with_preserved_mtime(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path, startup_maintenance=False)
    key = ("session", "turn")
    store.append_event(*key, kind="manager.context", payload={"note": "older"})
    store.append_event(*key, kind="turn.completed", payload={})
    assert store.events_after(*key, None)[0]["payload"]["note"] == "older"

    path = store._event_path(*key)
    _rewrite_preserving_mtime(path, path.read_bytes().replace(b'"older"', b'"newer"'))
    assert store.events_after(*key, None)[0]["payload"]["note"] == "newer"


@pytest.mark.parametrize("switch", ["during-replay", "after-append"])
def test_event_cache_revision_follows_the_opened_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, switch: str,
) -> None:
    store = ChatSessionStore(tmp_path, startup_maintenance=False)
    key = ("session", "view")
    for turn_id in ("first", "other"):
        store.append_event("session", turn_id, kind="manager.context", payload={"source": turn_id})
        store.append_event("session", turn_id, kind="turn.completed", payload={})
    first = store._event_path("session", "first")
    other = store._event_path("session", "other")
    journal = store._event_path(*key)

    def select(target: Path) -> None:
        replacement = journal.with_name("replacement")
        replacement.symlink_to(target.name)
        replacement.replace(journal)

    try:
        select(first)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"cannot create a local event journal symlink: {exc}")

    if switch == "during-replay":
        original_open = Path.open

        def open_other_then_restore(path: Path, mode: str = "r", *args, **kwargs):
            if path != journal or mode != "rb":
                return original_open(path, mode, *args, **kwargs)
            select(other)
            try:
                return original_open(path, mode, *args, **kwargs)
            finally:
                select(first)

        with monkeypatch.context() as boundary:
            boundary.setattr(Path, "open", open_other_then_restore)
            assert store.events_after(*key, None)[0]["payload"]["source"] == "other"
        assert store.events_after(*key, None)[0]["payload"]["source"] == "first"
    else:
        assert store.events_after(*key, None)[0]["payload"]["source"] == "first"
        original_append = chat_store._append_jsonl_rows

        def append_then_switch(path: Path, rows: list[dict], **kwargs):
            written = original_append(path, rows, **kwargs)
            if path == journal:
                select(other)
            return written

        with monkeypatch.context() as boundary:
            boundary.setattr(chat_store, "_append_jsonl_rows", append_then_switch)
            store.append_event(*key, kind="manager.context", payload={"source": "appended"})
        assert store.events_after(*key, None)[0]["payload"]["source"] == "other"
        assert len(store.events_after(*key, None)) == 2


def test_stable_event_appends_use_the_cached_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ChatSessionStore(tmp_path, startup_maintenance=False)
    key = ("session", "turn")
    store.append_event(*key, kind="manager.context", payload={"note": "start"})
    assert len(store.events_after(*key, None)) == 1
    journal = store._event_path(*key)
    original_open = Path.open

    def forbid_prefix_reload(path: Path, mode: str = "r", *args, **kwargs):
        if path == journal and mode == "rb":
            pytest.fail("a stable append should reuse the prefix")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbid_prefix_reload)
    for note in ("second", "third"):
        store.append_event(*key, kind="manager.context", payload={"note": note})
    assert [row["payload"]["note"] for row in store.events_after(*key, "1")] == ["second", "third"]


@pytest.mark.parametrize("exists_before_flush", [True, False], ids=["existing-prefix", "absent-prefix"])
def test_event_append_rechecks_its_opened_destination_before_repair_or_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exists_before_flush: bool,
) -> None:
    store = ChatSessionStore(tmp_path, startup_maintenance=False)
    key = ("session", "view")
    store.append_event("session", "first", kind="manager.context", payload={"source": "first"})
    store.append_event("session", "other", kind="manager.context", payload={"source": "other"})
    first = store._event_path("session", "first")
    other = store._event_path("session", "other")
    journal = store._event_path(*key)
    with other.open("ab") as stream:
        stream.write(b'{"kind":')
    original_first, original_other = first.read_bytes(), other.read_bytes()

    def select(target: Path) -> None:
        link = journal.with_name("replacement")
        link.symlink_to(target.name)
        link.replace(journal)

    try:
        select(first)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"cannot create a local event journal symlink: {exc}")
    if not exists_before_flush:
        journal.unlink()

    original_open = Path.open

    def switch_on_write_open(path: Path, mode: str = "r", *args, **kwargs):
        if path != journal or mode not in {"a", "r+b", "x+b"}:
            return original_open(path, mode, *args, **kwargs)
        select(other)
        try:
            return original_open(path, mode, *args, **kwargs)
        finally:
            if exists_before_flush:
                select(first)
            else:
                journal.unlink()

    pending = store.append_event(*key, kind="manager.context", payload={"source": "pending"}, buffered=True)
    with monkeypatch.context() as boundary:
        boundary.setattr(Path, "open", switch_on_write_open)
        with pytest.raises(OSError):
            store.flush_events(*key)
    assert first.read_bytes() == original_first
    assert other.read_bytes() == original_other
    assert journal.exists() == exists_before_flush

    assert store.flush_events(*key) == 1
    assert store.flush_events(*key) == 0
    rows = store.events_after(*key, None)
    assert [row["payload"]["source"] for row in rows] == (["first", "pending"] if exists_before_flush else ["pending"])
    assert pending["sequence"] == (2 if exists_before_flush else 1)


def test_event_flush_retry_after_durable_fsync_keeps_distinct_identical_events(
    tmp_path: Path, monkeypatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    key = ("session", "turn")
    monkeypatch.setattr(chat_store, "utc_now", lambda: "2026-10-05T00:00:00Z")
    first = store.append_event(
        *key, kind="assistant.delta", payload={"text": "same"}, buffered=True
    )
    second = store.append_event(
        *key, kind="assistant.delta", payload={"text": "same"}, buffered=True
    )
    actual_fsync = chat_store.os.fsync
    failed = False

    def uncertain_fsync(fd: int) -> None:
        nonlocal failed
        actual_fsync(fd)
        caller_name = inspect.currentframe().f_back.f_code.co_name
        if not failed and caller_name == "_append_jsonl_rows":
            failed = True
            raise OSError("fsync completed, but its result was lost")

    monkeypatch.setattr(chat_store.os, "fsync", uncertain_fsync)
    with pytest.raises(OSError, match="result was lost"):
        store.flush_events(*key)

    event_path = store._event_path(*key)
    assert [row["sequence"] for row in chat_store._read_jsonl(event_path)] == [1, 2]
    assert store.flush_events(*key) == 0
    replay = ChatSessionStore(tmp_path).events_after(*key, None)
    assert [row["sequence"] for row in replay] == [1, 2]
    assert [row["payload"] for row in replay] == [{"text": "same"}, {"text": "same"}]
    assert [first["sequence"], second["sequence"]] == [1, 2]
    assert store.events_after(*key, None) == replay
    assert all("_append_id" not in row for row in replay)
    _write_completed_turn(tmp_path, with_events=False)

    from http.client import HTTPConnection
    from threading import Thread
    from types import SimpleNamespace
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.chat_store = store
    # This retention test builds a bare server; the events entry point asks the
    # runtime owner to reconcile a dead worker, which is covered separately.
    server.runtime_controller = SimpleNamespace(
        recover_exited_turn=lambda *args: None, close=lambda: None,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request("GET", "/api/chat/sessions/session/turns/turn/events")
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert response.getheader("Content-Type") == "text/event-stream; charset=utf-8"
        assert [line for line in body.splitlines() if line.startswith("id: ")] == [
            "id: 1", "id: 2",
        ]
        data = [
            json.loads(line[6:])
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        assert [row["sequence"] for row in data] == [1, 2]
        assert all("_append_id" not in row for row in data)
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_failed_flush_does_not_resurrect_events_after_other_store_compacts(
    tmp_path: Path, monkeypatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    _write_completed_turn(tmp_path, with_events=False)
    key = ("session", "turn")
    delta = store.append_event(
        *key, kind="assistant.delta", payload={"text": "late"}, buffered=True
    )
    terminal = store.append_event(*key, kind="turn.completed", payload={}, buffered=True)
    append_rows = chat_store._append_jsonl_rows
    failed = False

    def durable_then_lose_result(path: Path, rows: list[dict], **kwargs):
        nonlocal failed
        written = append_rows(path, rows, **kwargs)
        if not failed:
            failed = True
            raise OSError("durable write result lost")
        return written

    monkeypatch.setattr(chat_store, "_append_jsonl_rows", durable_then_lose_result)
    with pytest.raises(OSError, match="result lost"):
        store.flush_events(*key)
    path = store._event_path(*key)
    assert [(row["sequence"], row["kind"]) for row in chat_store._read_jsonl(path)] == [
        (1, "assistant.delta"), (2, "turn.completed"),
    ]

    compacting_store = ChatSessionStore(tmp_path)
    assert [row["kind"] for row in compacting_store.events_after(*key, None)] == [
        "turn.completed",
    ]
    assert store.flush_events(*key) == 0
    assert [(row["sequence"], row["kind"]) for row in chat_store._read_jsonl(path)] == [
        (2, "turn.completed"),
    ]
    assert [delta["sequence"], terminal["sequence"]] == [1, 2]


def test_failed_flush_keeps_uncertain_events_if_locked_readback_fails(
    tmp_path: Path, monkeypatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    key = ("session", "turn")
    event = store.append_event(*key, kind="assistant.delta", payload={}, buffered=True)
    event_path = store._event_path(*key)
    append_rows = chat_store._append_jsonl_rows
    original_open = Path.open
    readback_failures = 0
    result_lost = False
    nonempty_appends = 0

    def lose_result(path: Path, rows: list[dict], **kwargs):
        nonlocal readback_failures, result_lost, nonempty_appends
        if rows:
            nonempty_appends += 1
        written = append_rows(path, rows, **kwargs)
        if not result_lost:
            result_lost = True
            readback_failures = 2
            raise OSError("append result lost")
        return written

    def fail_two_reads(path: Path, mode: str = "r", *args, **kwargs):
        nonlocal readback_failures
        if path == event_path and mode == "rb" and readback_failures:
            readback_failures -= 1
            raise PermissionError("readback temporarily unavailable")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(chat_store, "_append_jsonl_rows", lose_result)
    monkeypatch.setattr(Path, "open", fail_two_reads)
    with pytest.raises(OSError, match="append result lost"):
        store.flush_events(*key)
    persisted_size = event_path.stat().st_size
    with pytest.raises(PermissionError, match="readback temporarily unavailable"):
        store.flush_events(*key)
    assert event_path.stat().st_size == persisted_size
    assert nonempty_appends == 1
    assert store.flush_events(*key) == 1
    assert [row["sequence"] for row in chat_store._read_jsonl(event_path)] == [1]
    assert [row["sequence"] for row in store.events_after(*key, None)] == [1]
    assert event["sequence"] == 1


@pytest.mark.parametrize("durable", [True, False], ids=["compacted-append", "unwritten-append"])
def test_uncertain_append_retry_matches_compaction_receipts_by_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, durable: bool,
) -> None:
    store = ChatSessionStore(tmp_path, startup_maintenance=False)
    _, path = _write_completed_turn(tmp_path, with_events=False)
    key = ("session", "turn")
    pending = store.append_event(*key, kind="assistant.delta", payload={"text": "pending"}, buffered=True)
    original_append, original_open = chat_store._append_jsonl_rows, Path.open
    readback_fault = False

    def lose_result(target: Path, rows: list[dict], **kwargs):
        nonlocal readback_fault
        if durable:
            original_append(target, rows, **kwargs)
        readback_fault = True
        raise OSError("append result unavailable")

    def lose_readback(target: Path, mode: str = "r", *args, **kwargs):
        nonlocal readback_fault
        if target == path and mode == "rb" and readback_fault:
            readback_fault = False
            raise OSError("readback unavailable")
        return original_open(target, mode, *args, **kwargs)

    with monkeypatch.context() as boundary:
        boundary.setattr(chat_store, "_append_jsonl_rows", lose_result)
        boundary.setattr(Path, "open", lose_readback)
        with pytest.raises(OSError, match="append result unavailable"):
            store.flush_events(*key)
    assert not readback_fault
    assert pending["sequence"] == 1

    other = ChatSessionStore(tmp_path, startup_maintenance=False)
    if not durable:
        other.append_event(*key, kind="assistant.delta", payload={"text": "independent event"})
    other.append_event(*key, kind="turn.completed", payload={})
    assert other.compact_completed_events() == 1
    assert [row["sequence"] for row in other.events_after(*key, None)] == [2]

    assert store.flush_events(*key) == 1
    assert store.flush_events(*key) == 0
    replay = store.events_after(*key, None)
    assert [row["sequence"] for row in replay] == ([2] if durable else [2, 3])
    assert pending["sequence"] == (1 if durable else 3)


def test_compacted_journal_keeps_the_sequence_floor_when_no_rows_remain(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path, startup_maintenance=False)
    _write_completed_turn(tmp_path, with_events=False)
    key = ("session", "turn")
    store.append_event(*key, kind="assistant.delta", payload={"text": "first"})
    assert store.compact_completed_events() == 1
    assert store.events_after(*key, None) == []
    assert key in store._event_cache._finished

    store.append_event(*key, kind="assistant.delta", payload={"text": "second"})
    assert [row["sequence"] for row in store.events_after(*key, "1")] == [2]
    assert store.compact_completed_events() == 1
    assert store.events_after(*key, None) == []
    store.append_event(*key, kind="turn.completed", payload={})
    assert [row["sequence"] for row in store.events_after(*key, "2")] == [3]


def test_event_flush_retry_after_partial_batch_and_concurrent_writer(
    tmp_path: Path, monkeypatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    key = ("session", "turn")
    first = store.append_event(
        *key, kind="assistant.delta", payload={"text": "first"}, buffered=True
    )
    second = store.append_event(
        *key, kind="assistant.delta", payload={"text": "second"}, buffered=True
    )
    append_rows = chat_store._append_jsonl_rows
    failed = False

    def partially_durable(path: Path, rows: list[dict], **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            append_rows(path, rows[:1], **kwargs)
            raise OSError("batch interrupted after one durable row")
        return append_rows(path, rows, **kwargs)

    monkeypatch.setattr(chat_store, "_append_jsonl_rows", partially_durable)
    with pytest.raises(OSError, match="one durable row"):
        store.flush_events(*key)
    assert [
        row["sequence"] for row in chat_store._read_jsonl(store._event_path(*key))
    ] == [1]

    other = ChatSessionStore(tmp_path)
    other.append_event(*key, kind="assistant.delta", payload={"text": "other"})
    assert store.flush_events(*key) == 1
    replay = store.events_after(*key, None)
    assert [(row["sequence"], row["payload"]["text"]) for row in replay] == [
        (1, "first"), (2, "other"), (3, "second"),
    ]
    assert [first["sequence"], second["sequence"]] == [1, 3]
    assert [row["sequence"] for row in store.events_after(*key, "1")] == [2, 3]


def test_event_flush_retry_after_no_write_preserves_once_only_readback(
    tmp_path: Path, monkeypatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    key = ("session", "turn")
    event = store.append_event(*key, kind="turn.completed", payload={}, buffered=True)
    append_rows = chat_store._append_jsonl_rows
    failed = False

    def fail_before_write(path: Path, rows: list[dict], **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("write never started")
        return append_rows(path, rows, **kwargs)

    monkeypatch.setattr(chat_store, "_append_jsonl_rows", fail_before_write)
    with pytest.raises(OSError, match="write never started"):
        store.flush_events(*key)
    assert chat_store._read_jsonl(store._event_path(*key)) == []
    assert store.flush_events(*key) == 1
    assert [
        row["sequence"] for row in ChatSessionStore(tmp_path).events_after(*key, None)
    ] == [1]
    assert event["sequence"] == 1


def test_completed_replay_evicts_least_recently_used_log(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path)
    for i in range(8):
        store.append_event("session", str(i), kind="turn.completed", payload={})
        store.events_after("session", str(i), None)
    store.events_after("session", "0", None)
    store.append_event("session", "8", kind="turn.completed", payload={})
    store.events_after("session", "8", None)
    assert ("session", "0") in store._event_cache._entries
    assert ("session", "1") not in store._event_cache._entries
    assert len(store._event_cache._finished) == 8


@pytest.mark.parametrize("row_count,text_size", [(1, 2 * 1024 * 1024), (4096, 1)])
def test_oversized_completed_log_is_replayable_but_not_retained(
    tmp_path: Path, row_count: int, text_size: int,
) -> None:
    store = ChatSessionStore(tmp_path)
    key = ("session", "large")
    for _ in range(row_count):
        store.append_event(*key, kind="assistant.delta", payload={"text": "x" * text_size}, buffered=True)
    store.append_event(*key, kind="turn.completed", payload={})
    assert len(store.events_after(*key, None)) == row_count + 1
    assert key not in store._event_cache._entries
    assert key not in store._event_cache._finished


def test_completed_replay_budget_is_aggregate(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path)
    for turn in ("a", "b", "c"):
        store.append_event("session", turn, kind="assistant.delta", payload={"text": "x" * 800_000}, buffered=True)
        store.append_event("session", turn, kind="turn.completed", payload={})
        store.events_after("session", turn, None)
    assert ("session", "a") not in store._event_cache._entries
    assert set(store._event_cache._finished) == {("session", "b"), ("session", "c")}


def test_old_replay_cannot_retain_a_replaced_snapshot(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path)
    key = ("session", "turn")
    store.append_event(*key, kind="turn.completed", payload={})
    store.events_after(*key, None)
    old = store._event_cache._entries[key][1]
    store.append_event(*key, kind="assistant.delta", payload={"text": "continued"})
    store._event_cache.retain_terminal(key, old)
    assert key not in store._event_cache._finished
    assert store.events_after(*key, "1")[0]["kind"] == "assistant.delta"


def test_keyed_locks_are_released_after_callers_drop_them(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path)
    session_lock = store._session_lock("session")
    event_lock = store._event_flush_lock(("session", "turn"))

    assert store._session_lock("session") is session_lock
    assert store._event_flush_lock(("session", "turn")) is event_lock

    del session_lock, event_lock
    gc.collect()

    assert "session" not in store._session_locks
    assert ("session", "turn") not in store._event_flush_locks


def test_completed_event_compaction_is_skipped_until_the_file_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    turn_path, event_path = _write_completed_turn(tmp_path)
    first = ChatSessionStore(tmp_path)
    turn = json.loads(turn_path.read_text(encoding="utf-8"))

    assert turn["event_compaction_revision"] == list(
        first._event_revision(event_path) or ()
    )
    assert ("session", "turn") not in first._event_cache._entries

    original_open = Path.open

    def reject_redundant_event_read(path: Path, mode: str = "r", *args, **kwargs):
        if path == event_path and mode == "rb":
            raise AssertionError("an unchanged compacted event stream was read again")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_redundant_event_read)
    restarted = ChatSessionStore(tmp_path)

    assert not restarted._event_cache._entries
    assert not restarted._event_flush_locks


def test_completed_event_compaction_marker_is_invalidated_by_a_new_event(
    tmp_path: Path,
) -> None:
    turn_path, event_path = _write_completed_turn(tmp_path)
    ChatSessionStore(tmp_path)
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"kind": "answer.delta", "sequence": 3, "event_id": "3"})
        )
        handle.write("\n")

    restarted = ChatSessionStore(tmp_path)
    rows = chat_store._read_jsonl(event_path)
    turn = json.loads(turn_path.read_text(encoding="utf-8"))

    assert [row["kind"] for row in rows] == ["turn.completed"]
    assert turn["event_compaction_revision"] == list(
        restarted._event_revision(event_path) or ()
    )
    assert ("session", "turn") not in restarted._event_cache._entries


@pytest.mark.parametrize("point", ["before-publish", "after-publish", "after-verification", "after-noop"])
def test_compaction_preserves_a_new_journal_and_keeps_it_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str,
) -> None:
    store = ChatSessionStore(tmp_path, startup_maintenance=False)
    turn_path, event_path = _write_completed_turn(tmp_path)
    if point == "after-noop":
        event_path.write_bytes(event_path.read_bytes().splitlines(keepends=True)[1])

    incoming = [
        {"kind": "manager.context", "event_id": "3", "sequence": 3, "payload": {"source": "new"}},
        {"kind": "answer.delta", "event_id": "4", "sequence": 4},
        {"kind": "turn.completed", "event_id": "5", "sequence": 5},
    ]
    incoming_bytes = "".join(json.dumps(row) + "\n" for row in incoming).encode()
    replacement = event_path.with_name("replacement")
    replacement.write_bytes(incoming_bytes)
    original_publish = chat_store._replace_jsonl
    original_replace = os.replace
    original_open = Path.open

    def switch_journal() -> None:
        original_replace(replacement, event_path)

    def publish_with_interleaving(path: Path, rows: list[dict], **kwargs):
        if path == event_path and point == "before-publish":
            switch_journal()
        published = original_publish(path, rows, **kwargs)
        if path == event_path and point == "after-verification":
            switch_journal()
        return published

    def replace_with_interleaving(source, destination, **kwargs):
        original_replace(source, destination, **kwargs)
        if Path(destination) == event_path:
            switch_journal()

    @contextmanager
    def open_with_interleaving(path: Path, mode: str = "r", *args, **kwargs):
        with original_open(path, mode, *args, **kwargs) as stream:
            yield stream
        if path == event_path and mode == "rb" and replacement.exists():
            switch_journal()

    with monkeypatch.context() as boundary:
        if point == "after-publish":
            boundary.setattr(os, "replace", replace_with_interleaving)
        elif point == "after-noop":
            boundary.setattr(Path, "open", open_with_interleaving)
        else:
            boundary.setattr(chat_store, "_replace_jsonl", publish_with_interleaving)
        assert store.compact_completed_events() == int(point in {"after-publish", "after-verification"})

    assert event_path.read_bytes() == incoming_bytes
    turn = json.loads(turn_path.read_bytes())
    assert turn.get("event_compaction_revision") != list(store._event_revision(event_path) or ())
    assert list(event_path.parent.glob(f".{event_path.name}.*.tmp")) == []
    assert store.compact_completed_events() == 1
    assert chat_store._read_jsonl(event_path, strict=True) == [incoming[0], incoming[2]]
    assert store.compact_completed_events() == 0


@pytest.mark.parametrize(
    ("legacy_marker", "rewrite"),
    [(False, True), (True, True), (True, False)],
    ids=["current-rewrite", "legacy-rewrite", "legacy-unchanged"],
)
def test_compaction_revalidates_metadata_preserving_rewrites_and_legacy_markers(
    tmp_path: Path, legacy_marker: bool, rewrite: bool,
) -> None:
    turn_path, event_path = _write_completed_turn(tmp_path)
    intact = event_path.read_bytes().replace(b"answer.delta", b"result.ready")
    event_path.write_bytes(intact)
    ChatSessionStore(tmp_path)
    if legacy_marker:
        turn = json.loads(turn_path.read_bytes())
        turn["event_compaction_revision"] = turn["event_compaction_revision"][:3]
        turn_path.write_text(json.dumps(turn, separators=(",", ":")), encoding="utf-8")
    if rewrite:
        _rewrite_preserving_mtime(event_path, intact.replace(b"result.ready", b"answer.delta"))

    ChatSessionStore(tmp_path)
    rows = chat_store._read_jsonl(event_path, strict=True)
    expected = ["turn.completed"] if rewrite else ["result.ready", "turn.completed"]
    assert [row["kind"] for row in rows] == expected
    assert len(json.loads(turn_path.read_bytes())["event_compaction_revision"]) == 4
    if not rewrite:
        assert event_path.read_bytes() == intact


@pytest.mark.parametrize("damage", [
    "malformed_row", "non_object", "invalid_kind", "nonmonotonic_sequence", "torn_tail_with_buffer",
])
def test_replay_compaction_preserves_damaged_journal_and_retries_after_repair(tmp_path: Path, damage: str) -> None:
    store = ChatSessionStore(tmp_path, startup_maintenance=False)
    turn_path, event_path = _write_completed_turn(tmp_path, with_events=False)
    key = ("session", "turn")
    store.append_event(*key, kind="answer.delta", payload={"text": "increment"})
    store.append_event(*key, kind="manager.context", payload={"note": "retained observation"})
    store.append_event(*key, kind="turn.completed", payload={})
    intact = event_path.read_bytes()
    parts = intact.splitlines(keepends=True)
    if damage == "torn_tail_with_buffer":
        damaged = intact + parts[1][:-2]
    else:
        invalid_row = {
            "malformed_row": parts[1][:-2] + b"\n",
            "non_object": b"[]\n",
            "invalid_kind": json.dumps({**json.loads(parts[1]), "kind": []}).encode() + b"\n",
            "nonmonotonic_sequence": json.dumps({**json.loads(parts[1]), "sequence": 1}).encode() + b"\n",
        }[damage]
        damaged = parts[0] + invalid_row + parts[2]
    event_path.write_bytes(damaged)
    # A tolerant replay may already have cached the remaining readable rows.
    store.events_after(*key, None)
    if damage == "torn_tail_with_buffer":
        store.append_event(*key, kind="manager.context", payload={"note": "buffered observation"}, buffered=True)
    original_turn = turn_path.read_bytes()

    assert store.compact_completed_events() == 0
    assert event_path.read_bytes() == damaged
    assert turn_path.read_bytes() == original_turn

    event_path.write_bytes(intact)
    assert store.compact_completed_events() == 1
    expected = ["manager.context", "turn.completed"]
    if damage == "torn_tail_with_buffer":
        expected.append("manager.context")
    assert [row["kind"] for row in store.events_after(*key, None)] == expected
    assert "event_compaction_revision" in json.loads(turn_path.read_bytes())


def test_missing_event_stream_is_marked_without_retaining_empty_state(
    tmp_path: Path,
) -> None:
    turn_path, _event_path = _write_completed_turn(tmp_path, with_events=False)

    store = ChatSessionStore(tmp_path)
    turn = json.loads(turn_path.read_text(encoding="utf-8"))

    assert turn["event_compaction_revision"] == []
    assert not store._event_cache._entries
    assert not store._event_flush_locks


def test_compaction_marker_does_not_make_legacy_terminal_turns_unreadable(
    tmp_path: Path,
) -> None:
    turn_path, event_path = _write_completed_turn(tmp_path)
    turn = json.loads(turn_path.read_text(encoding="utf-8"))
    turn.pop("schema_version")
    turn_path.write_text(json.dumps(turn), encoding="utf-8")

    store = ChatSessionStore(tmp_path)
    persisted = json.loads(turn_path.read_text(encoding="utf-8"))

    assert persisted["event_compaction_revision"] == list(
        store._event_revision(event_path) or ()
    )
    assert [row["event_id"] for row in store.events_after("session", "turn", None)] == ["2"]
