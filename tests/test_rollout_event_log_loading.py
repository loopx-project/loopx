from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import loopx.rollout_event_log as rollout_event_log
from loopx.rollout_event_log import (
    append_rollout_event,
    append_rollout_event_once,
    build_rollout_event,
    iter_rollout_events,
    load_rollout_events,
)


@pytest.mark.parametrize("legacy_runtime", [False, True])
def test_rollout_script_keeps_one_default_route_and_explicit_overrides(
    tmp_path: Path, legacy_runtime: bool,
) -> None:
    home = tmp_path / "home"
    current, legacy = home / ".loopx", home / ".codex" / "loopx"
    if legacy_runtime:
        legacy.mkdir(parents=True)
        (legacy / "registry.global.json").write_text('{"goals": []}', encoding="utf-8")
    selected = legacy if legacy_runtime else current
    script = Path(__file__).resolve().parents[1] / "scripts" / "goal_rollout_event_log.py"
    env = dict(os.environ, HOME=str(home), LOOPX_USAGE_PING="0")
    command = [sys.executable, str(script), "append", "--goal-id", "fixture",
               "--event-kind", "quota_should_run"]

    def run(*extra: str):
        return subprocess.run([*command, *extra], env=env, capture_output=True,
                              text=True, encoding="utf-8", timeout=15)

    written = run()
    assert written.returncode == 0, written.stderr
    log = selected / "goals" / "fixture" / "rollout-event-log.jsonl"
    before = log.read_bytes()
    for root in (current, legacy):
        root.mkdir(parents=True, exist_ok=True)
        (root / "registry.global.json").write_text('{"goals": []}', encoding="utf-8")
    refused = run()
    assert refused.returncode != 0
    assert "Both default LoopX registries exist" in refused.stderr
    assert log.read_bytes() == before
    explicit = tmp_path / "explicit-runtime"
    assert run("--runtime-root", str(explicit)).returncode == 0
    assert (explicit / "goals/fixture/rollout-event-log.jsonl").exists()
    explicit_log = tmp_path / "explicit-log.jsonl"
    assert run("--log-path", str(explicit_log)).returncode == 0
    assert explicit_log.exists()


def test_limited_rollout_event_load_keeps_only_a_bounded_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class TrackedEvent(dict[str, object]):
        alive = 0
        peak = 0

        def __init__(self, value: dict[str, object]) -> None:
            super().__init__(value)
            type(self).alive += 1
            type(self).peak = max(type(self).peak, type(self).alive)

        def __del__(self) -> None:
            type(self).alive -= 1

    log = tmp_path / "events.jsonl"
    log.write_text("".join(json.dumps({
        "schema_version": rollout_event_log.ROLLOUT_EVENT_SCHEMA_VERSION,
        "index": index,
    }) + "\n" for index in range(100)), encoding="utf-8")
    original_loads = json.loads

    def tracked_loads(value):
        return TrackedEvent(original_loads(value))

    monkeypatch.setattr(rollout_event_log.json, "loads", tracked_loads)

    events = rollout_event_log.load_rollout_events(log, limit=3)

    assert [event["index"] for event in events] == [97, 98, 99]
    assert TrackedEvent.peak <= 4


