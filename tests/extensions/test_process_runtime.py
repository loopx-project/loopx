from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time
from unittest.mock import Mock

import pytest

import loopx.extensions.process_runtime as process_runtime
from loopx.extensions.process_runtime import (
    force_terminate_process,
    remove_owned_tree,
    run_capped_process,
    terminate_process_tree,
    write_process_stdin_before_deadline,
)


@pytest.mark.parametrize("written", [None, 0, 2])
def test_partial_native_frame_retires_receiver_before_return(written) -> None:
    process = Mock(spec=subprocess.Popen)
    process.stdin = Mock()
    process.stdin.write.return_value = written
    process.poll.return_value = None
    retired = []

    def cancel() -> None:
        retired.append(True)
        process.poll.return_value = -9

    with pytest.raises(OSError, match="incomplete native input frame"):
        write_process_stdin_before_deadline(
            process, "完整☃\n", deadline=time.monotonic() + 2, cancel_and_reap=cancel,
        )
    assert retired == [True] and process.poll() is not None
    process.stdin.flush.assert_not_called()
    process.stdin.close.assert_not_called()


def test_complete_native_unicode_frame_uses_character_count() -> None:
    process = Mock(spec=subprocess.Popen)
    process.stdin = Mock()
    text = "完整☃\n"
    process.stdin.write.return_value = len(text)
    cancel = Mock()
    write_process_stdin_before_deadline(
        process, text, deadline=time.monotonic() + 2, cancel_and_reap=cancel,
    )
    process.stdin.write.assert_called_once_with(text)
    process.stdin.flush.assert_called_once_with()
    process.stdin.close.assert_not_called()
    cancel.assert_not_called()


def test_failed_native_cancellation_still_observes_blocked_writer() -> None:
    process = Mock(spec=subprocess.Popen)
    process.stdin = Mock()
    release = threading.Event()
    writers = []

    def blocked_write(text):
        writers.append(threading.current_thread())
        assert release.wait(timeout=5)
        return len(text)

    process.stdin.write.side_effect = blocked_write
    failure = OSError("synthetic native cleanup failure")
    try:
        began = time.monotonic()
        with pytest.raises(RuntimeError, match="writer did not drain") as raised:
            write_process_stdin_before_deadline(
                process, "frame\n", deadline=began + .1,
                cancel_and_reap=Mock(side_effect=failure),
            )
        assert raised.value.__cause__ is failure
        assert time.monotonic() - began < 2.5
        process.stdin.close.assert_not_called()
    finally:
        release.set()
        for writer in writers:
            writer.join(timeout=2)
            assert not writer.is_alive()


@pytest.mark.parametrize("phase", ["write", "flush"])
def test_native_stream_error_retires_receiver_before_propagation(phase) -> None:
    process = Mock(spec=subprocess.Popen)
    process.stdin = Mock()
    process.stdin.write.return_value = len("frame\n")
    failure = OSError("synthetic partial native stream error")
    getattr(process.stdin, phase).side_effect = failure
    process.poll.return_value = None
    cancel = Mock(side_effect=lambda: setattr(process.poll, "return_value", -9))
    with pytest.raises(OSError) as raised:
        write_process_stdin_before_deadline(
            process, "frame\n", deadline=time.monotonic() + 2, cancel_and_reap=cancel,
        )
    assert raised.value is failure and process.poll() is not None
    cancel.assert_called_once_with()
    process.stdin.close.assert_not_called()


