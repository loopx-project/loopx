"""Host observer remains outside execution authority and never waits for HTTP."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from loopx import usage_goal, usage_ping


@pytest.fixture
def usage_transport(tmp_path, monkeypatch):
    """Own opt-in and detached children through failed assertions as well as success."""
    monkeypatch.setattr(usage_ping, "select_default_runtime_root", lambda: tmp_path)
    for name in ("CI", "DO_NOT_TRACK", "LOOPX_USAGE_PING", "LOOPX_USAGE_POLICY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOOPX_USAGE_PING_ENDPOINT", "http://127.0.0.1:1/v1/ping")
    popen = subprocess.Popen
    children = []

    def spawn(command, *args, **kwargs):
        # Preserve the real detach flags, cwd, stdin and process entry point.
        # Only replace discarded stderr in this explicitly opted-in fixture.
        if kwargs.get("stderr") != subprocess.DEVNULL:
            return popen(command, *args, **kwargs)
        error = tmp_path / f"usage-child-{len(children)}.stderr"
        with error.open("wb") as stream:
            child = popen(command, *args, **{**kwargs, "stderr": stream})
        children.append((child, error))
        return child

    def diagnostics():
        return json.dumps([
            {"returncode": child.poll(), "stderr": error.read_text(errors="replace")[-2048:]}
            for child, error in children
        ])

    monkeypatch.setattr(subprocess, "Popen", spawn)
    try:
        usage_ping.control("enable")
        yield diagnostics
    finally:
        try:
            # The TS consent lock fences later descendants too. Reaping the
            # first Python hop alone would not stop its detached Node sender.
            usage_ping.control("disable")
        finally:
            for child, _ in children:
                try:
                    child.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=4)


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


def test_real_host_call_matches_wall_duration_through_detached_ts(tmp_path, monkeypatch, usage_transport):
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
        pytest.fail(f"real host observation did not reach detached TS: {usage_transport()}")
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


def _bound_cycle_through_detached_ts(tmp_path, monkeypatch, usage_transport, *, header_characters: int = 0, numeric_seconds: bool = False,
                                     caller_relative_paths: bool = False):
    """Drive the real quota observer -> detached TS chain over one bound session.

    `header_characters` widens the session_meta line the way Codex's recorder
    does with base_instructions, so the same entry point covers a header that
    does not fit in one fixed read.  `caller_relative_paths` names the registry
    and runtime root the way an ordinary `loopx --registry .loopx/registry.json`
    call does: relative to the caller, not to the detached child.
    """
    from datetime import datetime, timezone
    registry, rollout = _bound_fixture(tmp_path, monkeypatch)
    if header_characters:
        rollout.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": "thread-a", "base_instructions": {"text": "i" * header_characters}}}) + "\n")
    cycle_path = Path(str(usage_ping.state_path()) + ".cycles")

    def wait_for(predicate, phase):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.03)
        pytest.fail(f"{phase}: detached timing did not reach TS owner: {usage_transport()}")

    now = time.time_ns() // 1_000_000
    registry_path, runtime_root = registry, tmp_path / "runtime"
    if caller_relative_paths:
        # The observer is a caller: its paths are relative to this directory,
        # while the detached child runs in the stable OS temp directory.
        monkeypatch.chdir(tmp_path)
        registry_path, runtime_root = Path("registry.json"), Path("runtime")

    def publish(phase):
        usage_goal.observe_quota_cycle(registry_path=registry_path, runtime_root=runtime_root, goal_id="goal",
                                       agent_id="agent", turn_id="turn-a", phase=phase,
                                       at=time.time_ns() // 1_000_000, host="unknown")
    publish("start")
    wait_for(lambda: bool(json.loads(cycle_path.read_text())["cursors"]), "binding cursor")
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
    wait_for(lambda: len(json.loads(goals.read_text())["goals"]) == 2, "paired observations")
    preview = usage_ping.control("status")["goal_preview"]
    return preview, cycle_path, goals, publish


def test_real_detached_cycle_and_bound_codex_event_reach_shared_ts_aggregator(tmp_path, monkeypatch, usage_transport):
    preview, cycle_path, goals, publish = _bound_cycle_through_detached_ts(tmp_path, monkeypatch, usage_transport)
    assert {row["measurement"] for row in preview["counters"]} == {"quota_cycle", "codex_turn"}
    assert all(row["host"] == "codex_app" for row in preview["counters"])
    assert "PRIVATE CONTENT" not in cycle_path.read_text() + goals.read_text()
    assert "thread-a" not in json.dumps(preview)
    usage_ping.control("disable")
    monkeypatch.setattr(usage_ping, "_detach", lambda *a, **k: pytest.fail("disabled observer spawned"))
    publish("start")
    assert not cycle_path.exists() and not goals.exists()


def test_caller_relative_registry_and_runtime_reach_the_detached_observer(tmp_path, monkeypatch, usage_transport):
    """Leaving the caller's directory must not re-resolve the caller's own paths."""
    preview, _, _, _ = _bound_cycle_through_detached_ts(tmp_path, monkeypatch, usage_transport, caller_relative_paths=True)
    assert {row["measurement"] for row in preview["counters"]} == {"quota_cycle", "codex_turn"}
    assert all(row["host"] == "codex_app" for row in preview["counters"])
    usage_ping.control("disable")


@pytest.mark.parametrize("package_kind", ["namespace", "regular"])
def test_detached_cycle_ignores_packages_in_its_temporary_working_directory(
    tmp_path, monkeypatch, usage_transport, package_kind,
):
    from loopx.cli_commands.opencode2_goal_worker import _materialized_worker_path

    temporary = tmp_path / "detached-cwd"
    temporary.mkdir()
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(temporary))
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))
    # Qualify the installed child entry point without a source-path override or
    # an ambient safe-path setting masking the historical startup failure.
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
    worker = _materialized_worker_path()
    assert worker.parent.parent == temporary / "loopx"
    if package_kind == "regular":
        (temporary / "loopx" / "__init__.py").write_text(
            "raise AssertionError('temporary package must not be imported')\n",
            encoding="utf-8",
        )

    preview, _, _, _ = _bound_cycle_through_detached_ts(tmp_path, monkeypatch, usage_transport)
    assert {row["measurement"] for row in preview["counters"]} == {"quota_cycle", "codex_turn"}
    assert all(row["host"] == "codex_app" for row in preview["counters"])


def test_provider_numeric_timing_reaches_real_detached_installation_aggregator(tmp_path, monkeypatch, usage_transport):
    preview, _, _, _ = _bound_cycle_through_detached_ts(tmp_path, monkeypatch, usage_transport, numeric_seconds=True)
    assert {row["measurement"] for row in preview["counters"]} == {"quota_cycle", "codex_turn"}
    installation = json.loads(Path(str(usage_ping.state_path()) + ".installation").read_text())
    timed = [day for day in installation["days"] if "codex_turn" in day["intervals"]]
    assert len(timed) == 1
    start, end = timed[0]["intervals"]["codex_turn"][0]
    assert end - start == 2000
    assert next(row for row in timed[0]["runtime"] if row["measurement"] == "codex_turn")["observed_minutes"] == 0
    usage_ping.control("disable")


def test_bound_session_with_a_long_metadata_header_still_reaches_ts(tmp_path, monkeypatch, usage_transport):
    """Codex records base_instructions in session_meta; a long header must bind."""
    characters = 72_000
    header = json.dumps({"type": "session_meta", "payload": {
        "id": "thread-a", "base_instructions": {"text": "i" * characters}}}) + "\n"
    assert len(header) > 65536, "fixture header must exceed one fixed read"
    preview, _, _, publish = _bound_cycle_through_detached_ts(
        tmp_path, monkeypatch, usage_transport, header_characters=characters)
    assert {row["measurement"] for row in preview["counters"]} == {"quota_cycle", "codex_turn"}
    assert all(row["host"] == "codex_app" for row in preview["counters"])
    usage_ping.control("disable")
    monkeypatch.setattr(usage_ping, "_detach", lambda *a, **k: pytest.fail("disabled observer spawned"))
    publish("start")


def test_usage_transport_reports_real_child_exit(usage_transport):
    usage_ping._detach({}, command=[sys.executable, "-c",
                                   "import sys; sys.stdin.read(); sys.stderr.write('fixture child failure'); sys.exit(7)"])
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        records = json.loads(usage_transport())
        if records and records[0]["returncode"] is not None:
            break
        time.sleep(0.03)
    assert records == [{"returncode": 7, "stderr": "fixture child failure"}]


def test_failed_observer_test_revokes_opt_in_and_fences_late_dispatch(tmp_path):
    """A failing assertion must not leave a valid generation for a late child."""
    nested = tmp_path / "nested"
    nested.mkdir()
    home = nested / "home"
    home.mkdir()
    request_file = nested / "request.json"
    (nested / "test_failure.py").write_text('''import json
from pathlib import Path
from test_usage_goal import usage_transport
from loopx import usage_ping

def test_failed_assertion(tmp_path, usage_transport):
    path = usage_ping.state_path()
    request = {"path": str(path), "generation": json.loads(path.read_text())["generation"]}
    Path("request.json").write_text(json.dumps(request))
    raise AssertionError("fixture assertion failed")
''')
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
           "PYTHONPATH": os.pathsep.join([str(Path(__file__).parent), str(Path(__file__).parents[1])])}
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-cov", "--confcutdir", str(nested),
                             "test_failure.py"], cwd=nested, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "fixture assertion failed" in result.stdout
    request = json.loads(request_file.read_text())
    path = Path(request["path"])
    state = json.loads(path.read_text())
    assert state["consent"] == "disabled"
    assert state["generation"] != request["generation"]
    request.update({"registry": str(nested / "unused.json"), "runtime": str(nested / "runtime"),
                    "goal": "fixture-goal", "agent": "fixture-agent", "turn": "fixture-turn",
                    "phase": "start", "at": time.time_ns() // 1_000_000, "host": "unknown"})
    late = subprocess.run([sys.executable, "-m", "loopx.usage_goal"], input=json.dumps(request), env=env,
                          capture_output=True, text=True, timeout=8)
    assert late.returncode == 0, late.stderr
    assert json.loads(path.read_text()) == state
    assert not Path(str(path) + ".cycles").exists()
    assert not Path(str(path) + ".goals").exists()
