"""File-backed Chat journal discovery and bounded reply image artifacts."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from copy import deepcopy
from datetime import datetime
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote
from threading import Lock
from typing import Any


def external_source_route_path(root: Path, binding_id: str, source_ref: str) -> Path:
    """Locate the original source's cross-process admission fence."""
    return root / "source-fences" / f"{binding_id}.{source_ref}.json"


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


_RECOVERY_JOURNAL_FIELDS = (
    "request_ref",
    "status",
    "delivery_verified",
    "commission_adoption_pending",
    "agent_target",
    "session_id",
    "turn_id",
    "binding_id",
    "source",
    "capture_origin",
    "native_mention",
    "created_at",
    "commission_resources",
)


def recovery_journal_fields(_path: Path, row: dict[str, Any]) -> dict[str, Any] | None:
    """Keep only bounded recovery/commission metadata in the long-lived file cache."""
    if not isinstance(row, dict) or "request_ref" not in row or "status" not in row:
        return None
    return {key: row[key] for key in _RECOVERY_JOURNAL_FIELDS if key in row}


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


_MISSING_FILE_FP: tuple[int, ...] = (-1,)


def file_change_fingerprint(path: Path) -> tuple[int, ...] | None:
    """A local mutation token, or no reuse when the platform lacks one."""
    # Windows stat ctime can be creation time, not a mutation stamp. Keep
    # the original fresh reader there rather than trusting an mtime match.
    if sys.platform == "win32":
        return None
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


def optional_file_fingerprint(path: Path) -> tuple[int, ...] | None:
    """Return a file fingerprint or a sentinel when absent; None when unavailable."""
    try:
        return file_change_fingerprint(path)
    except FileNotFoundError:
        return None if sys.platform == "win32" else _MISSING_FILE_FP
    except OSError:
        return None


def _set_bounded_cache(cache_map: dict[Any, Any], key: Any, value: Any, max_entries: int) -> None:
    if key in cache_map:
        del cache_map[key]
    elif len(cache_map) >= max_entries:
        del cache_map[next(iter(cache_map))]
    cache_map[key] = value