@pytest.mark.parametrize("outcome", ["success", "failed", "timeout", "missing", "unreaped_helper"])
def test_windows_force_cleanup_reserves_bounded_original_receiver_reap(monkeypatch, outcome):
    process = Mock(spec=subprocess.Popen)
    helper = Mock(spec=subprocess.Popen)
    process.pid, process.poll.return_value = 12345, None
    helper.poll.return_value = None
    clock, events = [0.0], []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def taskkill(command, **kwargs):
        assert command == ["taskkill", "/PID", "12345", "/T", "/F"]
        assert kwargs == {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        events.append("tree-start")
        if outcome == "missing":
            raise FileNotFoundError("taskkill unavailable")
        return helper

    def helper_wait(*, timeout):
        assert 0 < timeout <= .5
        first = "tree-wait" not in events
        events.append("tree-wait" if first else "tree-reap")
        clock[0] += timeout
        if outcome == "unreaped_helper" or (first and outcome == "timeout"):
            raise subprocess.TimeoutExpired("taskkill", timeout)
        helper.poll.return_value = int(outcome == "failed")
        return helper.poll.return_value

    def reap(*, timeout):
        assert 0 < timeout <= (1 if outcome == "missing" else .5)
        events.append("reap")
        process.poll.return_value = -9
        return -9

    monkeypatch.setattr(subprocess, "Popen", taskkill)
    helper.kill.side_effect = lambda: events.append("tree-kill")
    helper.wait.side_effect = helper_wait
    process.kill.side_effect = lambda: events.append("kill")
    process.wait.side_effect = reap
    if outcome == "success":
        process_runtime._force_terminate_windows_process_tree(process, 1)
    else:
        with pytest.raises((RuntimeError, subprocess.TimeoutExpired, FileNotFoundError)):
            process_runtime._force_terminate_windows_process_tree(process, 1)
    assert events[-2:] == ["kill", "reap"] and process.poll() is not None
    if outcome == "missing":
        assert events == ["tree-start", "kill", "reap"]
    else:
        assert events.count("tree-start") == 1 and events.count("tree-reap") == 1
        assert ("tree-kill" in events) == (outcome in {"timeout", "unreaped_helper"})


def test_windows_exited_leader_does_not_certify_descendant_cleanup(monkeypatch):
    monkeypatch.setattr(process_runtime.time, "monotonic", lambda: 0.0)
    process = Mock(spec=subprocess.Popen)
    process.poll.return_value = 0
    taskkill = Mock()
    monkeypatch.setattr(subprocess, "Popen", taskkill)
    with pytest.raises(RuntimeError, match="unconfirmed after leader exit"):
        process_runtime._force_terminate_windows_process_tree(process, .1)
    taskkill.assert_not_called()
    process.kill.assert_not_called()
    assert 0 <= process.wait.call_args.kwargs["timeout"] <= .1


def test_windows_force_cleanup_cannot_accept_an_unreaped_receiver(monkeypatch):
    monkeypatch.setattr(process_runtime.time, "monotonic", lambda: 0.0)
    process = Mock(spec=subprocess.Popen)
    helper = Mock(spec=subprocess.Popen)
    helper.wait.return_value, helper.poll.return_value = 0, 0
    process.pid, process.poll.return_value = 12345, None
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=helper))
    process.wait.side_effect = subprocess.TimeoutExpired("native", .1)
    with pytest.raises(subprocess.TimeoutExpired):
        process_runtime._force_terminate_windows_process_tree(process, .1)
    process.wait.assert_called_once()
    assert 0 <= process.wait.call_args.kwargs["timeout"] <= .1


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
def test_force_cleanup_darwin_observation_spends_only_remaining_budget(monkeypatch):
    process = Mock(spec=subprocess.Popen)
    process.pid, process.poll.return_value = 12345, 0
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os, "killpg", Mock(side_effect=PermissionError("empty owned group")))
    observer = Mock(spec=subprocess.Popen)
    observer.communicate.return_value = ("1 S\n12345 Z\n", None)
    observer.poll.return_value, observer.returncode = 0, 0
    observer.args, observer.stdout = ["ps"], Mock()
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=observer))
    force_terminate_process(process, process_tree_owned=True, timeout=.1)
    assert 0 < observer.communicate.call_args.kwargs["timeout"] <= .05
    assert 0 <= observer.wait.call_args.kwargs["timeout"] <= .1
    assert 0 <= process.wait.call_args.kwargs["timeout"] <= .1
    process.kill.assert_not_called()


