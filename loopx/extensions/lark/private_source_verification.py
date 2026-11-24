"""Provider source envelope readback, ancestry verification, and reply-context checks."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING, Protocol, cast

from ...control_plane.collaboration import conversation_reply_context
from .cli_transport import CommandRunner
from .conversation_identity import identity_ref, lark_private_source
from .event_collector_runtime import enrich_lark_event_reply_context
from .event_inbox import lark_event_mentions_bot
from .goal_channel_transport import call, json_payload, lark_args, lark_provider_mention_identities
from .inbox_reply import _message
from .private_ingress import private_source_text, recovery_source_matches
from .private_web_projection import _same_fields

if TYPE_CHECKING:
    from ...capabilities.native_chat.conversation_bindings import ChatConversationBindings
    from ...capabilities.native_chat.external_conversations import ChatExternalConversations

Payload = dict[str, object]
PayloadView = Mapping[str, object]

CHAT_CONTROL_COMMANDS = {"/status": "status", "/help": "help", "/new": "new", "/stop": "stop"}
_UNSET = object()

if TYPE_CHECKING:
    class _DateTimeProvider(Protocol):
        def now(self, tz: dt.tzinfo | None = ...) -> dt.datetime: ...

    class _PrivateConversationsModule(Protocol):
        datetime: _DateTimeProvider
        def _read_json(self, path: Path) -> Payload: ...
        def _atomic_write_json(self, path: Path, payload: Payload) -> None: ...
        def record_event_timing(self, profile: str, message_id: str, stage: str) -> None: ...
        def _lark_payload(self, runner: CommandRunner, cli_bin: str, profile: str, tail: list[str]) -> PayloadView | None: ...
        def resumable_answer_image_session(
            self, core: ChatExternalConversations, request: Payload, turn: Payload | None,
        ) -> Payload | None: ...


def _pc() -> _PrivateConversationsModule:
    from . import private_conversations
    return cast("_PrivateConversationsModule", private_conversations)


def _mapping(value: object) -> PayloadView:
    return value if isinstance(value, Mapping) else {}


def _req_dict(mapping: PayloadView, key: str) -> Payload:
    value = mapping[key]
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _ensure_dict(mapping: Payload, key: str) -> Payload:
    value = mapping.get(key)
    if isinstance(value, dict):
        return value
    created: Payload = {}
    mapping[key] = created
    return created


def _str_field(mapping: PayloadView, key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _msg_type(message: PayloadView) -> object:
    return message.get("msg_type", message.get("message_type"))


def _valid_user_sender(
    message: PayloadView | None, chat_id: str, sender_id: object = _UNSET, *,
    msg_type: object = _UNSET, not_deleted: bool = False, message_id: object = _UNSET,
) -> bool:
    if not isinstance(message, Mapping):
        return False
    sender = message.get("sender")
    return (
        message.get("chat_id") == chat_id
        and isinstance(sender, Mapping) and sender.get("sender_type") == "user"
        and (sender_id is _UNSET or sender.get("id") == sender_id)
        and (msg_type is _UNSET or _msg_type(message) == msg_type)
        and (message_id is _UNSET or message.get("message_id") == message_id)
        and (not not_deleted or message.get("deleted") is not True)
    )


def _lark_payload(runner: CommandRunner, cli_bin: str, profile: str, tail: list[str]) -> PayloadView | None:
    result = call(runner, lark_args(cli_bin=cli_bin, profile=profile, tail=tail))
    payload = json_payload(result)
    if result.get("returncode") != 0 or payload.get("code", 0) != 0 or payload.get("ok") is False:
        return None
    return payload


class PrivateSourceVerificationMixin:
    """Source message readback, group provenance, control hints, and reply context."""

    if TYPE_CHECKING:
        cli_bin: str
        runner: CommandRunner
        core: ChatExternalConversations
        bindings: ChatConversationBindings

        def _binding_row(self, binding_id: str) -> Payload | None: ...
        def _resolve_binding(
            self, binding_id: str, source: PayloadView, *, include_observation: bool = False,
            session: PayloadView | None = None, session_context: PayloadView | None = None,
        ) -> Payload: ...

    def _rendered_mget(
        self, profile: str, message_id: str, *, runner: CommandRunner | None = None,
    ) -> PayloadView:
        return call(
            runner or self.runner,
            lark_args(cli_bin=self.cli_bin, profile=profile, tail=[
                "im", "+messages-mget", "--message-ids", message_id,
                "--as", "bot", "--no-reactions", "--format", "json",
            ]),
        )

    def _group_source_message(self, profile: str, message_id: str) -> PayloadView | None:
        # The display-oriented mget projection omits reply ancestry. Read the
        # original provider envelope for group provenance, including root @.
        payload = _lark_payload(
            self.runner, self.cli_bin, profile,
            ["api", "GET", f"/open-apis/im/v1/messages/{message_id}", "--as", "bot"],
        )
        return _message(payload, message_id) if payload is not None else None

    def _original_message_pair(
        self, profile: str, message_id: str, other_id: str, *, runner: CommandRunner | None = None,
    ) -> tuple[PayloadView | None, PayloadView | None]:
        # Read both admitted IDs at once. Callers verify the returned envelopes
        # and the current Core selection before using either as authority.
        payload = _lark_payload(
            runner or self.runner, self.cli_bin, profile,
            ["api", "GET", "/open-apis/im/v1/messages/mget",
             "--params", json.dumps({"message_ids": [message_id, other_id]}), "--as", "bot"],
        )
        data = payload.get("data") if payload is not None else None
        items = data.get("items") if isinstance(data, Mapping) else None
        if (not isinstance(items, list) or len(items) != 2
                or any(not isinstance(item, Mapping) or not isinstance(item.get("message_id"), str) for item in items)
                or {item.get("message_id") for item in items} != {message_id, other_id}):
            return None, None
        messages: dict[str, PayloadView] = {str(item["message_id"]): item for item in items}
        return messages[message_id], messages[other_id]

    def _group_source_pair(self, profile: str, message_id: str, root_id: str) -> tuple[PayloadView | None, PayloadView | None]:
        return self._original_message_pair(profile, message_id, root_id)

    def _completed_stop_root(self, record: PayloadView, binding: PayloadView) -> str | None:
        """Read scheduling only; fresh Core resolution still owns authorization."""
        event = _mapping(record["event"])
        message_id = _str_field(event, "message_id")
        root = str(event.get("root_id") or message_id)
        path = self.core.root / f"{_str_field(record, 'request_ref')}.json"
        native: PayloadView = _pc()._read_json(path) if path.exists() else {}
        if (binding.get("enabled") is True and binding.get("audience") == "group"
                and binding.get("binding_id") == record["binding_id"]
                and binding.get("transport_ref") == record["profile"]
                and root != message_id and native.get("command") == "stop"
                and native.get("status") == "command_completed" and native.get("target_recorded") is True
                and native.get("binding_id") == record["binding_id"]
                and native.get("source") == record["source"] == lark_private_source(
                    provider_ref=_str_field(binding, "provider_ref"), event=event)
                and identity_ref(_str_field(binding, "provider_ref"), message_id)
                == record["request_ref"] == native.get("request_ref")):
            return root
        return None

    def _resolve_source_selection(
        self, record: PayloadView, *, render_content: bool, sender_identity: dict[str, str] | None,
    ) -> tuple[Payload, tuple[PayloadView | None, PayloadView | None] | None, PayloadView | None] | None:
        event = _mapping(record["event"])
        binding_id = _str_field(record, "binding_id")
        source = _mapping(record["source"])
        read_binding = (self._binding_row(binding_id) or {}) if not render_content else None
        root = self._completed_stop_root(record, read_binding) if read_binding else None
        if root:
            with ThreadPoolExecutor(max_workers=1) as reads:
                envelopes = reads.submit(
                    self._group_source_pair, _str_field(record, "profile"), _str_field(event, "message_id"), root,
                )
                selected = self._resolve_binding(binding_id, source, include_observation=True)
                prefetched = envelopes.result()
            if self._binding_row(binding_id) not in (read_binding, selected["binding"]) or read_binding != selected["binding"]:
                return None  # Revocation/replacement while reads ran is not a delivery grant.
            return selected, prefetched, read_binding
        return self._resolve_binding(binding_id, source, include_observation=sender_identity is not None), None, read_binding

    def _validate_group_source(
        self, record: PayloadView, message: Payload, canonical: PayloadView | None,
        stop_root: PayloadView | None, observation: PayloadView | None, *,
        render_content: bool, paired_root: bool,
    ) -> tuple[Payload, PayloadView] | None:
        event = _mapping(record["event"])
        chat_id, message_id = _str_field(event, "chat_id"), _str_field(event, "message_id")
        sender_id = _mapping(message["sender"])["id"]
        if (canonical is None
                or not _valid_user_sender(canonical, chat_id, sender_id, msg_type=_msg_type(message), not_deleted=True)
                or (not render_content and _msg_type(canonical) != event.get("message_type"))):
            return None
        merged: Payload = {**canonical, **({"content": message.get("content")} if render_content else {})}
        # Provider readback, never event text, fixes the topic root. The compact
        # event projection emits null for absent ancestry; parent/root must match.
        root = str(merged.get("root_id") or merged["message_id"])
        parent = str(merged.get("parent_id") or "")
        event_parent, event_thread = str(event.get("parent_id") or ""), str(event.get("thread_id") or "")
        if (root != str(event.get("root_id") or message_id) or (parent and not merged.get("root_id"))
                or parent != event_parent or (event_thread and event_thread != str(merged.get("thread_id") or ""))):
            return None
        profile = _str_field(record, "profile")
        original = (
            merged if root == message_id else stop_root if paired_root
            else self._group_source_message(profile, root)
        )
        if observation is None:
            observation = self.bindings.observe(profile)
        if (original is None or not _valid_user_sender(original, chat_id, not_deleted=True)
                or original.get("parent_id") or original.get("root_id") not in (None, "", root)
                or not isinstance(original.get("mentions"), list)
                or not lark_event_mentions_bot(
                    original, bot_display_name=str(observation["bot_display_name"]),
                    bot_app_id=str(observation["bot_app_id"]),
                    bot_open_id=str(observation["bot_open_id"]), allow_text_fallback=False,
                )):
            return None
        if _msg_type(merged) == "text":
            cmd_input = self._group_text_command_input(merged, observation)
            if not cmd_input:
                return None  # A display rendering cannot supply missing canonical text.
            merged["_command_input"] = cmd_input
        return merged, observation

    def _source_message(
        self, record: Payload, *, selected: Payload | None = None,
        render_content: bool = True, sender_identity: dict[str, str] | None = None,
        source_pair: tuple[PayloadView | None, PayloadView | None] | None = None,
        report_private_recall: bool = False,
    ) -> Payload | None:
        """Read the exact source under this App, then recheck the Core audience.

        A p2p source read proves the destination without requiring group-member
        scopes. A caller cannot opt into this path with an HTTP boolean.
        Queued private cards can request a recall marker after source verification.
        """
        event = _mapping(record["event"])
        profile, message_id = _str_field(record, "profile"), _str_field(event, "message_id")
        chat_id, request_ref = _str_field(event, "chat_id"), _str_field(record, "request_ref")
        read_json = _pc()._read_json
        try:
            prefetched: tuple[PayloadView | None, PayloadView | None] | None = None
            read_binding: PayloadView | None = None
            if selected is None:
                resolved = self._resolve_source_selection(
                    record, render_content=render_content, sender_identity=sender_identity,
                )
                if resolved is None:
                    return None
                selected, prefetched, read_binding = resolved
            native_path = self.core.root / f"{request_ref}.json"
            native: PayloadView = read_json(native_path) if native_path.exists() else {}
            if native.get("agent_target"):
                self.bindings.resolve_agent_target(selected, _req_dict(native, "agent_target"))
            group = _mapping(selected["binding"]).get("audience") == "group"
            raw_obs = selected.get("observation")
            observation: PayloadView | None = raw_obs if isinstance(raw_obs, Mapping) else None
            if sender_identity is not None and observation is not None:
                sender_identity["bot_display_name"] = str(observation.get("bot_display_name") or "")
            stop_root: PayloadView | None = None
            canonical: PayloadView | None = None
            paired_root = False
            if group and render_content:
                control_hint = (
                    event.get("message_type") == "text"
                    and isinstance(event.get("content"), str)
                    and event.get("content") in CHAT_CONTROL_COMMANDS
                )
                with ThreadPoolExecutor(max_workers=3) as reads:
                    current = reads.submit(self._group_source_message, profile, message_id)
                    display = None if control_hint else reads.submit(self._rendered_mget, profile, message_id)
                    identity = reads.submit(self.bindings.observe, profile) if observation is None else None
                    canonical = current.result()
                    if identity is not None:
                        observation = identity.result()
                    cmd_input = (
                        self._group_text_command_input(canonical, observation)
                        if control_hint and canonical is not None and observation is not None
                        and _msg_type(canonical) == "text" else None
                    )
                    if (cmd_input in CHAT_CONTROL_COMMANDS and canonical is not None
                            and (not native or native.get("message") == cmd_input)):
                        result: PayloadView = {"returncode": 0}
                        message: PayloadView | None = {**canonical, "content": cmd_input}
                    else:
                        result = display.result() if display is not None else self._rendered_mget(profile, message_id)
                        message = _message(json_payload(result), message_id)
            elif group:
                root_id = str(event.get("root_id") or message_id)
                completed_stop = self._completed_stop_root(record, _mapping(selected["binding"])) == root_id
                paired_root = completed_stop or source_pair is not None

                def sources() -> tuple[PayloadView | None, PayloadView | None]:
                    if source_pair is not None:
                        return source_pair
                    if completed_stop:
                        if prefetched is not None and read_binding == selected["binding"]:
                            return prefetched
                        return self._group_source_pair(profile, message_id, root_id)
                    return self._group_source_message(profile, message_id), None

                if observation is not None:
                    canonical, stop_root = sources()
                else:
                    with ThreadPoolExecutor(max_workers=2) as reads:
                        envelopes = reads.submit(sources)
                        identity = reads.submit(self.bindings.observe, profile)
                        (canonical, stop_root), observation = envelopes.result(), identity.result()
                result, message = {"returncode": 0 if canonical else 1}, canonical
            else:
                if render_content:
                    with ThreadPoolExecutor(max_workers=2) as reads:
                        display = reads.submit(self._rendered_mget, profile, message_id)
                        envelope = reads.submit(self._group_source_message, profile, message_id)
                        result, canonical = display.result(), envelope.result()
                else:
                    result = self._rendered_mget(profile, message_id)
                message = _message(json_payload(result), message_id)
            if result.get("returncode") != 0 or not _valid_user_sender(message, chat_id, event["sender_id"]):
                return None
            assert message is not None
            sender_id = _mapping(message["sender"])["id"]
            if render_content and not group:
                if (canonical is None
                        or not _valid_user_sender(canonical, chat_id, sender_id, msg_type=_msg_type(message), message_id=message_id)
                        or any(message.get(key) and canonical.get(key) != message[key] for key in ("root_id", "parent_id", "thread_id"))):
                    return None
                if message.get("deleted") is True or canonical.get("deleted") is True:
                    return {"deleted": True} if report_private_recall else None
                merged: Payload = {**canonical, "content": message.get("content")}
            elif message.get("deleted") is True:
                return None
            else:
                merged = dict(message)
            if render_content:
                if observation is None:
                    observation = self.bindings.observe(profile)
                bot_open_id, bot_app_id = observation.get("bot_open_id"), observation.get("bot_app_id")
                merged["_native_mention"] = (
                    isinstance(canonical, Mapping) and isinstance(canonical.get("mentions"), list)
                    and bool(bot_open_id or bot_app_id)
                    and lark_event_mentions_bot(
                        canonical, bot_display_name="",
                        bot_app_id=str(bot_app_id) if isinstance(bot_app_id, str) else None,
                        bot_open_id=str(bot_open_id) if isinstance(bot_open_id, str) else None,
                        allow_text_fallback=False,
                    )
                )
            if group:
                validated = self._validate_group_source(
                    record, merged, canonical, stop_root, observation,
                    render_content=render_content, paired_root=paired_root,
                )
                if validated is None:
                    return None
                merged, observation = validated
            if not render_content:
                if self._binding_row(_str_field(record, "binding_id")) != selected["binding"]:
                    return None
                review = _mapping(record.get("context_recovery")).get("answer_review") is True
                result_turn = _mapping(native.get("native_result_recovery")).get("turn_id")
                if review or (result_turn is not None and result_turn == record.get("turn_id")):
                    current_request = self.core.read_request(request_ref)
                    if (not _same_fields(current_request, record, "request_ref", "binding_id", "source", "session_id", "turn_id")
                            or not recovery_source_matches(record, merged, current_request)):
                        return None
            if sender_identity is not None and observation is None:
                return None
            return merged
        except (KeyError, ValueError, OSError):
            return None

    def _source_verified(
        self, record: Payload, *, sender_identity: dict[str, str] | None = None, expected_text: str | None = None,
    ) -> bool:
        message = self._source_message(record, render_content=False, sender_identity=sender_identity)
        return message is not None and (
            expected_text is None
            or (message.get("deleted") is not True and _msg_type(message) == "text"
                and private_source_text(message) == expected_text)
        )

    def _source_authority(
        self, record: Payload, profile: str, chat_id: str, message_id: str, *, expected_text: str | None = None,
    ) -> tuple[str | None, bool]:
        """Verify one exact outbound operation; retain no identity or grant."""
        event = _mapping(record["event"])
        if profile != record["profile"] or chat_id != event["chat_id"] or message_id != event["message_id"]:
            return None, False
        identity: dict[str, str] = {}
        timing = _pc().record_event_timing
        timing(profile, message_id, "outbound_source_check_started")
        verified = self._source_verified(record, sender_identity=identity, expected_text=expected_text)
        timing(profile, message_id, "outbound_source_check_returned")
        return identity.get("bot_display_name"), verified

    def _recovery_source_observation(self, record: Payload, selected: Payload) -> Payload | None:
        source = self._source_message(record, selected=selected)
        if source is None:
            return None
        native = self.core.read_request(_str_field(record, "request_ref"))
        return dict(source) if recovery_source_matches(record, source, native) else None

    @staticmethod
    def _group_control_hint(event: PayloadView) -> str | None:
        # Rendered event mentions only schedule readonly source IO. The raw
        # provider text and current App identity still authorize the command.
        text = event.get("content")
        if not isinstance(text, str):
            return None
        text = text.strip()
        if text in CHAT_CONTROL_COMMANDS:
            return text
        mentions = event.get("mentions")
        if not isinstance(mentions, list):
            return None
        candidates: set[str] = set()
        for mention in mentions:
            if not isinstance(mention, Mapping):
                continue
            name = mention.get("name")
            for prefix in (mention.get("key"), "@" + name if isinstance(name, str) else None):
                if isinstance(prefix, str) and prefix and text.startswith(prefix) and text[len(prefix):len(prefix) + 1].isspace():
                    command = text[len(prefix):].strip()
                    if command in CHAT_CONTROL_COMMANDS:
                        candidates.add(command)
        return next(iter(candidates)) if len(candidates) == 1 else None

    def _admitted_control_root(self, record: PayloadView, binding: PayloadView) -> str | None:
        """Schedule readonly IO only for a topic already admitted by this Core."""
        event = _mapping(record["event"])
        root = event.get("root_id")
        if (binding.get("enabled") is not True or binding.get("audience") != "group"
                or event.get("message_type") != "text" or not isinstance(event.get("content"), str)
                or self._group_control_hint(event) is None
                or not isinstance(root, str) or not root or root == event["message_id"]):
            return None
        try:
            read_json = _pc()._read_json
            original_ref = identity_ref(_str_field(binding, "provider_ref"), root)
            original = read_json(self.core.root / f"{original_ref}.json")
            existing = self.core.root / f"{_str_field(record, 'request_ref')}.json"
            if existing.exists() and read_json(existing).get("message") != event["content"]:
                return None  # Keep older journals' exact rendered representation.
            orig_source = _mapping(original.get("source"))
            if (original.get("request_ref") == original_ref
                    and original.get("binding_id") == binding["binding_id"]
                    and original.get("status") == "accepted" and original.get("command") is None
                    and original.get("session_id")
                    and orig_source.get("group_human_message") is True
                    and orig_source.get("source_ref") == _mapping(record["source"]).get("source_ref")):
                return root
        except (KeyError, ValueError, OSError):
            pass
        return None

    @staticmethod
    def _group_text_command_input(message: PayloadView, observation: PayloadView) -> str | None:
        # Display readback replaces native mention keys with names. Parse only
        # the lossless provider text, so names and quoted mentions cannot grant
        # a control operation. Model input keeps its original rendered text.
        try:
            body_content = _str_field(_mapping(message["body"]), "content")
            text = json.loads(body_content)["text"]
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(text, str):
            return None
        text = text.strip()
        mentions = message.get("mentions")
        if not isinstance(mentions, list):
            return text
        leading = [
            mention for mention in mentions if isinstance(mention, Mapping)
            and isinstance(mention.get("key"), str) and mention["key"]
            and text.startswith(mention["key"]) and text[len(mention["key"]):len(mention["key"]) + 1].isspace()
        ]
        if len(leading) == 1:
            identities = {str(observation["bot_app_id"]), str(observation["bot_open_id"])} - {""}
            if lark_provider_mention_identities(leading[0]).intersection(identities):
                return text[len(_str_field(leading[0], "key")):].strip()
        return text

    def _reply_context_message(
        self, path: Path, record: Payload, *, selected: PayloadView | None, source_message: PayloadView, text: str,
    ) -> str:
        """Freeze new ordinary reply context before the existing Core admission.

        Core remains the owner of accepted requests. In particular, an older
        request's exact message must survive an adapter upgrade unchanged.
        """
        if "reply_context_message" in record:
            if record["reply_context_source_text"] != text:
                raise ValueError("reply source content changed")
            return str(record["reply_context_message"])
        event = _mapping(record["event"])
        message_id = _str_field(event, "message_id")
        if ((self.core.root / f"{_str_field(record, 'request_ref')}.json").exists()
                or not (event.get("parent_id") or (event.get("root_id") and event["root_id"] != message_id))):
            return text
        if selected is None:
            raise ValueError("reply binding observation unavailable")
        profile, chat_id = _str_field(record, "profile"), _str_field(event, "chat_id")
        current = (
            source_message if _mapping(selected["binding"]).get("audience") == "group"
            else self._group_source_message(profile, message_id)
        )
        if (current is None
                or not _valid_user_sender(current, chat_id, event["sender_id"],
                                           msg_type=event["message_type"], not_deleted=True, message_id=message_id)
                or (event.get("thread_id") and current.get("thread_id") != event["thread_id"])
                or any(str(current.get(key) or "") != str(event.get(key) or "") for key in ("parent_id", "root_id"))):
            raise ValueError("reply source ancestry unavailable or changed")

        def read(args: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            result = call(self.runner, list(args))
            return subprocess.CompletedProcess(
                args, int(result["returncode"]), str(result.get("stdout") or ""), str(result.get("stderr") or ""),
            )

        context = enrich_lark_event_reply_context(
            event, runner=read, command_prefix=[self.cli_bin], profile=profile,
            profile_app_id=_str_field(_mapping(selected["observation"]), "bot_app_id"),
            configured_chat_id=chat_id, current_message=current,
        )
        projected = conversation_reply_context({**context, "source_conversation_id": chat_id})
        prefix = str(projected["context_text"])
        message = f"{prefix}\n\nCurrent user message:\n{text}" if prefix else text
        thread_id = context.get("thread_id")
        if (event["chat_type"] == "p2p" and event.get("parent_id") and not event.get("thread_id")
                and context.get("reply_context_verified") is True
                and isinstance(thread_id, str) and 0 < len(thread_id) <= 200):
            record["event"] = {**event, "thread_id": thread_id}
        record.update(reply_context_message=message, reply_context_source_text=text)
        _pc()._atomic_write_json(path, record)
        return message
