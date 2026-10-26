"""Darwin process IO for native account consumer discovery.

Process arguments/environment never leave this adapter. Only CODEX_HOME is
returned; the caller fences the read with kernel executable/start identity.
This is discovery evidence, not authorization to signal an arbitrary process.
"""
from __future__ import annotations

import ctypes
import os
import sys


def codex_home(pid: int) -> str | None:
    """Read the launch environment without putting arguments/secrets in ps output."""
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
    return _codex_home(buffer.raw[:size.value])


def _codex_home(raw: bytes) -> str | None:
    argc = int.from_bytes(raw[:4], sys.byteorder, signed=True)
    if not 0 < argc <= 65536:
        raise OSError("invalid process environment")
    position = raw.find(b"\0", 4)
    if position < 0:
        raise OSError("invalid process environment")
    while position < len(raw) and raw[position] == 0:
        position += 1
    for _ in range(argc):
        end = raw.find(b"\0", position)
        if end < 0:
            raise OSError("invalid process environment")
        position = end + 1
    values = [value[11:] for value in raw[position:].split(b"\0") if value.startswith(b"CODEX_HOME=")]
    if len(values) > 1:
        raise OSError("ambiguous process environment")
    return os.fsdecode(values[0]) if values else None
