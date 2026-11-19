"""Absolute-deadline races in the native stdin transport, without a model host."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from loopx.extensions import process_runtime


def _scheduled_writer(monkeypatch, *, late_start=False, late_flush=False):
    """Let the writer finish before the caller is scheduled to inspect its event."""
    clock = SimpleNamespace(now=0.0)
    writes = []
    cancellations = []

    class Stream:
        def write(self, text):
            writes.append(text)
            return len(text)

        def flush(self):
            if late_flush:
                clock.now = 2.0

    class ScheduledThread(threading.Thread):
        def run(self):
            if late_start:
                clock.now = 2.0
            super().run()

        def start(self):
            super().start()
            self.join(timeout=2)
            assert not self.is_alive(), "controlled writer did not finish"
            clock.now = 3.0

    monkeypatch.setattr(process_runtime, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(
        process_runtime, "threading",
        SimpleNamespace(Event=threading.Event, Thread=ScheduledThread),
    )
    return SimpleNamespace(stdin=Stream(), poll=lambda: 0), writes, cancellations, clock


def test_native_stdin_rejects_writer_starting_after_absolute_deadline(monkeypatch):
    process, writes, cancellations, _ = _scheduled_writer(monkeypatch, late_start=True)
    with pytest.raises(TimeoutError, match="dispatch deadline"):
        process_runtime.write_process_stdin_before_deadline(
            process, "late frame\n", deadline=1.0,
            cancel_and_reap=lambda: cancellations.append("cancelled"),
        )
    assert writes == []
    assert cancellations == ["cancelled"]


def test_native_stdin_rejects_flush_completed_after_absolute_deadline(monkeypatch):
    process, writes, cancellations, _ = _scheduled_writer(monkeypatch, late_flush=True)
    with pytest.raises(TimeoutError, match="dispatch deadline"):
        process_runtime.write_process_stdin_before_deadline(
            process, "partial or late frame\n", deadline=1.0,
            cancel_and_reap=lambda: cancellations.append("cancelled"),
        )
    assert writes == ["partial or late frame\n"]
    assert cancellations == ["cancelled"]


def test_native_stdin_accepts_timely_completion_when_caller_resumes_late(monkeypatch):
    process, writes, cancellations, clock = _scheduled_writer(monkeypatch)
    process_runtime.write_process_stdin_before_deadline(
        process, "timely frame\n", deadline=1.0,
        cancel_and_reap=lambda: cancellations.append("cancelled"),
    )
    assert clock.now == 3.0
    assert writes == ["timely frame\n"]
    assert cancellations == []


def test_native_stdin_expired_before_dispatch_does_not_write(monkeypatch):
    process, writes, cancellations, clock = _scheduled_writer(monkeypatch)
    clock.now = 2.0
    with pytest.raises(TimeoutError, match="dispatch deadline"):
        process_runtime.write_process_stdin_before_deadline(
            process, "not sent\n", deadline=1.0,
            cancel_and_reap=lambda: cancellations.append("cancelled"),
        )
    assert writes == []
    assert cancellations == []


def _close_child(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=2)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except (BrokenPipeError, OSError):
                pass


def test_native_stdin_large_unicode_frame_preserves_wire_text():
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.readline()); sys.stdout.flush()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    text = "目标保留🙂" * 32768 + "\n"
    cancellations = []

    def cancel():
        cancellations.append("cancelled")
        _close_child(process)

    try:
        process_runtime.write_process_stdin_before_deadline(
            process, text, deadline=time.monotonic() + 5,
            cancel_and_reap=cancel,
        )
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0
        assert stdout == text
        assert stderr == ""
        assert cancellations == []
    finally:
        _close_child(process)


def test_native_stdin_backpressure_cancels_peer_and_joins_writer():
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys, time; print('ready', flush=True); time.sleep(60)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    cancellations = []

    def cancel():
        cancellations.append("cancelled")
        process.kill()
        process.wait(timeout=2)

    try:
        assert process.stdout.readline() == "ready\n"
        with pytest.raises(TimeoutError, match="dispatch deadline"):
            process_runtime.write_process_stdin_before_deadline(
                process, "x" * (8 * 1024 * 1024) + "\n",
                deadline=time.monotonic() + 0.05, cancel_and_reap=cancel,
            )
        assert process.poll() is not None
        assert cancellations == ["cancelled"]
        assert not any(
            thread.name == "loopx-native-stdin-writer" and thread.is_alive()
            for thread in threading.enumerate()
        )
    finally:
        _close_child(process)
