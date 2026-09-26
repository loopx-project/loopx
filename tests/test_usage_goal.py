"""Host observer remains outside execution authority and never waits for HTTP."""
import json
import threading
import time

import pytest

from loopx import usage_goal, usage_ping


def test_telemetry_failure_cannot_replace_host_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_ping, "DEFAULT_RUNTIME_ROOT", tmp_path)
    usage_ping.state_path().write_text(json.dumps({"generation": "fixture", "consent": "enabled"}))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(usage_ping, "_detach", lambda _: (_ for _ in ()).throw(OSError("fixture failure")))
    with pytest.raises(ValueError, match="host failure"):
        with usage_goal.observe_goal_execution(tmp_path, "fixture-goal"):
            raise ValueError("host failure")


def test_disabled_observer_starts_no_worker_or_process(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_ping, "DEFAULT_RUNTIME_ROOT", tmp_path)
    usage_ping.state_path().write_text(json.dumps({"generation": "fixture", "consent": "disabled"}))
    monkeypatch.setattr(threading.Thread, "start", lambda _: pytest.fail("disabled worker"))
    monkeypatch.setattr(usage_ping, "_detach", lambda _: pytest.fail("disabled process"))
    with usage_goal.observe_goal_execution(tmp_path, "fixture-goal"):
        pass


def test_periodic_observation_does_not_need_turn_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_ping, "DEFAULT_RUNTIME_ROOT", tmp_path)
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


def test_real_detached_cycle_and_bound_codex_event_reach_shared_ts_aggregator(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from pathlib import Path
    registry, rollout = _bound_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(usage_ping, "DEFAULT_RUNTIME_ROOT", tmp_path)
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
    end = time.time_ns() // 1_000_000
    with rollout.open("a") as stream:
        stream.write(json.dumps({"type": "event_msg", "timestamp": datetime.now(timezone.utc).isoformat(), "payload": {
            "type": "task_complete", "turn_id": "turn-a", "started_at": datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(),
            "completed_at": datetime.fromtimestamp(end / 1000, timezone.utc).isoformat(),
            "last_agent_message": "PRIVATE CONTENT MUST NOT LEAVE THE SESSION",
        }}) + "\n")
    publish("spend")
    goals = Path(str(usage_ping.state_path()) + ".goals")
    wait_for(lambda: len(json.loads(goals.read_text())["goals"]) == 2)
    preview = usage_ping.control("status")["goal_preview"]
    assert {row["measurement"] for row in preview["counters"]} == {"quota_cycle", "codex_turn"}
    assert all(row["host"] == "codex_app" for row in preview["counters"])
    assert "PRIVATE CONTENT" not in cycle_path.read_text() + goals.read_text()
    assert "thread-a" not in json.dumps(preview)
    usage_ping.control("disable")
    monkeypatch.setattr(usage_ping, "_detach", lambda *a, **k: pytest.fail("disabled observer spawned"))
    publish("start")
    assert not cycle_path.exists() and not goals.exists()
