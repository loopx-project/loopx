"""Darwin process IO for native account consumer discovery.

Process arguments/environment never leave this adapter. Only CODEX_HOME or a
Lima hostagent's pidfile is returned; callers fence kernel launch identity.
This is discovery evidence, not authorization to signal an arbitrary process.
"""
from __future__ import annotations

import ctypes
import os
import sys


def codex_home(pid: int) -> str | None:
    """Read the launch environment without putting arguments/secrets in ps output."""
    return _codex_home(_procargs(pid))


def responsible_pid(pid: int) -> int | None:
    """Best-effort Darwin responsibility, including reparented XPC processes.

    This optional SPI can be absent or denied. A responsible PID is provenance
    for diagnostics only, never permission to terminate it or its workload.
    """
    if sys.platform != "darwin":
        return None
    try:
        probe = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True).responsibility_get_pid_responsible_for_pid
        probe.argtypes, probe.restype = [ctypes.c_int], ctypes.c_int
        result = probe(pid)
        return result if result > 1 and result != pid else None
    except (AttributeError, OSError):
        return None


def lima_pidfile(pid: int) -> str | None:
    """Return just the explicit hostagent pidfile, never raw argv or env."""
    arguments, _ = _split_procargs(_procargs(pid))
    if len(arguments) < 2 or os.path.basename(arguments[0]) != b"limactl" or arguments[1] != b"hostagent":
        return None
    values = []
    for index, value in enumerate(arguments[2:], 2):
        if value == b"--pidfile":
            if index + 1 >= len(arguments):
                return None
            values.append(arguments[index + 1])
        elif value.startswith(b"--pidfile="):
            values.append(value[len(b"--pidfile="):])
    if len(values) != 1 or not values[0].startswith(b"/"):
        return None
    return os.fsdecode(values[0])


def _procargs(pid: int) -> bytes:
    if sys.platform != "darwin":
        raise OSError("process environment unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
    size = ctypes.c_size_t()
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or not 4 < size.value <= 1048576:
        raise OSError("process environment unavailable")
    buffer = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
        raise OSError("process environment unavailable")
    return buffer.raw[:size.value]


def _split_procargs(raw: bytes) -> tuple[list[bytes], list[bytes]]:
    argc = int.from_bytes(raw[:4], sys.byteorder, signed=True)
    if not 0 < argc <= 65536:
        raise OSError("invalid process environment")
    position = raw.find(b"\0", 4)
    if position < 0:
        raise OSError("invalid process environment")
    while position < len(raw) and raw[position] == 0:
        position += 1
    arguments = []
    for _ in range(argc):
        end = raw.find(b"\0", position)
        if end < 0:
            raise OSError("invalid process environment")
        arguments.append(raw[position:end])
        position = end + 1
    return arguments, raw[position:].split(b"\0")


def _codex_home(raw: bytes) -> str | None:
    _, environment = _split_procargs(raw)
    values = [value[11:] for value in environment if value.startswith(b"CODEX_HOME=")]
    if len(values) > 1:
        raise OSError("ambiguous process environment")
    return os.fsdecode(values[0]) if values else None
