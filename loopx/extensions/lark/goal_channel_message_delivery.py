from __future__ import annotations

import hashlib
import html
import json
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock
from .goal_channel_contracts import binding_for_goal, read_goal_channel_binding
from .goal_channel_delivery_contract import goal_channel_delivery_route
from .goal_channel_targets import (
    goal_channel_target_for_name,
    read_goal_channel_targets,
)
from .goal_channel_transport import (
    MESSAGE_ID_PATTERN,
    auth_verified,
    bot_membership_verified,
    call,
    chat_verified,
    contains_exact_field,
    find_first_string,
    json_payload,
    lark_args,
    verified_app_id,
)
from .cli_transport import CommandRunner


class GoalChannelDeliveryStageError(ValueError):
    """One typed, public-safe failure stage of a Goal Channel delivery.

    The summary is the only user-visible text and must never carry private
    provider or configuration detail. `external_write_performed` is True or
    False only when the provider outcome is known; None means the outcome is
    unknown and the projected receipt must treat the provider write as
    performed instead of claiming a clean run.
    """

    def __init__(
        self,
        summary: str,
        *,
        blocker: str,
        failure_stage: str,
        external_write_performed: bool | None = False,
    ) -> None:
        super().__init__(summary)
        self.blocker = blocker
        self.failure_stage = failure_stage
        self.external_write_performed = external_write_performed


def _has_provider_response_body(result: Mapping[str, Any]) -> bool:
    """Whether the provider answered at all, rejection text included."""

    return any(str(result.get(key) or "").strip() for key in ("stdout", "stderr"))


def delivery_send_failure(
    result: Mapping[str, Any],
) -> GoalChannelDeliveryStageError:
    """Classify a send result that produced no usable message id.

    Only a provider response body is a verdict, and only a non-zero exit with
    that body is a rejection. A send that timed out, never started, or answered
    without a body leaves the outcome unknown, and the card may already be live
    in the chat: reporting a clean no-write there would be the misprojection
    this stage's contract forbids. A zero exit without a readable message id is
    the same unknown, because the provider accepted a write we cannot name.
    """

    if result.get("spawn_failed") is True:
        return GoalChannelDeliveryStageError(
            "Goal Channel delivery could not start the Lark CLI",
            blocker="provider_unavailable",
            failure_stage="send_operation_card",
        )
    if (
        result.get("timed_out") is True
        or result.get("returncode") == 0
        or not _has_provider_response_body(result)
    ):
        return GoalChannelDeliveryStageError(
            "Goal Channel delivery send outcome is unknown",
            blocker="delivery_outcome_unknown",
            failure_stage="send_operation_card",
            external_write_performed=None,
        )
    return GoalChannelDeliveryStageError(
        "Goal Channel delivery send failed",
        blocker="provider_send_rejected",
        failure_stage="send_operation_card",
    )


def resolve_bound_goal_channel(
    *,
    binding_path: Path,
    target_path: Path,
    goal_id: str,
    agent_id: str | None = None,
) -> dict[str, Any]:
    payload = read_goal_channel_binding(binding_path)
    raw = (
        binding_for_goal(payload, goal_id, agent_id=agent_id)
        if agent_id is not None
        else None
    )
    if raw is None:
        raw = binding_for_goal(payload, goal_id)
    if raw is None:
        raise ValueError("Goal Channel delivery requires one durable binding")
    target_ref = str(raw.get("target_ref") or "").strip()
    target = None
    if target_ref:
        target = goal_channel_target_for_name(
            read_goal_channel_targets(target_path), target_ref
        )
        if target is None:
            raise ValueError("Goal Channel delivery target is missing")
    resolved = binding_for_goal(
        payload,
        goal_id,
        provider_target=target,
        agent_id=(
            agent_id
            if agent_id is not None
            and binding_for_goal(payload, goal_id, agent_id=agent_id) is not None
            else None
        ),
    )
    if resolved is None:
        raise ValueError("Goal Channel delivery binding is incomplete")
    goal_channel_delivery_route(goal_id, lambda _goal_id: resolved)
    return resolved


