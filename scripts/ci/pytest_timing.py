"""Stream pytest-owned timings from the controller, including interrupted runs.

This is a CI observation adapter, not a splitter, timing cache or test gate.
Load explicitly with ``-p scripts.ci.pytest_timing``. Optional cooperative
stacks do not change test selection or results; timing values come from pytest.
"""
from __future__ import annotations

from collections import deque
import json
import hashlib
import math
import os
import platform
import re
import signal
import subprocess
import time
import sys
import threading
import _thread
import xml.etree.ElementTree as ET
from collections.abc import Generator, Sequence
from typing import Any, Literal

import pytest
from _pytest.subtests import SubtestReport

from scripts.ci.pytest_failure_context import PREFIX as FAILURE_PREFIX
from scripts.ci.pytest_failure_context import exception_type_name, failure_context
from scripts.ci.shard_budget import (
    DIAGNOSTICS_SCHEMA_VERSION,
    atomic_write_text as _atomic_write_text,
)


PREFIX = "LOOPX_PYTEST_TIMING "
DEADLINE_PREFIX = "LOOPX_PYTEST_DEADLINE "
_DEADLINE_WORKER_EXIT_TIMEOUT = 1.0
_STACK_FD = pytest.StashKey[int]()
# Tests may replace public subprocess helpers. The observer owns this transport;
# subprocess.run would still resolve a test's replacement Popen at call time.
_STACK_POPEN = subprocess.Popen
_PROC_POPEN = subprocess.Popen
_STACK_WRITE_PROGRAM = (
    "import os,sys,threading;"
    "deadline=threading.Timer(0.1,lambda:os._exit(0));"
    "deadline.daemon=True;deadline.start();"
    "os.write(2,sys.stdin.buffer.read())"
)


def _render_junit_xml(xml: Any) -> str | None:
    """Serialize current LogXML testcases without mutating observer state."""
    suite_start = getattr(xml, "suite_start", None)
    stats = getattr(xml, "stats", None)
    node_reporters = getattr(xml, "node_reporters_ordered", None)
    if suite_start is None or not isinstance(stats, dict) or node_reporters is None:
        return None
    duration = suite_start.elapsed()
    numtests = (
        int(stats.get("passed", 0))
        + int(stats.get("failure", 0))
        + int(stats.get("skipped", 0))
        + int(stats.get("error", 0))
        - int(getattr(xml, "cnt_double_fail_tests", 0))
    )
    suite_name = str(getattr(xml, "suite_name", "pytest"))
    suite_node = ET.Element(
        "testsuite",
        name=suite_name,
        errors=str(stats.get("error", 0)),
        failures=str(stats.get("failure", 0)),
        skipped=str(stats.get("skipped", 0)),
        tests=str(numtests),
        time=f"{duration.seconds:.3f}",
        timestamp=suite_start.as_utc().astimezone().isoformat(),
        hostname=platform.node(),
    )
    get_props = getattr(xml, "_get_global_properties_node", None)
    if callable(get_props):
        global_properties = get_props()
        if global_properties is not None:
            suite_node.append(global_properties)
    for node_reporter in node_reporters:
        suite_node.append(node_reporter.to_xml())
    testsuites = ET.Element("testsuites")
    testsuites.set("name", "pytest tests")
    testsuites.append(suite_node)
    return '<?xml version="1.0" encoding="utf-8"?>' + ET.tostring(testsuites, encoding="unicode")


class _JUnitSummaryHook:
    """Preserve pytest's JUnit terminal summary line after an early atomic flush."""

    def __init__(self, logfile: str) -> None:
        self._logfile = logfile

    @pytest.hookimpl
    def pytest_terminal_summary(
        self, terminalreporter: Any, config: pytest.Config,
    ) -> None:
        if config.get_verbosity() >= 0:
            terminalreporter.write_sep("-", f"generated xml file: {self._logfile}")


