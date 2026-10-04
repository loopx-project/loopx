"""Host observer remains outside execution authority and never waits for HTTP."""
import json
import threading
import time

import pytest

from loopx import usage_goal, usage_ping


def test_telemetry_failure_cannot_replace_host_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_ping, "select_default_runtime_root", lambda: tmp_path)
    usage_ping.state_path().write_text(json.dumps({"generation": "fixture", "consent": "enabled"}))
    for name in ("CI", "DO_NOT_TRACK", "LOOPX_USAGE_PING"):
        monkeypatch.delenv(name, raising=False)
    attempts = []

    def fail_transport(request):
        attempts.append(request)
        raise OSError("fixture failure")

    monkeypatch.setattr(usage_ping, "_detach", fail_transport)
    with pytest.raises(ValueError, match="host failure"):
        with usage_goal.observe_goal_execution(tmp_path, "fixture-goal"):
            raise ValueError("host failure")
    assert attempts, "the test must exercise transport failure, not an environment opt-out"


def test_disabled_observer_starts_no_worker_or_process(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_ping, "select_default_runtime_root", lambda: tmp_path)
    usage_ping.state_path().write_text(json.dumps({"generation": "fixture", "consent": "disabled"}))
    monkeypatch.setattr(threading.Thread, "start", lambda _: pytest.fail("disabled worker"))
    monkeypatch.setattr(usage_ping, "_detach", lambda _: pytest.fail("disabled process"))
    with usage_goal.observe_goal_execution(tmp_path, "fixture-goal"):
        pass


def test_periodic_observation_does_not_need_turn_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_ping, "select_default_runtime_root", lambda: tmp_path)
    usage_ping.state_path().write_text(json.dumps({"generation": "fixture", "consent": "enabled"}))
    for name in ("CI", "DO_NOT_TRACK", "LOOPX_USAGE_PING"):
        monkeypatch.delenv(name, raising=False)
    observed = []
    monkeypatch.setattr(usage_ping, "_detach", observed.append)
    # Accelerate only the observer's checkpoint interval, not clocks or threads.
    real_event = threading.Event
    from types import SimpleNamespace

    class CheckpointEvent:
        def __init__(self):
            self.event = real_event()

        def wait(self, seconds):
            return self.event.wait(0.01)

        def set(self):
            self.event.set()

    monkeypatch.setattr(usage_goal, "threading", SimpleNamespace(Event=CheckpointEvent, Lock=threading.Lock, Thread=threading.Thread))
    with usage_goal.observe_goal_execution(tmp_path, "private-goal"):
        deadline = time.monotonic() + 1
        while not observed and time.monotonic() < deadline:
            time.sleep(0.01)
        assert observed, "unfinished Host should checkpoint"
    assert all(row["observation"]["start"] <= row["observation"]["end"] for row in observed)
    assert "private-goal" not in json.dumps(observed)
    assert str(tmp_path) not in json.dumps([row["observation"] for row in observed])


