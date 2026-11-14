"""Read-only Steward cards on the original, authenticated Lark conversation."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from ...capabilities.native_chat.steward_triage import (
    StewardFastPathError,
    StewardFastPathIntent,
    parse_steward_fast_path_intent,
    project_steward_triage,
)
from ...chat_store import RESUMABLE_SESSION_STATES, _atomic_write_json, _read_json
from ...file_lock import exclusive_file_lock
from .card_callback import callback_timestamp, read_callback_card_content
from .conversation_identity import identity_ref
from .goal_channel_transport import call, find_first_string, json_payload, lark_args
from .identity_shapes import LARK_MESSAGE_ID_PATTERN, require_card_callback_identity
from .presentation.message_card import build_lark_markdown_reply_card
from .presentation.triage_card import (
    FOCUS_TITLES,
    build_steward_triage_card,
    build_steward_triage_menu,
    build_steward_triage_projects,
    steward_triage_without_buttons,
)
from .private_ingress import private_source_text

if TYPE_CHECKING:
    from .private_conversations import LarkPrivateConversations

TRIAGE_CARD_ACTION = "loopx_private_steward_triage_v0"
_ROW_ID = re.compile(r"^[0-9a-f]{24}$")
_CARD_LIFETIME = timedelta(days=1)
# Lark's send UUID is time-bounded. Keep a smaller retry window and retain an
# ambiguous receipt after it expires instead of resending a possibly sent card.
_SEND_RETRY_WINDOW = timedelta(minutes=20)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def private_steward(binding: Mapping[str, Any]) -> bool:
    return (
        binding.get("enabled") is True and binding.get("context_kind") == "steward"
        and binding.get("audience", "owner") == "owner"
    )


def _message_action(text: str) -> dict[str, str] | None:
    # Only documented, explicit shortcuts are intercepted. Legacy aliases and
    # sentences containing the words keep the ordinary conversation behavior.
    tokens = text.split(maxsplit=1)
    if not tokens or tokens[0].casefold() not in {"/triage", "/gates", "/todos"}:
        return None
    try:
        intent = parse_steward_fast_path_intent(text)
    except StewardFastPathError:
        return {"operation": "invalid"}
    if intent is None:
        return None
    if text.casefold() == "/triage":
        return {"operation": "menu"}
    return {
        "operation": "query", "focus": intent.focus,
        "goals": " ".join(intent.requested_goal_ids),
    }


def profile_supports_cards(profile: Mapping[str, str]) -> bool:
    return profile.get("audience") == "owner" and profile.get("context_kind") == "steward"


def _selected_session(transport: LarkPrivateConversations, channel: str) -> dict[str, Any] | None:
    store = transport.core.controller.store
    rows = store.list_sessions(channel_id=channel)
    active = [row for row in rows if row.get("status") in RESUMABLE_SESSION_STATES]
    current = max(active, key=lambda row: str(row.get("created_at") or ""), default=None)
    if current is None:
        return None
    if sum(row["session_id"] == current["session_id"] for row in active) != 1:
        raise ValueError("ambiguous Steward Session")
    session = store.load_session(current["session_id"])
    if session is None or session.get("status") not in RESUMABLE_SESSION_STATES:
        raise ValueError("Steward Session changed")
    return session


def _resolve(
    transport: LarkPrivateConversations, record: dict[str, Any], selected: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None]:
    binding_id, source = record["binding_id"], record["source"]
    if record["event"].get("chat_type") != "p2p" or source.get("private_human_message") is not True:
        raise ValueError("triage requires a private owner source")
    if selected is None:
        selected = transport.bindings.resolve(binding_id=binding_id, include_observation=True, **source)
    binding = selected["binding"]
    if not private_steward(binding) or binding["transport_ref"] != record["profile"]:
        raise ValueError("triage source is unavailable")
    session = _selected_session(transport, selected["channel_id"])
    if session is not None:
        if not isinstance(session.get("steward_context"), dict):
            raise ValueError("Steward Session has no bound context")
        selected = transport.bindings.resolve(
            binding_id=binding_id, session=session, session_context=session["steward_context"],
            include_observation=True, **source,
        )
    sid = session["session_id"] if session is not None else None
    stamp = record.get("triage")
    if stamp is not None and (stamp["session"] != sid or stamp["channel"] != selected["channel_id"]):
        raise ValueError("triage card belongs to another Session")
    return selected, sid


def capture_message(
    transport: LarkPrivateConversations, path: Path, record: dict[str, Any],
    selected: dict[str, Any], text: str, *, history_review: bool,
) -> dict[str, Any] | None:
    if not private_steward(selected["binding"]) or record["source"].get("private_human_message") is not True:
        return None
    action = _message_action(text.strip())
    if action is None:
        return None
    if history_review:
        return {"status": "history_answer_held"}
    if "triage" not in record:
        if (record["status"] != "captured" or
                (transport.core.root / f"{record['request_ref']}.json").exists()):
            return None  # A previously admitted request keeps its original owner on replay.
        try:
            selected, session = _resolve(transport, record, selected)
        except (KeyError, ValueError):
            return {"status": "triage_binding_unavailable"}
        record["triage"] = {
            "action": action, "channel": selected["channel_id"], "session": session,
            "source_text": text, "captured_at": _now().isoformat(),
        }
        _atomic_write_json(path, record)
    if record["triage"]["source_text"] != text:
        return {"status": "source_conflict"}
    return {"status": "triage_queued"}


def _buttons(card: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [button["value"] for element in card.get("elements", [])
            if element.get("tag") == "action" for button in element["actions"]]


def _read_exact_card(
    transport: LarkPrivateConversations, record: dict[str, Any], selected: dict[str, Any],
) -> bool:
    stamp = record["triage"]
    observed = read_callback_card_content(
        runner=transport.runner, cli_bin=transport.cli_bin, profile=record["profile"],
        message_id=stamp["message_id"], chat_id=record["event"]["chat_id"],
        app_id=selected["observation"]["bot_app_id"],
    )
    # A lossy visual projection cannot prove which callback values were sent.
    try:
        payload = json.loads(observed) if isinstance(observed, str) else observed
    except ValueError:
        return False
    expected = stamp["card"]
    return (
        isinstance(payload, Mapping) and payload.get("header") == expected["header"]
        and payload.get("elements") == expected["elements"]
        and (not isinstance(payload.get("config"), Mapping)
             or payload["config"].get("enable_forward") is not True)
    )


def capture_callback(
    transport: LarkPrivateConversations, profile: str, event: Mapping[str, Any], action: Mapping[str, Any],
) -> dict[str, Any]:
    require_card_callback_identity(event, error_prefix="Steward callback")
    if (
        event.get("type") != "card.action.trigger" or event.get("action_tag") != "button"
        or event.get("host") != "im_message" or action.get("schema_version") != TRIAGE_CARD_ACTION
    ):
        raise ValueError("unsupported Steward card callback")
    parent_ref = str(action.get("request") or "")
    if not _ROW_ID.fullmatch(parent_ref):
        raise ValueError("invalid Steward card reference")
    parent = _read_json(transport.root / f"{parent_ref}.json")
    stamp = parent.get("triage")
    # A card can be visible as soon as Send returns an ID. Let the click use
    # its own exact provider readback while the delivery worker verifies Send.
    if (not isinstance(stamp, dict) or stamp.get("retired")
            or parent["status"] not in {"captured", "delivered"} or not stamp.get("message_id")):
        raise ValueError("Steward card receipt is unavailable")
    if (
        parent["profile"] != profile or parent["event"]["chat_id"] != event["chat_id"]
        or parent["event"]["sender_id"] != event["operator_id"]
        or stamp.get("message_id") != event["message_id"] or action not in _buttons(stamp["card"])
    ):
        raise ValueError("callback does not match the original Steward card")
    clicked_at = datetime.fromisoformat(callback_timestamp(event.get("timestamp"), subject="Steward"))
    issued_at = datetime.fromisoformat(stamp["captured_at"])
    now = _now()
    if not (now <= issued_at + _CARD_LIFETIME
            and issued_at - timedelta(seconds=1) <= clicked_at <= issued_at + _CARD_LIFETIME
            and now - _CARD_LIFETIME <= clicked_at <= now + timedelta(minutes=1)):
        raise ValueError("Steward card expired")
    selected, _ = _resolve(transport, parent)
    if event.get("app_id") not in (None, "", selected["observation"]["bot_app_id"]):
        raise ValueError("callback App does not match the binding")
    if not _read_exact_card(transport, parent, selected):
        raise ValueError("original Steward card could not be verified")
    if action.get("goal") and action["goal"] not in selected["context"]["goal_ids"]:
        raise ValueError("selected project is no longer available")
    ref = identity_ref(parent_ref, str(event["event_id"]))
    path = transport.root / f"{ref}.json"
    payload = {
        "action": {k: v for k, v in action.items() if k not in {"request", "schema_version"}},
        "channel": stamp["channel"], "session": stamp["session"], "source_text": stamp["source_text"],
        "parent": parent_ref, "clicked_at": clicked_at.isoformat(),
    }
    with exclusive_file_lock(path, operation="capture_private_steward_callback"):
        if path.exists():
            previous = _read_json(path)["triage"]
            # Provider redelivery can have a new delivery timestamp.
            if any(previous.get(k) != v for k, v in payload.items() if k != "clicked_at"):
                raise ValueError("callback identity was reused for another action")
            return {"ok": True, "status": "triage_callback_already_captured"}
        record = {key: parent[key] for key in ("schema_version", "binding_id", "profile", "source", "event")}
        _atomic_write_json(path, {
            **record, "request_ref": ref, "status": "captured", "deliveries": {},
            "triage": {**payload, "captured_at": now.isoformat()},
        })
    return {"ok": True, "status": "triage_callback_queued"}


def _render(
    transport: LarkPrivateConversations, record: dict[str, Any], selected: dict[str, Any],
) -> dict[str, Any]:
    action = record["triage"]["action"]

    def button(operation: str, **params: str) -> dict[str, str]:
        return {"schema_version": TRIAGE_CARD_ACTION, "request": record["request_ref"],
                "operation": operation, **params}

    operation = action["operation"]
    if operation == "menu":
        return build_steward_triage_menu(button)
    if operation == "invalid":
        card = build_steward_triage_menu(button)
        card["elements"].insert(0, {"tag": "div", "text": {
            "tag": "plain_text", "content": "无法识别项目参数。",
        }})
        return card
    goals = tuple(action.get("goals", action.get("goal", "")).split())
    focus = action.get("focus")
    if focus not in FOCUS_TITLES:
        raise ValueError("unsupported query focus")
    if operation == "projects":
        offset = int(action.get("offset", "0"))
        available = sorted(selected["context"]["goal_ids"])
        if offset >= len(available):
            offset = 0
        return build_steward_triage_projects(
            button, available, focus=focus, offset=offset, selected_goal=action.get("goal", ""),
        )
    if operation != "query":
        raise ValueError("unsupported query operation")
    controller = transport.core.controller
    projection = project_steward_triage(
        StewardFastPathIntent("triage", focus, goals), selected, registry_path=controller.registry_path,
        runtime_root_arg=str(controller.coordination_runtime_root),
    )
    return build_steward_triage_card(
        projection, value=button, navigation_goal=goals[0] if len(goals) == 1 else "" if not goals else None,
    )


def pending(record: dict[str, Any]) -> bool:
    return not record.get("triage", {}).get("retired")


def reconcile(transport: LarkPrivateConversations, path: Path) -> int:
    with exclusive_file_lock(path, operation="deliver_private_steward_card"):
        record = _read_json(path)
        stamp = record["triage"]
        if record["status"] == "delivered" or not pending(record):
            return 0

        def retire(reason: str) -> int:
            stamp["retired"] = reason
            _atomic_write_json(path, record)
            return 0

        now = _now()
        if now - datetime.fromisoformat(stamp["captured_at"]) > _CARD_LIFETIME:
            return retire("card_delivery_expired")
        try:
            selected, _ = _resolve(transport, record)
        except ValueError:
            # A transient identity/read failure remains recoverable without
            # changing the source or invoking the model.
            return 0
        source = transport._source_message(record, selected=selected)
        if source is None:
            return 0
        if source.get("deleted") is True or private_source_text(source) != stamp["source_text"]:
            return retire("original_source_changed")
        if "parent" in stamp:
            parent = _read_json(transport.root / f"{stamp['parent']}.json")
            if (now - datetime.fromisoformat(parent["triage"]["captured_at"]) > _CARD_LIFETIME
                    or not _read_exact_card(transport, parent, selected)):
                return 0

        scope = selected["context"]["goal_ids"]
        if "card" not in stamp:
            try:
                stamp["card"] = _render(transport, record, selected)
            except StewardFastPathError as exc:
                if exc.code != "goal_outside_authorized_scope":
                    raise
                stamp["card"] = build_lark_markdown_reply_card(
                    "该项目不在当前可查看范围。请重新输入 /triage。",
                    title="暂无法查询", template="orange",
                )
                stamp["card"]["config"]["enable_forward"] = False
            stamp["scope"] = scope
            _atomic_write_json(path, record)
        # Revalidate immediately before publishing. A scope change after the
        # read cannot release the already prepared payload to the old audience.
        try:
            current, _ = _resolve(transport, record)
        except ValueError:
            return 0
        if stamp["scope"] != current["context"]["goal_ids"]:
            return retire("scope_changed_after_projection")
        if "message_id" not in stamp:
            fresh_source = transport._source_message(record, selected=current)
            if fresh_source is None:
                return 0
            if fresh_source.get("deleted") is True or private_source_text(fresh_source) != stamp["source_text"]:
                return retire("original_source_changed")
            with exclusive_file_lock(transport.bindings.path, operation="deliver_bound_steward_card"):
                binding = next((row for row in transport.bindings.read()["bindings"]
                                if row["binding_id"] == record["binding_id"]), None)
                if binding != current["binding"]:
                    return 0
                session = _selected_session(transport, stamp["channel"])
                if stamp["session"] != (session["session_id"] if session else None):
                    return retire("original_session_changed")
                now = _now()
                previous = stamp.get("send_started_at")
                if previous and now - datetime.fromisoformat(previous) >= _SEND_RETRY_WINDOW:
                    return retire("provider_delivery_receipt_unknown")
                if not previous and _buttons(stamp["card"]):
                    key = (record["profile"], current["observation"]["bot_app_id"])
                    ready = transport.steward_callback_readiness.get(key)
                    if ready is None or not ready():
                        stamp["card"] = steward_triage_without_buttons(
                            stamp["card"], query_result=stamp["action"]["operation"] == "query",
                        )
                # After a send starts, keep the exact payload for UUID retries.
                stamp.setdefault("send_started_at", now.isoformat())
                _atomic_write_json(path, record)
                sent = call(transport.runner, lark_args(cli_bin=transport.cli_bin, profile=record["profile"], tail=[
                    "im", "+messages-send", "--chat-id", record["event"]["chat_id"], "--msg-type", "interactive",
                    "--content", json.dumps(stamp["card"], ensure_ascii=False, separators=(",", ":")),
                    "--idempotency-key", f"loopx-{record['request_ref']}", "--as", "bot", "--format", "json",
                ]))
            message = find_first_string(json_payload(sent), {"message_id"}, LARK_MESSAGE_ID_PATTERN)
            if sent.get("returncode") != 0 or not message:
                return 0
            stamp["message_id"] = message
            _atomic_write_json(path, record)
        if not _read_exact_card(transport, record, current):
            return 0
        record["status"] = "delivered"
        _atomic_write_json(path, record)
        return 1