class QuotaBacklogObservationCache:
    """Revision-bound cache for read-only quota backlog health classification.

    Stores only pure boolean verdicts and Turn status labels keyed by file
    fingerprints, Session status, and evaluator identity. Never retains Turn
    bodies or request attachments.
    """

    def __init__(self, *, max_entries: int = 8192) -> None:
        self._lock = Lock()
        self._max_entries = max(1, max_entries)
        self._base: dict[str, tuple[tuple[Any, ...], tuple[bool, str | None]]] = {}
        self._stopped: dict[str, tuple[tuple[Any, ...], bool]] = {}
        self._event_summaries: dict[tuple[str, str], tuple[tuple[int, ...], tuple[dict[str, Any] | None, bool, bool]]] = {}

    def get_base(self, request_ref: str, key: tuple[Any, ...]) -> tuple[bool, str | None] | None:
        with self._lock:
            entry = self._base.get(request_ref)
            return entry[1] if entry is not None and entry[0] == key else None

    def has_base_prefix(self, request_ref: str, key_prefix: tuple[Any, ...]) -> bool:
        with self._lock:
            entry = self._base.get(request_ref)
            return entry is not None and entry[0][:-1] == key_prefix

    def put_base(self, request_ref: str, key: tuple[Any, ...], pending: bool, turn_status: str | None) -> None:
        with self._lock:
            _set_bounded_cache(self._base, request_ref, (key, (pending, turn_status)), self._max_entries)
            stopped = self._stopped.get(request_ref)
            if stopped is not None and stopped[0] != key:
                self._stopped.pop(request_ref, None)

    def get_stopped(self, request_ref: str, key: tuple[Any, ...]) -> bool | None:
        with self._lock:
            entry = self._stopped.get(request_ref)
            return entry[1] if entry is not None and entry[0] == key else None

    def put_stopped(self, request_ref: str, key: tuple[Any, ...], pending: bool) -> None:
        with self._lock:
            _set_bounded_cache(self._stopped, request_ref, (key, pending), self._max_entries)

    def drop_request(self, request_ref: str) -> None:
        with self._lock:
            self._base.pop(request_ref, None)
            self._stopped.pop(request_ref, None)

    @staticmethod
    def build_key_prefix(
        root: Path, store: Any, row: dict[str, Any], evaluator_id: int,
        *, req_fp_override: tuple[int, ...] | None = None,
    ) -> tuple[Any, Path | None, Path | None, tuple[int, ...] | None, tuple[int, ...] | None]:
        try:
            req_path = root / f"{row['request_ref']}.json"
            turn_path = store._turn_path(row["session_id"], row["turn_id"])
            req_fp = req_fp_override if isinstance(req_fp_override, tuple) else optional_file_fingerprint(req_path)
            turn_fp = optional_file_fingerprint(turn_path)
        except (OSError, ValueError):
            return None, None, None, None, None
        if req_fp is None or turn_fp is None:
            return None, req_path, turn_path, req_fp, turn_fp
        prefix = (req_fp, turn_fp, row["session_id"], row["turn_id"],
                  row.get("status"), row.get("command"), evaluator_id)
        return prefix, req_path, turn_path, req_fp, turn_fp

    def commit(self, request_ref: str, meta_entry: tuple[Any, ...], pending: bool, *,
               turn_status: str | None = None, stopped: bool = False) -> None:
        key, req_path, turn_path, req_fp, turn_fp = meta_entry
        if key is None or req_path is None or turn_path is None:
            return
        if (optional_file_fingerprint(req_path), optional_file_fingerprint(turn_path)) != (req_fp, turn_fp):
            self.drop_request(request_ref)
            return
        if stopped:
            self.put_stopped(request_ref, key, pending)
        else:
            self.put_base(request_ref, key, pending, turn_status)

    def get_event_summary(
        self, session_id: str, turn_id: str, events_fp: tuple[int, ...],
    ) -> tuple[dict[str, Any] | None, bool, bool] | None:
        with self._lock:
            entry = self._event_summaries.get((session_id, turn_id))
            if entry is None or entry[0] != events_fp:
                return None
            anchor, steering, interrupted = entry[1]
            return (deepcopy(anchor) if anchor is not None else None), steering, interrupted

    def put_event_summary(
        self, session_id: str, turn_id: str, events_fp: tuple[int, ...],
        summary: tuple[dict[str, Any] | None, bool, bool],
    ) -> None:
        anchor, steering, interrupted = summary
        stored = (deepcopy(anchor) if anchor is not None else None), steering, interrupted
        with self._lock:
            _set_bounded_cache(self._event_summaries, (session_id, turn_id), (events_fp, stored), self._max_entries)

    def drop_event_summary(self, session_id: str, turn_id: str) -> None:
        with self._lock:
            self._event_summaries.pop((session_id, turn_id), None)

    def get_interrupt(self, session_id: str, turn_id: str, events_fp: tuple[int, ...]) -> bool | None:
        summary = self.get_event_summary(session_id, turn_id, events_fp)
        return summary[2] if summary is not None else None

    def put_interrupt(self, session_id: str, turn_id: str, events_fp: tuple[int, ...], observed: bool) -> None:
        self.put_event_summary(session_id, turn_id, events_fp, (None, False, observed))

    def drop_interrupt(self, session_id: str, turn_id: str) -> None:
        self.drop_event_summary(session_id, turn_id)


def _summarize_turn_events(events: Sequence[dict[str, Any]]) -> tuple[dict[str, Any] | None, bool, bool]:
    anchors = [
        item["payload"]["native_input"]
        for item in events
        if item.get("kind") == "turn.started" and item.get("payload", {}).get("native_input")
    ]
    single_anchor = deepcopy(anchors[0]) if len(anchors) == 1 else None
    steering = any(e.get("kind") == "turn.steered" for e in events)
    interrupted = any(e.get("kind") in {"turn.interrupt_requested", "turn.interrupted"} for e in events)
    return single_anchor, steering, interrupted


def observed_turn_event_summary(
    store: Any,
    session_id: str,
    turn_id: str,
    cache_store: QuotaBacklogObservationCache | None,
    *,
    real_events: bool,
) -> tuple[dict[str, Any] | None, bool, bool]:
    """Read compact Turn event summary with fingerprint/pending-buffer guard on real stores."""
    if not real_events or cache_store is None:
        return _summarize_turn_events(store.events_after(session_id, turn_id, None))
    key = (session_id, turn_id)
    try:
        event_path = store._event_path(session_id, turn_id)
    except ValueError:
        return _summarize_turn_events(store.events_after(session_id, turn_id, None))
    flush_lock = store._event_flush_lock(key)
    fp_before: tuple[int, ...] | None = None
    if flush_lock.acquire(blocking=False):
        try:
            with store._event_lock:
                has_pending = bool(store._event_pending.get(key))
            if not has_pending:
                fp_before = optional_file_fingerprint(event_path)
                if fp_before is not None:
                    cached = cache_store.get_event_summary(session_id, turn_id, fp_before)
                    if cached is not None:
                        return cached
        finally:
            flush_lock.release()
    summary = _summarize_turn_events(store.events_after(session_id, turn_id, None))
    if fp_before is not None and flush_lock.acquire(blocking=False):
        try:
            with store._event_lock:
                has_pending_after = bool(store._event_pending.get(key))
            fp_after = None if has_pending_after else optional_file_fingerprint(event_path)
            if fp_after == fp_before:
                cache_store.put_event_summary(session_id, turn_id, fp_after, summary)
            else:
                cache_store.drop_event_summary(session_id, turn_id)
        finally:
            flush_lock.release()
    return summary


