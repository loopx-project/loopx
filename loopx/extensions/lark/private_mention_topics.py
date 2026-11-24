"""Keep verified private steward reply topics distinct at mention admission."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...capabilities.native_chat.external_mention_recovery import requeue_rejected_mention
from ...chat_store import _atomic_write_json, _read_json
from ...file_lock import exclusive_file_lock
from .private_web_projection import _same_fields

if TYPE_CHECKING:
    from .private_conversations import LarkPrivateConversations

_REPLAY_FOLDER = "rejected-mention-replays"


def distinct_reply_topic(transport: LarkPrivateConversations, record: dict[str, Any], binding: Mapping[str, Any]) -> bool:
    if (binding.get("context_kind") != "steward" or binding.get("audience", "owner") != "owner"
            or record["event"].get("chat_type") != "p2p"):
        return False
    root_id = record["event"].get("root_id")
    capture = record.get("mention_capture") or {}
    if (not root_id or record.get("reply_context_message") is None
            or not capture.get("session_id") or not capture.get("target_turn_id")):
        return False
    turn = transport.core.controller.store.load_turn(capture["session_id"], capture["target_turn_id"])
    match = re.fullmatch(r"external-([a-f0-9]{24})", str((turn or {}).get("client_turn_id") or ""))
    if match is None or (turn or {}).get("origin") != "lark":
        return False
    try:
        original = _read_json(transport.root / f"{match[1]}.json")
    except (OSError, ValueError):
        return False
    previous = original["event"]
    return (original.get("turn_id") == capture["target_turn_id"]
        and original.get("session_id") == capture["session_id"]
        and original.get("reply_context_message") is not None
        and _same_fields(original, record, "binding_id", "profile", "source")
        and previous.get("chat_id") == record["event"].get("chat_id")
        and bool(previous.get("root_id"))
        and previous["root_id"] != root_id)

def steer_in_topic(transport: LarkPrivateConversations, path: Path, record: dict[str, Any],
                   binding: Mapping[str, Any]) -> bool:
    # Freeze this decision before Core admission so redelivery can restore a
    # missing transport projection with the same request identity and mode.
    if record.get("mention_topic_queue"):
        return False
    if (transport.core.root / path.name).exists() or not distinct_reply_topic(transport, record, binding):
        return True
    record["mention_topic_queue"] = True
    _atomic_write_json(path, record)
    return False


def replay_presentation(admitted: Mapping[str, Any]) -> dict[str, str] | None:
    if admitted["status"] == "accepted" and admitted.get("rejected_mention_replay"):
        return {"presentation_key": "rejected-mention.1"}
    return None


def pending_replays(transport: LarkPrivateConversations, scheduled: set[Path]) -> Iterator[Path]:
    folder = transport.root / _REPLAY_FOLDER
    if folder.is_symlink():
        return
    for plan in sorted(folder.glob("*.json")):
        path = transport.root / plan.name
        if path in scheduled or not re.fullmatch(r"[a-f0-9]{24}", path.stem) or plan.is_symlink() or not plan.is_file():
            continue
        if time.monotonic() < transport._context_recovery_probe_at.get(plan, 0):
            continue
        try:
            row = _read_json(path)
        except (OSError, ValueError):
            continue
        if (row.get("status") == "delivered" and not row.get("context_recovery")
                and row.get("mention_delivery", {}).get("action") == "reject"):
            yield path

def reconcile_rejection(transport: LarkPrivateConversations, path: Path, row: dict[str, Any]) -> bool:
    if (row.get("status") != "delivered" or row.get("context_recovery")
            or row.get("mention_delivery", {}).get("action") != "reject"):
        return False
    plan_path = transport.root / _REPLAY_FOLDER / path.name
    if plan_path.parent.is_symlink() or plan_path.is_symlink() or not plan_path.is_file():
        return False
    _apply_replay(transport, path, plan_path)
    return True


def _apply_replay(transport: LarkPrivateConversations, path: Path, plan_path: Path) -> None:
    transport._context_recovery_probe_at[plan_path] = time.monotonic() + 60
    with exclusive_file_lock(path, operation="deliver_private_chat_request"):
        record = _read_json(path)
        if record.get("status") != "delivered" or record.get("context_recovery"):
            return
        terminal = record["deliveries"].get("terminal") or {}
        try:
            with plan_path.open("rb") as handle:
                raw = handle.read(4097)
            if len(raw) > 4096:
                return
            plan = json.loads(raw)
            event = record["event"]
            if (terminal.get("verified") is not True or plan != {
                    "message_id": event["message_id"],
                    "rejection_message_id": (terminal.get("attempt") or {}).get("message_ref"),
                    "target_turn_id": record["turn_id"],
            } or not plan["rejection_message_id"] or record["request_ref"] != path.stem):
                return

            def verify_source(selected: dict[str, Any]) -> None:
                source = transport._recovery_source_observation(record, selected)
                if (source is None or source.get("_native_mention") is not True
                        or any(str(source.get(key) or "") != str(event.get(key) or "") for key in ("root_id", "parent_id"))
                        or (event.get("thread_id") and source.get("thread_id") != event["thread_id"])
                        or not distinct_reply_topic(transport, record, selected["binding"])):
                    raise ValueError("the original distinct topic could not be verified")

            recovered = requeue_rejected_mention(transport.core, record["request_ref"],
                original_turn_id=plan["target_turn_id"], source_preflight=verify_source)
            transport._record_admission(path, record, recovered)
        except (KeyError, ValueError, OSError, RuntimeError):
            # An unavailable plan or uncertain source stays beside the
            # original failure for a later independent observation.
            return
