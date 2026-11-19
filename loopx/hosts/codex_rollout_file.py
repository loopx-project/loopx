"""Read-only Codex provider IO that does not prevent native log rotation."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import BinaryIO


_MAX_ROLLOUT_RECORD_BYTES = 16 * 1024 * 1024
_ROLLOUT_TIMESTAMP = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.(\d{1,9}))?(?:Z|[+-]\d\d:\d\d)')


def open_rollout(path: Path) -> BinaryIO:
    if os.name != "nt":
        return path.open("rb")

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    filename = os.path.abspath(path)
    if not filename.startswith("\\\\?\\"):
        filename = "\\\\?\\UNC\\" + filename[2:] if filename.startswith("\\\\") else "\\\\?\\" + filename
    # GENERIC_READ, FILE_SHARE_READ|WRITE|DELETE, OPEN_EXISTING. Sharing delete
    # permits the host's rename/replace; it grants this reader no write access.
    handle = create_file(filename, 0x80000000, 0x7, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        close_handle(handle)
        raise
    # open_osfhandle transfers handle ownership to the descriptor, and fdopen
    # transfers it to the returned stream. Release it if stream creation fails.
    try:
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _rollout_records(stream: BinaryIO, *, deadline: float):
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError('Failed native rollout readback window closed')
        raw = stream.readline(_MAX_ROLLOUT_RECORD_BYTES + 1)
        if not raw:
            return
        if len(raw) > _MAX_ROLLOUT_RECORD_BYTES or not raw.endswith(b'\n'):
            raise ValueError('Failed native rollout has an incomplete or oversized record')
        row = json.loads(raw)
        if not isinstance(row, dict) or not isinstance(row.get('payload'), dict):
            raise ValueError('Failed native rollout has an invalid record')
        yield raw, row, row['payload']


def read_failed_turn_cursor(path: Path, *, session_id: str, workspace: str,
                            process_started_at: float, process_stopped_at: float,
                            deadline: float) -> dict[str, str | float]:
    """Read one failed native Turn from the precise records of the observed process."""
    with open_rollout(path) as stream:
        records = _rollout_records(stream, deadline=deadline)
        _, first, meta = next(records, (None, {}, {}))
        if (first.get('type') != 'session_meta' or meta.get('id') != session_id
                or meta.get('cwd') != workspace):
            raise ValueError('Failed native rollout differs from the bound session')
        markers = []
        for raw, row, payload in records:
            kind = payload.get('type')
            if row.get('type') != 'event_msg' or kind not in (
                    'task_started', 'task_complete', 'task_completed', 'turn_aborted'):
                continue
            timestamp = row.get('timestamp')
            match = _ROLLOUT_TIMESTAMP.fullmatch(timestamp) if isinstance(timestamp, str) else None
            if match is None:
                raise ValueError('Failed native rollout requires precise turn timestamps')
            observed = datetime.fromisoformat(timestamp).timestamp()
            # A timestamp truncated to its recorded precision must fit entirely
            # within the observed process window.
            latest = observed + 10 ** -min(len(match[1] or ''), 6)
            if not markers and latest <= process_started_at:
                continue
            if observed < process_started_at or latest > process_stopped_at or len(markers) == 2:
                raise ValueError('Native rollout has another turn in or after the returned process window')
            markers.append((raw, kind, observed, payload))

    if len(markers) != 2:
        raise ValueError('Failed native rollout requires one returned turn')
    started_raw, start_kind, started, start = markers[0]
    ended_raw, end_kind, ended, end = markers[1]
    turn_id = start.get('turn_id')
    if (start_kind != 'task_started' or end_kind not in ('task_complete', 'task_completed') or started > ended
            or not isinstance(turn_id, str) or not turn_id or end.get('turn_id') != turn_id
            or not isinstance(end.get('error'), dict)):
        raise ValueError('Native rollout did not record the exact failed turn')
    return {'turn_id': turn_id, 'started_at': started, 'completed_at': ended,
            'record_sha256': hashlib.sha256(started_raw + ended_raw).hexdigest()}
