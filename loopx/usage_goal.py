"""Best-effort Host observation transport; TS owns union, limits and consent.

Not an execution controller. Only confirmed 60-second prefixes survive a crash;
there is no open interval that a later process can extrapolate indefinitely.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from collections.abc import Iterator

from . import usage_ping


@contextmanager
def observe_goal_execution(runtime_root: Path, goal_id: str, *, host: str = "unknown") -> Iterator[None]:
    stop = threading.Event()
    publish = None
    try:
        # This is only a scheduling hint. TS rechecks consent, environment,
        # notice and generation under the same lock used by disable.
        path = usage_ping.state_path()
        state = json.loads(path.read_text())
        generation = state.get("generation")
        if (goal_id and generation and state.get("consent") != "disabled"
                and os.environ.get("LOOPX_USAGE_PING") != "0"
                and os.environ.get("DO_NOT_TRACK") != "1"
                and os.environ.get("CI") != "true"):
            key = hashlib.sha256(json.dumps([generation, str(runtime_root.resolve()), goal_id]).encode()).hexdigest()
            wall = time.time_ns() // 1_000_000
            origin = time.monotonic()
            previous = 0
            lock = threading.Lock()

            def checkpoint() -> None:
                nonlocal previous
                try:
                    if not lock.acquire(blocking=False):
                        return
                    try:
                        elapsed = int((time.monotonic() - origin) * 1000)
                        # Scheduling suspension is not proven execution. Drop a
                        # delayed prefix rather than calling hours asleep work.
                        start = wall + previous
                        previous = elapsed
                        usage_ping._detach(usage_ping._request(
                            "goal", path, generation=generation,
                            observation={"key": key, "start": start, "end": wall + elapsed, "measurement": "host_call", "host": host},
                        ))
                    finally:
                        lock.release()
                except Exception:
                    pass

            def periodically() -> None:
                while not stop.wait(60):
                    checkpoint()

            publish = checkpoint
            worker = threading.Thread(target=periodically, daemon=True, name="loopx-usage-goal")
            worker.start()
    except Exception:
        pass
    try:
        yield
    finally:
        stop.set()
        # No join or HTTP await on the business path. A racing checkpoint is
        # harmless: the TS interval union deduplicates it.
        if publish is not None:
            publish()


def observe_quota_cycle(*, registry_path: Path, runtime_root: Path, goal_id: str,
                        agent_id: str | None, turn_id: str | None, phase: str,
                        at: int, host: str) -> None:
    """Detach binding discovery and session metadata lookup from quota latency."""
    try:
        import sys
        state = json.loads(usage_ping.state_path().read_text())
        if state.get("consent") == "disabled" or not state.get("generation"):
            return
        if os.environ.get("LOOPX_USAGE_PING") == "0" or os.environ.get("DO_NOT_TRACK") == "1" or os.environ.get("CI") == "true":
            return
        usage_ping._detach({"registry": str(registry_path), "runtime": str(runtime_root),
                           "goal": goal_id, "agent": agent_id, "turn": turn_id,
                           "phase": phase, "at": at, "host": host,
                           "path": str(usage_ping.state_path()), "generation": state["generation"]},
                          command=[sys.executable, "-m", "loopx.usage_goal"])
    except Exception:
        pass


def _bound_codex_session(registry_path: Path, goal_id: str, agent_id: str | None):
    """Use accepted exact binding and only the selected Codex home; never infer by cwd."""
    import sqlite3
    from .control_plane.projects.registry_codec import load_registry
    from .registry import find_registry_goal
    from .thread_agent_binding import collect_accepted_bindings, resolve_thread_agent_binding
    goal = find_registry_goal(load_registry(registry_path), goal_id)
    if not goal or not agent_id:
        return None
    bindings = [b for b in collect_accepted_bindings([goal])
                if b["agent_id"] == agent_id and b["host_surface"] in
                {"codex-app", "codex-app-ssh", "codex-cli-tui", "codex-ide-plugin"}]
    current = os.environ.get("CODEX_THREAD_ID")
    if current:
        bindings = [b for b in bindings if b["thread_id"] == current]
    if len(bindings) != 1:
        return None
    binding = bindings[0]
    if resolve_thread_agent_binding(goal, host_surface=binding["host_surface"], thread_id=binding["thread_id"])["status"] != "bound":
        return None
    home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser().resolve()
    # SQLite is a read-only Host metadata adapter, not LoopX state authority.
    for database in sorted(home.glob("state_*.sqlite"), reverse=True)[:4]:
        try:
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as connection:
                row = connection.execute("SELECT rollout_path FROM threads WHERE id = ?", (binding["thread_id"],)).fetchone()
            if row:
                path = Path(row[0]).resolve()
                if any(path.is_relative_to(home / directory) for directory in ("sessions", "archived_sessions")):
                    return {"path": str(path), "id": binding["thread_id"]}, binding["host_surface"]
        except (OSError, ValueError, sqlite3.Error):
            continue
    return None


def _dispatch_cycle(request) -> None:
    path = Path(request["path"])
    state = json.loads(path.read_text())
    if state.get("consent") == "disabled" or state.get("generation") != request["generation"]:
        return
    generation = request["generation"]

    def digest(*parts):
        return hashlib.sha256(json.dumps([generation, *parts]).encode()).hexdigest()

    observation = {"key": digest(str(Path(request["runtime"]).resolve()), request["goal"]),
                   "lane": digest(request["agent"] or "unscoped"),
                   "turn": digest(request["turn"]) if request["turn"] else None,
                   "phase": request["phase"], "at": request["at"], "host": request["host"]}
    try:
        binding = _bound_codex_session(Path(request["registry"]), request["goal"], request["agent"])
        if binding:
            observation["codex"], observation["host"] = binding
    except Exception:
        pass  # Common cycle collection must survive unavailable Host metadata.
    usage_ping._detach(usage_ping._request("cycle", path, generation=generation, observation=observation))


if __name__ == "__main__":
    import sys
    try:
        raw = sys.stdin.buffer.read(8193)
        if len(raw) <= 8192:
            _dispatch_cycle(json.loads(raw))
    except Exception:
        pass  # No private paths or transcript errors on CLI output.