def test_force_posix_observer_timeout_has_bounded_self_reap(monkeypatch):
    observer = Mock(spec=subprocess.Popen)
    observer.poll.return_value, observer.stdout = None, Mock()
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def collect(*, timeout):
        assert 0 < timeout <= .05
        clock[0] += timeout
        raise subprocess.TimeoutExpired("ps", timeout)

    observer.communicate.side_effect = collect
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=observer))
    with pytest.raises(subprocess.TimeoutExpired):
        process_runtime._bounded_posix_group_snapshot(.1)
    observer.kill.assert_called_once_with()
    assert 0 < observer.wait.call_args.kwargs["timeout"] <= .05
    observer.stdout.close.assert_called_once_with()


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_force_cleanup_rejects_an_unbounded_or_invalid_budget(timeout):
    process = Mock(spec=subprocess.Popen)
    with pytest.raises(ValueError, match="positive and finite"):
        force_terminate_process(process, process_tree_owned=True, timeout=timeout)
    process.kill.assert_not_called()
    process.wait.assert_not_called()


@pytest.mark.parametrize("failure", ["backpressure", "short_write", "partial_error"])
def test_native_pipe_failure_reaps_receiver_and_writer_before_fence_release(tmp_path, failure):
    ready, release, accepted = (tmp_path / name for name in ("ready", "release", "accepted"))
    if failure != "backpressure":
        release.touch()
    peer = (
        "import sys,time\nfrom pathlib import Path\n"
        f"Path({str(ready)!r}).touch()\n"
        f"while not Path({str(release)!r}).exists(): time.sleep(.01)\n"
        "line=sys.stdin.readline()\n"
        f"if line.endswith('\\n'): Path({str(accepted)!r}).write_text(line, encoding='utf-8')\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", peer], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        start_new_session=os.name == "posix",
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
    )
    stream = process.stdin
    assert stream is not None
    writers, retired = [], []
    fence = threading.Lock()

    class NativeInput:
        def write(self, text):
            writers.append(threading.current_thread())
            if failure == "backpressure":
                return stream.write(text)
            stream.write(text[:32])
            stream.flush()
            if failure == "partial_error":
                raise BrokenPipeError("synthetic failure after a real partial send")
            return 32

        def flush(self):
            stream.flush()

    process.stdin = NativeInput()

    def cancel() -> None:
        assert fence.locked()
        force_terminate_process(process, process_tree_owned=True)
        retired.append(process.returncode)

    def stop() -> int:
        with fence:
            assert process.poll() is not None
            assert all(not writer.is_alive() for writer in writers)
            return process.returncode

    try:
        ready_deadline = time.monotonic() + 5
        while not ready.exists() and process.poll() is None and time.monotonic() < ready_deadline:
            time.sleep(.01)
        assert ready.exists()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with fence:
                stopped = executor.submit(stop)
                began = time.monotonic()
                expected = TimeoutError if failure == "backpressure" else OSError
                with pytest.raises(expected):
                    write_process_stdin_before_deadline(
                        process, "完整☃" * (1024 * 1024) + "\n",
                        deadline=began + .1, cancel_and_reap=cancel,
                    )
                assert time.monotonic() - began < 2.5
                assert retired == [process.returncode] and process.poll() is not None
                assert writers and all(not writer.is_alive() for writer in writers)
                assert not stopped.done()
            assert stopped.result(timeout=2) == process.returncode
        assert not accepted.exists()
    finally:
        release.touch()
        if process.poll() is None:
            force_terminate_process(process, process_tree_owned=True)
        for writer in writers:
            writer.join(timeout=2)
            assert not writer.is_alive()
        stream.close()
    assert not accepted.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
