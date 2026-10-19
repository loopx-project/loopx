"""A durable feedback hint wakes the existing worker, never grants dispatch."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from benchmark.runtime.research_scheduler import FeedbackEventSleep, main
from benchmark.runtime.research_feedback_wait import synchronize_feedback_waits
from scripts import external_scheduler_worker as worker
import test_research_feedback_wait as wait_fixture

runtime = wait_fixture.runtime


def publish(path, revision):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"revision": revision}))
    temporary.replace(path)


def test_event_between_quota_and_sleep_is_not_lost_or_replayed_as_endless_wake(tmp_path):
    path = tmp_path / "ingestion/wake.json"
    publish(path, "a" * 64)
    now, pauses = [0.0], []

    def pause(seconds):
        pauses.append(seconds)
        now[0] += seconds

    sleeper = FeedbackEventSleep(path, clock=lambda: now[0], pause=pause)
    publish(path, "b" * 64)  # Publication races the last quota read, before sleep.
    sleeper(3600)
    assert now[0] == 0 and pauses == []
    sleeper(3)
    assert now[0] == 3 and sum(pauses) == 3
    path.write_text("{partial")
    sleeper(2)
    assert now[0] == 5  # Missing/partial signals preserve the requested cadence.
    publish(path, "c" * 64)
    sleeper(3600)
    assert now[0] == 5


def test_research_scheduler_entry_is_explicit_and_reuses_ordinary_arguments(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOPX_RESEARCH_LOOP", raising=False)
    with pytest.raises(ValueError, match="explicit research-loop"):
        main([])
    monkeypatch.setenv("LOOPX_RESEARCH_LOOP", "1")
    monkeypatch.setenv("LOOPX_RESEARCH_FEEDBACK_ROOT", str(tmp_path))
    called = []
    monkeypatch.setattr(worker, "main", lambda argv, **kwargs: called.append((argv, kwargs)) or 0)
    argv = ["--goal-id", "synthetic-goal", "--agent-id", "synthetic-agent", "--once"]
    assert main(argv) == 0
    assert called[0][0] is argv
    assert called[0][1]["sleep"].path == tmp_path / "ingestion/wake.json"


def test_feedback_sync_cli_refuses_overlap_before_reading_native_packet(tmp_path, monkeypatch):
    from benchmark.runtime.research_feedback_sync import _consumer_lock
    from benchmark.runtime.research_feedback_wait import main as feedback_main

    monkeypatch.setenv("LOOPX_RESEARCH_FEEDBACK_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["loopx-research-feedback", "sync"])
    (tmp_path / "owner.json").write_text(json.dumps({"LOOPX_RESEARCH_FEEDBACK_ROOT": str(tmp_path)}))
    # There is deliberately no native packet: the entry point must acquire
    # consumer ownership before reading a snapshot that could become stale.
    with _consumer_lock(tmp_path):
        with pytest.raises(RuntimeError, match="native_feedback_consumer_busy"):
            feedback_main()
    with pytest.raises(FileNotFoundError, match="native.json"):
        feedback_main()  # The failed contender did not retain the lock.


def test_event_rechecks_real_quota_and_only_released_dependency_dispatches(runtime, monkeypatch):
    original = wait_fixture.waiting(runtime)
    event = runtime.temp / "native-feedback/ingestion/wake.json"
    publish(event, "a" * 64)
    seen, pauses, now, dispatches = [], [], [0.0], []
    original_turn = runtime.env["LOOPX_TURN"]
    original_deadline = runtime.env["LOOPX_PHASE_DEADLINE_EPOCH"]

    def quota(_command, **_kwargs):
        value = runtime.quota()
        seen.append(value)
        return value

    def observe(seconds):
        pauses.append(seconds)
        now[0] += seconds
        if len(pauses) == 1:
            publish(event, "b" * 64)  # A signal alone cannot satisfy the dependency.
        elif len(pauses) == 2:
            value = wait_fixture.packet(status="completed", attribution="verified")
            receipt = synchronize_feedback_waits(runtime.env, value)
            assert receipt["released"][0]["dependency_todo_id"] == original["dependency_todo_id"]
            publish(event, "c" * 64)  # Consumer publication follows durable evidence.
        else:
            raise AssertionError("released evidence did not interrupt the long scheduler sleep")

    def wake(_command, **_kwargs):
        assert len(seen) == 3 and [packet["should_run"] for packet in seen] == [False, False, True]
        assert seen[-1]["selected_todo"]["todo_id"] == wait_fixture.fixture.TODO_ID
        assert runtime.env["LOOPX_TURN"] == original_turn
        assert runtime.env["LOOPX_PHASE_DEADLINE_EPOCH"] == original_deadline
        dispatches.append(original_turn)
        # Transport-only bounded synthetic Host; no model or scoring request.
        return SimpleNamespace(returncode=75, failure_kind=None)

    monkeypatch.setattr(worker, "run_quota_should_run", quota)
    monkeypatch.setattr(worker, "_run_wake", wake)
    args = SimpleNamespace(cli_bin="unused", registry=str(runtime.registry), runtime_root=str(runtime.root),
        runtime_profile="generic_cli", goal_id=runtime.env["LOOPX_GOAL_ID"], agent_id=runtime.env["LOOPX_AGENT_ID"],
        state_file=str(runtime.temp / "scheduler.json"), once=False, wake_cmd="synthetic-host",
        wake_stop_exit_code=75, error_backoff_seconds=5, quota_timeout_seconds=60, wake_timeout_seconds=30)
    sleeper = FeedbackEventSleep(event, clock=lambda: now[0], pause=observe)
    assert worker.run_worker(args, sleep=sleeper) == 0
    assert len(dispatches) == 1 and now[0] == 2
    assert json.loads((runtime.temp / "scheduler.json").read_text())["unchanged_count"] == 0


def test_overlapping_consumers_and_old_packets_preserve_newly_runnable_work(runtime, monkeypatch, record_property):
    from benchmark.edgebench.research_feedback import canonical_json, write_bytes_atomic
    from benchmark.runtime import research_feedback_sync as consumer
    from benchmark.runtime.research_feedback_wait import wait_for_feedback

    root = Path(runtime.env["LOOPX_RESEARCH_FEEDBACK_ROOT"])
    old = wait_fixture.packet(status="completed", attribution="verified")
    pending = wait_fixture.packet(submission="submission-2")["records"][0]
    pending["round_id"] = "agent-2"
    old["records"].append(pending)
    write_bytes_atomic(root / "native.json", canonical_json(old))
    wait_for_feedback(runtime.env, todo_id=wait_fixture.fixture.TODO_ID,
        submission_id="submission-2", packet=old)
    consumer.synchronize_native_feedback(runtime.env)
    assert not runtime.quota()["should_run"]
    event = root / "ingestion/wake.json"
    before = event.read_bytes()
    now, pauses = [0.0], []

    def pause(seconds):
        pauses.append(seconds)
        now[0] += seconds

    sleeper = FeedbackEventSleep(event, clock=lambda: now[0], pause=pause)
    entered, finish = threading.Event(), threading.Event()
    original_publish = consumer._publish_feedback_wake

    def delayed_publication(env, feedback_root):
        entered.set()
        assert finish.wait(timeout=45), "the test must release its paused consumer"
        return original_publish(env, feedback_root)

    new = copy.deepcopy(old)
    new["records"][1] = wait_fixture.packet(status="completed", attribution="verified",
        submission="submission-2")["records"][0]
    new["records"][1]["round_id"] = "agent-2"
    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=1) as executor:
        patch.setattr(consumer, "_publish_feedback_wake", delayed_publication)
        older = executor.submit(consumer.synchronize_native_feedback, runtime.env)
        try:
            assert entered.wait(timeout=45), "the old snapshot did not reach publication"
            write_bytes_atomic(root / "native.json", canonical_json(new))
            # The real second process must be refused before reading the new
            # snapshot or touching any Goal/provider state. There is no polling
            # sleep or elapsed-time threshold deciding the race's outcome.
            contender = subprocess.run([sys.executable, "-c", """