def _flush_partial_junit(config: pytest.Config) -> bool:
    """Atomically flush partial JUnit XML before worker/fixture teardown blocks."""
    try:
        from _pytest.junitxml import xml_key
    except ImportError:
        return False
    xml = config.stash.get(xml_key, None)
    if xml is None:
        return False
    logfile = getattr(xml, "logfile", None)
    if not isinstance(logfile, str) or not logfile:
        return False
    try:
        rendered = _render_junit_xml(xml)
        if rendered is None:
            return False
        _atomic_write_text(logfile, rendered)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    # Unregister LogXML so its non-atomic open('w') does not re-truncate logfile
    # during teardown, while retaining the terminal summary path line.
    # Also redirect xml.logfile on POSIX in case pytest_sessionfinish already
    # started iterating its hookimpl list before unregister(xml).
    try:
        del config.stash[xml_key]
        config.pluginmanager.unregister(xml)
        if os.name != "nt":
            xml.logfile = os.devnull
        config.pluginmanager.register(_JUnitSummaryHook(logfile), "loopx-ci-junit-summary")
    except Exception:
        pass
    return True


def _bound_xdist_teardown(config: pytest.Config) -> None:
    """Signal active local xdist workers to unwind and cap teardown grace on deadline."""
    dsession = config.pluginmanager.get_plugin("dsession")
    if dsession is None:
        return
    nodemanager = getattr(dsession, "nodemanager", None)
    if nodemanager is None:
        return
    current_timeout = getattr(nodemanager, "EXIT_TIMEOUT", None)
    if isinstance(current_timeout, (int, float)):
        nodemanager.EXIT_TIMEOUT = min(float(current_timeout), _DEADLINE_WORKER_EXIT_TIMEOUT)
    group = getattr(nodemanager, "group", None)
    if group is None or os.name == "nt":
        return
    for gw in list(group):
        popen = getattr(getattr(gw, "_io", None), "popen", None)
        if popen is not None and popen.poll() is None:
            try:
                popen.send_signal(signal.SIGINT)
            except OSError:
                pass


class _DeadlineSessionHook:
    """Flush partial JUnit and bound worker teardown before normal sessionfinish waits."""

    def __init__(self, observer: TimingObserver) -> None:
        self._observer = observer

    @pytest.hookimpl(tryfirst=True)
    def pytest_keyboard_interrupt(self, excinfo: pytest.ExceptionInfo[BaseException]) -> None:
        self._observer.prepare_deadline_sessionfinish()

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        self._observer.prepare_deadline_sessionfinish()


def _sanitize_comm(raw_comm: str) -> str:
    token = raw_comm.strip().split()[0] if raw_comm.strip() else ""
    base = os.path.basename(token)
    cleaned = re.sub(r"[^A-Za-z0-9_.+-]", "_", base)
    return cleaned[:64] or "unknown"