def test_zero_grace_sends_one_force_kill_and_reaps_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Mock(spec=subprocess.Popen)
    process.pid = 12345
    process.poll.return_value = None
    signals: list[tuple[int, int]] = []

    def killpg(pid: int, sig: int) -> None:
        signals.append((pid, sig))

    monkeypatch.setattr(os, "killpg", killpg)
    stopped = Mock()
    monkeypatch.setattr(process_runtime, "_wait_for_posix_process_group_stop", stopped)
    terminate_process_tree(process, grace_seconds=0)

    assert signals == [(process.pid, signal.SIGKILL)]
    stopped.assert_called_once_with(process.pid)
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
def test_zero_grace_reaps_leader_when_owned_group_is_already_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Mock(spec=subprocess.Popen)
    process.pid = 12345

    def missing_group(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", missing_group)
    terminate_process_tree(process, grace_seconds=0)

    process.wait.assert_called_once_with()
    process.kill.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
@pytest.mark.parametrize("grace_seconds", [0, 1])
def test_darwin_empty_group_permission_error_is_confirmed_before_acceptance(
    monkeypatch: pytest.MonkeyPatch, grace_seconds: float,
) -> None:
    process = Mock(spec=subprocess.Popen)
    process.pid = 12345
    process.poll.return_value = 0
    signals = []

    def killpg(pid: int, sig: int) -> None:
        signals.append(sig)
        if sig == signal.SIGKILL:
            raise PermissionError("group gone")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os, "killpg", killpg)
    snapshot = Mock(return_value=subprocess.CompletedProcess([], 0, "1 S\n6789 Z\n", ""))
    monkeypatch.setattr(subprocess, "run", snapshot)
    terminate_process_tree(process, grace_seconds)
    assert signals[-1] == signal.SIGKILL
    snapshot.assert_called_once()
    process.kill.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
@pytest.mark.parametrize("platform,leader_status,returncode,groups", [
    ("darwin", None, 0, "1 S\n"),
    ("darwin", 0, 0, "1 S\n12345 S\n"),
    ("darwin", 0, 1, "1 S\n"),
    ("darwin", 0, 0, ""),
    ("darwin", 0, 0, "invalid\n"),
    ("linux", 0, 0, "1 S\n"),
])
def test_permission_error_stays_failure_when_owned_group_exit_is_unproven(
    monkeypatch: pytest.MonkeyPatch, platform: str,
    leader_status: int | None, returncode: int, groups: str,
) -> None:
    process = Mock(spec=subprocess.Popen)
    process.pid = 12345
    process.poll.return_value = leader_status

    def denied(_pid: int, _sig: int) -> None:
        raise PermissionError("cannot signal owned group")

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(os, "killpg", denied)
    monkeypatch.setattr(subprocess, "run", Mock(return_value=
        subprocess.CompletedProcess([], returncode, groups, "")))
    with pytest.raises(PermissionError, match="cannot signal"):
        terminate_process_tree(process, 0)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
def test_zero_grace_waits_for_live_descendants_after_leader_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    process = Mock(spec=subprocess.Popen)
    process.pid = 12345
    process.poll.return_value = 0
    signals = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    snapshots = Mock(side_effect=[
        subprocess.CompletedProcess([], 0, "1 S\n12345 S\n", ""),
        subprocess.CompletedProcess([], 0, "1 S\n12345 Z\n", ""),
    ])
    monkeypatch.setattr(subprocess, "run", snapshots)
    pause = Mock()
    monkeypatch.setattr(time, "sleep", pause)
    terminate_process_tree(process, 0)
    assert signals == [(12345, signal.SIGKILL), (12345, 0), (12345, 0)]
    assert snapshots.call_count == 2
    pause.assert_called_once()
    process.kill.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
