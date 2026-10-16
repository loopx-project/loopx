"""Stream pytest-owned timings from the controller, including interrupted runs.

This is a CI observation adapter, not a splitter, timing cache or test gate.
Load explicitly with ``-p scripts.ci.pytest_timing``. Optional cooperative
stacks do not change test selection or results; timing values come from pytest.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import subprocess
import time
import sys
import threading
from collections.abc import Generator, Sequence
from typing import Any

import pytest
from _pytest.subtests import SubtestReport

from scripts.ci.pytest_failure_context import PREFIX as FAILURE_PREFIX
from scripts.ci.pytest_failure_context import exception_type_name, failure_context


PREFIX = "LOOPX_PYTEST_TIMING "
_STACK_FD = pytest.StashKey[int]()
# Tests may replace public subprocess helpers. The observer owns this transport;
# subprocess.run would still resolve a test's replacement Popen at call time.
_STACK_POPEN = subprocess.Popen
_STACK_WRITE_PROGRAM = (
    "import os,sys,threading;"
    "deadline=threading.Timer(0.1,lambda:os._exit(0));"
    "deadline.daemon=True;deadline.start();"
    "os.write(2,sys.stdin.buffer.read())"
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--ci-stall-after", type=float, default=0,
                     help="Print the executing test thread's Python stack after this many seconds; never stop the test.")


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
    if delay:
        # Capture the original descriptor before per-test fd capture starts,
        # so a blocked worker's diagnostic is visible before it returns.
        if sys.__stderr__ is None:
            raise pytest.UsageError("--ci-stall-after requires an original stderr file descriptor")
        try:
            config.stash[_STACK_FD] = os.dup(sys.__stderr__.fileno())
        except (AttributeError, OSError, ValueError) as error:
            raise pytest.UsageError("--ci-stall-after requires an original stderr file descriptor") from error
    # xdist sends TestReports to the controller. Worker output would duplicate
    # reports and can be lost when a worker is killed.
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(TimingObserver(config), "loopx-ci-timing-observer")


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

    def emit(self, *, _prefix: str = PREFIX, **row: Any) -> None:
        row["elapsed_seconds"] = round(time.perf_counter() - self.started, 6)
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

    def record_collection(self, ids: Sequence[str]) -> None:
        if self.collection is None:
            self.collection = tuple(ids)
            self.emit(event="collection", selected=len(ids))
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

    def pytest_runtest_logreport(self, report: Any) -> None:
        test_id = hashlib.sha256(report.nodeid.encode("utf-8", errors="surrogatepass")).hexdigest()
        display = report.nodeid.partition("[")[0][:256]
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
        # Parameter payloads can be huge or trigger hosted secret masking,
        # corrupting JSON. Hash full identity; display only the parent name.
        self.emit(event="phase", test_id=test_id,
                  display_nodeid=display, phase=report.when,
                  outcome=report.outcome,
                  seconds=duration if math.isfinite(duration) and duration >= 0 else None)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        self.emit(event="end", exit_code=int(session.exitstatus))
