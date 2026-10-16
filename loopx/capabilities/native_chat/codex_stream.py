"""Visible unfinished work from the original Desktop owner, without execution.

This version-sensitive provider vocabulary is local to Codex. Saved history and
input eligibility retain their existing owners. No raw snapshot is persisted or
returned to the browser; compact it before retaining the patch baseline.
"""

from __future__ import annotations

from collections.abc import Generator
import hashlib
import json
from pathlib import Path
import select
import time
from typing import Any

from ...public_safe_text import CATEGORY_CREDENTIAL, classify_private_text
from .codex_attached import DesktopClient, DesktopProtocolError

# Observed Desktop snapshots exceed the ordinary 8 MiB RPC bound. This separate
# read-only bound never changes the input/RPC limit; browser output is smaller.
STREAM_FRAME_BYTES = 64 * 1024 * 1024
VIEW_BYTES = 256 * 1024
FIELD_BYTES = 64 * 1024
MAX_ITEMS = 4096
_ITEM_FIELDS = {
    "agentMessage": {"id", "type", "text", "phase"},
    "commandExecution": {
        "id",
        "type",
        "command",
        "aggregatedOutput",
        "status",
        "exitCode",
    },
    "mcpToolCall": {
        "id",
        "type",
        "server",
        "tool",
        "arguments",
        "result",
        "error",
        "status",
    },
    "fileChange": {"id", "type", "changes", "status"},
    "error": {"id", "type", "message", "willRetry"},
}


def _item(value: Any) -> dict[str, Any] | None:
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("type"), str)
        or value["type"] not in _ITEM_FIELDS
    ):
        return None  # Includes reasoning, user input and unknown host items.
    result: dict[str, Any] = {"_unavailable": True} if value.get("_unavailable") else {}
    for key in _ITEM_FIELDS[value["type"]]:
        field = value.get(key)
        if key == "result" and isinstance(field, dict):
            content = field.get("content")
            if not isinstance(content, list):
                result["_unavailable"] = True
                continue
            field = {
                "content": [
                    {"type": "text", "text": part["text"]}
                    for part in content
                    if isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                ]
            }
        if key == "error" and isinstance(field, dict):
            field = {"message": field.get("message")}
        if key == "changes" and isinstance(field, list):
            field = [
                {
                    **{key: row.get(key) for key in ("path", "diff")},
                    "kind": {key: row["kind"].get(key) for key in ("type", "move_path")}
                    if isinstance(row.get("kind"), dict)
                    else row.get("kind"),
                }
                for row in field
                if isinstance(row, dict)
            ]
        if len(json.dumps(field, ensure_ascii=False).encode()) > FIELD_BYTES:
            result["_unavailable"] = True
        else:
            result[key] = field
    return result