def test_real_host_call_matches_wall_duration_through_detached_ts(tmp_path, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path
    monkeypatch.setattr(usage_ping, "select_default_runtime_root", lambda: tmp_path)
    for name in ("CI", "DO_NOT_TRACK", "LOOPX_USAGE_PING", "LOOPX_USAGE_POLICY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOOPX_USAGE_PING_ENDPOINT", "http://127.0.0.1:1/v1/ping")
    usage_ping.control("enable")
    before = time.monotonic_ns()
    with usage_goal.observe_goal_execution(tmp_path / "runtime", "fixture-goal", host="generic-cli"):
        child_start = time.time_ns() // 1_000_000
        subprocess.run([sys.executable, "-c", "import time; time.sleep(0.25)"], check=True)
        child_end = time.time_ns() // 1_000_000
    duration = (time.monotonic_ns() - before) // 1_000_000
    goals = Path(str(usage_ping.state_path()) + ".goals")
    installation_path = Path(str(usage_ping.state_path()) + ".installation")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if goals.exists() and installation_path.exists():
            state = json.loads(goals.read_text())
            installation = json.loads(installation_path.read_text())
            if state["goals"] and installation["days"][0]["runtime"]:
                break
        time.sleep(0.03)
    else:
        pytest.fail("real host observation did not reach detached TS")
    intervals = state["goals"][0]["intervals"]
    measured = sum(end - start for start, end in intervals)
    assert state["goals"][0]["measurement"] == "host_call"
    assert 250 <= measured <= duration + 1
    # The wall origin and monotonic elapsed duration are each floored to
    # milliseconds; their sum can be one millisecond below floor(wall end).
    assert intervals[0][0] <= child_start and intervals[-1][1] + 1 >= child_end
    assert installation["days"][0]["runtime"] == [{"measurement": "host_call", "observed_minutes": 0}]
    usage_ping.control("disable")


def _bound_fixture(tmp_path, monkeypatch):
    import sqlite3
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    rollout = sessions / "bound.jsonl"
    rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread-a"}}) + "\n")
    database = home / "state_5.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE threads(id TEXT, rollout_path TEXT)")
        conn.execute("INSERT INTO threads VALUES (?, ?)", ("thread-a", str(rollout)))
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": "0.1", "goals": [{"id": "goal", "coordination": {
        "registered_agents": ["agent"], "thread_agent_bindings": [
            {"thread_id": "thread-a", "host_surface": "codex-app", "agent_id": "agent"},
        ],
    }}]}))
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    return registry, rollout


def test_binding_discovery_uses_exact_agent_thread_and_selected_home_only(tmp_path, monkeypatch):
    registry, rollout = _bound_fixture(tmp_path, monkeypatch)
    assert usage_goal._bound_codex_session(registry, "goal", "agent") == (
        {"path": str(rollout), "id": "thread-a"}, "codex-app")
    assert usage_goal._bound_codex_session(registry, "goal", "other") is None
    monkeypatch.setenv("CODEX_THREAD_ID", "unbound-thread")
    assert usage_goal._bound_codex_session(registry, "goal", "agent") is None
    monkeypatch.delenv("CODEX_THREAD_ID")
    doc = json.loads(registry.read_text())
    doc["goals"][0]["coordination"]["thread_agent_bindings"].append(
        {"thread_id": "thread-b", "host_surface": "codex-app", "agent_id": "agent"})
    registry.write_text(json.dumps(doc))
    assert usage_goal._bound_codex_session(registry, "goal", "agent") is None
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-a")
    assert usage_goal._bound_codex_session(registry, "goal", "agent") is not None
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "other-home"))
    assert usage_goal._bound_codex_session(registry, "goal", "agent") is None


def test_metadata_cannot_redirect_timing_read_outside_selected_home(tmp_path, monkeypatch):
    import sqlite3
    registry, rollout = _bound_fixture(tmp_path, monkeypatch)
    outside = tmp_path / "outside.jsonl"
    outside.write_text(rollout.read_text())
    with sqlite3.connect(rollout.parent.parent / "state_5.sqlite") as conn:
        conn.execute("UPDATE threads SET rollout_path = ?", (str(outside),))
    assert usage_goal._bound_codex_session(registry, "goal", "agent") is None


