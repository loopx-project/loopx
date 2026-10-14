from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO


_PROCESS_IO_CHUNK_BYTES = 64 * 1024
_PROCESS_TERMINATE_GRACE_SECONDS = 1.0
_PROCESS_GROUP_STOP_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True)
class CappedProcessResult:
    returncode: int
    stdout: bytes
    failure_kind: str | None = None


def _wait_for_process(process: subprocess.Popen[bytes] | subprocess.Popen[str], timeout: float) -> bool:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


def _posix_owned_group_has_exited(process_group_id: int, timeout: float) -> bool:
    snapshot = subprocess.run(
        ["ps", "-A", "-o", "pgid=", "-o", "stat="],
        capture_output=True, text=True, encoding="utf-8", check=False,
        timeout=timeout,
    )
    if snapshot.returncode != 0 or not snapshot.stdout.strip():
        raise RuntimeError("owned POSIX process-group observation failed")
    live = False
    for line in snapshot.stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 2 or not fields[0].isdecimal():
            raise RuntimeError("invalid owned POSIX process-group observation")
        # Zombies cannot execute. Stopped and unknown states remain live.
        if int(fields[0]) == process_group_id and not fields[1].startswith("Z"):
            live = True
    return not live


def _wait_for_posix_process_group_stop(process_group_id: int) -> None:
    deadline = time.monotonic() + _PROCESS_GROUP_STOP_TIMEOUT_SECONDS
    while True:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            # Darwin can report EPERM for a dead, unreaped group. Require
            # observation rather than accepting a sent signal as cleanup.
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("owned POSIX process group did not stop before cleanup deadline")
        try:
            exited = _posix_owned_group_has_exited(process_group_id, remaining)
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
            raise RuntimeError("owned POSIX process-group observation failed") from error
        if exited:
            return
        time.sleep(min(.01, max(0, deadline - time.monotonic())))


def _darwin_owned_group_has_exited(process: subprocess.Popen[bytes] | subprocess.Popen[str]) -> bool:
    # Darwin can report EPERM rather than ESRCH for a now-empty process group.
    # A reaped leader alone does not prove its descendants have exited.
    if sys.platform != "darwin" or process.poll() is None:
        return False
    try:
        return _posix_owned_group_has_exited(process.pid, 1)
    except (OSError, subprocess.TimeoutExpired, UnicodeError, RuntimeError):
        return False


def _terminate_posix_process_group(
    process: subprocess.Popen[bytes] | subprocess.Popen[str], grace_seconds: float
) -> None:
    process_group_id = process.pid
    try:
        os.killpg(
            process_group_id,
            signal.SIGKILL if grace_seconds <= 0 else signal.SIGTERM,
        )
    except ProcessLookupError:
        process.wait()
        return
    except PermissionError:
        if not _darwin_owned_group_has_exited(process):
            raise
        process.wait()
        return
    if grace_seconds > 0:
        _wait_for_process(process, grace_seconds)
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            if not _darwin_owned_group_has_exited(process):
                raise
            process.wait()
            return
    if process.poll() is None:
        process.kill()
        process.wait()
    # KILL delivery is asynchronous; reaping only the leader does not prove
    # descendants stopped writing. Observe absence or an all-zombie group.
    _wait_for_posix_process_group_stop(process_group_id)


def _terminate_windows_process_tree(
    process: subprocess.Popen[bytes] | subprocess.Popen[str], grace_seconds: float
) -> None:
    if process.poll() is not None:
        return
    subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if _wait_for_process(process, grace_seconds):
        return
    subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process.wait()


def terminate_process_tree(
    process: subprocess.Popen[bytes] | subprocess.Popen[str], grace_seconds: float
) -> None:
    """Stop an owned, previously isolated process tree and reap its leader.

    POSIX callers must launch with ``start_new_session=True``. Zero grace sends
    one force-kill signal, not TERM followed by KILL against an exiting group.
    After KILL, POSIX cleanup confirms absence or only zombie members within a
    one-second observation budget. Unknown/failed observation raises rather than
    certifying cleanup. This is OS transport only; callers own execution deadlines
    and failure decisions.
    """
    if os.name == "posix":
        _terminate_posix_process_group(process, grace_seconds)
        return
    if os.name == "nt":  # pragma: no cover - exercised on Windows hosts.
        _terminate_windows_process_tree(process, grace_seconds)
        return
    if process.poll() is not None:  # pragma: no cover - unsupported platform fallback.
        return
    process.terminate()
    if not _wait_for_process(process, grace_seconds):
        process.kill()
        process.wait()