def observed_turn_interrupt(
    store: Any,
    session_id: str,
    turn_id: str,
    cache_store: QuotaBacklogObservationCache | None,
    *,
    real_events: bool,
) -> bool:
    """Check Turn interrupt events with fingerprint/pending-buffer guard on real stores."""
    return observed_turn_event_summary(
        store, session_id, turn_id, cache_store, real_events=real_events,
    )[2]


def _same_store_method(store: Any, name: str, expected_cls: type[Any]) -> bool:
    return isinstance(store, expected_cls) and getattr(getattr(store, name, None), "__func__", None) is getattr(expected_cls, name)


def observe_quota_backlog_batch(
    *,
    root: Path,
    store: Any,
    rows: Sequence[dict[str, Any]],
    build_inputs: Callable[[dict[str, Any]], dict[str, Any]],
    eval_flags: Callable[[Sequence[dict[str, Any]]], list[bool]],
    stop_controls: Callable[[], tuple[tuple[Any, Any, datetime], ...]],
    evaluator_id: int,
    cache_store: QuotaBacklogObservationCache | None,
) -> list[bool]:
    """Evaluate backlog pending flags with optional revision-bound caching."""
    from ...chat_store import ChatSessionStore

    real_load_turn = cache_store is not None and _same_store_method(store, "load_turn", ChatSessionStore)
    real_events = cache_store is not None and _same_store_method(store, "events_after", ChatSessionStore)
    flags: list[bool] = [False] * len(rows)
    turn_statuses: list[str | None] = [None] * len(rows)
    params: list[dict[str, Any] | None] = [None] * len(rows)
    meta: list[tuple[Any, ...]] = [(None, None, None, None, None)] * len(rows)
    for index, row in enumerate(rows):
        entry = (cache_store.build_key_prefix(root, store, row, evaluator_id, req_fp_override=getattr(row, "req_fp", None))
                 if real_load_turn and cache_store is not None else (None, None, None, None, None))
        meta[index] = entry
        if entry[0] is not None and cache_store is not None and cache_store.has_base_prefix(row["request_ref"], entry[0]):
            continue
        params[index] = build_inputs(row)
    load_session = store.load_session
    sessions: dict[str, dict[str, Any] | None] = {}
    for session_id in dict.fromkeys(row["session_id"] for row in rows):
        # A transient read failure must not mask a closed Session.
        session = load_session(session_id) or load_session(session_id)
        sessions[session_id] = (
            {key: session[key] for key in ("session_id", "status") if key in session}
            if session is not None else None
        )
    misses: list[tuple[int, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        session_row = sessions[row["session_id"]]
        prefix, req_path, turn_path, req_fp, turn_fp = meta[index]
        if prefix is not None and cache_store is not None:
            session_status = session_row.get("status") if session_row is not None else None
            full_key = (*prefix, session_status)
            meta[index] = (full_key, req_path, turn_path, req_fp, turn_fp)
            cached = cache_store.get_base(row["request_ref"], full_key)
            if cached is not None:
                flags[index], turn_statuses[index] = cached
                continue
        values = params[index] if params[index] is not None else build_inputs(row)
        values["current_session"] = session_row
        params[index] = values
        misses.append((index, values))
    if misses:
        for (index, values), pending in zip(misses, eval_flags([v for _, v in misses]), strict=True):
            turn_dict = values["pending_observation"]["turn"]
            turn_status = turn_dict.get("status") if isinstance(turn_dict, dict) else None
            flags[index], turn_statuses[index] = pending, turn_status
            if cache_store is not None:
                cache_store.commit(rows[index]["request_ref"], meta[index], pending, turn_status=turn_status)
    stopped_misses: list[tuple[int, dict[str, Any]]] = []
    for index, (row, pending) in enumerate(zip(rows, flags, strict=True)):
        if not pending:
            continue
        if not observed_turn_interrupt(store, row["session_id"], row["turn_id"], cache_store, real_events=real_events):
            if turn_statuses[index] not in {"failed", "completed"}:
                continue
            row_created = datetime.fromisoformat(row["created_at"])
            if not any(b == row["binding_id"] and s == row["source"] and c >= row_created
                       for b, s, c in stop_controls()):
                continue
        cache_key = meta[index][0]
        if cache_store is not None and cache_key is not None:
            cached_stop = cache_store.get_stopped(row["request_ref"], cache_key)
            if cached_stop is not None:
                flags[index] = cached_stop
                continue
        values = params[index]
        if values is None:
            values = build_inputs(row)
            values["current_session"] = sessions[row["session_id"]]
        values["pending_observation"]["stop_observed"] = True
        stopped_misses.append((index, values))
    if stopped_misses:
        for (index, _), pending in zip(stopped_misses, eval_flags([v for _, v in stopped_misses]), strict=True):
            flags[index] = pending
            if cache_store is not None:
                cache_store.commit(rows[index]["request_ref"], meta[index], pending, stopped=True)
    return flags


class FileChatDiscovery:
    """Keep only caller-selected metadata while the complete file is unchanged.

    Native Chat uses this for terminal-Turn discovery and source-control fields.
    Selected Turns/requests and every admission still use their original fresh
    readers. No body, grant, queue decision or provider effect is retained here.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._directories: dict[Path, dict[Path, tuple[tuple[int, ...], dict[str, Any]]]] = {}

    def rows_with_fingerprints(
        self,
        directory: Path,
        paths: Iterable[Path],
        *,
        read: Callable[[Path], dict[str, Any]],
        project: Callable[[Path, dict[str, Any]], dict[str, Any] | None],
        known_fingerprints: Mapping[Path, tuple[int, ...]] | None = None,
    ) -> Iterator[tuple[Path, dict[str, Any], tuple[int, ...] | None]]:
        seen = set()
        with self._lock:
            entries = self._directories.setdefault(directory, {})
        for path in paths:
            seen.add(path)
            before = known_fingerprints.get(path) if known_fingerprints is not None else None
            if before is None:
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
                yield path, deepcopy(saved[1]), before
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
            yield path, (value if view is None else view), before
        with self._lock:
            for path in entries.keys() - seen:
                del entries[path]

    def rows(self, directory: Path, paths: Iterable[Path], *,
             read: Callable[[Path], dict[str, Any]],
             project: Callable[[Path, dict[str, Any]], dict[str, Any] | None],
             ) -> Iterator[tuple[Path, dict[str, Any]]]:
        for path, view, _ in self.rows_with_fingerprints(directory, paths, read=read, project=project):
            yield path, view


_REPLY_IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}
_REPLY_IMAGE_SIGNATURES = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/gif": b"GIF8"}
REPLY_IMAGE_NAME_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}")


def reply_image_directory(turn_id: str) -> Path:
    """Return the sole workspace output directory for this exact Core Turn."""
    if not re.fullmatch(r"[a-f0-9]{32}", turn_id):
        raise ValueError("invalid reply image Turn")
    return Path(".local/loopx-reply-images") / turn_id


def reply_image_workspace(session: dict[str, Any], turn: dict[str, Any]) -> Path | None:
    """Local image output belongs to an ordinary, bound-owner Lark project Turn."""
    context = session.get("project_context") or {}
    if (turn.get("origin") != "lark" or session.get("session_mode") != "managed_runtime"
            or session.get("agent_id") != "codex" or context.get("audience") != "bound_owner"
            or context.get("grant") != "workspace_write" or not context.get("workspace_path")
            or session.get("steward_context")):
        return None
    return Path(context["workspace_path"]).resolve()


def _check_reply_image(data: bytes, mime: str) -> None:
    from ...chat.attachments import CHAT_IMAGE_MAX_BYTES

    if not data or len(data) > CHAT_IMAGE_MAX_BYTES:
        raise ValueError("reply image is empty or exceeds its size bound")
    if not ((mime in _REPLY_IMAGE_SIGNATURES and data.startswith(_REPLY_IMAGE_SIGNATURES[mime]))
            or (mime == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP")):
        raise ValueError("unsupported reply image bytes")


def reply_image_attachment(data: bytes, mime: str, index: int) -> dict[str, Any]:
    _check_reply_image(data, mime)
    return {"id": f"reply-image-{index}", "name": f"original-{index}.{mime.split('/')[1]}",
            "mime_type": mime, "size": len(data),
            "data_url": f"data:{mime};base64," + base64.b64encode(data).decode("ascii")}


def read_reply_image(workspace: Path, turn_id: str, name: str) -> tuple[str, bytes]:
    from ...control_plane.collaboration.result_files import _workspace_file

    if not REPLY_IMAGE_NAME_PATTERN.fullmatch(name):
        raise ValueError("invalid reply image name")
    mime = _REPLY_IMAGE_TYPES.get(Path(name).suffix.lower())
    if mime is None:
        raise ValueError("unsupported reply image type")
    ref = reply_image_directory(turn_id) / name
    data = _workspace_file(workspace, ref)
    _check_reply_image(data, mime)
    return mime, data


def capture_reply_image_plan(workspace: Path, turn_id: str, targets: list[str]) -> list[str | dict[str, Any] | None]:
    """Pin exact local bytes without keeping host paths in Chat responses."""
    from ...chat.attachments import CHAT_IMAGE_MAX_COUNT, CHAT_IMAGE_MAX_TOTAL_BYTES

    if all(target.startswith("https://") for target in targets):
        return []  # The original remote-image path remains authoritative.
    if len(targets) > CHAT_IMAGE_MAX_COUNT:
        return [None]
    output_dir = workspace / reply_image_directory(turn_id)
    result: list[str | dict[str, Any] | None] = []
    total = 0
    for target in targets:
        if target.startswith("https://"):
            result.append(target)
            continue
        try:
            path = Path(unquote(target))
            if path.parent != output_dir or ".." in path.parts:
                raise ValueError("reply image is outside the current Turn's output directory")
            _, data = read_reply_image(workspace, turn_id, path.name)
            total += len(data)
            if total > CHAT_IMAGE_MAX_TOTAL_BYTES:
                raise ValueError("reply images exceed their aggregate size bound")
            result.append({"name": path.name, "sha256": hashlib.sha256(data).hexdigest()})
        except (OSError, ValueError):
            result.append(None)
    return result


class _DeferredBacklogRequest(dict[str, Any]):
    """Bounded backlog request view carrying its observed file fingerprint."""

    __slots__ = ("req_fp", "needs_full_read")

    def __init__(
        self,
        data: Mapping[str, Any],
        *,
        req_fp: tuple[int, ...] | None,
        needs_full_read: bool,
    ) -> None:
        super().__init__(data)
        self.req_fp = req_fp
        self.needs_full_read = needs_full_read


def _preparation_image_identity(value: Any) -> dict[str, Any] | None:
    """Fingerprint already normalized Core images without exporting their bytes."""
    from ...chat.attachments import normalize_chat_image_attachments

    if not isinstance(value, list) or not value:
        return None
    try:
        images = normalize_chat_image_attachments(value)
        if images != value:
            return None
        encoded = json.dumps(images, sort_keys=True, separators=(",", ":")).encode()
        return {"count": len(images), "sha256": hashlib.sha256(encoded).hexdigest()}
    except (ValueError, TypeError, OverflowError):
        return None


def _project_source_request_digest(_path: Path, item: dict[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "request_ref": item.get("request_ref"),
        "binding_id": item.get("binding_id"),
        "source": item.get("source"),
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _project_user_message_hint(message: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: message[key]
        for key in ("role", "turn_id", "text", "origin", "created_at")
        if key in message
    }


class _RecoveryDiscoveryReads:
    """Lazy facts for one queue-discovery pass, never recovery admission.

    Historical requests share Session history. Retain queue/control/history
    hints, never provider identity or a recovery decision. A new
    pass builds new observations; normal admission never receives this object.
    """

    def __init__(self, core: Any) -> None:
        self.core = core
        self.queues: dict[str, bool] = {}
        self.histories: dict[str, list[dict[str, Any]]] = {}
        self.message_indexes: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self.controls: list[dict[str, Any]] | None = None

    def queued(self, session_id: str) -> bool:
        if session_id not in self.queues:
            self.queues[session_id] = bool(self.core.controller.store.queued_turns(session_id))
        return self.queues[session_id]

    def user_messages(self, session_id: str) -> list[dict[str, Any]]:
        if session_id not in self.histories:
            self.histories[session_id] = list(self.core._cached_user_messages(session_id))
        return self.histories[session_id]

    def source_controls(self) -> list[dict[str, Any]]:
        if self.controls is None:
            self.controls = self.core.source_controls()
        return self.controls

    def matching_user_messages(self, session_id: str, text: str) -> list[dict[str, Any]]:
        if session_id not in self.message_indexes:
            index: dict[str, list[dict[str, Any]]] = {}
            for row in self.user_messages(session_id):
                index.setdefault(row.get("text", ""), []).append(row)
            self.message_indexes[session_id] = index
        return self.message_indexes[session_id].get(text, [])