import os
from benchmark.runtime.research_feedback_sync import synchronize_native_feedback
try:
    synchronize_native_feedback(dict(os.environ))
except RuntimeError as error:
    assert str(error) == "native_feedback_consumer_busy", str(error)
    print("busy")
else:
    raise AssertionError("a second consumer entered while publication was held")
"""], env=runtime.env, cwd=Path(__file__).resolve().parents[2],
                text=True, capture_output=True, timeout=15, check=True)
            assert contender.stdout.strip() == "busy"
            assert event.read_bytes() == before
            assert not runtime.quota()["should_run"], "contention cannot grant model admission"
        finally:
            finish.set()
        assert older.result(timeout=45)["released"] == []

    started = time.monotonic()
    released = consumer.synchronize_native_feedback(runtime.env)
    record_property("feedback_consumer_seconds", time.monotonic() - started)
    assert len(released["released"]) == 1 and runtime.quota()["should_run"]
    after = event.read_bytes()
    assert after != before
    # A delayed caller may still hold the older in-memory packet. Its readback
    # must include the newer canonical completion, not restore the old marker.
    assert consumer.synchronize_native_feedback(runtime.env, packet=old)["released"] == []
    assert event.read_bytes() == after
    sleeper(600)
    assert now[0] == 0 and pauses == []
    assert consumer.synchronize_native_feedback(runtime.env, packet=new)["released"] == []
    sleeper(3)
    assert now[0] == 3, "unchanged feedback must not manufacture another wake"


def test_retry_rebuilds_notification_after_todo_completion_was_durable(runtime, monkeypatch):
    from benchmark.edgebench import research_feedback as producer
    from benchmark.runtime import research_feedback_sync as consumer

    wait_fixture.waiting(runtime)
    pending = wait_fixture.packet()
    consumer.synchronize_native_feedback(runtime.env, packet=pending)
    event = Path(runtime.env["LOOPX_RESEARCH_FEEDBACK_ROOT"]) / "ingestion/wake.json"
    before = event.read_bytes()
    completed = wait_fixture.packet(status="completed", attribution="verified")
    writer = producer.write_bytes_atomic

    def unavailable(path, raw, **kwargs):
        if path == event:
            raise OSError("synthetic wake publication unavailable")
        return writer(path, raw, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(producer, "write_bytes_atomic", unavailable)
        with pytest.raises(OSError, match="wake publication unavailable"):
            consumer.synchronize_native_feedback(runtime.env, packet=completed)
    assert event.read_bytes() == before
    assert runtime.quota()["should_run"], "notification failure must not undo committed evidence"
    context = runtime.call("explore", "turn-context", *runtime.binding)
    assert len(context["graph"]["writeback_results"]) == 1
    now = [0.0]
    sleeper = FeedbackEventSleep(event, clock=lambda: now[0],
        pause=lambda seconds: now.__setitem__(0, now[0] + seconds))
    # Even an older packet whose row still says running must recover the wake
    # from the already completed canonical observation, without another result.
    recovered = consumer.synchronize_native_feedback(runtime.env, packet=pending)
    assert recovered["released"] == [] and event.read_bytes() != before
    sleeper(600)
    assert now[0] == 0
    consumer.synchronize_native_feedback(runtime.env, packet=completed)
    sleeper(3)
    assert now[0] == 3
