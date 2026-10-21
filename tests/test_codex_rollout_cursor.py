"""Long-turn observation must converge without inventing host execution."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import loopx.codex_app_thread_activity as observer
from loopx.codex_rollout_json import SMALL_RECORD_BYTES, MetadataLine
from loopx.control_plane.agents.host_thread_activity import (
    HostDeliveryExpectation,
    HostDeliveryWindowState,
    HostThreadState,
    HostThreadUnknownReason,
    build_host_delivery_window,
)

T0, T1, T2 = "2026-01-01T10:00:00Z", "2026-01-01T10:01:00Z", "2026-01-01T10:02:00Z"


def event(kind, timestamp=T0, **extra):
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {"type": kind, **extra},
    }


def item(text="example", timestamp=T1):
    return {
        "timestamp": timestamp,
        "type": "response_item",
        "payload": {"type": "message", "content": text},
    }


def encode(record):
    return json.dumps(record, ensure_ascii=False).encode() + b"\n"


def write(path, data):
    first = data.split(b"\n", 1)[0]
    if json.loads(first).get("type") != "session_meta":
        data = (
            encode(
                {"timestamp": T0, "type": "session_meta", "payload": {"id": "thread"}}
            )
            + data
        )
    path.write_bytes(data)


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "host"
    root.mkdir()
    path = root / "rollout.jsonl"
    db = root / "state_5.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, archived INTEGER)"
        )
        connection.execute("INSERT INTO threads VALUES ('thread', ?, 0)", (str(path),))
    observer._rollout_cache.clear()
    monkeypatch.setattr(observer, "_OBSERVATION_READ_BYTES", 32 * 1024)
    monkeypatch.setattr(observer, "_OBSERVATION_CHUNK_BYTES", 4096)
    return root, path, tmp_path / "cache"


def observe(store, *, cached=False):
    root, _path, cache = store
    return observer.observe_codex_threads(
        ["thread"], homes=[root], cache_root=cache if cached else None
    )["thread"]


def converge(store, *, cached=False, maximum=100):
    for _ in range(maximum):
        result = observe(store, cached=cached)
        if result.state is not HostThreadState.UNKNOWN:
            return result
    pytest.fail("finite immutable input did not converge within its bounded reads")


def write_long(store):
    path = store[1]
    write(path, encode(event("task_started")) + encode(item("x" * 700)) * 120)
    return path


def test_growing_and_cold_long_turns_recover_and_process_restart_reuses_verified_cache(
    store,
):
    path = store[1]
    write(path, encode(event("task_started")))
    assert observe(store, cached=True).state is HostThreadState.TURN_OPEN
    with path.open("ab") as stream:
        stream.write(encode(item("DO_NOT_CACHE_MESSAGE_BODY" * 30)) * 120)
    result = converge(store, cached=True)
    assert (result.state, result.turn_started_at, result.last_event_at) == (
        HostThreadState.TURN_OPEN,
        T0,
        T1,
    )
    observer._rollout_cache.clear()
    assert observe(store, cached=True) == result
    raw = b"".join(p.read_bytes() for p in store[2].glob("*.json"))
    assert b"DO_NOT_CACHE" not in raw
    assert b"thread" not in raw
    assert str(store[0]).encode() not in raw
    observer._rollout_cache.clear()
    assert observe(store).state is HostThreadState.UNKNOWN
    assert converge(store) == result


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("task_complete", HostThreadState.IDLE),
        ("turn_aborted", HostThreadState.IDLE),
        ("task_started", HostThreadState.TURN_OPEN),
    ],
)
def test_latest_marker_interrupts_pending_historical_recovery(store, kind, expected):
    path = write_long(store)
    assert observe(store).state is HostThreadState.UNKNOWN
    with path.open("ab") as stream:
        stream.write(encode(event(kind, T2)))
    result = observe(store)
    assert result.state is expected
    assert result.last_event_at == T2


def test_busy_cursor_does_not_delay_or_hide_another_thread(store):
    root, path, _cache = store
    write(path, encode(event("task_started")))
    assert observe(store).state is HostThreadState.TURN_OPEN
    cursor = next(iter(observer._rollout_cache.values()))
    other = root / "other.jsonl"
    other.write_bytes(
        encode({"type": "session_meta", "payload": {"id": "other"}})
        + encode(event("task_complete", T2))
    )
    with sqlite3.connect(root / "state_5.sqlite") as connection:
        connection.execute("INSERT INTO threads VALUES ('other', ?, 0)", (str(other),))
    with cursor.lock:
        result = observer.observe_codex_threads(["thread", "other"], homes=[root])
    assert result["thread"].reason is HostThreadUnknownReason.STORE_UNAVAILABLE
    assert result["other"].state is HostThreadState.IDLE
    assert observe(store).state is HostThreadState.TURN_OPEN


def test_source_replaced_with_out_of_home_symlink_is_not_read(store, tmp_path):
    _root, path, _cache = store
    write(path, encode(event("task_started")))
    assert observe(store).state is HostThreadState.TURN_OPEN
    outside = tmp_path / "outside.jsonl"
    write(outside, encode(event("task_complete", T2)))
    path.unlink()
    path.symlink_to(outside)
    assert observe(store).reason is HostThreadUnknownReason.RECORD_UNRECOGNIZED
    path.unlink()
    write(path, encode(event("task_complete", T2)))
    assert observe(store).state is HostThreadState.IDLE


def test_small_appends_do_not_restart_cold_recovery(store):
    path = write_long(store)
    for _ in range(30):
        result = observe(store)
        if result.state is HostThreadState.TURN_OPEN:
            break
        with path.open("ab") as stream:
            stream.write(encode(item("additional observation")))
    assert result.state is HostThreadState.TURN_OPEN
    assert result.turn_started_at == T0


@pytest.mark.parametrize(
    "kind, expected",
    [
        (None, HostThreadState.TURN_OPEN),
        ("task_started", HostThreadState.TURN_OPEN),
        ("task_complete", HostThreadState.IDLE),
        ("turn_aborted", HostThreadState.IDLE),
    ],
)
def test_giant_complete_records_are_streamed_without_a_line_size_deadlock(
    store, kind, expected
):
    body = 'opaque \\ " 中文 😀 ' * (SMALL_RECORD_BYTES // 8)
    record = item(body) if kind is None else event(kind, T1, detail=body)
    write(store[1], encode(event("task_started")) + encode(record))
    result = converge(store)
    assert result.state is expected
    assert result.last_event_at == T1
    for cursor in observer._rollout_cache.values():
        assert (
            cursor.back.pending is None
            or len(cursor.back.pending) <= SMALL_RECORD_BYTES
        )


def test_giant_malformed_event_is_unknown_and_a_new_complete_marker_recovers(store):
    body = encode(event("task_complete", T1, detail="x" * (SMALL_RECORD_BYTES + 9000)))
    path = store[1]
    write(path, encode(event("task_started")) + body[:-3] + b"\n")
    for _ in range(50):
        result = observe(store)
        if result.reason is HostThreadUnknownReason.RECORD_UNRECOGNIZED:
            break
    assert result.state is HostThreadState.UNKNOWN
    assert result.reason is HostThreadUnknownReason.RECORD_UNRECOGNIZED
    with path.open("ab") as stream:
        stream.write(encode(event("task_complete", T2)))
    assert converge(store).state is HostThreadState.IDLE


def test_unterminated_giant_end_is_not_an_end_until_newline(store):
    path = store[1]
    write(
        path,
        encode(event("task_started"))
        + encode(
            event("task_complete", T1, detail="x" * (SMALL_RECORD_BYTES + 2000))
        ).rstrip(b"\n"),
    )
    result = converge(store)
    assert result.state is HostThreadState.TURN_OPEN
    with path.open("ab") as stream:
        stream.write(b"\n")
    assert converge(store).state is HostThreadState.IDLE


@pytest.mark.parametrize("replace", [False, True])
def test_truncation_and_rotation_do_not_reuse_an_open_marker(store, replace):
    path = write_long(store)
    assert converge(store, cached=True).state is HostThreadState.TURN_OPEN
    if replace:
        new = path.with_suffix(".new")
        write(new, encode(event("task_complete", T2)))
        os.replace(new, path)
    else:
        write(path, encode(event("task_complete", T2)))
    assert converge(store, cached=True).state is HostThreadState.IDLE


def test_rewritten_marker_cannot_be_hidden_by_an_append(store):
    path = write_long(store)
    assert converge(store, cached=True).state is HostThreadState.TURN_OPEN
    data = path.read_bytes().replace(b"task_started", b"turn_aborted", 1)
    write(path, data + encode(item("appended")))
    assert converge(store, cached=True).state is HostThreadState.IDLE


@pytest.mark.parametrize("cache_damage", ["corrupt", "unavailable", "symlink-loop"])
def test_optional_cache_failure_does_not_prevent_recovery(store, cache_damage):
    write_long(store)
    assert converge(store, cached=True).state is HostThreadState.TURN_OPEN
    observer._rollout_cache.clear()
    if cache_damage == "corrupt":
        for path in store[2].glob("*.json"):
            path.write_text('{"body": {"state": "turn_open"}}')
    elif cache_damage == "unavailable":
        for path in store[2].glob("*.json"):
            path.unlink()
        store[2].rmdir()
        store[2].write_text("not a directory")
    else:
        for path in store[2].glob("*.json"):
            path.unlink()
        store[2].rmdir()
        store[2].symlink_to(store[2])
    assert converge(store, cached=True).state is HostThreadState.TURN_OPEN


def test_cache_location_never_writes_into_the_host_store(store):
    write(store[1], encode(event("task_started")))
    result = observer.observe_codex_threads(
        ["thread"], homes=[store[0]], cache_root=store[0] / "cache"
    )["thread"]
    assert result.state is HostThreadState.TURN_OPEN
    assert not (store[0] / "cache").exists()


def test_cached_start_and_file_mtime_do_not_refresh_the_event_clock(store):
    write_long(store)
    before = converge(store, cached=True)
    os.utime(store[1], None)
    after = converge(store, cached=True)
    assert before == after
    assert after.last_event_at == T1
    window = build_host_delivery_window(
        after,
        HostDeliveryExpectation(15, "agreed-cadence"),
        now=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
    )
    assert window.state is HostDeliveryWindowState.STALE


def test_every_cold_and_incremental_read_obeys_the_byte_budget(store, monkeypatch):
    path = write_long(store)
    original = Path.open
    reads = [0]

    class Counted:
        def __init__(self, stream, *, host=True):
            self.stream = stream
            self.host = host

        def read(self, count=-1):
            if not self.host:
                assert 0 <= count <= 8192, "Cache reads need their own explicit bound"
            value = self.stream.read(count)
            if self.host:
                reads[0] += len(value)
            return value

        def __getattr__(self, key):
            return getattr(self.stream, key)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

    def opened(target, mode="r", *args, **kwargs):
        stream = original(target, mode, *args, **kwargs)
        if target.parent == store[2] and mode == "rb":
            return Counted(stream, host=False)
        return Counted(stream) if target == path and mode == "rb" else stream

    monkeypatch.setattr(Path, "open", opened)
    for _ in range(30):
        reads[0] = 0
        result = observe(store, cached=True)
        assert reads[0] <= observer._OBSERVATION_READ_BYTES
        if result.state is HostThreadState.TURN_OPEN:
            break
    assert result.state is HostThreadState.TURN_OPEN
    with original(path, "ab") as stream:
        stream.write(encode(item("new")))
    reads[0] = 0
    assert observe(store, cached=True).state is HostThreadState.TURN_OPEN
    assert reads[0] < 4096
    observer._rollout_cache.clear()
    reads[0] = 0
    assert observe(store, cached=True).state is HostThreadState.TURN_OPEN
    assert reads[0] <= observer._OBSERVATION_READ_BYTES


@pytest.mark.parametrize("replace", [False, True])
def test_a_new_end_or_rotation_during_read_is_not_reported_as_open(
    store, monkeypatch, replace
):
    path = store[1]
    write(path, encode(event("task_started")))
    original = Path.open
    injected = [False]

    class Racing:
        def __init__(self, stream):
            self.stream = stream

        def read(self, count=-1):
            value = self.stream.read(count)
            if not injected[0]:
                injected[0] = True
                if replace:
                    replacement = path.with_suffix(".replacement")
                    write(replacement, encode(event("task_complete", T2)))
                    os.replace(replacement, path)
                else:
                    with original(path, "ab") as stream:
                        stream.write(encode(event("task_complete", T2)))
            return value

        def __getattr__(self, key):
            return getattr(self.stream, key)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

    def opened(target, mode="r", *args, **kwargs):
        stream = original(target, mode, *args, **kwargs)
        return Racing(stream) if target == path and mode == "rb" else stream

    monkeypatch.setattr(Path, "open", opened)
    assert observe(store).state is HostThreadState.UNKNOWN
    assert converge(store).state is HostThreadState.IDLE


@pytest.mark.parametrize("chunk", [1, 7, 41, 4096])
def test_stream_envelopes_match_json_metadata_including_escaped_and_duplicate_keys(
    chunk,
):
    records = [
        encode(event("task_complete", T1, detail="x" * (SMALL_RECORD_BYTES + 100))),
        (
            '{"timestamp":"'
            + T1
            + '","type":"response_item","payload":{},"type":"event_msg","payload":{"type":"task_complete","detail":"'
            + "x" * (SMALL_RECORD_BYTES + 100)
            + '"}}\n'
        ).encode(),
        encode(
            {
                "timestamp": T1,
                "type": "event_msg",
                "payload": {
                    "type": ["not a marker"],
                    "large": "x" * (SMALL_RECORD_BYTES + 100),
                },
            }
        ),
    ]
    for data in records:
        line = MetadataLine()
        for offset in range(0, len(data), chunk):
            line.feed(data[offset : offset + chunk])
        parsed = json.loads(data)
        result = line.finish()
        assert result["type"] == parsed["type"]
        assert result["timestamp"] == parsed["timestamp"]
        expected = parsed["payload"].get("type")
        assert result["payload"]["type"] == (
            expected if isinstance(expected, str) else None
        )


@pytest.mark.parametrize(
    "suffix", [b'"bad\\q"}', b'{"missing":}', b"[1,]", b'"unterminated}', b'"bad\xff"}']
)
def test_large_malformed_json_is_not_accepted_from_an_early_type_prefix(suffix):
    data = (
        b'{"timestamp":"'
        + T1.encode()
        + b'","type":"event_msg","payload":{"type":"task_started"},"content":"'
        + b"x" * (SMALL_RECORD_BYTES + 30)
        + b'","invalid":'
        + suffix
    )
    line = MetadataLine()
    for offset in range(0, len(data), 4096):
        line.feed(data[offset : offset + 4096])
    assert line.finish() is None


@pytest.mark.parametrize(
    "header",
    [
        None,
        {"type": "session_meta", "payload": {"id": "another-thread"}},
        {"type": "session_meta", "payload": None},
    ],
)
def test_missing_or_mismatched_session_header_cannot_borrow_an_open_turn(store, header):
    content = (encode(header) if header else b"") + encode(event("task_started"))
    store[1].write_bytes(content)
    for _ in range(3):
        result = observe(store, cached=True)
        assert result.state is HostThreadState.UNKNOWN
        assert result.reason is HostThreadUnknownReason.RECORD_UNRECOGNIZED
    write(store[1], encode(event("task_complete", T2)))
    assert converge(store, cached=True).state is HostThreadState.IDLE


def test_a_large_session_header_is_verified_without_an_unbounded_read(store):
    header = {
        "type": "session_meta",
        "payload": {"instructions": "opaque" * SMALL_RECORD_BYTES, "id": "thread"},
    }
    store[1].write_bytes(encode(header) + encode(event("task_started")))
    assert converge(store).state is HostThreadState.TURN_OPEN


def test_same_thread_name_in_another_home_does_not_reuse_activity(store, tmp_path):
    write_long(store)
    assert converge(store, cached=True).state is HostThreadState.TURN_OPEN
    other = tmp_path / "other-host"
    other.mkdir()
    path = other / "rollout.jsonl"
    write(path, encode(event("task_complete", T2)))
    with sqlite3.connect(other / "state_5.sqlite") as connection:
        connection.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, archived INTEGER)"
        )
        connection.execute("INSERT INTO threads VALUES ('thread', ?, 0)", (str(path),))
    result = observer.observe_codex_threads(
        ["thread"], homes=[other], cache_root=store[2]
    )["thread"]
    assert result.state is HostThreadState.IDLE
    assert result.last_event_at == T2
