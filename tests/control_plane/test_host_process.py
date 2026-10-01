"""Real managed Host boundaries: no paid model, no external side effects."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver.executor import _run_host
from loopx.control_plane.turn_driver.host_process_transport import (
    HostOutputLines,
    run_host_process,
)
from tests.control_plane.host_process_fixture import COUNTER_PROCESS_SOURCE


def test_host_output_lines_bound_storage_and_use_lf() -> None:
    rows: list[str] = []
    lines = HostOutputLines(rows.append, max_chars=20)
    lines.feed("one\u2028two\n" + "x" * 100)
    assert lines.pending == ""
    lines.feed("tail\nlast")
    lines.finish()
    assert rows == ["one\u2028two", "last"]
    assert not lines.complete


def test_real_generic_host_roundtrip_and_stream_budget(tmp_path: Path) -> None:
    request = {"message": "one private local request"}
    result = _run_host(
        request,
        argv=[
            sys.executable,
            "-c",
            "import sys,json;print(json.dumps(json.load(sys.stdin)))",
        ],
        project=tmp_path,
        timeout_seconds=5,
    )
    assert result == {"ok": True, "value": request, "returncode": 0}
    overflow = _run_host(
        request,
        argv=[
            sys.executable,
            "-c",
            "import sys,time;sys.stdout.write('x'*1000000);sys.stdout.flush();time.sleep(30)",
        ],
        project=tmp_path,
        timeout_seconds=5,
    )
    assert overflow["ok"] is False
    assert overflow["reason"] == "host stdout exceeded the result budget"


def test_callback_failure_waits_for_owned_host_cleanup(tmp_path: Path) -> None:
    def reject(_text: str) -> None:
        raise ValueError("consumer stopped")

    started = time.monotonic()
    with pytest.raises(ValueError, match="consumer stopped"):
        run_host_process(
            [
                sys.executable,
                "-c",
                "import time;print('ready',flush=True);time.sleep(30)",
            ],
            project=tmp_path,
            input_text="",
            timeout_seconds=20,
            on_stdout=reject,
        )
    assert time.monotonic() - started < 8


@pytest.mark.skipif(os.name == "nt", reason="POSIX process interruption contract")
def test_counter_process_fixture_publishes_atomically(tmp_path: Path) -> None:
    marker = tmp_path / "counter"
    pid_path = tmp_path / "pid"
    pause_path = tmp_path / "before-publish"
    marker.write_text("published", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            COUNTER_PROCESS_SOURCE,
            str(marker),
            str(pid_path),
            "0.02",
            str(pause_path),
        ]
    )
    try:
        deadline = time.monotonic() + 5
        while not pause_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pause_path.exists(), "Counter process never reached the publication fence"
        process.kill()
        process.wait(timeout=5)
        assert marker.read_text(encoding="utf-8") == "published"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cancellation contract")
def test_disappearing_python_owner_cancels_real_host(tmp_path: Path) -> None:
    marker = tmp_path / "counter"
    pid_path = tmp_path / "pid"
    host_argv = [
        sys.executable,
        "-c",
        COUNTER_PROCESS_SOURCE,
        str(marker),
        str(pid_path),
        "0.02",
    ]
    launcher = f"""
from pathlib import Path
from loopx.control_plane.turn_driver.host_process_transport import run_host_process
run_host_process({host_argv!r}, project=Path({str(tmp_path)!r}), input_text='', timeout_seconds=30)
"""
    owner = subprocess.Popen(
        [sys.executable, "-c", launcher],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "Host never started"
        owner.kill()
        owner.wait(timeout=5)
        time.sleep(1)
        before = marker.read_text()
        time.sleep(0.15)
        assert marker.read_text() == before, "Host survived loss of its owner"
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        if pid_path.exists():
            try:
                os.kill(int(pid_path.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cancellation contract")
def test_generic_host_timeout_stops_real_descendant(tmp_path: Path) -> None:
    marker = tmp_path / "child-work"
    pid_path = tmp_path / "child-pid"
    child_argv = [
        sys.executable,
        "-c",
        COUNTER_PROCESS_SOURCE,
        str(marker),
        str(pid_path),
        "0.02",
    ]
    host = f"import subprocess,time;subprocess.Popen({child_argv!r});time.sleep(30)"
    try:
        result = _run_host(
            {}, argv=[sys.executable, "-c", host], project=tmp_path, timeout_seconds=1
        )
        assert result["ok"] is False
        assert result["reason"] == "host process timeout"
        before = marker.read_text()
        time.sleep(0.15)
        assert marker.read_text() == before
    finally:
        if pid_path.exists():
            try:
                os.kill(int(pid_path.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_windows_transport_relay_preserves_argv_and_stdin(tmp_path: Path) -> None:
    from loopx.control_plane.turn_driver.host_process_transport import _WINDOWS_COMMAND_RELAY

    # The relay is tested here on any OS; real .cmd resolution remains a Windows
    # integration obligation. All args stay argv entries, not interpolated code.
    values = ["two words", "a&b", "%NAME%", 'one"quote', "界"]
    result = _run_host(
        {},
        argv=[
            sys.executable,
            "-c",
            _WINDOWS_COMMAND_RELAY,
            sys.executable,
            "-c",
            "import json,sys;print(json.dumps({'args':sys.argv[1:],'input':json.load(sys.stdin)}))",
            *values,
        ],
        project=tmp_path,
        timeout_seconds=5,
    )
    assert result["ok"] is True
    assert result["value"] == {"args": values, "input": {}}