def _find_message(value: Any, message_id: str) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        if str(value.get("message_id") or "") == message_id:
            return value
        for child in value.values():
            found = _find_message(child, message_id)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_message(child, message_id)
            if found is not None:
                return found
    return None


def _message_rows(value: Any) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        message_id = str(value.get("message_id") or "")
        if MESSAGE_ID_PATTERN.fullmatch(message_id):
            rows.append(value)
        for child in value.values():
            rows.extend(_message_rows(child))
    elif isinstance(value, list):
        for child in value:
            rows.extend(_message_rows(child))
    return rows


def _history_is_complete(value: Mapping[str, Any]) -> bool:
    completeness: list[bool] = []
    for candidate in (value, value.get("data")):
        if not isinstance(candidate, Mapping):
            continue
        if isinstance(candidate.get("has_more"), bool):
            completeness.append(candidate["has_more"] is False)
    meta = value.get("meta")
    pagination = meta.get("pagination") if isinstance(meta, Mapping) else None
    if isinstance(pagination, Mapping) and isinstance(pagination.get("complete"), bool):
        completeness.append(pagination["complete"] is True)
    return bool(completeness) and all(completeness)


def _message_card(value: Mapping[str, Any]) -> Mapping[str, Any] | None:
    body = value.get("body")
    content = body.get("content") if isinstance(body, Mapping) else None
    if isinstance(content, Mapping):
        return content
    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def normalized_card_text(card: Mapping[str, Any]) -> str | None:
    if card.get("schema") == "2.0":
        return _normalized_card_v2_text(card)
    header = card.get("header")
    elements = card.get("elements")
    if not isinstance(header, Mapping) or not isinstance(elements, list):
        return None
    title = header.get("title")
    title = title.get("content") if isinstance(title, Mapping) else None
    if not isinstance(title, str) or not elements:
        return None
    first = elements[0]
    first = first if isinstance(first, Mapping) else {}
    text = first.get("text")
    markdown = text.get("content") if isinstance(text, Mapping) else None
    if not isinstance(markdown, str):
        return None
    footer = None
    if len(elements) == 3 and elements[1] == {"tag": "hr"}:
        note = elements[2]
        note_elements = note.get("elements") if isinstance(note, Mapping) else None
        if isinstance(note_elements, list) and len(note_elements) == 1:
            note_text = note_elements[0]
            footer = (
                note_text.get("content") if isinstance(note_text, Mapping) else None
            )
    lines = [f'<card title="{title}">', markdown]
    if isinstance(footer, str) and footer:
        lines.extend(["---", f"📝 {footer}"])
    lines.append("</card>")
    return "\n".join(lines)


