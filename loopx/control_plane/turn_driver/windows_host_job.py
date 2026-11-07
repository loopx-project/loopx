"""Windows-only kernel containment IO for the existing typed Host supervisor.

No task, stop, lease or deadline decision belongs here. The parent assigns the
control bridge before sending its first request; the bridge cannot launch a
Host before that input. Neither breakaway nor inherited Job handles is allowed.
"""
from __future__ import annotations

import ctypes
import re
import time
import uuid
from ctypes import wintypes


class _BasicLimit(ctypes.Structure):
    _fields_ = [("per_process_time", ctypes.c_longlong), ("per_job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("basic", _BasicLimit), ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [("user", ctypes.c_longlong), ("kernel", ctypes.c_longlong),
        ("period_user", ctypes.c_longlong), ("period_kernel", ctypes.c_longlong),
        ("page_faults", wintypes.DWORD), ("total", wintypes.DWORD),
        ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]


_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
for _name, _arguments, _result in (
    ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
    ("OpenJobObjectW", [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
    ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
    ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
    ("QueryInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
    ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
    ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
):
    _function = getattr(_kernel, _name)
    _function.argtypes, _function.restype = _arguments, _result


def _checked(ok):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return ok


def _active(handle) -> int:
    info = _Accounting()
    _checked(_kernel.QueryInformationJobObject(handle, 1, ctypes.byref(info), ctypes.sizeof(info), None))
    return info.active


def valid_job_name(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"Local\\LoopXHost-[0-9a-f]{32}", value) is not None


def active_job_processes(name: str) -> int | None:
    """Fresh kernel observation. None means the named Job no longer exists.

    An inaccessible/unreadable Job is never absence. Closing this temporary
    non-inherited query handle also avoids keeping a dead owner's Job alive.
    """
    if not valid_job_name(name):
        raise ValueError("invalid managed Host Job identity")
    handle = _kernel.OpenJobObjectW(0x0004, False, name)  # JOB_OBJECT_QUERY
    if not handle:
        error = ctypes.get_last_error()
        if error == 2:  # ERROR_FILE_NOT_FOUND, never access denied
            return None
        raise ctypes.WinError(error)
    try:
        return _active(handle)
    finally:
        _checked(_kernel.CloseHandle(handle))


class WindowsHostJob:
    """One execution's non-inherited, non-breakaway Job; no sandbox claims."""
    def __init__(self):
        self.name = "Local\\LoopXHost-" + uuid.uuid4().hex
        self.handle = _checked(_kernel.CreateJobObjectW(None, self.name))
        try:
            if ctypes.get_last_error() == 183:  # Never reuse another execution's Job.
                raise RuntimeError("managed Host Job identity already exists")
            limits = _ExtendedLimit()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, no breakaway
            _checked(_kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        except BaseException:
            _kernel.CloseHandle(self.handle)
            raise

    def __enter__(self):
        return self

    def assign(self, process) -> None:
        # Popen retains the original process HANDLE, so PID reuse cannot redirect this assignment.
        _checked(_kernel.AssignProcessToJobObject(self.handle, int(process._handle)))

    def finish(self) -> None:
        if _active(self.handle):
            _checked(_kernel.TerminateJobObject(self.handle, 1))
        deadline = time.monotonic() + 5
        while _active(self.handle):
            if time.monotonic() >= deadline:
                raise TimeoutError("managed Host Job still has active processes")
            time.sleep(.01)

    def __exit__(self, *_exc):
        try:
            self.finish()
        finally:
            _checked(_kernel.CloseHandle(self.handle))