def prepare_owned_process_cleanup(process: subprocess.Popen[bytes]) -> Callable[[], None]:
    """Retain diagnostic tree ownership even if its leader exits.

    Windows callers must create the child suspended (CREATE_SUSPENDED) so
    it cannot spawn descendants before assignment to the private Job Object.
    POSIX callers must start a new session, as for terminate_process_tree.
    """
    if os.name != "nt":
        return lambda: terminate_process_tree(process, 0)
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll")
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    native.NtResumeProcess.argtypes = [wintypes.HANDLE]
    native.NtResumeProcess.restype = ctypes.c_long

    class Accounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in ("user", "kernel", "period_user", "period_kernel")] + [
            (name, wintypes.DWORD) for name in ("faults", "total", "active", "terminated")]

    job = kernel.CreateJobObjectW(None, None)
    if not job:
        process.kill()
        process.wait()
        raise OSError("Owned process job creation failed")
    try:
        if not kernel.AssignProcessToJobObject(job, int(process._handle)):
            raise OSError("Owned process job assignment failed")
        if native.NtResumeProcess(int(process._handle)) != 0:
            raise OSError("Owned process resume failed")
    except BaseException:
        process.kill()
        process.wait()
        kernel.CloseHandle(job)
        raise
    closed = False

    def cleanup() -> None:
        nonlocal closed
        if closed:
            return
        try:
            if not kernel.TerminateJobObject(job, 1):
                raise OSError("Owned process job termination failed")
            deadline = time.monotonic() + 1
            while True:
                accounting = Accounting()
                if not kernel.QueryInformationJobObject(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                    raise OSError("Owned process job observation failed")
                if accounting.active == 0:
                    process.wait(timeout=max(.01, deadline - time.monotonic()))
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("Owned process job cleanup unconfirmed")
                time.sleep(.01)
        finally:
            kernel.CloseHandle(job)
            closed = True
    return cleanup

def run_capped_process(
    argv: Sequence[str],
    *,
    stdin: bytes,
    timeout_seconds: float | None,
    output_limit_bytes: int,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    termination_grace_seconds: float = _PROCESS_TERMINATE_GRACE_SECONDS,
) -> CappedProcessResult:
    """Run a provider while bounding both output streams during execution."""

    process = subprocess.Popen(
        list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        env=dict(env) if env is not None else None,
        cwd=cwd,
        start_new_session=os.name == "posix",
        creationflags=(
            int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP"))
            if os.name == "nt" else 0
        ),
    )
    stdin_stream = process.stdin
    assert stdin_stream is not None
    assert process.stdout is not None
    assert process.stderr is not None

    stdout = bytearray()
    stderr = bytearray()
    limit_event = threading.Event()
    limit_lock = threading.Lock()
    limit_kind: str | None = None

    def record_limit(kind: str) -> None:
        nonlocal limit_kind
        with limit_lock:
            if limit_kind is None:
                limit_kind = kind
                limit_event.set()

    def read_stream(
        stream: BinaryIO,
        destination: bytearray,
        *,
        overflow_kind: str,
    ) -> None:
        try:
            while chunk := stream.read(_PROCESS_IO_CHUNK_BYTES):
                remaining = output_limit_bytes + 1 - len(destination)
                if remaining > 0:
                    destination.extend(chunk[:remaining])
                if len(destination) > output_limit_bytes:
                    record_limit(overflow_kind)
                    return
        except (OSError, ValueError):
            return

    def write_stdin() -> None:
        try:
            stdin_stream.write(stdin)
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                stdin_stream.close()
            except (OSError, ValueError):
                pass

    threads = [
        threading.Thread(
            target=read_stream,
            args=(process.stdout, stdout),
            kwargs={"overflow_kind": "response_too_large"},
            name="loopx-extension-stdout-reader",
            daemon=True,
        ),
        threading.Thread(
            target=read_stream,
            args=(process.stderr, stderr),
            kwargs={"overflow_kind": "stderr_too_large"},
            name="loopx-extension-stderr-reader",
            daemon=True,
        ),
        threading.Thread(
            target=write_stdin,
            name="loopx-extension-stdin-writer",
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    timed_out = False
    try:
        while process.poll() is None:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                timed_out = True
                terminate_process_tree(process, termination_grace_seconds)
                break
            if limit_event.wait(timeout=0.05 if remaining is None else min(0.05, remaining)):
                terminate_process_tree(process, termination_grace_seconds)
                break
    except BaseException:
        terminate_process_tree(process, termination_grace_seconds)
        raise
    finally:
        for thread in threads:
            thread.join(timeout=_PROCESS_TERMINATE_GRACE_SECONDS)
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    returncode = process.wait()
    return CappedProcessResult(
        returncode=returncode,
        stdout=bytes(stdout),
        failure_kind="timeout" if timed_out else limit_kind,
    )