def _collect_descendant_processes(root_pid: int, limit: int = 16) -> list[dict[str, Any]]:
    """Collect bounded public-safe metadata (no argv/paths/env) for descendant processes."""
    if root_pid <= 0 or limit <= 0 or os.name == "nt":
        return []
    children_by_ppid: dict[int, list[int]] = {}
    meta: dict[int, dict[str, Any]] = {}
    helper_pid = -1
    try:
        if os.path.isdir("/proc"):
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                try:
                    with open(f"/proc/{entry}/stat", encoding="utf-8", errors="replace") as handle:
                        stat_text = handle.read().strip()
                except OSError:
                    continue
                rparen = stat_text.rfind(")")
                lparen = stat_text.find("(")
                if lparen < 0 or rparen <= lparen:
                    continue
                pid_part = stat_text[:lparen].strip()
                comm_raw = stat_text[lparen + 1:rparen]
                rest = stat_text[rparen + 1:].split()
                if not pid_part.isdigit() or len(rest) < 2 or not rest[1].lstrip("-").isdigit():
                    continue
                pid = int(pid_part)
                state = re.sub(r"[^A-Za-z0-9+]", "", rest[0])[:16] or "?"
                ppid = int(rest[1])
                meta[pid] = {"pid": pid, "ppid": ppid, "state": state, "comm": _sanitize_comm(comm_raw)}
                children_by_ppid.setdefault(ppid, []).append(pid)
        else:
            proc = _PROC_POPEN(
                ["ps", "-eo", "pid=,ppid=,stat=,comm="],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            helper_pid = proc.pid
            try:
                stdout_bytes, _ = proc.communicate(timeout=0.3)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=0.2)
            for line in stdout_bytes.decode("utf-8", errors="replace").splitlines():
                parts = line.split(None, 3)
                if len(parts) != 4:
                    continue
                pid_str, ppid_str, state_raw, comm_raw = parts
                if not (pid_str.isdigit() and ppid_str.lstrip("-").isdigit()):
                    continue
                pid = int(pid_str)
                if pid == helper_pid:
                    continue
                ppid = int(ppid_str)
                state = re.sub(r"[^A-Za-z0-9+]", "", state_raw)[:16] or "?"
                meta[pid] = {"pid": pid, "ppid": ppid, "state": state, "comm": _sanitize_comm(comm_raw)}
                children_by_ppid.setdefault(ppid, []).append(pid)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []

    descendants: list[dict[str, Any]] = []
    seen = {root_pid}
    queue: deque[int] = deque(sorted(children_by_ppid.get(root_pid, ())))
    while queue and len(descendants) < limit:
        current = queue.popleft()
        if current in seen or current == helper_pid:
            continue
        seen.add(current)
        row = meta.get(current)
        if row is not None:
            descendants.append(row)
        for child_pid in sorted(children_by_ppid.get(current, ())):
            if child_pid not in seen:
                queue.append(child_pid)
    return descendants


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--ci-stall-after", type=float, default=0,
                     help="Print the executing test thread's Python stack after this many seconds; never stop the test.")
    parser.addoption("--ci-session-deadline", type=float, default=0,
                     help="Cooperatively interrupt the session after this many seconds so partial JUnit and diagnostics flush before runner timeout.")
    parser.addoption("--ci-diagnostics-file", type=str, default="",
                     help="Write bounded live and final JSON progress/timeout diagnostics on the controller.")


@pytest.hookimpl(wrapper=True, trylast=True)
def pytest_runtest_protocol(item: pytest.Item) -> Generator[None, object, object]:
    delay = item.config.getoption("ci_stall_after")
    if not delay:
        return (yield)
    owner_thread = threading.get_ident()
    finished = threading.Event()

    def dump() -> None:
        # Strong frame references avoid asynchronous native frame traversal.
        # Only this test's executing thread is inspected; no locals are read.
        frame = sys._current_frames().get(owner_thread)
        rows = ["LOOPX_PYTEST_STACK " + item.nodeid.partition("[")[0][:256]]
        while frame is not None and len(rows) <= 24:
            rows.append(f"  {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}")
            frame = frame.f_back
        del frame
        if not finished.is_set() and (os.path.isdir("/proc") or delay >= 0.05):
            for child in _collect_descendant_processes(os.getpid(), limit=8):
                rows.append(
                    f"  [child pid={child['pid']} ppid={child['ppid']} "
                    f"state={child['state']} comm={child['comm']}]"
                )
        if not finished.is_set():
            try:
                # A duplicate shares stderr's flags: never change them or
                # write a potentially full pipe in this process. The isolated
                # writer has its own exit deadline, even if pytest is killed;
                # The parent also kills/reaps it before releasing this FD.
                with _STACK_POPEN(
                    [sys.executable, "-I", "-S", "-c", _STACK_WRITE_PROGRAM],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=item.config.stash[_STACK_FD],
                ) as writer:
                    try:
                        writer.communicate(
                            ("\n" + "\n".join(rows) + "\n").encode("utf-8", errors="backslashreplace"),
                            timeout=0.5,
                        )
                    finally:
                        # Popen's context manager waits but does not kill on an
                        # IO error. Stop our child before that unconditional reap.
                        if writer.poll() is None:
                            writer.kill()
            except (OSError, subprocess.TimeoutExpired):
                # Optional diagnostics cannot replace pytest's result on IO failure.
                pass

    timer = threading.Timer(delay, dump)
    timer.daemon = True
    timer.start()
    try:
        return (yield)
    finally:
        finished.set()
        timer.cancel()
        timer.join()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: Any) -> Generator[None, Any, Any]:
    report = yield
    if call.excinfo is not None:
        try:
            report.loopx_failure_type = exception_type_name(call.excinfo.type, item.config.rootpath.resolve())
        except Exception:
            # Optional diagnostic metadata never replaces pytest's own report.
            report.loopx_failure_type = None
    return report


