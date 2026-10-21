"""Recoverable bounded IO for Codex activity; cached facts never execute work."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from threading import Lock
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .codex_rollout_json import SMALL_RECORD_BYTES, MetadataLine
from .control_plane.agents.host_thread_activity import (
    HostThreadActivity,
    HostThreadState,
    HostThreadUnknownReason,
)

CHUNK_BYTES = 256 * 1024
_ANCHOR_BYTES = 64


class Reader:
    def __init__(
        self, handle: BinaryIO, budget: int, *, chunk_bytes: int = CHUNK_BYTES
    ) -> None:
        self.handle, self.remaining, self.chunk_bytes = handle, budget, chunk_bytes

    def read(self, offset: int, count: int) -> bytes:
        count = min(count, self.remaining)
        self.handle.seek(offset)
        data = self.handle.read(count)
        self.remaining -= len(data)
        return data


@dataclass
class Fact:
    start: int
    end: int
    record: dict[str, Any]

    def activity(self, last_event: Fact) -> HostThreadActivity | None:
        if self.record["type"] != "event_msg":
            return None
        payload = self.record.get("payload")
        kind = payload.get("type") if isinstance(payload, dict) else None
        if not isinstance(kind, str):
            return HostThreadActivity.unknown(
                HostThreadUnknownReason.RECORD_UNRECOGNIZED
            )
        if kind == "task_started":
            return HostThreadActivity(
                state=HostThreadState.TURN_OPEN,
                turn_started_at=self.record["timestamp"],
                last_event_at=last_event.record["timestamp"],
            )
        if kind in ("task_complete", "turn_aborted"):
            return HostThreadActivity(
                state=HostThreadState.IDLE,
                last_turn_ended_at=self.record["timestamp"],
                last_event_at=last_event.record["timestamp"],
            )
        return None


def _fact(start: int, end: int, line: MetadataLine) -> Fact | None:
    record = line.finish()
    return Fact(start, end, record) if record else None


class Backward:
    def __init__(self, extent: int) -> None:
        self.pos = extent
        self.end: int | None = None
        self.append_floor: int | None = None
        self.pending: bytes | None = b""
        self.big: tuple[int, int, int, MetadataLine] | None = None
        self.latest: Fact | None = None
        self.marker: Fact | None = None
        self.invalid = False
        self.done = False

    def accept(self, fact: Fact | None, *, blank: bool = False) -> None:
        if blank:
            return
        if fact is None:
            self.invalid = self.done = True
            return
        self.latest = self.latest or fact
        activity = fact.activity(self.latest)
        if activity is not None:
            self.marker, self.done = fact, True
            self.invalid = activity.state is HostThreadState.UNKNOWN

    def _parse_big(self, reader: Reader) -> None:
        assert self.big is not None
        start, end, pos, line = self.big
        while pos < end and reader.remaining:
            data = reader.read(pos, min(reader.chunk_bytes, end - pos))
            if not data:
                return
            pos += len(data)
            line.feed(data)
        self.big = (start, end, pos, line)
        if pos == end:
            self.accept(_fact(start, end, line), blank=not line.nonblank)
            self.big = None

    def step(self, reader: Reader) -> None:
        while not self.done and reader.remaining:
            if self.big is not None:
                self._parse_big(reader)
                if self.big is not None or self.done:
                    return
            if self.pos == 0:
                if self.end is None:
                    self.invalid = self.done = True
                    return
                if self.end > SMALL_RECORD_BYTES:
                    self.big = (0, self.end, 0, MetadataLine())
                    self.end = None
                    continue
                line = MetadataLine()
                line.feed(self.pending or b"")
                self.accept(_fact(0, self.end, line), blank=not line.nonblank)
                self.done = True
                return
            lower = max(0, self.pos - min(reader.chunk_bytes, reader.remaining))
            data = reader.read(lower, self.pos - lower)
            self.pos = lower
            if self.end is None:
                index = data.rfind(b"\n")
                if index < 0:
                    continue
                self.end = lower + index
                self.append_floor = self.end + 1
                data = data[:index]
            while data and not self.done:
                index = data.rfind(b"\n")
                if index < 0:
                    if self.pending is not None:
                        self.pending = (
                            data + self.pending
                            if len(data) + len(self.pending) <= SMALL_RECORD_BYTES
                            else None
                        )
                    break
                start, end = lower + index + 1, self.end
                assert end is not None
                self.end = lower + index
                if end - start > SMALL_RECORD_BYTES:
                    self.big = (start, end, start, MetadataLine())
                    self.pos = self.end
                    self.pending = b""
                    break
                line = MetadataLine()
                line.feed(data[index + 1 :] + (self.pending or b""))
                self.pending = b""
                self.accept(_fact(start, end, line), blank=not line.nonblank)
                data = data[:index]


class Forward:
    def __init__(self, offset: int) -> None:
        self.pos = self.start = offset
        self.line = MetadataLine()
        self.latest: Fact | None = None
        self.marker: Fact | None = None
        self.invalid = False

    def step(self, reader: Reader, extent: int) -> None:
        while self.pos < extent and reader.remaining:
            data = reader.read(self.pos, min(reader.chunk_bytes, extent - self.pos))
            if not data:
                return
            pieces = data.split(b"\n")
            for index, piece in enumerate(pieces):
                self.line.feed(piece)
                self.pos += len(piece)
                if index == len(pieces) - 1:
                    break
                fact = _fact(self.start, self.pos, self.line)
                if self.line.nonblank:
                    if fact is None:
                        self.invalid = True
                    else:
                        self.latest = fact
                        activity = fact.activity(fact)
                        if activity is not None:
                            self.marker = fact
                            self.invalid = activity.state is HostThreadState.UNKNOWN
                self.pos += 1
                self.start = self.pos
                self.line = MetadataLine()


def _anchor(reader: Reader, extent: int) -> tuple[str, str]:
    head = reader.read(0, min(_ANCHOR_BYTES, extent))
    tail = reader.read(max(0, extent - _ANCHOR_BYTES), min(_ANCHOR_BYTES, extent))
    return hashlib.sha256(head).hexdigest(), hashlib.sha256(tail).hexdigest()


class RolloutCursor:
    def __init__(self, binding: str, thread_id: str) -> None:
        self.binding = binding
        self.lock = Lock()
        self.thread_id = thread_id
        self.saved_hint: tuple[str, str] | None = None
        self.identity: tuple[int, int] | None = None
        self.size = self.mtime = self.ctime = 0
        self.anchors: tuple[str, str] | None = None
        self.back: Backward | None = None
        self.forward: Forward | None = None
        self.header = MetadataLine()
        self.header_pos = 0
        self.header_valid = self.header_invalid = self.loaded = False

    def reset(self, stat: os.stat_result) -> None:
        self.identity = (stat.st_dev, stat.st_ino)
        self.size, self.mtime = stat.st_size, stat.st_mtime_ns
        self.ctime = stat.st_ctime_ns
        self.back = Backward(stat.st_size)
        self.forward = None
        self.anchors = None
        self.header = MetadataLine()
        self.header_pos = 0
        self.header_valid = self.header_invalid = self.loaded = False

    def _header(self, reader: Reader, extent: int) -> None:
        while reader.remaining and self.header_pos < extent and not self.header_invalid:
            data = reader.read(
                self.header_pos, min(reader.chunk_bytes, extent - self.header_pos)
            )
            if not data:
                return
            newline = data.find(b"\n")
            self.header.feed(data if newline < 0 else data[:newline])
            self.header_pos += len(data) if newline < 0 else newline + 1
            if newline >= 0:
                record = self.header.finish(header=True)
                payload = record.get("payload") if record else None
                self.header_valid = bool(
                    record
                    and record["type"] == "session_meta"
                    and isinstance(payload, dict)
                    and (payload.get("id") or payload.get("session_id"))
                    == self.thread_id
                )
                self.header_invalid = not self.header_valid
                return

    def advance(self, reader: Reader, stat: os.stat_result) -> HostThreadActivity:
        assert self.back is not None
        if self.forward is None and self.back.append_floor is not None:
            self.forward = Forward(self.back.append_floor)
        if self.forward is not None:
            # New records get priority without starving cold historical recovery.
            allowance = (
                reader.remaining if self.back.done else max(1, reader.remaining // 4)
            )
            sub = Reader(reader.handle, allowance, chunk_bytes=reader.chunk_bytes)
            self.forward.step(sub, stat.st_size)
            reader.remaining -= allowance - sub.remaining
            if self.forward.pos == stat.st_size and self.forward.marker is not None:
                self.back.marker = self.forward.marker
                self.back.latest = self.forward.latest
                self.back.invalid = self.forward.invalid
                self.back.done = True
        if not self.back.done:
            self.back.step(reader)
        if self.forward is None and self.back.append_floor is not None:
            self.forward = Forward(self.back.append_floor)
        if (
            self.forward is not None
            and self.forward.pos < stat.st_size
            and reader.remaining
        ):
            self.forward.step(reader, stat.st_size)
        if (
            self.forward is not None
            and self.forward.pos == stat.st_size
            and self.forward.marker is not None
        ):
            self.back.marker, self.back.latest = (
                self.forward.marker,
                self.forward.latest,
            )
            self.back.invalid, self.back.done = self.forward.invalid, True
        latest = (
            self.forward.latest if self.forward is not None else None
        ) or self.back.latest
        marker = (
            self.forward.marker if self.forward is not None else None
        ) or self.back.marker
        if self.back.invalid or (self.forward is not None and self.forward.invalid):
            return HostThreadActivity.unknown(
                HostThreadUnknownReason.RECORD_UNRECOGNIZED
            )
        if not self.back.done or (
            self.forward is not None and self.forward.pos < stat.st_size
        ):
            return HostThreadActivity.unknown(HostThreadUnknownReason.NO_TURN_MARKER)
        activity = (
            marker.activity(latest)
            if marker is not None and latest is not None
            else None
        )
        return activity or HostThreadActivity.unknown(
            HostThreadUnknownReason.NO_TURN_MARKER
        )

    def observe(
        self, reader: Reader, stat: os.stat_result, *, cache_root: Path | None = None
    ) -> HostThreadActivity:
        anchors = _anchor(reader, stat.st_size)
        identity = (stat.st_dev, stat.st_ino)
        if (
            self.identity != identity
            or stat.st_size < self.size
            or stat.st_mtime_ns < self.mtime
            or (
                stat.st_size == self.size
                and (stat.st_mtime_ns != self.mtime or stat.st_ctime_ns != self.ctime)
            )
        ):
            self.reset(stat)
        elif self.anchors is not None and _anchor(reader, self.size) != self.anchors:
            self.reset(stat)
        if self.back is None:
            self.reset(stat)
        if not self.header_valid:
            self._header(reader, stat.st_size)
            if not self.header_valid:
                self.size, self.mtime, self.ctime, self.anchors = (
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                    anchors,
                )
                return HostThreadActivity.unknown(
                    HostThreadUnknownReason.RECORD_UNRECOGNIZED
                )
        if not self.loaded:
            self.load(cache_root, reader, stat)
            # Cache load/reset cannot certify or erase the independently read header.
            self.header_valid = self.loaded = True
        # Also verify retained marker/event bytes; append metadata is not a clock.
        if self.back is not None and self.back.done and not self._verify_facts(reader):
            self.reset(stat)
        result = self.advance(reader, stat)
        self.size, self.mtime = stat.st_size, stat.st_mtime_ns
        self.ctime = stat.st_ctime_ns
        self.anchors = anchors
        return result

    def _verify_facts(self, reader: Reader) -> bool:
        assert self.back is not None
        facts = [self.back.marker, self.back.latest]
        for fact in facts:
            if fact is None or fact.end - fact.start > SMALL_RECORD_BYTES:
                continue
            if reader.remaining < fact.end - fact.start + 2:
                return False
            data = reader.read(
                max(0, fact.start - 1), fact.end - max(0, fact.start - 1) + 1
            )
            if (fact.start and not data.startswith(b"\n")) or not data.endswith(b"\n"):
                return False
            line = MetadataLine()
            line.feed(data[1:-1] if fact.start else data[:-1])
            if line.finish() != fact.record:
                return False
        return True

    def save(self, root: Path | None) -> None:
        if (
            root is None
            or self.back is None
            or not self.back.done
            or self.back.marker is None
            or self.back.latest is None
        ):
            return
        latest = (self.forward.latest if self.forward else None) or self.back.latest
        marker = (self.forward.marker if self.forward else None) or self.back.marker
        if any(f.end - f.start > SMALL_RECORD_BYTES for f in (latest, marker)):
            return
        body = {
            "version": 1,
            "binding": self.binding,
            "identity": self.identity,
            "size": self.size,
            "mtime": self.mtime,
            "ctime": self.ctime,
            "anchors": self.anchors,
            "marker": vars(marker),
            "latest": vars(latest),
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        if self.saved_hint == (str(root), digest):
            return
        wrapper = json.dumps({"body": body, "sha256": digest})
        temporary = None
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Fixed slots bound on-disk retention without directory-wide scans.
            with tempfile.NamedTemporaryFile(
                mode="w", dir=root, prefix=".activity-", delete=False
            ) as stream:
                stream.write(wrapper)
                temporary = Path(stream.name)
            os.replace(temporary, root / (self.binding[:2] + ".json"))
            self.saved_hint = (str(root), digest)
        except OSError:
            pass  # Optional observation cache failure never becomes host authority.
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def load(self, root: Path | None, reader: Reader, stat: os.stat_result) -> None:
        if root is None:
            return
        try:
            path = root / (self.binding[:2] + ".json")
            if path.is_symlink() or path.stat().st_size > 8192:
                return
            with path.open("rb") as handle:
                raw_hint = handle.read(8192)
                if os.fstat(handle.fileno()).st_size > 8192:
                    return
            wrapper = json.loads(raw_hint)
            body = wrapper["body"]
            encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
            if (
                wrapper["sha256"] != hashlib.sha256(encoded.encode()).hexdigest()
                or body["version"] != 1
                or body["binding"] != self.binding
            ):
                return
            if (
                body["identity"] != [stat.st_dev, stat.st_ino]
                or not 0 <= body["size"] <= stat.st_size
            ):
                return
            if body["mtime"] > stat.st_mtime_ns or (
                body["size"] == stat.st_size
                and (
                    body["mtime"] != stat.st_mtime_ns
                    or body["ctime"] != stat.st_ctime_ns
                )
            ):
                return
            self.reset(stat)
            self.size, self.mtime = body["size"], body["mtime"]
            self.ctime = body["ctime"]
            self.anchors = tuple(body["anchors"])
            assert self.back is not None
            for key in ("marker", "latest"):
                raw = body[key]
                if (
                    not isinstance(raw["start"], int)
                    or not isinstance(raw["end"], int)
                    or not 0 <= raw["start"] <= raw["end"] < self.size
                    or raw["end"] - raw["start"] > SMALL_RECORD_BYTES
                ):
                    raise ValueError("invalid fact extent")
                setattr(self.back, key, Fact(raw["start"], raw["end"], raw["record"]))
            if _anchor(reader, self.size) != self.anchors or not self._verify_facts(
                reader
            ):
                raise ValueError("stale observation cache")
            self.back.done = True
            assert self.back.latest is not None
            self.forward = Forward(self.back.latest.end + 1)
            self.saved_hint = (str(root), wrapper["sha256"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.reset(stat)
