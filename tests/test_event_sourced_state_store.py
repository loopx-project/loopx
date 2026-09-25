import json
from contextlib import contextmanager
from pathlib import Path

import pytest

import loopx.event_sourced_state as event_sourced_state
from loopx.event_sourced_state import (
    TODO_ADDED,
    AppendOnlyStateEventStore,
    StateEventError,
    make_state_event,
)


def test_load_observes_events_appended_by_another_store(tmp_path: Path) -> None:
    event_log = tmp_path / "events.jsonl"
    reader = AppendOnlyStateEventStore(event_log)
    writer = AppendOnlyStateEventStore(event_log)
    assert reader.load() == []

    appended = writer.append(
        make_state_event(
            event_id="evt-concurrent-writer",
            goal_id="goal-a",
            event_type=TODO_ADDED,
            refs={"todo_id": "todo_concurrent_writer"},
            payload={"role": "agent", "title": "Observe the durable event."},
            recorded_at="2026-09-06T00:00:00Z",
        )
    )

    assert appended["append_sequence"] == 1
    assert reader.load() == [appended]


def test_append_many_loads_once_and_preserves_idempotent_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AppendOnlyStateEventStore(tmp_path / "events.jsonl")
    events = [
        make_state_event(
            event_id=f"event-{index}",
            goal_id="goal-a",
            event_type=TODO_ADDED,
            refs={"todo_id": f"todo_event_{index}"},
            payload={"role": "agent", "title": f"Event {index}"},
            recorded_at="2026-09-08T00:00:00Z",
        )
        for index in range(3)
    ]
    first = store.append(events[0])
    load = store.load
    load_count = 0

    def counted_load() -> list[dict[str, object]]:
        nonlocal load_count
        load_count += 1
        return load()

    monkeypatch.setattr(store, "load", counted_load)
    appended = store.append_many([events[1], events[1], events[0], events[2]])

    assert load_count == 1
    assert [event["append_sequence"] for event in appended] == [2, 2, 1, 3]
    assert [event["event_id"] for event in load()] == [
        "event-0",
        "event-1",
        "event-2",
    ]
    assert appended[2] == first


def test_append_many_preserves_lazy_iterable_visibility_and_reentrancy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AppendOnlyStateEventStore(tmp_path / "events.jsonl")
    events = [
        make_state_event(
            event_id=f"lazy-event-{index}",
            goal_id="goal-a",
            event_type=TODO_ADDED,
            refs={"todo_id": f"todo_lazy_event_{index}"},
            payload={"role": "agent", "title": f"Lazy event {index}"},
            recorded_at="2026-09-09T00:00:00Z",
        )
        for index in range(3)
    ]
    lock_held = False

    @contextmanager
    def non_reentrant_lock(_path: Path):
        nonlocal lock_held
        assert not lock_held
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    monkeypatch.setattr(
        event_sourced_state,
        "exclusive_file_lock",
        non_reentrant_lock,
    )
    observed_prefix: list[str] = []

    def lazy_events():
        yield events[0]
        observed_prefix.extend(event["event_id"] for event in store.load())
        store.append(events[1])
        yield events[2]

    appended = store.append_many(lazy_events())

    assert observed_prefix == ["lazy-event-0"]
    assert [event["event_id"] for event in appended] == [
        "lazy-event-0",
        "lazy-event-2",
    ]
    assert [event["event_id"] for event in store.load()] == [
        "lazy-event-0",
        "lazy-event-1",
        "lazy-event-2",
    ]


def test_append_many_does_not_iterate_list_subclasses_under_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AppendOnlyStateEventStore(tmp_path / "events.jsonl")
    events = [
        make_state_event(
            event_id=f"subclass-event-{index}",
            goal_id="goal-a",
            event_type=TODO_ADDED,
            refs={"todo_id": f"todo_subclass_event_{index}"},
            payload={"role": "agent", "title": f"Subclass event {index}"},
            recorded_at="2026-09-10T00:00:00Z",
        )
        for index in range(2)
    ]
    lock_held = False

    @contextmanager
    def non_reentrant_lock(_path: Path):
        nonlocal lock_held
        assert not lock_held
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    monkeypatch.setattr(event_sourced_state, "exclusive_file_lock", non_reentrant_lock)

    class ReentrantList(list):
        def __iter__(self):
            store.append(events[1])
            return super().__iter__()

    appended = store.append_many(ReentrantList([events[0]]))

    assert [event["event_id"] for event in appended] == ["subclass-event-0"]
    assert [event["event_id"] for event in store.load()] == [
        "subclass-event-1",
        "subclass-event-0",
    ]