@pytest.mark.parametrize("state", ["T", "?", "S"])
def test_stopped_or_unknown_group_state_does_not_certify_cleanup(
    monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    process = Mock(spec=subprocess.Popen)
    process.pid = 12345
    process.poll.return_value = 0
    monkeypatch.setattr(os, "killpg", lambda _pid, _sig: None)
    monkeypatch.setattr(subprocess, "run", Mock(return_value=
        subprocess.CompletedProcess([], 0, f"1 S\n12345 {state}\n", "")))
    clock = iter([0, .25, .5, 1.1])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(time, "sleep", Mock())
    with pytest.raises(TimeoutError, match="cleanup deadline"):
        terminate_process_tree(process, 0)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
@pytest.mark.parametrize("returncode,output", [(1, "1 S\n"), (0, ""), (0, "12345\n"), (0, "bad S\n")])
def test_failed_or_malformed_observation_does_not_certify_cleanup(
    monkeypatch: pytest.MonkeyPatch, returncode: int, output: str,
) -> None:
    process = Mock(spec=subprocess.Popen)
    process.pid = 12345
    process.poll.return_value = 0
    monkeypatch.setattr(os, "killpg", lambda _pid, _sig: None)
    monkeypatch.setattr(subprocess, "run", Mock(return_value=
        subprocess.CompletedProcess([], returncode, output, "")))
    with pytest.raises(RuntimeError, match="process-group observation"):
        terminate_process_tree(process, 0)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
@pytest.mark.parametrize("error", [subprocess.TimeoutExpired(["ps"], 1), FileNotFoundError("ps absent")])
def test_unavailable_observation_is_an_explicit_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, error: Exception,
) -> None:
    process = Mock(spec=subprocess.Popen)
    process.pid = 12345
    process.poll.return_value = 0
    monkeypatch.setattr(os, "killpg", lambda _pid, _sig: None)
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=error))
    with pytest.raises(RuntimeError, match="process-group observation failed") as raised:
        terminate_process_tree(process, 0)
    assert raised.value.__cause__ is error


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
def test_signal_zero_permission_requires_fresh_zombie_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    process = Mock(spec=subprocess.Popen)
    process.pid = 12345
    process.poll.return_value = 0

    def signal_group(_pid: int, sig: int) -> None:
        if sig == 0:
            raise PermissionError("group observation denied")

    monkeypatch.setattr(os, "killpg", signal_group)
    snapshot = Mock(return_value=subprocess.CompletedProcess([], 0, "1 S\n12345 Z+\n", ""))
    monkeypatch.setattr(subprocess, "run", snapshot)
    terminate_process_tree(process, 0)
    snapshot.assert_called_once()
    process.kill.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
@pytest.mark.parametrize("grace_seconds", [0, .1])
def test_return_waits_until_child_with_closed_pipes_cannot_execute(
    tmp_path: Path, grace_seconds: float,
) -> None:
    ready = tmp_path / "child-pid"
    heartbeat = tmp_path / "heartbeat"
    child_code = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
        f"while True: Path({str(heartbeat)!r}).touch(); time.sleep(.005)\n"
    )
    provider_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}], "
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "time.sleep(30)"
    )
    process = subprocess.Popen([sys.executable, "-c", provider_code], start_new_session=True,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(.005)
        assert heartbeat.exists()
        child_pid = int(ready.read_text())
        terminate_process_tree(process, grace_seconds)
        assert process.poll() is not None
        # Independently observe the child immediately after return, without a
        # quiet-period delay that could hide asynchronous KILL delivery.
        snapshot = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "stat="],
                                  check=True, capture_output=True, text=True, timeout=1)
        states = [line.split()[1] for line in snapshot.stdout.splitlines()
                  if line.split() and line.split()[0] == str(child_pid)]
        assert all(state.startswith("Z") for state in states), states
    finally:
        terminate_process_tree(process, 0)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
def test_timeout_force_kills_descendant_that_ignores_term(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    marker = tmp_path / "descendant-effect"
    child_code = (
        "from pathlib import Path; import signal,time; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"Path({str(ready)!r}).touch(); time.sleep(3); "
        f"Path({str(marker)!r}).touch()"
    )
    provider_code = (
        "import subprocess,sys,time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        f"ready=Path({str(ready)!r})\n"
        "while not ready.exists(): time.sleep(.01)\n"
        "print('ready',flush=True); time.sleep(5)"
    )
    result = run_capped_process(
        [sys.executable, "-c", provider_code], stdin=b"{}",
        timeout_seconds=2, output_limit_bytes=1024,
        termination_grace_seconds=.1,
    )
    assert ready.exists()
    assert result.failure_kind == "timeout"
    time.sleep(1.5)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
def test_timeout_terminates_provider_descendants(tmp_path: Path) -> None:
    marker = tmp_path / "descendant-effect"
    child_code = (
        "from pathlib import Path; import time; "
        "time.sleep(1.2); "
        f"Path({str(marker)!r}).write_text('effect', encoding='utf-8')"
    )
    provider_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(5)"
    )

    result = run_capped_process(
        [sys.executable, "-c", provider_code],
        stdin=json.dumps({"schema_version": "request_v0"}).encode(),
        timeout_seconds=1,
        output_limit_bytes=1024,
    )

    assert result.failure_kind == "timeout"
    time.sleep(0.5)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
