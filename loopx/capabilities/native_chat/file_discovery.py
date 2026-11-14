"""Small, invalidatable File Chat journal observations, never execution state."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from copy import deepcopy
from datetime import datetime
import hashlib
from pathlib import Path
import sys
from threading import Lock
from typing import Any


def _ordinary_request_text_digest(row: dict[str, Any]) -> str | None:
    """Match ordinary private text without retaining its body in discovery."""
    message = row.get("message")
    source = row.get("source")
    status = row.get("status")
    if (status not in ("accepted", "prepared") or row.get("command") is not None
            or row.get("capture_origin") is not None or row.get("agent_target") is not None
            or row.get("attachments") not in (None, []) or not isinstance(message, str) or not message
            or not row.get("session_id")
            or not isinstance(source, dict) or source.get("private_human_message") is not True):
        return None
    return hashlib.sha256(message.encode("utf-8", errors="surrogatepass")).hexdigest()


def source_control_fields(path: Path, row: dict[str, Any]) -> dict[str, Any] | None:
    """Keep exact source controls and the metadata needed to prove admission."""
    fields = ("binding_id", "source", "command", "created_at")
    # A tolerant reader can return an incomplete journal after a transient
    # failure. Do not cache that read as an unchanged-file observation.
    if not all(key in row for key in fields):
        return None
    result = {key: row[key] for key in fields}
    digest = _ordinary_request_text_digest(row)
    if digest is None:
        return result
    if row["status"] == "accepted" and row.get("turn_id"):
        result.update(text_sha256=digest, session_id=row["session_id"])
    elif (row["status"] == "prepared" and row.get("turn_id") is None
            and row.get("request_ref") == path.stem):
        result.update(text_sha256=digest, session_id=row["session_id"],
                      prepared_client_turn_id=f"external-{path.stem}", origin=row.get("origin", "lark"))
    return result


def later_equivalent_source_input(
    row: dict[str, Any],
    read_controls: Callable[[], list[dict[str, Any]]],
    read_turn_for_client: Callable[[str, str], dict[str, Any] | None],
) -> bool | None:
    """Check canonical acceptance in the same source and Session, even before dispatch."""
    if row.get("status") != "accepted" or not row.get("turn_id"):
        return None
    digest = _ordinary_request_text_digest(row)
    if digest is None:
        return None
    created = datetime.fromisoformat(row["created_at"])
    for item in read_controls():
        if (item.get("text_sha256") != digest or item["binding_id"] != row["binding_id"]
                or item["source"] != row["source"] or item["session_id"] != row["session_id"]
                or datetime.fromisoformat(item["created_at"]) <= created):
            continue
        client_turn_id = item.get("prepared_client_turn_id")
        if client_turn_id is None:
            return True
        # Core owns the durable queue receipt even if the outer journal has
        # not finished its write. Never treat preparation alone as admission.
        turn = read_turn_for_client(item["session_id"], client_turn_id)
        if (turn is not None and turn.get("origin") == item["origin"]
                and turn.get("_queue_original_message", turn.get("message")) == row["message"]
                and turn.get("attachments") in (None, [])
                and turn.get("external_agent_target") is None and turn.get("collaboration_request") is None):
            return True
    return False


def file_change_fingerprint(path: Path) -> tuple[int, ...] | None:
    """A local mutation token, or no reuse when the platform lacks one."""
    # Windows stat ctime can be creation time, not a mutation stamp. Keep
    # the original fresh reader there rather than trusting an mtime match.
    if sys.platform == "win32":
        return None
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


class FileChatDiscovery:
    """Keep only caller-selected metadata while the complete file is unchanged.

    Native Chat uses this for terminal-Turn discovery and source-control fields.
    Selected Turns/requests and every admission still use their original fresh
    readers. No body, grant, queue decision or provider effect is retained here.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._directories: dict[Path, dict[Path, tuple[tuple[int, ...], dict[str, Any]]]] = {}

    def rows(self, directory: Path, paths: Iterable[Path], *,
             read: Callable[[Path], dict[str, Any]],
             project: Callable[[Path, dict[str, Any]], dict[str, Any] | None],
             ) -> Iterator[tuple[Path, dict[str, Any]]]:
        seen = set()
        with self._lock:
            entries = self._directories.setdefault(directory, {})
        for path in paths:
            seen.add(path)
            try:
                before = file_change_fingerprint(path)
            except OSError:
                before = None
            with self._lock:
                saved = entries.get(path)
                if before is None or saved is None or saved[0] != before:
                    entries.pop(path, None)
                    saved = None
            if saved is not None:
                yield path, deepcopy(saved[1])
                continue
            # Preserve the owning reader's missing/unreadable/strict behavior.
            value = read(path)
            view = project(path, value)
            if before is not None and view is not None:
                try:
                    unchanged = file_change_fingerprint(path) == before
                except OSError:
                    unchanged = False
                if unchanged:
                    with self._lock:
                        entries[path] = before, deepcopy(view)
            yield path, value if view is None else view
        with self._lock:
            for path in entries.keys() - seen:
                del entries[path]
