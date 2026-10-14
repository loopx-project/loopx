"""Stream pytest-owned timings from the controller, including interrupted runs.

This is a CI observation adapter, not a splitter, timing cache or test gate.
Load explicitly with ``-p scripts.ci.pytest_timing``. No configuration or test
selection changes; node ids and phase/outcome values come from pytest itself.
"""
from __future__ import annotations

import json
import hashlib
import math
import re
import time

import pytest
from _pytest.subtests import SubtestReport

from scripts.ci.pytest_failure_context import PREFIX as FAILURE_PREFIX
from scripts.ci.pytest_failure_context import exception_type_name, failure_context


PREFIX = "LOOPX_PYTEST_TIMING "


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if call.excinfo is not None:
        try:
            report.loopx_failure_type = exception_type_name(call.excinfo.type, item.config.rootpath.resolve())
        except Exception:
            # Optional diagnostic metadata never replaces pytest's own report.
            report.loopx_failure_type = None
    return report


def pytest_configure(config):
    # xdist sends TestReports to the controller. Worker output would duplicate
    # reports and can be lost when a worker is killed.
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(TimingObserver(config), "loopx-ci-timing-observer")


class TimingObserver:
    def __init__(self, config):
        self.config = config
        self.started = time.perf_counter()
        self.collection = None

    def emit(self, *, _prefix=PREFIX, **row):
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
    def pytest_sessionstart(self, session):
        self.emit(event="start", version=1)

    def record_collection(self, ids):
        if self.collection is None:
            self.collection = tuple(ids)
            self.emit(event="collection", selected=len(ids))
        elif self.collection != tuple(ids):
            # xdist owns rejecting different worker collections. Preserve the
            # observation without changing that decision or claiming coverage.
            self.emit(event="collection_mismatch")

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids):
        self.record_collection(ids)

    def pytest_collection_finish(self, session):
        if not getattr(self.config.option, "numprocesses", None):
            self.record_collection([item.nodeid for item in session.items])

    def pytest_runtest_logreport(self, report):
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
    def pytest_sessionfinish(self, session, exitstatus):
        self.emit(event="end", exit_code=int(session.exitstatus))
