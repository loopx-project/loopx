"""Consume the current CLI capture through its reader, without new admission."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from typing import Any

from loopx.control_plane.testing.model_tool_behavior import (
    ScriptedAssistantAction,
    ScriptedDoubaoExecTransport,
    ScriptedExecToolAction,
    is_context_read_command,
)


def _object(value: Any) -> dict[str, Any] | None:
    try:
        payload = json.loads(value)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _reader(command: Any) -> str:
    if not isinstance(command, str) or not is_context_read_command(command):
        raise ValueError("the current capture must supply its own context reader")
    return command


def _retained_packet(pages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """EOF alone is insufficient: retain contiguous UTF-8 bytes and their digest."""
    if not pages:
        raise ValueError("the current quota capture was not read")
    digest, total = pages[-1].get("sha256"), pages[-1].get("total_bytes")
    if not isinstance(digest, str) or type(total) is not int or total <= 0:
        raise ValueError("the current capture identity is incomplete")
    retained = bytearray()
    for page in sorted(pages, key=lambda item: item.get("offset", -1)):
        offset, content = page.get("offset"), page.get("content")
        if (page.get("ok") is not True or page.get("schema_version") != "context_capture_page_v1"
                or page.get("sha256") != digest or page.get("total_bytes") != total
                or type(offset) is not int or not 0 <= offset <= len(retained)
                or not isinstance(content, str)):
            raise ValueError("the current capture pages do not share one contiguous source")
        data = content.encode("utf-8")
        end = offset + len(data)
        if (not data or end > total or page.get("eof") is not (end == total)
                or page.get("next_offset") != (None if end == total else end)):
            raise ValueError("the current capture page byte range is invalid")
        overlap = min(len(retained) - offset, len(data))
        if retained[offset:offset + overlap] != data[:overlap]:
            raise ValueError("repeated capture bytes changed")
        retained.extend(data[overlap:])
    if len(retained) != total or sha256(retained).hexdigest() != digest:
        raise ValueError("the complete current capture was not retained")
    payload = _object(retained)
    if payload is None:
        raise ValueError("the complete capture is not a JSON object")
    return payload


def latest_quota_payload(request: Mapping[str, Any]) -> dict[str, Any]:
    packet, pages, navigation = None, [], False
    for message in request["messages"]:
        if message.get("role") != "tool":
            continue
        value = _object(message.get("content"))
        if value is None:
            continue
        if value.get("schema_version") == "context_capture_navigation_v1":
            packet, pages, navigation = None, [], True
        elif value.get("schema_version") == "context_capture_page_v1":
            pages.append(value)
        elif isinstance(value.get("interaction_contract"), dict):
            packet, pages, navigation = value, [], False
    if pages:
        packet = _retained_packet(pages)
    if packet is None or "interaction_contract" not in packet:
        suffix = "current capture was not read" if navigation else "quota result was not observed"
        raise AssertionError(suffix)
    return packet


def context_reading_transport(actions: Sequence[Any]) -> ScriptedDoubaoExecTransport:
    """Script native reads as model tool calls within the actor's unchanged budget."""
    remaining = list(actions)

    def choose(request: Mapping[str, Any]) -> Any:
        message = request["messages"][-1]
        value = _object(message.get("content")) if message.get("role") == "tool" else None
        if value is not None:
            if value.get("schema_version") == "context_capture_navigation_v1":
                return ScriptedExecToolAction(_reader(value["recovery"]["command"]))
            if value.get("schema_version") == "context_capture_page_v1" and value.get("eof") is not True:
                return ScriptedExecToolAction(_reader(value.get("next_command")))
        if not remaining:
            return ScriptedAssistantAction("No further scripted action.")
        action = remaining.pop(0)
        return action(request) if callable(action) else action

    return ScriptedDoubaoExecTransport([choose] * 40)