def pytest_configure(config: pytest.Config) -> None:
    delay = config.getoption("ci_stall_after")
    if not math.isfinite(delay) or delay < 0:
        raise pytest.UsageError("--ci-stall-after must be finite and nonnegative")
    deadline = config.getoption("ci_session_deadline")
    if not math.isfinite(deadline) or deadline < 0:
        raise pytest.UsageError("--ci-session-deadline must be finite and nonnegative")
    if delay:
        # Capture the original descriptor before per-test fd capture starts,
        # so a blocked worker's diagnostic is visible before it returns.
        # execnet redirects descriptor 2 (and thus sys.__stderr__) to NUL
        # on Windows, preserving the controller's stderr in sys.stderr.
        # pytest has suspended global capture during configuration, so this
        # is the live transport stream rather than a per-test capture file.
        stderr = sys.stderr if os.name == "nt" and hasattr(config, "workerinput") else sys.__stderr__
        if stderr is None:
            raise pytest.UsageError("--ci-stall-after requires an original stderr file descriptor")
        try:
            config.stash[_STACK_FD] = os.dup(stderr.fileno())
        except (AttributeError, OSError, ValueError) as error:
            raise pytest.UsageError("--ci-stall-after requires an original stderr file descriptor") from error
    # xdist sends TestReports to the controller. Worker output would duplicate
    # reports and can be lost when a worker is killed.
    if not hasattr(config, "workerinput"):
        observer = TimingObserver(config)
        config.pluginmanager.register(observer, "loopx-ci-timing-observer")
        if observer.deadline_seconds > 0:
            config.pluginmanager.register(_DeadlineSessionHook(observer), "loopx-ci-deadline-shutdown")


def pytest_unconfigure(config: pytest.Config) -> None:
    descriptor = config.stash.get(_STACK_FD, None)
    if descriptor is not None:
        os.close(descriptor)
        del config.stash[_STACK_FD]