def test_output_overflow_terminates_provider_descendants(tmp_path: Path) -> None:
    marker = tmp_path / "descendant-effect"
    child_code = (
        "from pathlib import Path; import time; "
        "time.sleep(0.6); "
        f"Path({str(marker)!r}).write_text('effect', encoding='utf-8')"
    )
    provider_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "sys.stdout.buffer.write(b'x' * 1025); sys.stdout.flush(); time.sleep(5)"
    )

    result = run_capped_process(
        [sys.executable, "-c", provider_code],
        stdin=b"{}",
        timeout_seconds=5,
        output_limit_bytes=1024,
    )

    assert result.failure_kind == "response_too_large"
    time.sleep(0.8)
    assert not marker.exists()


def test_a_provider_response_returns_byte_for_byte() -> None:
    response = '{"verdict": "巡检通过"}\n'.encode("utf-8")

    result = run_capped_process(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
        ],
        stdin=response,
        timeout_seconds=30,
        output_limit_bytes=1024,
    )

    assert result.failure_kind is None
    assert result.returncode == 0
    assert result.stdout == response


def test_a_provider_declining_is_not_reported_as_a_runtime_failure() -> None:
    result = run_capped_process(
        [sys.executable, "-c", "import sys; sys.stdout.write('declined'); sys.exit(3)"],
        stdin=b"{}",
        timeout_seconds=30,
        output_limit_bytes=1024,
    )

    assert result.failure_kind is None
    assert result.returncode == 3
    assert result.stdout == b"declined"


def test_a_response_of_exactly_the_limit_is_kept() -> None:
    limit = 4096

    result = run_capped_process(
        [sys.executable, "-c", f"import sys; sys.stdout.buffer.write(b'x' * {limit})"],
        stdin=b"{}",
        timeout_seconds=30,
        output_limit_bytes=limit,
    )

    assert result.failure_kind is None
    assert result.returncode == 0
    assert len(result.stdout) == limit


def test_a_spewing_provider_is_stopped_instead_of_drained() -> None:
    limit = 4096
    started = time.monotonic()

    result = run_capped_process(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "while True:\n"
            "    sys.stdout.buffer.write(b'x' * 65536)\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(30)\n",
        ],
        stdin=b"{}",
        timeout_seconds=30,
        output_limit_bytes=limit,
    )

    elapsed = time.monotonic() - started
    assert result.failure_kind == "response_too_large"
    # One byte past the limit is the caller's truncation signal, and the bound
    # must hold against a provider that never stops writing.
    assert len(result.stdout) == limit + 1
    assert elapsed < 20, (
        f"the capture drained the provider instead of stopping it: {elapsed:.1f}s"
    )


def test_a_spewing_provider_on_stderr_is_its_own_failure() -> None:
    result = run_capped_process(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "sys.stderr.buffer.write(b'e' * 262144)\n"
            "sys.stderr.flush()\n"
            "time.sleep(30)\n",
        ],
        stdin=b"{}",
        timeout_seconds=30,
        output_limit_bytes=1024,
    )

    assert result.failure_kind == "stderr_too_large"
    assert result.stdout == b""


