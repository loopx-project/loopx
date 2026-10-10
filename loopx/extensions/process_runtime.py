from __future__ import annotations

from collections.abc import Mapping, Sequence
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


def _darwin_owned_group_has_exited(process: subprocess.Popen[bytes] | subprocess.Popen[str]) -> bool:
    # Darwin can report EPERM rather than ESRCH for a now-empty process group.
    # A reaped leader alone does not prove its descendants have exited.
    if sys.platform != "darwin" or process.poll() is None:
        return False
    try:
        snapshot = subprocess.run(
            ["/bin/ps", "-axo", "pgid="], capture_output=True, text=True,
            encoding="utf-8", check=False, timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return False
    groups = snapshot.stdout.split()
    return (
        snapshot.returncode == 0
        and bool(groups)
        and all(group.isdecimal() for group in groups)
        and str(process.pid) not in groups
    )


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
    if process.poll() is None:
        process.kill()
        process.wait()


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
    This is OS transport only; callers own deadlines and failure decisions.
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
