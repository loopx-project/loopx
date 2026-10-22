"""Bounded, public-safe observation of terminal Codex provider failures."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path


_DEFAULT_TAIL_BYTES = 2 * 1024 * 1024
_DEFAULT_MAX_FILES = 32
_TERMINAL_EVENT_TYPES = {"task_complete", "task_completed", "turn_completed"}
_HTTP_FAILURE_KINDS = {
    "responseTooManyFailedAttempts": "response_too_many_failed_attempts",
    "response_too_many_failed_attempts": "response_too_many_failed_attempts",
}


def _jsonl_tail(path: Path, *, tail_bytes: int) -> Iterator[Mapping[str, object]]:
    """Yield complete JSON objects from a bounded file tail.

    Starting in the middle of a record is expected for long sessions.  The
    partial first line is always discarded instead of being repaired or parsed.
    """

    if tail_bytes <= 0 or path.is_symlink():
        return
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            start = max(0, size - tail_bytes)
            stream.seek(start)
            data = stream.read(tail_bytes)
    except OSError:
        return
    if start:
        _, separator, data = data.partition(b"\n")
        if not separator:
            return
    for line in data.splitlines():
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(value, Mapping):
            yield value


def _http_status(value: Mapping[str, object]) -> int | None:
    statuses = [value[key] for key in ("httpStatusCode", "http_status_code") if key in value]
    valid = {status for status in statuses if type(status) is int and 100 <= status <= 599}
    return next(iter(valid)) if len(valid) == 1 else None


def _terminal_provider_failure(
    event: Mapping[str, object],
) -> tuple[bool, dict[str, object] | None]:
    if event.get("type") != "event_msg":
        return False, None
    payload = event.get("payload")
    if not isinstance(payload, Mapping) or payload.get("type") not in _TERMINAL_EVENT_TYPES:
        return False, None
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return True, None
    infos = [error[key] for key in ("codexErrorInfo", "codex_error_info") if key in error]
    if len(infos) != 1 or not isinstance(infos[0], Mapping) or len(infos[0]) != 1:
        return True, None
    raw_kind, detail = next(iter(infos[0].items()))
    kind = _HTTP_FAILURE_KINDS.get(raw_kind)
    if kind is None or not isinstance(detail, Mapping):
        return True, None
    status = _http_status(detail)
    if status is None:
        return True, None
    return True, {
        "schema_version": "codex_terminal_provider_failure_v0",
        "kind": kind,
        "http_status_code": status,
    }


def observe_terminal_provider_failure(
    *session_roots: Path,
    tail_bytes: int = _DEFAULT_TAIL_BYTES,
    max_files: int = _DEFAULT_MAX_FILES,
) -> dict[str, object] | None:
    """Return the newest allowlisted terminal failure from collected sessions.

    Only structured terminal events are considered.  Provider prose, local
    paths, request messages and model output never enter the returned receipt.
    """

    if tail_bytes <= 0 or max_files <= 0:
        raise ValueError("tail_bytes and max_files must be positive")
    candidates: list[tuple[int, Path]] = []
    for root in session_roots:
        if not root.is_dir() or root.is_symlink():
            continue
        for path in root.rglob("*.jsonl"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                candidates.append((path.stat().st_mtime_ns, path))
            except OSError:
                continue
    for _, path in sorted(candidates, reverse=True)[:max_files]:
        for event in reversed(list(_jsonl_tail(path, tail_bytes=tail_bytes))):
            terminal, failure = _terminal_provider_failure(event)
            if terminal:
                return failure
    return None