def test_no_execution_deadline_preserves_completion_and_output_budget():
    result = run_capped_process(
        [sys.executable, "-c", "import time;time.sleep(.1);print('done')"],
        stdin=b"", timeout_seconds=None, output_limit_bytes=1024,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == b"done"
    assert result.failure_kind is None
    limited = run_capped_process(
        [sys.executable, "-c", "import sys,time;print('x'*2000,flush=True);time.sleep(30)"],
        stdin=b"", timeout_seconds=None, output_limit_bytes=1024,
    )
    assert limited.failure_kind == "response_too_large"


def test_remove_owned_tree_removes_a_read_only_member(tmp_path: Path) -> None:
    # Git marks every loose object it writes read-only, and Windows refuses to
    # unlink a read-only file, which stranded the whole fixture root.
    object_directory = tmp_path / ".git" / "objects" / "39"
    object_directory.mkdir(parents=True)
    object_file = object_directory / "6a2d80dac5ee96f7f8e1b120f2358ae0fcda"
    object_file.write_bytes(b"blob")
    os.chmod(object_file, stat.S_IREAD)

    remove_owned_tree(tmp_path)

    assert not tmp_path.exists()


def _write_read_only_fixture(root: Path) -> Path:
    """A git-shaped read-only object, the member the Windows pass rewrites."""
    root.mkdir()
    fixture = root / "object"
    fixture.write_bytes(b"blob")
    os.chmod(fixture, stat.S_IREAD)
    return fixture


def _assert_linked_target_untouched(target: Path, fixture: Path) -> None:
    assert target.is_dir()
    assert fixture.read_bytes() == b"blob"
    assert os.stat(fixture).st_mode & stat.S_IWRITE == 0


def test_remove_owned_tree_refuses_a_symlinked_root_without_touching_its_target(
    tmp_path: Path,
) -> None:
    # os.walk starts at the root even when it is a link, so the Windows
    # attribute pass would chmod the link target before shutil.rmtree refused
    # the link. The rejection has to happen while the target is still untouched.
    target = tmp_path / "external"
    fixture = _write_read_only_fixture(target)
    root = tmp_path / "owned-root"
    try:
        root.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"cannot create a directory symlink: {error}")

    with pytest.raises(OSError, match="linked tree root"):
        remove_owned_tree(root)

    assert root.is_symlink()
    _assert_linked_target_untouched(target, fixture)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_remove_owned_tree_refuses_a_junction_root_without_touching_its_target(
    tmp_path: Path,
) -> None:
    # Junctions are not symlinks, so os.path.islink misses them and os.walk
    # descends into the target; they are the unprivileged Windows equivalent of
    # the directory-symlink root the guard must refuse.
    target = tmp_path / "external"
    fixture = _write_read_only_fixture(target)
    root = tmp_path / "owned-root"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(root), str(target)],
        capture_output=True, text=True, check=False,
    )
    assert created.returncode == 0, created.stderr or created.stdout

    with pytest.raises(OSError, match="linked tree root"):
        remove_owned_tree(root)

    assert os.path.isdir(root)
    _assert_linked_target_untouched(target, fixture)


def test_remove_owned_tree_never_clears_a_shared_hard_link_attribute(
    tmp_path: Path,
) -> None:
    # git clone --local hard-links loose objects, so a read-only clone object is
    # the same file as the source object. The Windows attribute pass must not
    # clear its read-only bit: that would change a file outside the owned
    # fixture, and a successful removal would hide the side effect.
    source = tmp_path / "source"
    source.mkdir()
    source_object = source / "object"
    source_object.write_bytes(b"blob")
    os.chmod(source_object, stat.S_IREAD)
    owned = tmp_path / "owned"
    owned.mkdir()
    try:
        os.link(source_object, owned / "object")
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"cannot create a hard link: {error}")
    assert os.stat(source_object).st_nlink == 2

    if os.name == "nt":
        # Fail closed: this directory entry cannot be removed without changing
        # the shared file, so the fixture is retained and the caller reports
        # cleanup failure instead of mutating another owner's object.
        with pytest.raises(OSError):
            remove_owned_tree(owned)
        assert owned.exists()
    else:
        # POSIX unlinks the read-only directory entry without touching the
        # inode or its other link.
        remove_owned_tree(owned)
        assert not owned.exists()

    assert source_object.read_bytes() == b"blob"
    assert os.stat(source_object).st_mode & stat.S_IWRITE == 0