def test_eager_batch_has_no_visible_prefix_while_validating_later_events(
    tmp_path: Path,
) -> None:
    event_log = tmp_path / "events.jsonl"
    store = AppendOnlyStateEventStore(event_log)
    first = make_state_event(
        event_id="flush-event-0",
        goal_id="goal-a",
        event_type=TODO_ADDED,
        refs={"todo_id": "todo_flush_event_0"},
        payload={"role": "agent", "title": "First event"},
        recorded_at="2026-09-10T00:00:00Z",
    )
    observed_prefix: list[str] = []

    class ObservingEvent(dict):
        def get(self, key, default=None):
            if not observed_prefix:
                observed_prefix.append(event_log.read_text(encoding="utf-8") if event_log.exists() else "")
            return super().get(key, default)

    second = ObservingEvent(
        make_state_event(
            event_id="flush-event-1",
            goal_id="goal-a",
            event_type=TODO_ADDED,
            refs={"todo_id": "todo_flush_event_1"},
            payload={"role": "agent", "title": "Second event"},
            recorded_at="2026-09-10T00:00:00Z",
        )
    )

    store.append_many([first, second])

    assert observed_prefix == [""]
    assert len(store.load()) == 2


def _event(identity: str, title: str = "A complete event") -> dict:
    return make_state_event(
        event_id=identity,
        goal_id="goal-a",
        event_type=TODO_ADDED,
        refs={"todo_id": f"todo_{identity}"},
        payload={"role": "agent", "title": title},
        recorded_at="2026-09-24T00:00:00Z",
    )


@pytest.mark.parametrize("failure", ["invalid", "stored_conflict", "batch_conflict"])
def test_late_batch_failure_preserves_every_prior_byte(
    tmp_path: Path, failure: str
) -> None:
    store = AppendOnlyStateEventStore(tmp_path / "events.jsonl")
    store.append(_event("existing"))
    before = store.path.read_bytes()
    tail = (
        {"event_id": "invalid"}
        if failure == "invalid"
        else _event(
            "existing" if failure == "stored_conflict" else "new", "Conflicting payload"
        )
    )
    with pytest.raises(StateEventError):
        store.append_many([_event("new"), tail])
    assert store.path.read_bytes() == before


def test_stale_basis_cannot_publish_successor_prefix(tmp_path: Path) -> None:
    store = AppendOnlyStateEventStore(tmp_path / "events.jsonl")
    store.append(_event("original"))
    checksum = event_sourced_state.event_stream_checksum(store.load())
    store.append(_event("concurrent"))
    before = store.path.read_bytes()
    with pytest.raises(event_sourced_state.StateEventSourceChangedError):
        store.append_many([_event("successor")], expected_checksum=checksum)
    assert store.path.read_bytes() == before


def test_lost_publication_ack_replays_without_new_events_and_syncs_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.control_plane.todos import active_state_editing as io

    store = AppendOnlyStateEventStore(tmp_path / "events.jsonl")
    sync = io.fsync_state_directory
    attempts = []

    def fail_once(path):
        attempts.append(path)
        if len(attempts) == 1:
            raise OSError("injected directory sync failure after replacement")
        return sync(path)

    monkeypatch.setattr(io, "fsync_state_directory", fail_once)
    batch = [_event("first"), _event("second")]
    with pytest.raises(event_sourced_state.StateEventCommitUnknownError):
        store.append_many(batch)
    landed = store.path.read_bytes()
    assert [item["event_id"] for item in store.load()] == ["first", "second"]
    assert [item["append_sequence"] for item in store.append_many(batch)] == [1, 2]
    assert store.path.read_bytes() == landed
    assert len(attempts) == 2


def test_replay_durability_failure_remains_an_uncertain_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.control_plane.todos import active_state_editing as io

    store = AppendOnlyStateEventStore(tmp_path / "events.jsonl")
    event = _event("first")
    store.append(event)
    before = store.path.read_bytes()

    def fail_sync(path: Path) -> None:
        raise OSError("injected replay sync failure")

    monkeypatch.setattr(io, "fsync_state_directory", fail_sync)
    with pytest.raises(event_sourced_state.StateEventCommitUnknownError):
        store.append(event)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("sequence", [True, False, 1.5, "2"])