def _text_content(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    content = value.get("content")
    return content if isinstance(content, str) and content else None


def _card_v2_element_lines(value: object) -> list[str] | None:
    if not isinstance(value, Mapping):
        return None
    tag = value.get("tag")
    if tag == "markdown":
        content = value.get("content")
        # The provider's userDSL describes rendered Markdown, including entity
        # decoding. Plain-text elements below retain their literal content.
        return (
            [html.unescape(content)] if isinstance(content, str) and content else None
        )
    if tag in {"plain_text", "text"}:
        content = value.get("content") or value.get("text")
        return [content] if isinstance(content, str) and content else None
    if tag == "button":
        label = _text_content(value.get("text"))
        if not label:
            return None
        text = f"[{label}]"
        if "confirm" in value:
            confirm = value["confirm"]
            if not isinstance(confirm, Mapping):
                return None
            title = _text_content(confirm.get("title"))
            body = _text_content(confirm.get("text"))
            if not title or not body:
                return None
            text += f"(confirm:{json.dumps(f'{title}: {body}', ensure_ascii=False)})"
        return [text]
    children: object = None
    if tag == "column_set":
        children = value.get("columns")
    elif tag == "column":
        children = value.get("elements")
    if not isinstance(children, list):
        return None
    lines: list[str] = []
    button_labels: list[str] = []
    for child in children:
        child_lines = _card_v2_element_lines(child)
        if child_lines is None:
            return None
        if (
            isinstance(child, Mapping)
            and child.get("tag") == "column"
            and isinstance(child.get("elements"), list)
            and all(
                isinstance(item, Mapping) and item.get("tag") == "button"
                for item in child["elements"]
            )
        ):
            button_labels.extend(child_lines)
        else:
            lines.extend(child_lines)
    if button_labels:
        lines.append(" ".join(button_labels))
    return lines


def _normalized_card_v2_text(card: Mapping[str, Any]) -> str | None:
    header = card.get("header")
    body = card.get("body")
    if not isinstance(header, Mapping) or not isinstance(body, Mapping):
        return None
    title = _text_content(header.get("title"))
    subtitle = _text_content(header.get("subtitle"))
    elements = body.get("elements")
    tags = header.get("text_tag_list")
    if not title or not isinstance(elements, list) or not elements:
        return None
    attributes = f'title="{title}"'
    if subtitle:
        attributes += f' subtitle="{subtitle}"'
    lines = [f"<card {attributes}>"]
    if tags is not None:
        if not isinstance(tags, list):
            return None
        for item in tags:
            if not isinstance(item, Mapping):
                return None
            text = _text_content(item.get("text"))
            if not text:
                return None
            lines.append(f"「{text}」")
    for element in elements:
        element_lines = _card_v2_element_lines(element)
        if element_lines is None:
            return None
        lines.extend(element_lines)
    lines.append("</card>")
    return "\n".join(lines)


def card_projection_matches(
    observed: Mapping[str, Any], expected: Mapping[str, Any]
) -> bool:
    """Compare an exact card or its provider-normalized visible projection."""

    if observed == expected:
        return True
    observed_text = normalized_card_text(observed)
    expected_text = normalized_card_text(expected)
    return (
        observed_text is not None
        and expected_text is not None
        and observed_text == expected_text
    )


def _has_callback_behavior(value: object) -> bool:
    if isinstance(value, Mapping):
        if value.get("type") == "callback":
            return True
        return any(_has_callback_behavior(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_callback_behavior(child) for child in value)
    return False


def message_card_matches(
    value: Mapping[str, Any],
    expected: Mapping[str, Any] | None,
    *,
    allow_normalized: bool = True,
) -> bool:
    if expected is None:
        return False
    observed = _message_card(value)
    if isinstance(observed, Mapping) and observed == expected:
        return True
    if not allow_normalized:
        return False
    if isinstance(observed, Mapping) and card_projection_matches(observed, expected):
        return True
    content = value.get("content")
    return isinstance(content, str) and content == normalized_card_text(expected)


def _message_sender(value: Mapping[str, Any]) -> tuple[str, str]:
    sender = value.get("sender")
    sender = sender if isinstance(sender, Mapping) else {}
    sender_type = str(
        sender.get("sender_type") or value.get("sender_type") or ""
    ).strip()
    sender_id = str(
        sender.get("id") or sender.get("sender_id") or value.get("sender_id") or ""
    ).strip()
    return sender_type, sender_id


class GoalChannelMessageDeliverySession:
    """Verified project-Bot delivery with exact history dedupe and readback."""

    def __init__(
        self,
        *,
        goal_id: str,
        binding: Mapping[str, Any],
        binding_lock_path: Path,
        target_lock_path: Path,
        history_start_at: str,
        resolve_current_binding: Callable[[], Mapping[str, Any]],
        runner: CommandRunner,
    ) -> None:
        self.goal_id = goal_id
        self.binding = dict(binding)
        self.binding_lock_path = binding_lock_path
        self.target_lock_path = target_lock_path
        self.history_start_at = history_start_at
        self.resolve_current_binding = resolve_current_binding
        self.runner = runner
        self.route: dict[str, Any] = {}
        self.expected_cards: dict[str, list[dict[str, Any]]] = {}

    def _existing_message(
        self, card: Mapping[str, Any], route: Mapping[str, Any]
    ) -> str | None:
        result = call(
            self.runner,
            lark_args(
                cli_bin=str(route["cli_bin"]),
                profile=str(route["sender_profile"]),
                tail=[
                    "im",
                    "+chat-messages-list",
                    "--chat-id",
                    str(route["chat_id"]),
                    "--start",
                    self.history_start_at,
                    "--order",
                    "asc",
                    "--page-all",
                    "--page-limit",
                    "1000",
                    "--as",
                    "bot",
                    "--no-reactions",
                    "--format",
                    "json",
                ],
            ),
        )
        payload = json_payload(result)
        if result.get("returncode") != 0:
            raise GoalChannelDeliveryStageError(
                "Goal Channel delivery dedupe readback failed",
                blocker="dedupe_history_read_failed",
                failure_stage="read_dedupe_history",
            )
        for message in _message_rows(payload):
            sender_type, sender_app_id = _message_sender(message)
            if (
                message.get("deleted") is not True
                and str(message.get("chat_id") or "") == route["chat_id"]
                and sender_type == "app"
                and sender_app_id == route["bot_app_id"]
                and message_card_matches(
                    message,
                    card,
                    # Provider-normalized Card 2.0 history omits callback
                    # values. Visible equality therefore cannot prove that an
                    # old actionable message carries this operation id/digest.
                    allow_normalized=not _has_callback_behavior(card),
                )
            ):
                return str(message["message_id"])
        if not _history_is_complete(payload):
            raise GoalChannelDeliveryStageError(
                "Goal Channel delivery dedupe history is incomplete",
                blocker="dedupe_history_incomplete",
                failure_stage="read_dedupe_history",
            )
        return None

    def resolve(self, requested_goal_id: str) -> Mapping[str, Any]:
        if requested_goal_id != self.goal_id:
            raise ValueError("Goal Channel delivery goal identity changed")
        return self.binding

    def verify(self, route: Mapping[str, Any]) -> bool:
        cli_bin = str(route["cli_bin"])
        profile = str(route["sender_profile"])
        app_id = str(route["bot_app_id"])
        chat_id = str(route["chat_id"])
        verified = all(
            (
                auth_verified(
                    runner=self.runner,
                    cli_bin=cli_bin,
                    profile=profile,
                    identity="bot",
                    expected_bot_name=str(route["bot_display_name"]),
                ),
                verified_app_id(runner=self.runner, cli_bin=cli_bin, profile=profile)
                == app_id,
                chat_verified(
                    runner=self.runner,
                    cli_bin=cli_bin,
                    profile=profile,
                    identity="bot",
                    chat_id=chat_id,
                ),
                bot_membership_verified(
                    runner=self.runner,
                    cli_bin=cli_bin,
                    profile=profile,
                    chat_id=chat_id,
                    app_id=app_id,
                ),
            )
        )
        if verified:
            self.route = dict(route)
        return verified

    def send(
        self, card: Mapping[str, Any], key: str, route: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        with ExitStack() as locks:
            # Match the writer order used by Goal Topic connect: the binding
            # transaction owns the outer lock and target mutation the inner one.
            locks.enter_context(
                exclusive_file_lock(
                    self.binding_lock_path, operation="lark_goal_channel_delivery"
                )
            )
            if self.target_lock_path != self.binding_lock_path:
                locks.enter_context(
                    exclusive_file_lock(
                        self.target_lock_path, operation="lark_goal_channel_delivery"
                    )
                )
            if dict(self.resolve_current_binding()) != self.binding:
                raise GoalChannelDeliveryStageError(
                    "Goal Channel delivery binding drifted",
                    blocker="binding_drifted",
                    failure_stage="prepare_delivery_transaction",
                )
            existing_message_id = self._existing_message(card, route)
            # The history lookup is a provider round trip. Recheck under the same
            # lock used by binding writers immediately before either accepting
            # the dedupe result or performing the external write.
            if dict(self.resolve_current_binding()) != self.binding:
                raise GoalChannelDeliveryStageError(
                    "Goal Channel delivery binding drifted",
                    blocker="binding_drifted",
                    failure_stage="prepare_delivery_transaction",
                )
            if existing_message_id is not None:
                self.expected_cards.setdefault(existing_message_id, []).append(
                    dict(card)
                )
                return {
                    "message_id": existing_message_id,
                    "semantic_dedupe_status": "existing_exact_message",
                    "external_write_performed": False,
                }
            result = call(
                self.runner,
                lark_args(
                    cli_bin=str(route["cli_bin"]),
                    profile=str(route["sender_profile"]),
                    tail=[
                        "im",
                        "+messages-send",
                        "--chat-id",
                        str(route["chat_id"]),
                        "--content",
                        json.dumps(card, ensure_ascii=False, separators=(",", ":")),
                        "--msg-type",
                        "interactive",
                        "--idempotency-key",
                        f"loopx-{hashlib.sha256(key.encode()).hexdigest()[:32]}",
                        "--as",
                        "bot",
                        "--format",
                        "json",
                    ],
                ),
            )
        message_id = find_first_string(
            json_payload(result), {"message_id"}, MESSAGE_ID_PATTERN
        )
        if result.get("returncode") != 0 or not message_id:
            raise delivery_send_failure(result)
        self.expected_cards.setdefault(message_id, []).append(dict(card))
        return {
            "message_id": message_id,
            "semantic_dedupe_status": "no_existing_exact_message",
            "external_write_performed": True,
        }

    def readback(self, message_id: str) -> Mapping[str, Any]:
        result = call(
            self.runner,
            lark_args(
                cli_bin=str(self.route["cli_bin"]),
                profile=str(self.route["sender_profile"]),
                tail=[
                    "im",
                    "+messages-mget",
                    "--message-ids",
                    message_id,
                    "--as",
                    "bot",
                    "--no-reactions",
                    "--format",
                    "json",
                ],
            ),
        )
        message = _find_message(json_payload(result), message_id)
        sender_type, sender_app_id = (
            _message_sender(message) if message is not None else ("", "")
        )
        expected_card = (self.expected_cards.get(message_id) or [None]).pop(0)
        exact = bool(
            result.get("returncode") == 0
            and message is not None
            and contains_exact_field(message, "chat_id", str(self.route["chat_id"]))
            and message_card_matches(message, expected_card)
            and sender_type == "app"
            and sender_app_id == self.route["bot_app_id"]
            and auth_verified(
                runner=self.runner,
                cli_bin=str(self.route["cli_bin"]),
                profile=str(self.route["sender_profile"]),
                identity="bot",
                expected_bot_name=str(self.route["bot_display_name"]),
            )
            and verified_app_id(
                runner=self.runner,
                cli_bin=str(self.route["cli_bin"]),
                profile=str(self.route["sender_profile"]),
            )
            == self.route["bot_app_id"]
        )
        return {
            "verified": exact,
            "message_id": message_id,
            "chat_id": self.route["chat_id"] if exact else None,
            "sender_app_id": sender_app_id if exact else None,
            "sender_identity": "bot" if exact else None,
            "sender_evidence_source": "message_readback" if exact else None,
        }


__all__ = [
    "delivery_send_failure",
    "GoalChannelMessageDeliverySession",
    "GoalChannelDeliveryStageError",
    "normalized_card_text",
    "goal_channel_delivery_route",
    "resolve_bound_goal_channel",
]
