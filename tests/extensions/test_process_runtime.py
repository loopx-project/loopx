from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

import loopx.extensions.process_runtime as process_runtime
from loopx.extensions.process_runtime import run_capped_process, terminate_process_tree


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
