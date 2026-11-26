"""Real pytest observations survive failure/interruption without qualifying CI."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]
READER = ROOT / "scripts/ci/pytest_timing.ts"
PREFIX = "LOOPX_PYTEST_TIMING "


def environment():
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PYTEST", "COV_CORE", "COVERAGE"))}
    env["PYTHONPATH"] = str(ROOT)
    return env


def read_log(path, *, success=True):
    result = subprocess.run(["node", str(READER), str(path)], capture_output=True,
                            text=True, timeout=10, check=False)
    assert (result.returncode == 0) == success, result.stderr
    return json.loads(result.stdout) if success else result.stderr


@pytest.mark.parametrize("verbosity", ["-v", "-q"])
@pytest.mark.parametrize("workers", [0, 2])
@pytest.mark.parametrize("stack_timeout", [0, 0.01], ids=["no-stack", "stack"])
def test_real_xdist_timings_match_native_pytest_owner(tmp_path, verbosity, workers, stack_timeout):
    pass_duration = 0.25 if stack_timeout else 0.03
    (tmp_path / "test_subject.py").write_text(
        "import time, pytest\n"
        f"def test_pass(): time.sleep({pass_duration})\n"
        "def test_fail(): assert False\n"
        "@pytest.mark.skip(reason='fixture')\n"
        "def test_skip(): pass\n"
        "@pytest.fixture\n"
        "def bad(): raise ValueError('fixture')\n"
        "def test_setup_error(bad): pass\n"
        "import unittest\n"
        "class TestNested(unittest.TestCase):\n"
        "    def test_passed_subtests(self):\n"
        "        for value in (1, 2):\n"
        "            with self.subTest(value=value): self.assertGreater(value, 0)\n"
        "    def test_failed_subtests(self):\n"
        "        for value in (1, -1):\n"
        "            with self.subTest(value=value): self.assertGreater(value, 0)\n",
        encoding="utf-8")
    log = tmp_path / "job.log"
    with log.open("w") as output:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", verbosity, "-n", str(workers),
             "--ci-stall-after", str(stack_timeout),
             "-p", "scripts.ci.pytest_timing", "--store-durations", "--clean-durations",
             "--durations-path", "native.json", "--junitxml=junit.xml"],
            cwd=tmp_path, env=environment(), stdout=output, stderr=subprocess.STDOUT,
            timeout=30, check=False)
    assert result.returncode == 1
    assert ("LOOPX_PYTEST_STACK " in log.read_text()) == bool(stack_timeout)
    assert all(line.startswith(PREFIX) for line in log.read_text().splitlines()
               if PREFIX in line)
    report = read_log(log)
    # A completed traversal with test failures is not successful qualification.
    assert report["execution_complete"] is True
    assert report["pytest_exit_code"] == 1
    assert report["selected_cases"] == report["finished_cases"] == 6
    assert all(case["finished"] for case in report["longest_cases"])
    assert report["invalid_duration_count"] == 0
    native = json.loads((tmp_path / "native.json").read_text())
    assert report["measured_worker_seconds"] == pytest.approx(sum(native.values()), abs=1e-6)
    assert report["measured_worker_seconds"] >= 0.03
    # Actual GitHub timestamps/ANSI and CRLF do not change the observation.
    lines = log.read_text().splitlines()
    timestamped = tmp_path / "hosted-format.log"
    timestamped.write_bytes("\r\n".join(
        "2026-01-01T00:00:00.0000000Z \x1b[32m" + line + "\x1b[0m"
        for line in lines).encode())
    assert read_log(timestamped) == report
    # GitHub concatenates log chunks with a BOM before some timestamps.
    # It is transport framing; the observation and its strict ordering match.
    chunked = tmp_path / "hosted-chunks.log"
    chunked.write_text("\n".join(
        ("\ufeff" if index % 3 == 0 else "") + line
        for index, line in enumerate(timestamped.read_text().splitlines())), encoding="utf-8")
    assert read_log(chunked) == report


def test_real_pytest_subtest_cost_is_included_once_in_parent_call(tmp_path):
    (tmp_path / "test_subject.py").write_text(
        "import time, pytest\n"
        "@pytest.mark.parametrize('fail', [False, True])\n"
        "def test_nested(subtests, fail):\n"
        "    for value in (1, 2):\n"
        "        with subtests.test(value=value):\n"
        "            time.sleep(0.02)\n"
        "            assert not (fail and value == 2)\n", encoding="utf-8")
    # Independent native serialization records both parent and nested reports.
    # SubtestReport supplies its own discriminator; no observer output is the oracle.
    (tmp_path / "native_reports.py").write_text(
        "import json\nfrom pathlib import Path\nreports = []\n"
        "def pytest_runtest_logreport(report):\n"
        "    reports.append({key: value for key, value in report._to_json().items()\n"
        "                    if key in ('duration', 'when', 'outcome', '_report_type')})\n"
        "def pytest_sessionfinish(session):\n"
        "    if not hasattr(session.config, 'workerinput'):\n"
        "        Path('native-reports.json').write_text(json.dumps(reports))\n",
        encoding="utf-8")
    log = tmp_path / "subtests.log"
    env = environment()
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT), str(tmp_path)))
    with log.open("w") as output:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "-n", "2", "-p", "native_reports",
             "-p", "scripts.ci.pytest_timing"], cwd=tmp_path, env=env,
            stdout=output, stderr=subprocess.STDOUT, timeout=30, check=False)
    assert result.returncode == 1
    native = json.loads((tmp_path / "native-reports.json").read_text())
    nested = [row for row in native if row.get("_report_type") == "SubTestReport"]
    parents = [row for row in native if "_report_type" not in row]
    assert len(nested) == 4 and sum(row["duration"] for row in nested) >= 0.08
    assert len(parents) == 6  # three phases for each of two collected items
    report = read_log(log)
    assert report["execution_complete"] and report["pytest_exit_code"] == 1
    assert report["selected_cases"] == report["finished_cases"] == 2
    assert report["measured_worker_seconds"] == pytest.approx(
        sum(row["duration"] for row in parents), abs=1e-6)


@pytest.mark.parametrize("delay", ["-1", "nan", "inf", "-inf"])
def test_stall_snapshot_rejects_invalid_delay_before_running_tests(tmp_path, delay):
    (tmp_path / "test_subject.py").write_text(
        "from pathlib import Path\n"
        "def test_unreachable(): Path('ran').touch()\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "scripts.ci.pytest_timing",
         "--ci-stall-after=" + delay], cwd=tmp_path, env=environment(),
        capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 4
    assert "must be finite and nonnegative" in result.stderr
    assert not (tmp_path / "ran").exists()


@pytest.mark.parametrize("stderr", ["None", "io.StringIO()"])
@pytest.mark.parametrize("enabled", [False, True])
def test_stall_snapshot_requires_original_descriptor_only_when_enabled(tmp_path, stderr, enabled):
    (tmp_path / "test_subject.py").write_text(
        "from pathlib import Path\n"
        "def test_example(): Path('ran').touch()\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", "import io, sys, pytest; sys.__stderr__ = " + stderr
         + "; raise SystemExit(pytest.main(sys.argv[1:]))", "-q",
         "-p", "scripts.ci.pytest_timing", "--ci-stall-after=" + str(int(enabled))],
        cwd=tmp_path, env=environment(), capture_output=True, text=True,
        timeout=10, check=False)
    assert result.returncode == (4 if enabled else 0)
    assert (tmp_path / "ran").exists() == (not enabled)
    if enabled:
        assert "requires an original stderr file descriptor" in result.stderr


@pytest.mark.parametrize("workers", [0, 2])
@pytest.mark.parametrize("communication_failure", [False, pytest.param(
    True, marks=pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cleanup of a live fault child"),
)], ids=["spawn", "communication"])
def test_stall_snapshot_output_failure_preserves_native_test_results(tmp_path, workers, communication_failure):
    (tmp_path / "conftest.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "from scripts.ci import pytest_timing as observer\n"
        "original_popen = observer._STACK_POPEN\nwriters = []\n"
        "def broken_snapshot(command, *args, **kwargs):\n"
        "    if command[:3] == [sys.executable, '-I', '-S']:\n"
        "        Path('attempt-' + str(os.getpid())).touch()\n"
        f"        if {communication_failure}:\n"
        "            process = original_popen([*command[:-1], 'import time; time.sleep(30)'], *args, **kwargs)\n"
        "            writers.append(process)\n"
        "            def broken_communicate(*args, **kwargs): raise OSError('diagnostic pipe unavailable')\n"
        "            process.communicate = broken_communicate\n"
        "            return process\n"
        "        raise OSError('diagnostic output unavailable')\n"
        "    return original_popen(command, *args, **kwargs)\n"
        "def pytest_configure(config): observer._STACK_POPEN = broken_snapshot\n"
        "def pytest_sessionfinish(session):\n"
        "    assert all(process.poll() is not None for process in writers)\n"
        "    Path('writers-' + str(os.getpid()) + '.json').write_text(json.dumps([p.returncode for p in writers]))\n",
        encoding="utf-8")
    (tmp_path / "test_subject.py").write_text(
        "import time\n"
        "def test_pass(): time.sleep(0.1)\n"
        "def test_fail():\n"
        "    time.sleep(0.1)\n"
        "    assert False\n", encoding="utf-8")
    log = tmp_path / "io-failure.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "pytest", "-q", "-n", str(workers),
             "-p", "scripts.ci.pytest_timing", "--ci-stall-after=0.01"],
            cwd=tmp_path, env=environment(), stdout=output, stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt")
        try:
            assert process.wait(timeout=5) == 1
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    assert list(tmp_path.glob("attempt-*"))
    writers = [code for path in tmp_path.glob("writers-*.json") for code in json.loads(path.read_text())]
    assert len(writers) == (2 if communication_failure else 0)
    assert all(code < 0 for code in writers)
    assert "LOOPX_PYTEST_STACK " not in log.read_text()
    report = read_log(log)
    assert report["execution_complete"] and report["pytest_exit_code"] == 1
    assert report["selected_cases"] == report["finished_cases"] == 2


@pytest.mark.parametrize("workers", [0, 2])
@pytest.mark.parametrize("target", ["run", "Popen"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cleanup of the failing baseline")
def test_test_owned_subprocess_doubles_cannot_capture_diagnostic_writers(tmp_path, workers, target, enabled):
    (tmp_path / "conftest.py").write_text(
        "import subprocess, sys, threading, pytest\nfrom pathlib import Path\n"
        "from scripts.ci import pytest_timing as observer\n"
        "original_popen = subprocess.Popen\nwriters = []\n"
        "def tracked_writer(*args, **kwargs):\n"
        "    process = original_popen(*args, **kwargs)\n"
        "    writers.append(process)\n"
        "    return process\n"
        "observer._STACK_POPEN = tracked_writer\n"
        f"original = subprocess.{target}\n"
        "def test_double(command, *args, **kwargs):\n"
        "    if command[:3] == [sys.executable, '-I', '-S']:\n"
        "        Path('shared-subprocess-entered').touch()\n"
        "        threading.Event().wait(30)\n"
        "        raise AssertionError('test double must not own diagnostic transport')\n"
        "    return original(command, *args, **kwargs)\n"
        f"def pytest_configure(config): subprocess.{target} = test_double\n"
        "@pytest.hookimpl(wrapper=True, tryfirst=True)\n"
        "def pytest_runtest_protocol(item):\n"
        "    before = set(threading.enumerate())\n"
        "    result = yield\n"
        "    assert not any(isinstance(t, threading.Timer) and t not in before\n"
        "                   for t in threading.enumerate())\n"
        "    assert all(p.poll() is not None for p in writers)\n"
        "    return result\n", encoding="utf-8")
    (tmp_path / "test_subject.py").write_text(
        "import time\n"
        "def test_pass(): time.sleep(0.1)\n"
        "def test_fail():\n    time.sleep(0.1)\n    assert False\n", encoding="utf-8")
    log = tmp_path / "test-double.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "pytest", "-q", "-n", str(workers),
             "-p", "scripts.ci.pytest_timing", "--ci-stall-after=" + str(0.01 if enabled else 0)],
            cwd=tmp_path, env=environment(), stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True)
        try:
            assert process.wait(timeout=15) == 1
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    assert not (tmp_path / "shared-subprocess-entered").exists()
    assert ("LOOPX_PYTEST_STACK " in log.read_text()) == enabled
    report = read_log(log)
    assert report["execution_complete"] and report["pytest_exit_code"] == 1
    assert report["selected_cases"] == report["finished_cases"] == 2


@pytest.mark.parametrize("workers", [0, 2])
def test_completed_tests_cancel_pending_stall_snapshot(tmp_path, workers):
    (tmp_path / "test_subject.py").write_text("def test_quick(): pass\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-n", str(workers),
         "-p", "scripts.ci.pytest_timing", "--ci-stall-after=60"],
        cwd=tmp_path, env=environment(), capture_output=True, text=True,
        timeout=10, check=False)
    assert result.returncode == 0
    assert "LOOPX_PYTEST_STACK " not in result.stdout + result.stderr


@pytest.mark.parametrize("workers", [0, 2])
@pytest.mark.parametrize("enabled", [False, True])
def test_stall_snapshot_is_visible_before_the_blocked_test_returns(tmp_path, workers, enabled):
    # A diagnostic routed into capture or a discarded worker descriptor cannot
    # help investigate a live stall. Keep the test blocked until we read it.
    (tmp_path / "test_subject.py").write_text(
        "import time\nfrom pathlib import Path\n"
        "def test_wait_for_release():\n"
        "    Path('entered').touch()\n"
        "    while not Path('release').exists(): time.sleep(0.01)\n",
        encoding="utf-8")
    log = tmp_path / "live-stack.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "pytest", "-q", "-n", str(workers),
             "-p", "scripts.ci.pytest_timing", "--ci-stall-after=" + str(0.1 if enabled else 0)],
            cwd=tmp_path, env=environment(), stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            while True:
                text = log.read_text()
                # The one-shot snapshot can land in setup or call. Both are
                # this test's executing thread; require its node and real frames
                # before release, without assuming which phase won the timer.
                stack = "LOOPX_PYTEST_STACK test_subject.py::test_wait_for_release" in text
                frames = any(line.startswith("  ") and " in " in line for line in text.splitlines())
                ready = (tmp_path / "entered").exists() and (not enabled or (stack and frames))
                if ready:
                    assert process.poll() is None
                    break
                assert process.poll() is None, text
                assert time.monotonic() < deadline, "No live blocked-test stack was visible: " + text
                time.sleep(0.01)
        finally:
            (tmp_path / "release").touch()
            try:
                process.wait(timeout=10)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
    assert process.returncode == 0
    assert ("LOOPX_PYTEST_STACK " in log.read_text()) == enabled
    report = read_log(log)
    assert report["execution_complete"] and report["pytest_exit_code"] == 0
    assert report["selected_cases"] == report["finished_cases"] == 1


@pytest.fixture
def unread_stderr_pipe():
    reader, writer = os.pipe()
    try:
        os.set_blocking(writer, False)
        while True:
            try:
                os.write(writer, b"x" * 4096)
            except BlockingIOError:
                break
        os.set_blocking(writer, True)
        yield writer
    finally:
        os.close(reader)
        os.close(writer)


def track_stack_writers(tmp_path, *, child_deadline=True):
    (tmp_path / "conftest.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "from scripts.ci import pytest_timing as observer\n"
        "original_popen = observer._STACK_POPEN\nwriters = []\n"
        "def tracked_popen(command, *args, **kwargs):\n"
        "    diagnostic = command[:3] == [sys.executable, '-I', '-S']\n"
        f"    if diagnostic and {not child_deadline}:\n"
        "        command = [*command[:-1], 'import os,sys; os.write(2,sys.stdin.buffer.read())']\n"
        "    process = original_popen(command, *args, **kwargs)\n"
        "    if diagnostic:\n"
        "        writers.append(process)\n"
        "        Path('writer.pid').write_text(str(process.pid))\n"
        "    return process\n"
        "observer._STACK_POPEN = tracked_popen\n"
        "def pytest_sessionfinish(session):\n"
        "    assert all(process.poll() is not None for process in writers)\n"
        "    Path('writers.json').write_text(json.dumps([p.returncode for p in writers]))\n",
        encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="POSIX pipe/process groups; hosted diagnostic lanes are Linux")
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("child_deadline", [True, False], ids=["helper-deadline", "parent-timeout"])
def test_full_unread_stderr_does_not_block_native_test_results(
    tmp_path, unread_stderr_pipe, enabled, child_deadline,
):
    track_stack_writers(tmp_path, child_deadline=child_deadline)
    (tmp_path / "test_subject.py").write_text(
        "import time\n"
        "def test_pass(): time.sleep(0.1)\n"
        "def test_fail():\n    time.sleep(0.1)\n    assert False\n", encoding="utf-8")
    log = tmp_path / "job.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "pytest", "-q", "-p", "scripts.ci.pytest_timing",
             "--ci-stall-after=" + str(0.01 if enabled else 0)],
            cwd=tmp_path, env=environment(), stdout=output, stderr=unread_stderr_pipe,
            start_new_session=True)
        try:
            assert process.wait(timeout=4) == 1
            assert os.get_blocking(unread_stderr_pipe)
            writers = json.loads((tmp_path / "writers.json").read_text())
            assert len(writers) == (2 if enabled else 0)
            assert all(code == 0 if child_deadline else code < 0 for code in writers)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    report = read_log(log)
    assert report["execution_complete"] and report["pytest_exit_code"] == 1
    assert report["selected_cases"] == report["finished_cases"] == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX parent kill and process inspection")
def test_blocked_stack_writer_exits_after_only_its_pytest_parent_is_killed(tmp_path, unread_stderr_pipe):
    track_stack_writers(tmp_path)
    (tmp_path / "test_subject.py").write_text(
        "import time\ndef test_wait(): time.sleep(30)\n", encoding="utf-8")
    with (tmp_path / "job.log").open("w") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "pytest", "-q", "-p", "scripts.ci.pytest_timing", "--ci-stall-after=0.01"],
            cwd=tmp_path, env=environment(), stdout=output, stderr=unread_stderr_pipe,
            start_new_session=True)
        try:
            deadline = time.monotonic() + 4
            writer_pid = ""
            while not writer_pid.isdecimal():
                assert process.poll() is None
                assert time.monotonic() < deadline, "diagnostic writer never started"
                if (tmp_path / "writer.pid").exists():
                    writer_pid = (tmp_path / "writer.pid").read_text()
                time.sleep(0.01)
            process.kill()  # Leave the writer alive: its own deadline must stop it.
            process.wait(timeout=4)
            while True:
                inspected = subprocess.run(
                    ["ps", "-p", writer_pid, "-o", "stat="],
                    capture_output=True, text=True, timeout=2, check=False)
                assert inspected.returncode in (0, 1), inspected.stderr
                state = inspected.stdout.strip()
                if inspected.returncode == 1 or state.startswith("Z"):
                    break
                assert state, "process inspection returned no state"
                assert time.monotonic() < deadline, "orphan diagnostic writer remained blocked"
                time.sleep(0.02)
            assert os.get_blocking(unread_stderr_pipe)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group kill; hosted shards are Linux")
@pytest.mark.parametrize("verbosity", ["-v", "-q"])
@pytest.mark.parametrize("stack_timeout", [0, 0.1], ids=["no-stack", "stack"])
def test_killed_controller_leaves_partial_readable_native_observations(tmp_path, verbosity, stack_timeout):
    (tmp_path / "test_subject.py").write_text(
        "import time\n"
        "def test_first(): time.sleep(0.03)\n"
        "def test_wait(): time.sleep(30)\n", encoding="utf-8")
    log = tmp_path / "killed.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "pytest", verbosity, "-n", "2",
             "--ci-stall-after", str(stack_timeout),
             "-p", "scripts.ci.pytest_timing", "--junitxml=junit.xml"],
            cwd=tmp_path, env=environment(), stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                rows = [json.loads(line.removeprefix(PREFIX))
                        for line in log.read_text().splitlines() if line.startswith(PREFIX)]
                stack_visible = not stack_timeout or "in test_wait\n" in log.read_text()
                if any(row.get("phase") == "teardown" for row in rows) and stack_visible:
                    break
                assert process.poll() is None, log.read_text()
                time.sleep(0.02)
            else:
                pytest.fail("No completed test timing before interruption")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    report = read_log(log)
    assert report["execution_complete"] is False
    assert report["pytest_exit_code"] is None
    assert report["selected_cases"] == 2
    assert report["finished_cases"] == 1
    assert report["measured_worker_seconds"] >= 0.03
    assert ("in test_wait\n" in log.read_text()) == bool(stack_timeout)
    assert not (tmp_path / "junit.xml").exists()


def rows_log(tmp_path, rows):
    log = tmp_path / "records.log"
    log.write_text("\n".join(PREFIX + json.dumps({"elapsed_seconds": index, **row})
                             for index, row in enumerate(rows)), encoding="utf-8")
    return log


START = {"event": "start", "version": 1}
COLLECTION = {"event": "collection", "selected": 1}
SETUP = {"event": "phase", "test_id": hashlib.sha256(b"test.py::test_example").hexdigest(),
         "display_nodeid": "test.py::test_example", "phase": "setup",
         "outcome": "passed", "seconds": 0.1}
CALL = {**SETUP, "phase": "call", "seconds": 2}
TEARDOWN = {**SETUP, "phase": "teardown", "seconds": 0.2}
END = {"event": "end", "exit_code": 0}


@pytest.mark.parametrize("rows", [
    [], [COLLECTION], [START, START], [START, COLLECTION, SETUP, SETUP],
    [START, COLLECTION, SETUP, CALL, CALL],
    [START, COLLECTION, CALL], [START, COLLECTION, SETUP, {**CALL, "seconds": -1}],
    [START, COLLECTION, {**SETUP, "phase": "unknown"}],
    [START, COLLECTION, {**SETUP, "outcome": "unknown"}],
    [START, COLLECTION, {**SETUP, "seconds": "2"}],
    [START, COLLECTION, {**SETUP, "test_id": "unknown"}],
    [START, COLLECTION, SETUP, {**CALL, "display_nodeid": "changed"}],
    [START, {**COLLECTION, "selected": 0}, SETUP],
    [START, {"event": "collection_mismatch"}],
    [START, COLLECTION, END, SETUP], [START, {**END, "exit_code": 6}],
])
def test_malformed_or_contradictory_evidence_is_rejected(tmp_path, rows):
    log = rows_log(tmp_path, rows)
    read_log(log, success=False)
    log.write_text("\n".join("\ufeff2026-01-01T00:00:00.0000000Z " + line
                             for line in log.read_text().splitlines()), encoding="utf-8")
    read_log(log, success=False)


@pytest.mark.parametrize("rows,finished", [
    ([START], 0), ([START, COLLECTION, SETUP], 0),
    ([START, COLLECTION, SETUP, TEARDOWN, END], 0),
    ([START, COLLECTION, SETUP, CALL, TEARDOWN, {**END, "exit_code": 2}], 1),
    ([START, COLLECTION, SETUP, {**CALL, "seconds": None}, TEARDOWN, END], 1),
    ([START, {**COLLECTION, "selected": 0}, {**END, "exit_code": 5}], 0),
    ([START, {**COLLECTION, "selected": 2}, SETUP, CALL, TEARDOWN, END], 1),
])
def test_incomplete_or_invalid_timings_never_claim_complete_execution(tmp_path, rows, finished):
    report = read_log(rows_log(tmp_path, rows))
    assert report["execution_complete"] is False
    assert report["finished_cases"] == finished
    assert sum(case["finished"] for case in report["longest_cases"]) == finished


def test_diagnostic_cli_rejects_missing_oversized_or_corrupt_inputs(tmp_path):
    read_log(tmp_path / "missing.log", success=False)
    log = tmp_path / "large.log"
    with log.open("wb") as output:
        output.truncate(32 * 1024 * 1024 + 1)
    assert "32 MiB" in read_log(log, success=False)
    log.write_text(PREFIX + "{broken", encoding="utf-8")
    read_log(log, success=False)


def test_real_oversized_parameter_ids_keep_distinct_identity_and_bounded_display(tmp_path):
    (tmp_path / "test_subject.py").write_text(
        "import pytest\n@pytest.mark.parametrize('value', ['x'*20000, 'x'*20000+'y',\n"
        "    'curl -H \\\"Authorization: Bearer $TOKEN\\\"'])\n"
        "def test_long(value): assert value\n", encoding="utf-8")
    log = tmp_path / "long.log"
    with log.open("w") as output:
        subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "-n", "2", "-p", "scripts.ci.pytest_timing",
             "--store-durations", "--clean-durations", "--durations-path", "native.json"],
            cwd=tmp_path, env=environment(), stdout=output, stderr=subprocess.STDOUT,
            timeout=30, check=True)
    report = read_log(log)
    assert report["execution_complete"] and report["finished_cases"] == 3
    native = json.loads((tmp_path / "native.json").read_text())
    assert {case["test_id"] for case in report["longest_cases"]} == {
        hashlib.sha256(nodeid.encode()).hexdigest() for nodeid in native}
    assert len({case["display_nodeid"] for case in report["longest_cases"]}) == 1
    assert all(case["display_nodeid"].endswith("test_subject.py::test_long")
               for case in report["longest_cases"])
    assert all("Authorization" not in line and "$TOKEN" not in line
               for line in log.read_text().splitlines() if line.startswith(PREFIX))
    assert max(len(line) for line in log.read_text().splitlines() if line.startswith(PREFIX)) < 1200


@pytest.mark.parametrize("deadline", ["-1", "nan", "inf", "-inf"])
def test_session_deadline_rejects_invalid_values_before_running_tests(tmp_path, deadline):
    (tmp_path / "test_subject.py").write_text(
        "from pathlib import Path\n"
        "def test_unreachable(): Path('ran').touch()\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q",
            "-p", "scripts.ci.pytest_timing",
            "--ci-session-deadline=" + deadline,
        ],
        cwd=tmp_path,
        env=environment(),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 4
    assert "--ci-session-deadline must be finite and nonnegative" in result.stderr
    assert not (tmp_path / "ran").exists()


@pytest.mark.parametrize("workers,deadline", [(0, "0.6"), (2, "2.5")])
def test_session_deadline_flushes_partial_junit_and_in_flight_diagnostics(
    tmp_path, workers, deadline,
):
    (tmp_path / "test_subject.py").write_text(
        "import time\nfrom pathlib import Path\n"
        "def test_fast():\n"
        "    Path('fast.done').touch()\n"
        "def test_blocked():\n"
        "    waited = 0.0\n"
        "    while not Path('fast.done').exists() and waited < 5.0:\n"
        "        time.sleep(0.02)\n"
        "        waited += 0.02\n"
        "    Path('blocked.entered').touch()\n"
        "    time.sleep(25)\n",
        encoding="utf-8",
    )
    log = tmp_path / "deadline.log"
    diag_path = tmp_path / "ci-shard-diagnostics.json"
    junit_path = tmp_path / "junit.xml"
    with log.open("w") as output:
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-v", "-n", str(workers),
                "-p", "scripts.ci.pytest_timing",
                "--ci-session-deadline", deadline,
                "--ci-diagnostics-file", str(diag_path),
                "--junitxml", str(junit_path),
            ],
            cwd=tmp_path,
            env=environment(),
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
    assert result.returncode == 2, log.read_text()
    assert (tmp_path / "blocked.entered").exists(), log.read_text()
    assert junit_path.is_file(), "partial JUnit must be flushed on cooperative deadline"
    cases = {
        name
        for case in ET.parse(junit_path).findall(".//testcase")
        if (name := case.attrib.get("name"))
    }
    assert "test_fast" in cases
    diag = json.loads(diag_path.read_text(encoding="utf-8"))
    assert diag["schema_version"] == "loopx_ci_pytest_diagnostics_v1"
    assert diag["deadline_exceeded"] is True
    assert diag["execution_complete"] is False
    assert diag["pytest_exit_code"] == 2
    assert diag["selected_cases"] == 2
    assert diag["finished_cases"] == 1
    assert any(row["display_nodeid"] == "test_subject.py::test_blocked" for row in diag["in_flight_cases"])
    log_text = log.read_text()
    assert "LOOPX_PYTEST_DEADLINE " in log_text
    report = read_log(log)
    assert report["execution_complete"] is False
    assert report["pytest_exit_code"] == 2
    assert report["selected_cases"] == 2
    assert report["finished_cases"] == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX descendant ps inspection")
def test_stall_snapshot_reports_descendant_child_process_basename_without_argv(tmp_path):
    (tmp_path / "test_subject.py").write_text(
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        "def test_wait_child():\n"
        "    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])\n"
        "    try:\n"
        "        Path('entered').touch()\n"
        "        while not Path('release').exists():\n"
        "            time.sleep(0.01)\n"
        "    finally:\n"
        "        proc.kill()\n"
        "        proc.wait(timeout=5)\n",
        encoding="utf-8",
    )
    log = tmp_path / "child-stall.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [
                sys.executable, "-m", "pytest", "-q", "-n", "0",
                "-p", "scripts.ci.pytest_timing",
                "--ci-stall-after=0.1",
            ],
            cwd=tmp_path,
            env=environment(),
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                text = log.read_text()
                if (tmp_path / "entered").exists() and "[child pid=" in text:
                    break
                assert process.poll() is None, text
                assert time.monotonic() < deadline, "Expected child process metadata in stall snapshot: " + text
                time.sleep(0.02)
        finally:
            (tmp_path / "release").touch()
            process.wait(timeout=10)
    assert process.returncode == 0
    text = log.read_text()
    assert "  [child pid=" in text
    assert "comm=" in text
    assert "time.sleep(20)" not in text


@pytest.mark.parametrize("workers", [0, 2])
def test_collection_exceeding_session_deadline_aborts_and_writes_diagnostics(
    tmp_path: Path,
    workers: int,
) -> None:
    (tmp_path / "conftest.py").write_text(
        "import time\n"
        "def pytest_collection_modifyitems(items):\n"
        "    time.sleep(1.5)\n",
        encoding="utf-8",
    )
    (tmp_path / "test_subject.py").write_text(
        "import time\nfrom pathlib import Path\n"
        "def test_never_runs():\n"
        "    Path('ran').touch()\n"
        "    time.sleep(5.0)\n",
        encoding="utf-8",
    )
    log = tmp_path / "slow-collection.log"
    diag_path = tmp_path / "ci-shard-diagnostics.json"
    junit_path = tmp_path / "junit.xml"
    started = time.monotonic()
    with log.open("w") as output:
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-v", "-n", str(workers),
                "-p", "scripts.ci.pytest_timing",
                "--ci-session-deadline", "0.4",
                "--ci-diagnostics-file", str(diag_path),
                "--junitxml", str(junit_path),
            ],
            cwd=tmp_path,
            env=environment(),
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
    wall = time.monotonic() - started
    log_text = log.read_text(encoding="utf-8")
    assert result.returncode == 2, log_text
    assert wall < 4.5, f"Expected early abort before 5s test, took {wall:.2f}s:\n{log_text}"
    assert not (tmp_path / "ran").exists(), log_text
    assert diag_path.is_file(), log_text
    diag = json.loads(diag_path.read_text(encoding="utf-8"))
    assert diag["schema_version"] == "loopx_ci_pytest_diagnostics_v1"
    assert diag["deadline_exceeded"] is True
    assert diag["execution_complete"] is False
    assert diag["pytest_exit_code"] == 2
    assert diag["finished_cases"] == 0
    assert "LOOPX_PYTEST_DEADLINE " in log_text
    assert junit_path.is_file(), log_text
    cases = {
        name
        for case in ET.parse(junit_path).findall(".//testcase")
        if (name := case.attrib.get("name"))
    }
    assert "test_never_runs" not in cases
    report = read_log(log)
    assert report["execution_complete"] is False
    assert report["pytest_exit_code"] == 2


def test_runtestloop_entry_aborts_when_session_deadline_already_elapsed(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace
    from scripts.ci.pytest_timing import TimingObserver

    diag_path = tmp_path / "ci-shard-diagnostics.json"
    dsession = SimpleNamespace(shuttingdown=False, shouldstop=False)
    config = SimpleNamespace(
        getoption=lambda key: 0.4 if key == "ci_session_deadline" else (str(diag_path) if key == "ci_diagnostics_file" else 0),
        pluginmanager=SimpleNamespace(get_plugin=lambda name: dsession if name == "dsession" else None),
    )
    session = SimpleNamespace(shouldstop=False)
    observer = TimingObserver(config)  # type: ignore[arg-type]
    # Simulate deadline firing between collection and pytest_runtestloop (outside active hooks)
    observer.started -= 1.0
    observer._on_deadline(session)  # type: ignore[arg-type]
    assert observer.deadline_exceeded is True
    assert isinstance(session.shouldstop, str) and "0.4s" in session.shouldstop
    assert dsession.shouldstop == session.shouldstop
    assert diag_path.is_file()
    diag = json.loads(diag_path.read_text(encoding="utf-8"))
    assert diag["deadline_exceeded"] is True

    hook = observer.pytest_runtestloop(session)  # type: ignore[arg-type]
    with pytest.raises(pytest.Session.Interrupted):
        next(hook)

    # Also cover entering pytest_runtestloop when elapsed >= deadline before timer fired.
    diag_path2 = tmp_path / "ci-shard-diagnostics-2.json"
    dsession2 = SimpleNamespace(shuttingdown=False, shouldstop=False)
    config2 = SimpleNamespace(
        getoption=lambda key: 0.4 if key == "ci_session_deadline" else (str(diag_path2) if key == "ci_diagnostics_file" else 0),
        pluginmanager=SimpleNamespace(get_plugin=lambda name: dsession2 if name == "dsession" else None),
    )
    session2 = SimpleNamespace(shouldstop=False)
    observer2 = TimingObserver(config2)  # type: ignore[arg-type]
    observer2.started -= 1.0
    hook2 = observer2.pytest_runtestloop(session2)  # type: ignore[arg-type]
    with pytest.raises(pytest.Session.Interrupted):
        next(hook2)
    assert observer2.deadline_exceeded is True
    assert dsession2.shouldstop == session2.shouldstop
    assert diag_path2.is_file()
    assert json.loads(diag_path2.read_text(encoding="utf-8"))["deadline_exceeded"] is True


@pytest.mark.parametrize("workers", [0, 2])
def test_session_deadline_aborts_blocked_test_promptly_and_writes_junit(
    tmp_path: Path,
    workers: int,
) -> None:
    (tmp_path / "test_subject.py").write_text(
        "import time\nfrom pathlib import Path\n"
        "def test_fast():\n"
        "    Path('fast.done').touch()\n"
        "def test_blocked():\n"
        "    waited = 0.0\n"
        "    while not Path('fast.done').exists() and waited < 5.0:\n"
        "        time.sleep(0.02)\n"
        "        waited += 0.02\n"
        "    Path('blocked.entered').touch()\n"
        "    time.sleep(10.0)\n",
        encoding="utf-8",
    )
    log = tmp_path / "deadline-fast-abort.log"
    junit_path = tmp_path / "out.xml"
    started = time.monotonic()
    with log.open("w") as output:
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-v", "-n", str(workers),
                "-p", "scripts.ci.pytest_timing",
                "--ci-session-deadline=1.3",
                f"--junitxml={junit_path}",
            ],
            cwd=tmp_path,
            env=environment(),
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
    wall = time.monotonic() - started
    log_text = log.read_text(encoding="utf-8")
    assert result.returncode == 2, log_text
    assert (tmp_path / "blocked.entered").exists(), log_text
    assert wall < 6.0, f"Deadline abort should not wait for 10s blocked test, took {wall:.2f}s:\n{log_text}"
    assert junit_path.is_file(), log_text
    cases = {
        name
        for case in ET.parse(junit_path).findall(".//testcase")
        if (name := case.attrib.get("name"))
    }
    assert "test_fast" in cases
    assert "LOOPX_PYTEST_DEADLINE " in log_text


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIG_IGN worker termination")
def test_session_deadline_flushes_junit_early_and_bounds_uninterruptible_worker(
    tmp_path: Path,
) -> None:
    (tmp_path / "test_subject.py").write_text(
        "import signal, time\nfrom pathlib import Path\n"
        "def test_fast():\n"
        "    Path('fast.done').touch()\n"
        "def test_uninterruptible():\n"
        "    signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "    waited = 0.0\n"
        "    while not Path('fast.done').exists() and waited < 5.0:\n"
        "        time.sleep(0.02)\n"
        "        waited += 0.02\n"
        "    Path('blocked.entered').touch()\n"
        "    time.sleep(20.0)\n",
        encoding="utf-8",
    )
    log = tmp_path / "uninterruptible.log"
    diag_path = tmp_path / "ci-shard-diagnostics.json"
    junit_path = tmp_path / "out.xml"
    started = time.monotonic()
    junit_readable_while_running = False
    with log.open("w") as output:
        process = subprocess.Popen(
            [
                sys.executable, "-m", "pytest", "-v", "-n", "2",
                "-p", "scripts.ci.pytest_timing",
                "--ci-session-deadline=1.5",
                "--ci-diagnostics-file", str(diag_path),
                f"--junitxml={junit_path}",
            ],
            cwd=tmp_path,
            env=environment(),
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            wait_limit = started + 10.0
            while process.poll() is None and time.monotonic() < wait_limit:
                if junit_path.is_file():
                    try:
                        parsed = {
                            name
                            for case in ET.parse(junit_path).findall(".//testcase")
                            if (name := case.attrib.get("name"))
                        }
                        if "test_fast" in parsed:
                            junit_readable_while_running = True
                    except ET.ParseError:
                        pytest.fail("JUnit XML must never be observed partially written/truncated")
                time.sleep(0.02)
            returncode = process.wait(timeout=5)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
    wall = time.monotonic() - started
    log_text = log.read_text(encoding="utf-8")
    assert returncode == 2, log_text
    assert (tmp_path / "blocked.entered").exists(), log_text
    assert wall < 7.0, f"Bounded xdist teardown must not wait 10s+ EXIT_TIMEOUT or 20s sleep, took {wall:.2f}s"
    assert junit_path.is_file()
    final_cases = {
        name
        for case in ET.parse(junit_path).findall(".//testcase")
        if (name := case.attrib.get("name"))
    }
    assert "test_fast" in final_cases
    # Partial JUnit must be valid and readable while the uninterruptible worker is still tearing down.
    assert junit_readable_while_running
    diag = json.loads(diag_path.read_text(encoding="utf-8"))
    assert diag["deadline_exceeded"] is True
    assert diag["pytest_exit_code"] == 2


def test_concurrent_progress_and_deadline_diagnostics_never_corrupt_or_regress(
    tmp_path: Path,
) -> None:
    # 1. Real -n 2 CLI run: overlap a slow progress write with deadline write.
    (tmp_path / "conftest.py").write_text(
        "import time\nfrom pathlib import Path\n"
        "from scripts.ci import pytest_timing as observer\n"
        "original_write = observer._atomic_write_text\n"
        "slowed = False\n"
        "def wrapped_write(path, content):\n"
        "    global slowed\n"
        "    if path.endswith('ci-shard-diagnostics.json') and not slowed and '\"deadline_exceeded\": false' in content:\n"
        "        slowed = True\n"
        "        Path('slowed.ok').touch()\n"
        "        time.sleep(0.45)\n"
        "    return original_write(path, content)\n"
        "observer._atomic_write_text = wrapped_write\n",
        encoding="utf-8",
    )
    test_lines = ["import time\n"]
    for idx in range(105):
        test_lines.append(f"def test_fast_{idx}():\n    time.sleep(0.01)\n")
    (tmp_path / "test_subject.py").write_text("".join(test_lines), encoding="utf-8")

    log = tmp_path / "concurrent-diag.log"
    diag_path = tmp_path / "ci-shard-diagnostics.json"
    parse_errors: list[str] = []
    seen_deadline_true = False
    regressed = False
    stop_polling = threading.Event()

    def poll_diag() -> None:
        nonlocal seen_deadline_true, regressed
        while not stop_polling.is_set():
            if diag_path.is_file():
                try:
                    data = json.loads(diag_path.read_text(encoding="utf-8"))
                    if data.get("deadline_exceeded") is True:
                        seen_deadline_true = True
                    elif seen_deadline_true:
                        regressed = True
                except (OSError, json.JSONDecodeError) as exc:
                    parse_errors.append(repr(exc))
            time.sleep(0.005)

    poller = threading.Thread(target=poll_diag, daemon=True)
    poller.start()
    try:
        with log.open("w") as output:
            result = subprocess.run(
                [
                    sys.executable, "-m", "pytest", "-q", "-n", "2",
                    "-p", "scripts.ci.pytest_timing",
                    "--ci-session-deadline=0.9",
                    "--ci-diagnostics-file", str(diag_path),
                ],
                cwd=tmp_path,
                env=environment(),
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
            )
    finally:
        stop_polling.set()
        poller.join(timeout=5)
    log_text = log.read_text(encoding="utf-8")
    assert result.returncode == 2, log_text
    assert (tmp_path / "slowed.ok").is_file(), log_text
    assert not parse_errors, f"Observed corrupt diagnostics JSON during concurrent writes: {parse_errors}"
    assert not regressed, "Older progress snapshot overwrote deadline_exceeded=True"
    assert seen_deadline_true or json.loads(diag_path.read_text(encoding="utf-8"))["deadline_exceeded"] is True
    final_diag = json.loads(diag_path.read_text(encoding="utf-8"))
    assert final_diag["deadline_exceeded"] is True
    assert final_diag["pytest_exit_code"] == 2

    # 2. Direct unit proof: serialized _write_diagnostics prevents stale progress from overwriting deadline.
    from types import SimpleNamespace
    from scripts.ci.pytest_timing import TimingObserver

    unit_diag = tmp_path / "unit-diagnostics.json"
    config = SimpleNamespace(
        getoption=lambda key: 0.5 if key == "ci_session_deadline" else (str(unit_diag) if key == "ci_diagnostics_file" else 0),
        pluginmanager=SimpleNamespace(get_plugin=lambda name: None),
    )
    session = SimpleNamespace(shouldstop=False)
    unit_observer = TimingObserver(config)  # type: ignore[arg-type]
    unit_observer._on_deadline(session)  # type: ignore[arg-type]
    assert json.loads(unit_diag.read_text(encoding="utf-8"))["deadline_exceeded"] is True

    # A late periodic progress flush scheduled before deadline must not downgrade deadline_exceeded.
    unit_observer._write_diagnostics(execution_complete=False, exit_code=None, kind="progress")
    after = json.loads(unit_diag.read_text(encoding="utf-8"))
    assert after["deadline_exceeded"] is True
    assert after["deadline_elapsed_seconds"] is not None


def test_shard_budget_computation_and_fail_closed_validation(tmp_path: Path) -> None:
    from scripts.ci.shard_budget import (
        build_pre_shard_budget_diagnostics,
        compute_shard_budget,
        main as shard_budget_main,
        parse_job_start_epoch,
    )

    admitted = compute_shard_budget(
        job_budget_seconds=3600.0,
        upload_reserve_seconds=300.0,
        min_session_seconds=300.0,
        job_start_epoch=1000.0,
        now_epoch=1200.0,
    )
    assert admitted.admitted is True
    assert admitted.elapsed_prep_seconds == 200.0
    assert admitted.remaining_session_seconds == 3100.0
    assert admitted.reason is None

    # Exact minimum viable boundary is admitted; anything below fails fast.
    boundary = compute_shard_budget(
        job_budget_seconds=3600.0,
        upload_reserve_seconds=300.0,
        min_session_seconds=300.0,
        job_start_epoch=1000.0,
        now_epoch=4000.0,
    )
    assert boundary.admitted is True
    assert boundary.remaining_session_seconds == 300.0

    exhausted = compute_shard_budget(
        job_budget_seconds=3600.0,
        upload_reserve_seconds=300.0,
        min_session_seconds=300.0,
        job_start_epoch=1000.0,
        now_epoch=4000.5,
    )
    assert exhausted.admitted is False
    assert exhausted.remaining_session_seconds == 299.5
    assert exhausted.reason is not None and "299.50s remaining < 300.00s" in exhausted.reason
    diag = build_pre_shard_budget_diagnostics(exhausted)
    assert diag["schema_version"] == "loopx_ci_pytest_diagnostics_v1"
    assert diag["deadline_exceeded"] is True
    assert diag["pre_shard_budget_exhausted"] is True
    assert diag["execution_complete"] is False

    # Future clock skew clamps elapsed prep to 0 instead of inflating budget.
    skewed = compute_shard_budget(
        job_budget_seconds=3600.0,
        upload_reserve_seconds=300.0,
        min_session_seconds=300.0,
        job_start_epoch=2000.0,
        now_epoch=1995.0,
    )
    assert skewed.elapsed_prep_seconds == 0.0
    assert skewed.remaining_session_seconds == 3300.0

    assert parse_job_start_epoch(None, required=False) is None
    for bad in (None, "", " ", "abc", "-1", "0", "nan", "inf"):
        with pytest.raises(ValueError):
            parse_job_start_epoch(bad, required=True)
    with pytest.raises(ValueError):
        compute_shard_budget(
            job_budget_seconds=0.0,
            upload_reserve_seconds=300.0,
            min_session_seconds=300.0,
            job_start_epoch=None,
            now_epoch=1000.0,
        )
    with pytest.raises(ValueError):
        compute_shard_budget(
            job_budget_seconds=3600.0,
            upload_reserve_seconds=-1.0,
            min_session_seconds=300.0,
            job_start_epoch=None,
            now_epoch=1000.0,
        )
    assert shard_budget_main(["--job-budget-seconds", "10", "-q"]) == 4
    assert shard_budget_main(["--job-budget-seconds", "10", "--", "--ci-session-deadline", "5"]) == 4

    env = environment()
    env["GITHUB_ACTIONS"] = "true"
    env.pop("LOOPX_SHARD_JOB_START_EPOCH", None)
    (tmp_path / "test_subject.py").write_text(
        "from pathlib import Path\ndef test_unreachable(): Path('ran').touch()\n",
        encoding="utf-8",
    )
    missing_ci = subprocess.run(
        [sys.executable, "-m", "scripts.ci.shard_budget", "--job-budget-seconds", "10", "--", "-q"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10, check=False,
    )
    assert missing_ci.returncode == 4
    assert "LOOPX_SHARD_JOB_START_EPOCH" in missing_ci.stderr
    assert not (tmp_path / "ran").exists()


def test_shard_budget_preserves_reserve_across_fast_and_slow_prep(tmp_path: Path) -> None:
    def run_case(name: str, *, prep_seconds: float, job_budget: float, upload_reserve: float) -> tuple[subprocess.CompletedProcess[str], float, Path, Path, Path]:
        case_dir = tmp_path / name
        case_dir.mkdir()
        (case_dir / "test_subject.py").write_text(
            "import time\nfrom pathlib import Path\n"
            "def test_fast():\n"
            "    Path('fast.done').touch()\n"
            "def test_blocked():\n"
            "    waited = 0.0\n"
            "    while not Path('fast.done').exists() and waited < 5.0:\n"
            "        time.sleep(0.02)\n"
            "        waited += 0.02\n"
            "    Path('blocked.entered').touch()\n"
            "    time.sleep(10.0)\n",
            encoding="utf-8",
        )
        diag_path = case_dir / "ci-shard-diagnostics.json"
        junit_path = case_dir / "junit.xml"
        t0_mono = time.monotonic()
        t0_epoch = time.time()
        time.sleep(prep_seconds)
        remaining_outer = max(0.2, job_budget - (time.monotonic() - t0_mono))
        env = environment()
        env["LOOPX_SHARD_JOB_START_EPOCH"] = f"{t0_epoch:.6f}"
        env["GITHUB_ACTIONS"] = "true"
        result = subprocess.run(
            [
                sys.executable, "-m", "scripts.ci.shard_budget",
                "--job-budget-seconds", str(job_budget),
                "--upload-reserve-seconds", str(upload_reserve),
                "--min-session-seconds", "0.5",
                "--diagnostics-file", str(diag_path),
                "--",
                "-v", "-n", "2",
                "-p", "scripts.ci.pytest_timing",
                "--ci-diagnostics-file", str(diag_path),
                f"--junitxml={junit_path}",
            ],
            cwd=case_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=remaining_outer,
            check=False,
        )
        total_elapsed = time.monotonic() - t0_mono
        return result, total_elapsed, case_dir, junit_path, diag_path

    # 1. Fast prep (0.2s out of 5.0s outer budget, 2.6s reserve -> ~2.2s deadline):
    # exits 2 well before 5.0s outer limit and flushes partial JUnit + diagnostics.
    fast_res, fast_elapsed, _, fast_junit, fast_diag = run_case(
        "fast-prep", prep_seconds=0.2, job_budget=5.0, upload_reserve=2.6,
    )
    assert fast_res.returncode == 2, fast_res.stdout + fast_res.stderr
    assert fast_elapsed < 4.5, f"Fast prep should finish well before 5.0s outer limit, took {fast_elapsed:.2f}s"
    assert fast_junit.is_file()
    fast_cases = {
        name for case in ET.parse(fast_junit).findall(".//testcase") if (name := case.attrib.get("name"))
    }
    assert "test_fast" in fast_cases
    fast_payload = json.loads(fast_diag.read_text(encoding="utf-8"))
    assert fast_payload["deadline_exceeded"] is True
    assert fast_payload["pytest_exit_code"] == 2

    # 2. Slow prep (3.6s out of 5.0s outer budget, same entrypoint & parameters):
    # fails fast before launching shards (no 5.0s outer kill, clear budget error, diagnostics written).
    slow_res, slow_elapsed, slow_dir, slow_junit, slow_diag = run_case(
        "slow-prep-fail-fast", prep_seconds=3.6, job_budget=5.0, upload_reserve=2.6,
    )
    assert slow_res.returncode == 2, slow_res.stdout + slow_res.stderr
    assert slow_elapsed < 4.5, f"Slow prep should fail fast before 5.0s outer limit, took {slow_elapsed:.2f}s"
    assert "insufficient remaining job budget" in slow_res.stderr.lower()
    assert "elapsed_prep=" in slow_res.stderr and "upload_reserve=2.60s" in slow_res.stderr
    assert not (slow_dir / "fast.done").exists()
    assert not slow_junit.exists()
    slow_payload = json.loads(slow_diag.read_text(encoding="utf-8"))
    assert slow_payload["deadline_exceeded"] is True
    assert slow_payload["pre_shard_budget_exhausted"] is True
    assert slow_payload["execution_complete"] is False

    # 3. Tight but viable prep (3.6s out of 9.0s outer budget, 3.0s reserve -> ~2.4s deadline):
    # shortens inner pytest deadline dynamically so full reserve survives before 9.0s outer limit.
    tight_res, tight_elapsed, _, tight_junit, tight_diag = run_case(
        "slow-prep-viable", prep_seconds=3.6, job_budget=9.0, upload_reserve=3.0,
    )
    assert tight_res.returncode == 2, tight_res.stdout + tight_res.stderr
    assert tight_elapsed < 7.5, f"Tight budget should preserve upload reserve before 9.0s, took {tight_elapsed:.2f}s"
    assert tight_junit.is_file()
    tight_cases = {
        name for case in ET.parse(tight_junit).findall(".//testcase") if (name := case.attrib.get("name"))
    }
    assert "test_fast" in tight_cases
    tight_payload = json.loads(tight_diag.read_text(encoding="utf-8"))
    assert tight_payload["deadline_exceeded"] is True
    assert tight_payload["deadline_seconds"] < 2.6


@pytest.mark.parametrize("stall_phase", ["none", "before_yield", "after_yield"])
def test_shard_budget_enforces_deadline_during_stalled_controller_sessionfinish(
    tmp_path: Path,
    stall_phase: str,
) -> None:
    case_dir = tmp_path / stall_phase
    case_dir.mkdir()
    (case_dir / "conftest.py").write_text(
        "import os, time\n"
        "import pytest\n\n"
        "@pytest.hookimpl(wrapper=True)\n"
        "def pytest_sessionfinish(session):\n"
        "    if not hasattr(session.config, 'workerinput'):\n"
        "        before = float(os.environ.get('LOOPX_TEST_STALL_BEFORE', '0'))\n"
        "        after = float(os.environ.get('LOOPX_TEST_STALL_AFTER', '0'))\n"
        "        if before > 0:\n"
        "            time.sleep(before)\n"
        "        result = yield\n"
        "        if after > 0:\n"
        "            time.sleep(after)\n"
        "        return result\n"
        "    return (yield)\n",
        encoding="utf-8",
    )
    (case_dir / "test_subject.py").write_text(
        "from pathlib import Path\n\n"
        "def test_pass() -> None:\n"
        "    Path('passed.ok').touch()\n",
        encoding="utf-8",
    )
    diag_path = case_dir / "ci-shard-diagnostics.json"
    junit_path = case_dir / "junit.xml"
    log_path = case_dir / "job.log"
    job_budget = 6.0 if stall_phase == "after_yield" else 10.0
    upload_reserve = 3.0
    t0_mono = time.monotonic()
    t0_epoch = time.time()
    env = environment()
    env["LOOPX_SHARD_JOB_START_EPOCH"] = f"{t0_epoch:.6f}"
    env["GITHUB_ACTIONS"] = "true"
    if stall_phase == "before_yield":
        env["LOOPX_TEST_STALL_BEFORE"] = "20.0"
    elif stall_phase == "after_yield":
        env["LOOPX_TEST_STALL_AFTER"] = "20.0"

    result = subprocess.run(
        [
            sys.executable, "-m", "scripts.ci.shard_budget",
            "--job-budget-seconds", str(job_budget),
            "--upload-reserve-seconds", str(upload_reserve),
            "--min-session-seconds", "0.5",
            "--diagnostics-file", str(diag_path),
            "--",
            "-v", "-n", "2",
            "-p", "scripts.ci.pytest_timing",
            "--ci-diagnostics-file", str(diag_path),
            f"--junitxml={junit_path}",
        ],
        cwd=case_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=job_budget,
        check=False,
    )
    elapsed = time.monotonic() - t0_mono
    log_path.write_text(result.stdout, encoding="utf-8")
    combined = result.stdout + "\n" + result.stderr

    assert (case_dir / "passed.ok").is_file(), combined
    assert elapsed < job_budget - 0.5, (
        f"phase={stall_phase} must finish before {job_budget}s outer budget, took {elapsed:.2f}s:\n{combined}"
    )
    assert junit_path.is_file(), combined
    testcase_names = [
        case.attrib.get("name")
        for case in ET.parse(junit_path).findall(".//testcase")
    ]
    assert testcase_names == ["test_pass"], f"Expected 1 testcase, got {testcase_names}:\n{combined}"

    diag = json.loads(diag_path.read_text(encoding="utf-8"))
    summary = read_log(log_path)
    if stall_phase == "none":
        assert result.returncode == 0, combined
        assert diag["deadline_exceeded"] is False
        assert diag["execution_complete"] is True
        assert diag["pytest_exit_code"] == 0
        assert summary["execution_complete"] is True
        assert summary["pytest_exit_code"] == 0
        assert summary["finished_cases"] == 1
    else:
        assert result.returncode == 2, combined
        assert "LOOPX_PYTEST_DEADLINE " in result.stdout, combined
        assert diag["deadline_exceeded"] is True
        assert diag["execution_complete"] is False
        assert diag["pytest_exit_code"] == 2
        assert summary["execution_complete"] is False
        assert summary["pytest_exit_code"] == 2
        assert summary["finished_cases"] == 1