def _turn(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DesktopProtocolError("Unsupported live Turn shape")
    if not isinstance(value.get("status"), str) or value["status"] not in {
        "inProgress",
        "completed",
        "failed",
        "interrupted",
    }:
        raise DesktopProtocolError("Unsupported live Turn status")
    result = {"turnId": value.get("turnId"), "status": value.get("status")}
    if value.get("status") == "inProgress":
        items = value.get("items", [])
        completed = value.get("completedItemIds", {})
        if (
            not isinstance(items, list)
            or len(items) > MAX_ITEMS
            or not isinstance(completed, (dict, list))
            or len(completed) > MAX_ITEMS
            or (
                isinstance(completed, dict)
                and any(
                    not isinstance(key, str) or type(done) is not bool
                    for key, done in completed.items()
                )
            )
            or (
                isinstance(completed, list)
                and any(not isinstance(key, str) for key in completed)
            )
        ):
            raise DesktopProtocolError("Live Turn exceeds this reader's limit")
        result.update(items=[_item(item) for item in items], completedItemIds=completed)
    return result


def _patch(root: Any, path: list[Any], operation: str, value: Any) -> None:
    if not path or len(path) > 32 or operation not in {"add", "replace", "remove"}:
        raise DesktopProtocolError("Unsupported live patch")
    target = root
    for key in path[:-1]:
        if isinstance(target, list) and type(key) is int and 0 <= key < len(target):
            target = target[key]
        elif isinstance(target, dict) and isinstance(key, str) and key in target:
            target = target[key]
        else:
            raise DesktopProtocolError("Live patch baseline changed")
    key = path[-1]
    if isinstance(target, list) and type(key) is int:
        if not 0 <= key < len(target) + (operation == "add"):
            raise DesktopProtocolError("Live patch position changed")
        if operation == "add":
            target.insert(key, value)
        elif operation == "remove":
            target.pop(key)
        else:
            target[key] = value
    elif isinstance(target, dict) and isinstance(key, str):
        if operation != "add" and key not in target:
            raise DesktopProtocolError("Live patch field changed")
        if operation == "remove":
            del target[key]
        else:
            target[key] = value
    else:
        raise DesktopProtocolError("Unsupported live patch target")


class LiveView:
    """A compact baseline for one exact native thread and owner connection."""

    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.revision: int | None = None
        self.canonical = True
        self.turns: dict[str, dict[str, Any]] = {}

    def accept(self, change: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(change, dict):
            raise DesktopProtocolError("Unsupported live change shape")
        revision = change.get("revision")
        if type(revision) is not int or revision < 0:
            raise DesktopProtocolError("Unsupported live revision")
        if change.get("type") == "snapshot":
            state = change.get("conversationState")
            if not isinstance(state, dict) or state.get("id") != self.thread_id:
                raise DesktopProtocolError("Live snapshot belongs to another thread")
            history = state.get("turnHistory") or {}
            if not isinstance(history, dict):
                raise DesktopProtocolError("Unsupported live history shape")
            self.canonical = history.get("kind") == "canonical"
            if self.canonical:
                canonical = history.get("history")
                if not isinstance(canonical, dict):
                    raise DesktopProtocolError("Unsupported canonical history shape")
                values = canonical.get("entitiesByKey")
            else:
                turns = state.get("turns", [])
                if not isinstance(turns, list):
                    raise DesktopProtocolError("Unsupported live Turns shape")
                values = {str(i): row for i, row in enumerate(turns)}
            if not isinstance(values, dict) or len(values) > MAX_ITEMS:
                raise DesktopProtocolError("Unsupported live history shape")
            self.turns = {key: _turn(turn) for key, turn in values.items()}
        elif change.get("type") == "patches":
            if (
                self.revision is None
                or type(change.get("baseRevision")) is not int
                or change.get("baseRevision") != self.revision
                or revision <= self.revision
            ):
                raise DesktopProtocolError("Live revisions are discontinuous")
            patches = change.get("patches")
            if not isinstance(patches, list) or len(patches) > MAX_ITEMS:
                raise DesktopProtocolError("Unsupported live patch window")
            prefix = (
                ["turnHistory", "history", "entitiesByKey"]
                if self.canonical
                else ["turns"]
            )
            for patch in patches:
                path = patch.get("path") if isinstance(patch, dict) else None
                if not isinstance(path, list):
                    raise DesktopProtocolError("Unsupported live patch path")
                if not path or prefix[: len(path)] == path:
                    raise DesktopProtocolError("Live history was replaced")
                if path[: len(prefix)] != prefix:
                    continue  # Model/configuration/token metadata is not work content.
                relative = path[len(prefix) :]
                key = str(relative[0])
                if len(relative) == 1:
                    if patch.get("op") == "remove":
                        self.turns.pop(key, None)
                    elif patch.get("op") in {"add", "replace"}:
                        self.turns[key] = _turn(patch.get("value"))
                    else:
                        raise DesktopProtocolError("Unsupported live Turn patch")
                    continue
                turn = self.turns.get(key)
                if turn is None:
                    raise DesktopProtocolError("Live Turn baseline is missing")
                field = relative[1]
                if field not in {"items", "completedItemIds", "status"}:
                    continue
                if turn.get("status") != "inProgress":
                    if field == "status" and patch.get("value") == "inProgress":
                        raise DesktopProtocolError("Live Turn needs a fresh baseline")
                    continue
                if field == "items" and len(relative) > 3:
                    position = relative[2]
                    items = turn["items"]
                    if type(position) is not int or not 0 <= position < len(items):
                        raise DesktopProtocolError("Live item position changed")
                    item = items[position]
                    if item is None or relative[3] not in _ITEM_FIELDS.get(
                        item.get("type"), set()
                    ):
                        continue
                _patch(turn, relative[1:], patch.get("op"), patch.get("value"))
                self.turns[key] = _turn(turn)
        else:
            raise DesktopProtocolError("Unsupported live change")
        if len(self.turns) > MAX_ITEMS:
            raise DesktopProtocolError("Live history exceeds this reader's limit")
        self.revision = revision
        return self.packet()

    def packet(self) -> dict[str, Any]:
        active = [
            turn for turn in self.turns.values() if turn.get("status") == "inProgress"
        ]
        if len(active) > 1:
            raise DesktopProtocolError("Multiple live Turns are not qualified")
        records = []
        for turn in active:
            completed = turn.get("completedItemIds", [])
            for item in turn.get("items", []):
                if (
                    item is None
                    or not isinstance(item.get("id"), str)
                    or (
                        completed.get(item["id"]) is True
                        if isinstance(completed, dict)
                        else item["id"] in completed
                    )
                ):
                    continue
                kind = item["type"]
                if kind == "agentMessage":
                    phase = item.get("phase")
                    if phase is not None and (
                        not isinstance(phase, str)
                        or phase not in {"commentary", "final"}
                    ):
                        continue
                    role, label, text = "assistant", "message", item.get("text") or ""
                elif kind == "error":
                    role, label, text = "activity", "error", item.get("message") or ""
                else:
                    if item.get("status") != "inProgress":
                        continue
                    role = "tool"
                    label = item.get("tool") or (
                        "command" if kind == "commandExecution" else "file change"
                    )
                    fields = (
                        ("command", "aggregatedOutput", "exitCode")
                        if kind == "commandExecution"
                        else (
                            ("server", "tool", "arguments", "result", "error")
                            if kind == "mcpToolCall"
                            else ("changes",)
                        )
                    )
                    text = json.dumps(
                        {key: item.get(key) for key in fields},
                        ensure_ascii=False,
                        indent=2,
                    )
                if not isinstance(text, str):
                    raise DesktopProtocolError("Unsupported visible text shape")
                redacted = (
                    classify_private_text(
                        str(label) + "\n" + text,
                        categories=frozenset({CATEGORY_CREDENTIAL}),
                        include_compound_field_assignment=True,
                    )
                    is not None
                )
                records.append(
                    {
                        "id": "live:"
                        + hashlib.sha256(
                            f"{turn.get('turnId')}:{item['id']}".encode()
                        ).hexdigest(),
                        "end": 0,
                        "role": role,
                        "kind": "function_call" if role == "tool" else label,
                        "label": kind if redacted else str(label)[:160],
                        "text": "" if redacted else text,
                        "redacted": redacted,
                        "truncated": False,
                        **(
                            {"unavailable": "live_field_limit"}
                            if item.get("_unavailable")
                            else {}
                        ),
                    }
                )
        packet = {"kind": "records", "revision": self.revision, "records": records}
        if (
            len(records) > 32
            or len(json.dumps(packet, ensure_ascii=False).encode()) > VIEW_BYTES
        ):
            raise DesktopProtocolError("Live view exceeds this reader's limit")
        return packet


def observations(
    home: Path, thread_id: str
) -> Generator[dict[str, Any] | None, None, None]:
    """Subscribe only to the discovered owner. Disconnect always unsubscribes."""
    with DesktopClient(home) as client:
        owner = client.owner(thread_id)

        def following(value: bool) -> None:
            client._send(
                {
                    "type": "broadcast",
                    "method": "thread-stream-following-changed",
                    "version": 1,
                    "sourceClientId": client.client_id,
                    "targetClientIds": [owner],
                    "params": {
                        "conversationId": thread_id,
                        "hostId": "local",
                        "following": value,
                    },
                }
            )

        view = LiveView(thread_id)
        try:
            following(True)
            while True:
                if not select.select([client.socket], [], [], 0.25)[0]:
                    yield None  # HTTP heartbeat also notices a departing reader.
                    continue
                client.deadline = time.monotonic() + 6
                value = client.read_frame(limit=STREAM_FRAME_BYTES)
                if value.get("type") == "client-discovery-request":
                    client._send(
                        {
                            "type": "client-discovery-response",
                            "requestId": value["requestId"],
                            "response": {"canHandle": False},
                        }
                    )
                if (
                    value.get("type") != "broadcast"
                    or value.get("method") != "thread-stream-state-changed"
                ):
                    yield None
                    continue
                params = value.get("params") or {}
                if not isinstance(params, dict):
                    raise DesktopProtocolError("Unsupported live envelope shape")
                if (
                    params.get("conversationId") != thread_id
                    or params.get("hostId") != "local"
                ):
                    yield None
                    continue
                if value.get("sourceClientId") != owner or value.get("version") != 11:
                    raise DesktopProtocolError("Live owner or protocol changed")
                packet = view.accept(params.get("change") or {})
                del value, params  # Retain only the compact, visible baseline.
                yield packet
        finally:
            try:
                following(False)
            except OSError:
                pass