def test_recent_rollout_read_does_not_scan_unneeded_history(
    tmp_path: Path, monkeypatch,
) -> None:
    log = tmp_path / "events.jsonl"
    records = [{"schema_version": rollout_event_log.ROLLOUT_EVENT_SCHEMA_VERSION,
                "index": index, "summary": "retained history " * 32}
               for index in range(3000)]
    log.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    original_open = Path.open
    observed = {"bytes": 0}

    class CountedRead:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __iter__(self):
            for line in self.handle:
                observed["bytes"] += len(line)
                yield line

        def read(self, size=-1):
            value = self.handle.read(size)
            observed["bytes"] += len(value)
            return value

    def counted_open(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return CountedRead(handle) if path == log and mode == "rb" else handle

    monkeypatch.setattr(Path, "open", counted_open)

    assert load_rollout_events(log, limit=3) == records[-3:]
    assert observed["bytes"] < log.stat().st_size // 4


@pytest.mark.parametrize("limit", [1, 2, 7, 100])
@pytest.mark.parametrize("final_newline", [False, True])
def test_recent_rollout_read_counts_valid_records_and_preserves_complete_rows(
    tmp_path: Path, limit: int, final_newline: bool,
) -> None:
    log = tmp_path / "events.jsonl"
    records = [{"schema_version": rollout_event_log.ROLLOUT_EVENT_SCHEMA_VERSION,
                "index": index, "summary": "雪🙂" * (25000 if index == 4 else 3)}
               for index in range(8)]
    invalid = b'\xff\n{"torn":\n[]\nnull\n{"schema_version":"other"}\n\n'
    contents = b"".join(json.dumps(row, ensure_ascii=False).encode("utf-8")
                        + b"\n" + invalid for row in records[:-1])
    contents += json.dumps(records[-1], ensure_ascii=False).encode("utf-8")
    contents += b"\n" if final_newline else b""
    log.write_bytes(contents)

    assert load_rollout_events(log, limit=limit) == records[-limit:]
    assert log.read_bytes() == contents


@pytest.mark.parametrize("limit", [None, 0, -1])
def test_unlimited_rollout_read_retains_its_complete_history(
    tmp_path: Path, limit: int | None,
) -> None:
    log = tmp_path / "events.jsonl"
    records = [{"schema_version": rollout_event_log.ROLLOUT_EVENT_SCHEMA_VERSION,
                "index": index} for index in range(5)]
    log.write_text("\n".join(json.dumps(row) for row in records), encoding="utf-8")
    assert load_rollout_events(log, limit=limit) == records


def test_recent_rollout_read_reopens_the_current_file(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    records = [{"schema_version": rollout_event_log.ROLLOUT_EVENT_SCHEMA_VERSION,
                "index": index} for index in range(3)]
    log.write_text(json.dumps(records[0]) + "\n", encoding="utf-8")
    assert load_rollout_events(log, limit=2) == records[:1]
    with log.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(json.dumps(row) for row in records[1:]))
    assert load_rollout_events(log, limit=2) == records[1:]


def test_recent_rollout_read_preserves_nonseekable_input(
    tmp_path: Path, monkeypatch,
) -> None:
    log = tmp_path / "events.jsonl"
    records = [{"schema_version": rollout_event_log.ROLLOUT_EVENT_SCHEMA_VERSION,
                "index": index} for index in range(4)]
    contents = "\n".join(json.dumps(row) for row in records).encode()
    original_open = Path.open

    class NonseekableRead(io.BytesIO):
        def seekable(self):
            return False

        def seek(self, *args):
            raise io.UnsupportedOperation("not seekable")

    def opened(path, mode="r", *args, **kwargs):
        return NonseekableRead(contents) if path == log else original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    assert load_rollout_events(log, limit=2) == records[-2:]


@pytest.mark.parametrize("limit", [None, 1])
def test_rollout_read_keeps_missing_file_behavior(tmp_path: Path, limit: int | None) -> None:
    assert load_rollout_events(tmp_path / "missing.jsonl", limit=limit) == []


@pytest.mark.parametrize("row,message", [
    ("{", "must contain valid JSON"),
    ("[]", "must contain an object"),
    ('{"schema_version":"other"}', "must use loopx_rollout_event_v0"),
])
def test_strict_rollout_read_still_reports_the_original_line(
    tmp_path: Path, row: str, message: str,
) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text("\n" + row + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="rollout event log line 2 " + message):
        list(iter_rollout_events(log, strict=True))


@pytest.mark.parametrize("idempotent", [False, True])
@pytest.mark.parametrize(
    "torn_record",
    [
        b'{"schema_version":"loopx_rollout_event_v0"',
        b'{"summary":"' + "雪".encode()[:2],
    ],
    ids=["ascii", "mid-utf8"],
)
def test_append_starts_a_new_row_after_a_torn_final_record(
    tmp_path: Path,
    idempotent: bool,
    torn_record: bytes,
) -> None:
    log_path = tmp_path / "rollout-event-log.jsonl"
    existing = build_rollout_event(
        goal_id="goal-a",
        event_kind="quota_should_run",
        agent_id="agent-a",
        run_id="turn-before",
        status="run",
        recorded_at="2026-09-22T00:00:00Z",
    )
    log_path.write_bytes(
        json.dumps(existing, sort_keys=True).encode("utf-8")
        + b"\n"
        + torn_record
    )
    event = build_rollout_event(
        goal_id="goal-a",
        event_kind="quota_should_run",
        agent_id="agent-a",
        run_id="turn-a",
        status="run",
        recorded_at="2026-09-23T00:00:00Z",
    )

    if idempotent:
        written, appended = append_rollout_event_once(
            log_path,
            event,
            identity_fields=("goal_id", "event_kind", "agent_id", "run_id"),
        )
        assert appended is True
    else:
        written = append_rollout_event(log_path, event)

    assert torn_record + b"\n" in log_path.read_bytes()
    assert load_rollout_events(log_path) == [existing, written]


@pytest.mark.parametrize("idempotent", [False, True])
def test_append_replays_an_event_before_a_mid_utf8_torn_record(
    tmp_path: Path,
    idempotent: bool,
) -> None:
    log_path = tmp_path / "rollout-event-log.jsonl"
    event = build_rollout_event(
        goal_id="goal-a",
        event_kind="quota_should_run",
        agent_id="agent-a",
        run_id="turn-a",
        status="run",
        recorded_at="2026-09-23T00:00:00Z",
    )
    torn_record = b'{"summary":"' + "雪".encode()[:2]
    original = (
        json.dumps(event, sort_keys=True, ensure_ascii=False).encode("utf-8")
        + b"\n"
        + torn_record
    )
    log_path.write_bytes(original)

    if idempotent:
        replayed, appended = append_rollout_event_once(
            log_path,
            event,
            identity_fields=("goal_id", "event_kind", "agent_id", "run_id"),
        )
        assert appended is False
    else:
        replayed = append_rollout_event(log_path, event)

    assert replayed == event
    assert log_path.read_bytes() == original


def test_strict_read_reports_a_mid_utf8_torn_record(tmp_path: Path) -> None:
    log_path = tmp_path / "rollout-event-log.jsonl"
    event = build_rollout_event(
        goal_id="goal-a",
        event_kind="quota_should_run",
        recorded_at="2026-09-23T00:00:00Z",
    )
    log_path.write_bytes(
        json.dumps(event, sort_keys=True).encode("utf-8")
        + b"\n"
        + b'{"summary":"'
        + "雪".encode()[:2]
    )

    with pytest.raises(
        ValueError,
        match="rollout event log line 2 must contain valid UTF-8",
    ):
        list(iter_rollout_events(log_path, strict=True))
