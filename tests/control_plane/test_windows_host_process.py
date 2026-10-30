"""Real Windows descendant cleanup, including loss of the Python owner."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver import host_process_transport as transport

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object transport")


def host_source(marker: Path, pid: Path, *, exit_leader: bool = False) -> str:
    child = (
        "import os,time;from pathlib import Path;"
        f"Path({str(pid)!r}).write_text(str(os.getpid()));"
        f"p=Path({str(marker)!r});"
        "\nwhile True:\n with p.open('a') as f:f.write('x');f.flush()\n time.sleep(.02)"
    )
    return (
        "import subprocess,sys,time;from pathlib import Path;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}],stdin=subprocess.DEVNULL,"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True);"
        f"p=Path({str(marker)!r});"
        "\nwhile not p.exists():time.sleep(.01)\n"
        "print('started',flush=True)\n" + ("" if exit_leader else "time.sleep(60)")
    )


def clean_fixture_child(pid: Path) -> None:
    # Only a child this test actually launched and recorded; never enumerate processes.
    if pid.exists():
        try:
            os.kill(int(pid.read_text()), 9)
        except (ProcessLookupError, PermissionError, OSError):
            pass


@pytest.mark.parametrize("boundary", ["leader_exit", "timeout", "consumer_failure"])
def test_windows_real_host_descendants_stop_before_return(tmp_path: Path, boundary: str) -> None:
    marker, pid, record = (tmp_path / name for name in ("writes", "child.pid", "host.json"))
    environment = {**os.environ, transport.HOST_PROCESS_RECORD_ENV: str(record)}
    def consume(_text: str) -> None:
        if boundary == "consumer_failure":
            raise ValueError("consumer rejected")
    try:
        def run():
            return transport.run_host_process(
                [sys.executable, "-c", host_source(marker, pid, exit_leader=boundary == "leader_exit")],
                project=tmp_path, input_text="", timeout_seconds=2,
                environment=environment, on_stdout=consume,
            )
        if boundary == "consumer_failure":
            with pytest.raises(ValueError, match="consumer rejected"):
                run()
        else:
            result = run()
            assert result["outcome"] == ("exited" if boundary == "leader_exit" else "timeout")
        assert marker.exists(), "The real descendant must have executed"
        assert transport.execution_host_drain(record) == transport.HOST_PROCESS_DRAINED
        before = marker.read_bytes()
        time.sleep(.2)
        assert marker.read_bytes() == before, "A descendant continued writing after cleanup"
    finally:
        clean_fixture_child(pid)


def test_windows_cold_readback_after_owner_kill_sees_whole_job_exit(tmp_path: Path) -> None:
    marker, pid, record = (tmp_path / name for name in ("writes", "child.pid", "host.json"))
    argv = [sys.executable, "-c", host_source(marker, pid)]
    source = (
        "from pathlib import Path;from loopx.control_plane.turn_driver.host_process_transport "
        f"import run_host_process;run_host_process({argv!r},project=Path({str(tmp_path)!r}),"
        "input_text='',timeout_seconds=60)"
    )
    owner = subprocess.Popen([sys.executable, "-c", source],
        env={**os.environ, transport.HOST_PROCESS_RECORD_ENV: str(record)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists(), "Host never launched"
        assert transport.execution_host_drain(record) == transport.HOST_PROCESS_DRAINING
        owner.kill()
        owner.wait(timeout=5)
        deadline = time.monotonic() + 5
        while transport.execution_host_drain(record) != transport.HOST_PROCESS_DRAINED and time.monotonic() < deadline:
            time.sleep(.02)
        assert transport.execution_host_drain(record) == transport.HOST_PROCESS_DRAINED
        before = marker.read_bytes()
        time.sleep(.2)
        assert marker.read_bytes() == before
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        clean_fixture_child(pid)


def test_windows_legacy_finished_pid_does_not_prove_job_drain(tmp_path: Path) -> None:
    record = tmp_path / "old.json"
    from loopx.file_lock import lock_holder_host_label
    record.write_text(json.dumps({"schema_version": transport.HOST_PROCESS_RECORD_SCHEMA_VERSION,
        "host": lock_holder_host_label(), "supervises": "host", "supervision": "direct",
        "phase": "finished", "bridge_pid": 9999999, "process_group": None}))
    assert transport.execution_host_drain(record) == transport.HOST_PROCESS_UNSUPPORTED_PLATFORM


def test_windows_job_assignment_failure_launches_no_host(tmp_path: Path, monkeypatch) -> None:
    from loopx.control_plane.turn_driver.windows_host_job import WindowsHostJob
    marker = tmp_path / "must-not-execute"
    def deny(_self, _process):
        raise PermissionError("assignment rejected")
    monkeypatch.setattr(WindowsHostJob, "assign", deny)
    with pytest.raises(PermissionError, match="assignment rejected"):
        transport.run_host_process([sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).write_text('bad')"],
            project=tmp_path, input_text="", timeout_seconds=5)
    assert not marker.exists()


def test_windows_job_finish_failure_still_releases_launch_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.control_plane.turn_driver.host_bridge_transport import GuardedHostBridge
    from loopx.control_plane.turn_driver.windows_host_job import WindowsHostJob

    record_path = tmp_path / "record-target"
    record_path.mkdir()
    marker = tmp_path / "host-must-not-run"
    released = {"guard": False, "output": False}

    @contextmanager
    def launch_guard():
        try:
            yield
        finally:
            released["guard"] = True

    original_close_output = GuardedHostBridge.close_output

    def observe_close_output(self: GuardedHostBridge) -> None:
        original_close_output(self)
        released["output"] = self.output.closed

    original_finish = WindowsHostJob.finish

    def fail_after_job_cleanup(self: WindowsHostJob) -> None:
        original_finish(self)
        raise OSError("synthetic Job query failure")

    monkeypatch.setattr(GuardedHostBridge, "close_output", observe_close_output)
    monkeypatch.setattr(WindowsHostJob, "finish", fail_after_job_cleanup)

    environment = {**os.environ, transport.HOST_PROCESS_RECORD_ENV: str(record_path)}
    with pytest.raises(OSError, match="synthetic Job query failure"):
        transport.run_host_process(
            [
                sys.executable,
                "-c",
                f"from pathlib import Path;Path({str(marker)!r}).write_text('bad')",
            ],
            project=tmp_path,
            input_text="",
            timeout_seconds=5,
            environment=environment,
            launch_guard=launch_guard,
        )

    assert released == {"guard": True, "output": True}
    assert not marker.exists()