def test_load_rejects_non_integer_append_sequence(
    tmp_path: Path, sequence: object
) -> None:
    event_log = tmp_path / "events.jsonl"
    event = make_state_event(
        event_id="evt-bool-sequence",
        goal_id="goal-a",
        event_type=TODO_ADDED,
        refs={"todo_id": "todo_bool_sequence"},
        payload={"role": "agent", "title": "Reject corrupt sequence."},
        recorded_at="2026-09-07T00:00:00Z",
    )
    event["append_sequence"] = sequence
    event_log.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(StateEventError, match="append_sequence must be an integer"):
        AppendOnlyStateEventStore(event_log).load()


@pytest.mark.parametrize("ending", [b"\r\n\r\n", b""])
def test_atomic_append_preserves_historical_bytes(
    tmp_path: Path, ending: bytes
) -> None:
    store = AppendOnlyStateEventStore(tmp_path / "events.jsonl")
    first = store.append(_event("first"))
    historical = json.dumps(first, ensure_ascii=False).encode() + ending
    store.path.write_bytes(historical)
    store.append(_event("second"))
    assert store.path.read_bytes().startswith(historical)
    assert len(store.load()) == 2
    store.append(_event("first"))
    assert len(store.load()) == 2


def test_failure_before_replace_leaves_old_stream_intact(
    tmp_path: Path, monkeypatch
) -> None:
    from loopx.control_plane.todos import active_state_editing as io

    store = AppendOnlyStateEventStore(tmp_path / "events.jsonl")
    store.append(_event("first"))
    before = store.path.read_bytes()

    def fail_replace(*args):
        raise OSError("injected pre-publication failure")

    monkeypatch.setattr(io.os, "replace", fail_replace)
    with pytest.raises(event_sourced_state.StateEventCommitUnknownError):
        store.append_many([_event("second"), _event("third")])
    assert store.path.read_bytes() == before
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize(
    "choice",
    [
        {"kind": "unknown", "event_id": "second", "append_sequence": 2},
        {"kind": "append", "event_id": "wrong", "append_sequence": 2},
        {"kind": "append", "event_id": "second", "append_sequence": True},
    ],
)
def test_invalid_native_append_reply_does_not_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    choice: dict,
) -> None:
    from loopx.control_plane import effect_runtime

    store = AppendOnlyStateEventStore(tmp_path / "events.jsonl")
    store.append(_event("first"))
    before = store.path.read_bytes()
    monkeypatch.setattr(
        effect_runtime,
        "effect_runtime_result",
        lambda *args: {
            "schema_version": "loopx_state_event_append_result_v0",
            "status": "planned",
            "choices": [choice],
        },
    )
    with pytest.raises(StateEventError, match="invalid event append plan choice"):
        store.append(_event("second"))
    assert store.path.read_bytes() == before


def test_concurrent_processes_publish_contiguous_batches(tmp_path: Path) -> None:
    import subprocess
    import sys

    event_log = tmp_path / "events.jsonl"
    start = tmp_path / "start"
    script = """
import sys, time
from pathlib import Path
from loopx.event_sourced_state import AppendOnlyStateEventStore, make_state_event, TODO_ADDED
path, barrier, worker = sys.argv[1:]
while not Path(barrier).exists():
    time.sleep(0.01)
AppendOnlyStateEventStore(Path(path)).append_many([
    make_state_event(event_id=f"{worker}-{i}", goal_id="goal-a", event_type=TODO_ADDED,
        refs={"todo_id": f"todo_{worker}_{i}"}, payload={"role": "agent", "title": "Concurrent batch"},
        recorded_at="2026-09-24T00:00:00Z") for i in range(3)
])
"""
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(event_log), str(start), str(i)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(3)
    ]
    try:
        start.touch()
        for process in workers:
            _, error = process.communicate(timeout=40)
            assert process.returncode == 0, error
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
                process.wait()
    events = AppendOnlyStateEventStore(event_log).load()
    assert [row["append_sequence"] for row in events] == list(range(1, 10))
    for worker in range(3):
        positions = [
            i
            for i, row in enumerate(events)
            if row["event_id"].startswith(f"{worker}-")
        ]
        assert positions == list(range(positions[0], positions[0] + 3))
