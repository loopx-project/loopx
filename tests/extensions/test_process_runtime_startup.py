"""A launched child stays owned when capped-process setup does not finish."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from unittest.mock import Mock

import pytest

from loopx.extensions import process_runtime


@pytest.fixture
def owned_children(monkeypatch):
    children = []
    yield children
    # The historical implementation leaks these children. Keep the negative
    # case safe too, without treating test cleanup as product cleanup.
    monkeypatch.undo()
    for child in children:
        process_runtime.terminate_process_tree(child, grace_seconds=.1)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()


def _capture_child(monkeypatch, children, command, *, after_start=None):
    initialize = subprocess.Popen.__init__

    def capture(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        if self.args == command:
            children.append(self)
            if after_start is not None:
                after_start(self)

    monkeypatch.setattr(subprocess.Popen, "__init__", capture)


def _assert_retired(children):
    assert len(children) == 1
    child = children[0]
    assert child.poll() is not None, "capped-process setup left its child running"
    assert all(stream.closed for stream in (child.stdin, child.stdout, child.stderr))


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, OSError])
@pytest.mark.parametrize("boundary", ["constructor_return", "native_start"])
def test_constructor_unwind_after_launch_retires_acquired_child(
    monkeypatch, owned_children, error_type, boundary,
):
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    failure = error_type("cancelled after the native launch returned a PID")

    def interrupt(_child):
        raise failure

    if boundary == "constructor_return":
        _capture_child(monkeypatch, owned_children, command, after_start=interrupt)
    else:
        execute = subprocess.Popen._execute_child

        def start_then_unwind(self, *args, **kwargs):
            execute(self, *args, **kwargs)
            if self.args == command:
                # The native constructor now has a real PID, but its outer
                # initialization (and exception cleanup) has not returned.
                owned_children.append(self)
                raise failure

        monkeypatch.setattr(subprocess.Popen, "_execute_child", start_then_unwind)
    with pytest.raises(error_type) as raised:
        process_runtime.run_capped_process(
            command, stdin=b"", timeout_seconds=5, output_limit_bytes=1024,
            termination_grace_seconds=.1,
        )
    assert raised.value is failure
    _assert_retired(owned_children)


@pytest.mark.skipif(os.name != "posix", reason="POSIX cancellation signal")
def test_signal_after_launch_retires_child_before_cancel_returns(monkeypatch, owned_children):
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    failure = KeyboardInterrupt("caller cancelled")

    def cancelled(_number, _frame):
        raise failure

    previous = signal.signal(signal.SIGTERM, cancelled)
    try:
        _capture_child(monkeypatch, owned_children, command,
                       after_start=lambda _child: os.kill(os.getpid(), signal.SIGTERM))
        with pytest.raises(KeyboardInterrupt) as raised:
            process_runtime.run_capped_process(
                command, stdin=b"", timeout_seconds=5, output_limit_bytes=1024,
                termination_grace_seconds=.1,
            )
        assert raised.value is failure
        _assert_retired(owned_children)
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.parametrize("boundary", ["construct", "before_start", "after_start"])
def test_partial_stream_setup_retires_child_and_closes_all_pipes(
    monkeypatch, owned_children, boundary,
):
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    failure = RuntimeError("stream setup failed")
    _capture_child(monkeypatch, owned_children, command)
    initialize, start = threading.Thread.__init__, threading.Thread.start
    threads = []

    def construct(self, *args, **kwargs):
        selected = kwargs.get("name", "").startswith("loopx-extension-")
        if selected and boundary == "construct":
            raise failure
        initialize(self, *args, **kwargs)
        if selected:
            threads.append(self)

    def start_or_fail(self):
        if self not in threads:
            return start(self)
        if boundary == "after_start":
            start(self)
        raise failure

    monkeypatch.setattr(threading.Thread, "__init__", construct)
    monkeypatch.setattr(threading.Thread, "start", start_or_fail)
    with pytest.raises(RuntimeError) as raised:
        process_runtime.run_capped_process(
            command, stdin=b"", timeout_seconds=5, output_limit_bytes=1024,
            termination_grace_seconds=.1,
        )
    assert raised.value is failure
    _assert_retired(owned_children)
    assert not any(thread.is_alive() for thread in threads)


def test_failure_before_any_pid_does_not_terminate_an_unowned_process(monkeypatch):
    failure = OSError("launch was not attempted")

    def reject(self, *args, **kwargs):
        raise failure

    retire = Mock()
    monkeypatch.setattr(subprocess.Popen, "__init__", reject)
    monkeypatch.setattr(process_runtime, "terminate_process_tree", retire)
    with pytest.raises(OSError) as raised:
        process_runtime.run_capped_process(
            ["unused"], stdin=b"", timeout_seconds=5, output_limit_bytes=1024,
        )
    assert raised.value is failure
    retire.assert_not_called()


def test_failed_startup_cleanup_remains_an_error(monkeypatch, owned_children):
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    cancelled = KeyboardInterrupt("cancelled during setup")
    unconfirmed = RuntimeError("owned child cleanup unconfirmed")

    def interrupt(_child):
        raise cancelled

    _capture_child(monkeypatch, owned_children, command, after_start=interrupt)
    monkeypatch.setattr(process_runtime, "terminate_process_tree", Mock(side_effect=unconfirmed))
    with pytest.raises(BaseException) as raised:
        process_runtime.run_capped_process(
            command, stdin=b"", timeout_seconds=5, output_limit_bytes=1024,
        )
    assert raised.value is unconfirmed
    assert raised.value.__context__ is cancelled


@pytest.mark.parametrize("timeout", [None, 5])
def test_ordinary_launch_preserves_inputs_and_result(tmp_path: Path, timeout):
    text = "complete input ☃\n".encode()
    result = process_runtime.run_capped_process(
        [sys.executable, "-c",
         "import os,sys; assert os.getcwd() == sys.argv[1]; "
         "assert os.environ['PROCESS_FIXTURE'] == 'present'; "
         "sys.stdout.buffer.write(sys.stdin.buffer.read()); "
         "sys.stderr.write('diagnostic'); sys.exit(3)", str(tmp_path)],
        stdin=text, timeout_seconds=timeout, output_limit_bytes=1024,
        cwd=tmp_path, env={**os.environ, "PROCESS_FIXTURE": "present"},
    )
    assert (result.returncode, result.stdout, result.failure_kind) == (3, text, None)