def _bound_cycle_through_detached_ts(tmp_path, monkeypatch, *, header_characters: int = 0, numeric_seconds: bool = False):
    """Drive the real quota observer -> detached TS chain over one bound session.

    `header_characters` widens the session_meta line the way Codex's recorder
    does with base_instructions, so the same entry point covers a header that
    does not fit in one fixed read.
    """
    from datetime import datetime, timezone
    from pathlib import Path
    registry, rollout = _bound_fixture(tmp_path, monkeypatch)
    if header_characters:
        rollout.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": "thread-a", "base_instructions": {"text": "i" * header_characters}}}) + "\n")
    monkeypatch.setattr(usage_ping, "select_default_runtime_root", lambda: tmp_path)
    for name in ("CI", "DO_NOT_TRACK", "LOOPX_USAGE_PING", "LOOPX_USAGE_POLICY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOOPX_USAGE_PING_ENDPOINT", "http://127.0.0.1:1/v1/ping")
    usage_ping.control("enable")
    cycle_path = Path(str(usage_ping.state_path()) + ".cycles")

    def wait_for(predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.03)
        pytest.fail("detached timing did not reach TS owner")

    now = time.time_ns() // 1_000_000
    def publish(phase):
        usage_goal.observe_quota_cycle(registry_path=registry, runtime_root=tmp_path / "runtime", goal_id="goal",
                                       agent_id="agent", turn_id="turn-a", phase=phase,
                                       at=time.time_ns() // 1_000_000, host="unknown")
    publish("start")
    wait_for(lambda: bool(json.loads(cycle_path.read_text())["cursors"]))
    if numeric_seconds:
        # Wait for binding before choosing a whole-second provider interval.
        # Slow subprocess startup must not put its start before the cursor's
        # consent boundary and legitimately clip the expected two seconds.
        now = (time.time_ns() // 1_000_000_000 + 1) * 1000
        time.sleep(max(0, (now + 2000 - time.time_ns() // 1_000_000) / 1000))
    end = time.time_ns() // 1_000_000
    with rollout.open("a") as stream:
        stream.write(json.dumps({"type": "event_msg", "timestamp": datetime.now(timezone.utc).isoformat(), "payload": {
            "type": "task_complete", "turn_id": "turn-a", "started_at": now / 1000 if numeric_seconds else datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(),
            "completed_at": (now + 2000) / 1000 if numeric_seconds else datetime.fromtimestamp(end / 1000, timezone.utc).isoformat(),
            "last_agent_message": "PRIVATE CONTENT MUST NOT LEAVE THE SESSION",
        }}) + "\n")
    publish("spend")
    goals = Path(str(usage_ping.state_path()) + ".goals")
    wait_for(lambda: len(json.loads(goals.read_text())["goals"]) == 2)
    preview = usage_ping.control("status")["goal_preview"]
    return preview, cycle_path, goals, publish


def test_real_detached_cycle_and_bound_codex_event_reach_shared_ts_aggregator(tmp_path, monkeypatch):
    preview, cycle_path, goals, publish = _bound_cycle_through_detached_ts(tmp_path, monkeypatch)
    assert {row["measurement"] for row in preview["counters"]} == {"quota_cycle", "codex_turn"}
    assert all(row["host"] == "codex_app" for row in preview["counters"])
    assert "PRIVATE CONTENT" not in cycle_path.read_text() + goals.read_text()
    assert "thread-a" not in json.dumps(preview)
    usage_ping.control("disable")
    monkeypatch.setattr(usage_ping, "_detach", lambda *a, **k: pytest.fail("disabled observer spawned"))
    publish("start")
    assert not cycle_path.exists() and not goals.exists()


def test_provider_numeric_timing_reaches_real_detached_installation_aggregator(tmp_path, monkeypatch):
    from pathlib import Path
    preview, _, _, _ = _bound_cycle_through_detached_ts(tmp_path, monkeypatch, numeric_seconds=True)
    assert {row["measurement"] for row in preview["counters"]} == {"quota_cycle", "codex_turn"}
    installation = json.loads(Path(str(usage_ping.state_path()) + ".installation").read_text())
    timed = [day for day in installation["days"] if "codex_turn" in day["intervals"]]
    assert len(timed) == 1
    start, end = timed[0]["intervals"]["codex_turn"][0]
    assert end - start == 2000
    assert next(row for row in timed[0]["runtime"] if row["measurement"] == "codex_turn")["observed_minutes"] == 0
    usage_ping.control("disable")


def test_bound_session_with_a_long_metadata_header_still_reaches_ts(tmp_path, monkeypatch):
    """Codex records base_instructions in session_meta; a long header must bind."""
    characters = 72_000
    header = json.dumps({"type": "session_meta", "payload": {
        "id": "thread-a", "base_instructions": {"text": "i" * characters}}}) + "\n"
    assert len(header) > 65536, "fixture header must exceed one fixed read"
    preview, _, _, publish = _bound_cycle_through_detached_ts(
        tmp_path, monkeypatch, header_characters=characters)
    assert {row["measurement"] for row in preview["counters"]} == {"quota_cycle", "codex_turn"}
    assert all(row["host"] == "codex_app" for row in preview["counters"])
    usage_ping.control("disable")
    monkeypatch.setattr(usage_ping, "_detach", lambda *a, **k: pytest.fail("disabled observer spawned"))
    publish("start")