class TimingObserver:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.started = time.perf_counter()
        self.collection: tuple[str, ...] | None = None
        getoption = getattr(config, "getoption", None)
        raw_deadline = getoption("ci_session_deadline") if callable(getoption) else 0.0
        raw_diag = getoption("ci_diagnostics_file") if callable(getoption) else ""
        self.deadline_seconds = float(raw_deadline or 0.0)
        diag_str = str(raw_diag or "").strip()
        self.diagnostics_path = os.path.abspath(diag_str) if diag_str else ""
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._finished = False
        self._deadline_teardown_prepared = False
        self._collection_active = False
        self._loop_active = False
        self._runtestloop_finished = False
        self._deadline_timer: threading.Timer | None = None
        self.deadline_exceeded = False
        self.deadline_elapsed_seconds: float | None = None
        self.deadline_in_flight: list[dict[str, Any]] = []
        self.deadline_children: list[dict[str, Any]] = []
        self.in_flight: dict[str, dict[str, Any]] = {}
        self.cases: dict[str, dict[str, Any]] = {}
        self.phase_seconds = {"setup": 0.0, "call": 0.0, "teardown": 0.0}
        self.finished_cases = 0
        self.invalid_duration_count = 0
        self._last_diag_flush_elapsed = 0.0
        self._last_diag_flush_finished = 0

    @staticmethod
    def _case_identity(nodeid: str) -> tuple[str, str]:
        test_id = hashlib.sha256(nodeid.encode("utf-8", errors="surrogatepass")).hexdigest()
        display = nodeid.partition("[")[0][:256]
        return test_id, display

    def _elapsed(self) -> float:
        return round(time.perf_counter() - self.started, 6)

    def _in_flight_snapshot_locked(self, now_elapsed: float) -> list[dict[str, Any]]:
        rows = [
            {
                "test_id": test_id,
                "display_nodeid": entry["display_nodeid"],
                "started_elapsed_seconds": entry["started_elapsed_seconds"],
                "running_seconds": round(max(0.0, now_elapsed - entry["started_elapsed_seconds"]), 6),
            }
            for test_id, entry in self.in_flight.items()
        ]
        rows.sort(key=lambda row: (-row["running_seconds"], row["test_id"]))
        return rows[:16]

    def _build_diagnostics_locked(
        self,
        *,
        execution_complete: bool,
        exit_code: int | None,
        children: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        now_elapsed = self._elapsed()
        longest = sorted(
            (
                {
                    "test_id": test_id,
                    "display_nodeid": entry["display_nodeid"],
                    "measured_seconds": round(entry["measured_seconds"], 6),
                    "finished": entry["finished"],
                }
                for test_id, entry in self.cases.items()
            ),
            key=lambda row: (-row["measured_seconds"], row["test_id"]),
        )[:25]
        in_flight_rows = (
            list(self.deadline_in_flight)
            if self.deadline_exceeded
            else self._in_flight_snapshot_locked(now_elapsed)
        )
        active_children = children if children is not None else self.deadline_children
        return {
            "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
            "execution_complete": execution_complete,
            "deadline_exceeded": self.deadline_exceeded,
            "deadline_seconds": self.deadline_seconds,
            "deadline_elapsed_seconds": self.deadline_elapsed_seconds,
            "pytest_exit_code": exit_code,
            "selected_cases": len(self.collection) if self.collection is not None else None,
            "observed_cases": len(self.cases),
            "finished_cases": self.finished_cases,
            "in_flight_count": len(self.in_flight),
            "in_flight_cases": in_flight_rows,
            "invalid_duration_count": self.invalid_duration_count,
            "observed_elapsed_seconds": now_elapsed,
            "measured_worker_seconds": round(sum(self.phase_seconds.values()), 6),
            "phase_seconds": {key: round(value, 6) for key, value in self.phase_seconds.items()},
            "longest_cases": longest,
            "active_child_processes": list(active_children[:16]),
        }

    def _write_diagnostics(
        self,
        *,
        execution_complete: bool = False,
        exit_code: int | None = None,
        children: list[dict[str, Any]] | None = None,
        kind: Literal["progress", "deadline", "final"] = "progress",
    ) -> None:
        if not self.diagnostics_path:
            return
        with self._write_lock:
            with self._lock:
                if kind == "progress" and (self._finished or self.deadline_exceeded):
                    return
                if kind == "deadline" and self._finished:
                    return
                payload = self._build_diagnostics_locked(
                    execution_complete=execution_complete,
                    exit_code=exit_code,
                    children=children,
                )
                self._last_diag_flush_elapsed = payload["observed_elapsed_seconds"]
                self._last_diag_flush_finished = self.finished_cases
            try:
                content = json.dumps(payload, ensure_ascii=True, allow_nan=False, indent=2) + "\n"
                _atomic_write_text(self.diagnostics_path, content)
            except OSError:
                # Optional diagnostics cannot mask test outcomes.
                pass

    def _interrupt_main_locked(self) -> None:
        # _thread.interrupt_main() does not interrupt POSIX sleep/wait syscalls;
        # send SIGINT directly to the main thread when the default handler is active.
        main_ident = threading.main_thread().ident
        if (
            os.name != "nt"
            and main_ident is not None
            and hasattr(signal, "pthread_kill")
            and signal.getsignal(signal.SIGINT) is signal.default_int_handler
        ):
            try:
                signal.pthread_kill(main_ident, signal.SIGINT)
                return
            except OSError:
                pass
        _thread.interrupt_main()

    def prepare_deadline_sessionfinish(self) -> None:
        with self._lock:
            self._finished = True
            self._collection_active = False
            self._loop_active = False
            if not self.deadline_exceeded or self._deadline_teardown_prepared:
                return
            self._deadline_teardown_prepared = True
        _flush_partial_junit(self.config)
        _bound_xdist_teardown(self.config)

    def _record_deadline(self, session: pytest.Session, *, interrupt: bool) -> None:
        with self._lock:
            if self._finished or self._runtestloop_finished or self.deadline_exceeded:
                return
        children = _collect_descendant_processes(os.getpid(), limit=16)
        reason = f"CI session deadline exceeded ({self.deadline_seconds:g}s)"
        with self._lock:
            if self._finished or self._runtestloop_finished or self.deadline_exceeded:
                return
            now_elapsed = self._elapsed()
            self.deadline_exceeded = True
            self.deadline_elapsed_seconds = now_elapsed
            self.deadline_in_flight = self._in_flight_snapshot_locked(now_elapsed)
            self.deadline_children = children
            session.shouldstop = reason
            dsession = self.config.pluginmanager.get_plugin("dsession")
            if dsession is not None and hasattr(dsession, "shuttingdown") and not getattr(dsession, "shouldstop", False):
                dsession.shouldstop = reason
        self._write_diagnostics(execution_complete=False, exit_code=None, children=children, kind="deadline")
        if interrupt:
            with self._lock:
                if not self._finished and (self._collection_active or self._loop_active):
                    # Wake collection or pytest_runtestloop immediately even when blocked in IO/sleep.
                    self._interrupt_main_locked()

    def _on_deadline(self, session: pytest.Session) -> None:
        self._record_deadline(session, interrupt=True)

    def _abort_if_deadline_exceeded(self, session: pytest.Session) -> None:
        if self.deadline_seconds <= 0:
            return
        with self._lock:
            exceeded = self.deadline_exceeded or self._elapsed() >= self.deadline_seconds
        if not exceeded:
            return
        self._record_deadline(session, interrupt=False)
        reason = (
            session.shouldstop
            if isinstance(session.shouldstop, str) and session.shouldstop
            else f"CI session deadline exceeded ({self.deadline_seconds:g}s)"
        )
        raise pytest.Session.Interrupted(reason)

    def emit(self, *, _prefix: str = PREFIX, **row: Any) -> None:
        row["elapsed_seconds"] = self._elapsed()
        line = _prefix + json.dumps(row, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        reporter = self.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.ensure_newline()
            # Quiet progress writes directly to the terminal writer without
            # currentfspath, so ensure_newline alone cannot end that line.
            if reporter._tw.width_of_current_line:
                reporter._tw.line()
            reporter.write_line(line)
            reporter._tw.flush()

    @pytest.hookimpl(trylast=True)
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.emit(event="start", version=1)
        if self.deadline_seconds > 0:
            remaining = max(0.0, self.deadline_seconds - self._elapsed())
            timer = threading.Timer(remaining, self._on_deadline, args=(session,))
            timer.daemon = True
            with self._lock:
                self._deadline_timer = timer
            timer.start()

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_collection(self, session: pytest.Session) -> Generator[None, object, object]:
        if self.deadline_seconds <= 0:
            return (yield)
        self._abort_if_deadline_exceeded(session)
        with self._lock:
            self._collection_active = True
        try:
            return (yield)
        finally:
            with self._lock:
                self._collection_active = False

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtestloop(self, session: pytest.Session) -> Generator[None, object, object]:
        if self.deadline_seconds <= 0:
            return (yield)
        self._abort_if_deadline_exceeded(session)
        with self._lock:
            self._loop_active = True
        completed_normally = False
        try:
            result = yield
            completed_normally = True
            return result
        finally:
            with self._lock:
                self._loop_active = False
                if completed_normally and not self.deadline_exceeded:
                    self._runtestloop_finished = True

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> Generator[None, object, object]:
        if self.deadline_seconds <= 0:
            return (yield)
        self._abort_if_deadline_exceeded(item.session)
        try:
            return (yield)
        finally:
            if self.deadline_exceeded and not item.session.shouldstop:
                item.session.shouldstop = f"CI session deadline exceeded ({self.deadline_seconds:g}s)"

    def record_collection(self, ids: Sequence[str]) -> None:
        if self.collection is None:
            self.collection = tuple(ids)
            self.emit(event="collection", selected=len(ids))
            self._write_diagnostics(execution_complete=False, exit_code=None)
        elif self.collection != tuple(ids):
            # xdist owns rejecting different worker collections. Preserve the
            # observation without changing that decision or claiming coverage.
            self.emit(event="collection_mismatch")

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node: Any, ids: Sequence[str]) -> None:
        self.record_collection(ids)

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        if not getattr(self.config.option, "numprocesses", None):
            self.record_collection([item.nodeid for item in session.items])

    def pytest_runtest_logstart(self, nodeid: str, location: tuple[str, int | None, str]) -> None:
        test_id, display = self._case_identity(nodeid)
        with self._lock:
            if test_id not in self.in_flight:
                self.in_flight[test_id] = {
                    "display_nodeid": display,
                    "started_elapsed_seconds": self._elapsed(),
                }

    def pytest_runtest_logfinish(self, nodeid: str, location: tuple[str, int | None, str]) -> None:
        test_id, _ = self._case_identity(nodeid)
        with self._lock:
            self.in_flight.pop(test_id, None)

    def pytest_runtest_logreport(self, report: Any) -> None:
        test_id, display = self._case_identity(report.nodeid)
        if report.failed:
            try:
                self.emit(_prefix=FAILURE_PREFIX, version=1, test_id=test_id,
                          display_nodeid=display if re.fullmatch(r"[A-Za-z0-9_./:-]+", display) else None,
                          phase=report.when, subtest=isinstance(report, SubtestReport),
                          **failure_context(report, self.config.rootpath.resolve()))
            except Exception:
                # Do not mask the original failure, halt remaining tests or
                # lose timing/JUnit if optional context construction/IO fails.
                pass
        # A subtest is nested within its collected item's call phase. Its
        # duration is already included there; it is not another selected item
        # or another call phase. Keep pytest's own subtest result reporting.
        if isinstance(report, SubtestReport):
            return
        duration = report.duration
        valid_duration = duration if math.isfinite(duration) and duration >= 0 else None
        should_flush_diag = False
        now_elapsed = self._elapsed()
        with self._lock:
            if report.when == "setup" and test_id not in self.in_flight:
                self.in_flight[test_id] = {
                    "display_nodeid": display,
                    "started_elapsed_seconds": now_elapsed,
                }
            entry = self.cases.get(test_id)
            if entry is None:
                entry = {"display_nodeid": display, "phases": {}, "measured_seconds": 0.0, "finished": False}
                self.cases[test_id] = entry
            if report.when in self.phase_seconds and report.when not in entry["phases"]:
                entry["phases"][report.when] = report.outcome
                if valid_duration is None:
                    self.invalid_duration_count += 1
                else:
                    entry["measured_seconds"] += valid_duration
                    self.phase_seconds[report.when] += valid_duration
            if report.when == "teardown":
                self.in_flight.pop(test_id, None)
                if not entry["finished"] and ("call" in entry["phases"] or entry["phases"].get("setup") != "passed"):
                    entry["finished"] = True
                    self.finished_cases += 1
            if self.diagnostics_path and (
                self.finished_cases - self._last_diag_flush_finished >= 100
                or now_elapsed - self._last_diag_flush_elapsed >= 15.0
            ):
                should_flush_diag = True

        # Parameter payloads can be huge or trigger hosted secret masking,
        # corrupting JSON. Hash full identity; display only the parent name.
        self.emit(event="phase", test_id=test_id,
                  display_nodeid=display, phase=report.when,
                  outcome=report.outcome,
                  seconds=valid_duration)
        if should_flush_diag:
            self._write_diagnostics(execution_complete=False, exit_code=None)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        timer: threading.Timer | None = None
        with self._lock:
            self._finished = True
            self._collection_active = False
            self._loop_active = False
            timer = self._deadline_timer
            self._deadline_timer = None
        if timer is not None:
            timer.cancel()
            timer.join()

        if self.deadline_exceeded:
            session.exitstatus = pytest.ExitCode.INTERRUPTED
            self.emit(
                _prefix=DEADLINE_PREFIX,
                version=1,
                deadline_seconds=self.deadline_seconds,
                deadline_elapsed_seconds=self.deadline_elapsed_seconds,
                selected_cases=len(self.collection) if self.collection is not None else None,
                finished_cases=self.finished_cases,
                in_flight_cases=self.deadline_in_flight,
                active_child_processes=self.deadline_children,
            )

        final_exit = int(session.exitstatus)
        selected = len(self.collection) if self.collection is not None else None
        complete = (
            not self.deadline_exceeded
            and final_exit in (0, 1)
            and selected is not None
            and selected > 0
            and self.finished_cases == selected
            and self.invalid_duration_count == 0
        )
        self.emit(event="end", exit_code=final_exit)
        self._write_diagnostics(execution_complete=complete, exit_code=final_exit, kind="final")
