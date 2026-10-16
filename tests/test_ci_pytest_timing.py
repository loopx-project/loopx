"""Real pytest observations survive failure/interruption without qualifying CI."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

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
    (tmp_path / "test_subject.py").write_text(
        "import time, pytest\n"
        "def test_pass(): time.sleep(0.03)\n"
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
    with log.open("w") as output:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "-n", "2", "-p", "native_reports",
             "-p", "scripts.ci.pytest_timing"], cwd=tmp_path, env=environment(),
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
            assert process.wait(timeout=5) == 1
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
