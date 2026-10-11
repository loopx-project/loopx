"""Private-message transport for native, Core-bound project Chat.

Only provider provenance, Inbox presentation and delivery receipts live here.
Core owns audience grants, canonical Sessions, durable Turns and stop/recovery.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ...capabilities.native_chat.external_conversations import ChatExternalConversations
from ...chat_store import TERMINAL_TURN_STATES, _atomic_write_json, _read_json
from ...file_lock import exclusive_file_lock
from ...presentation.markdown import markdown_scalar
from ...presentation.renderers.conversation_status_markdown import render_conversation_status
from .conversation_identity import identity_ref, lark_private_source
from .event_inbox import acknowledge_lark_event_inbox, ingest_lark_event_inbox
from .goal_channel_transport import APP_ID_PATTERN, call, json_payload, lark_args, lark_provider_mention_identities
from .inbox_reply import _message, reply_lark_event_inbox, update_lark_inbox_reply, verify_lark_inbox_reply
from .manager_context import manager_failure_reply
from .inbox_reactions import mark_lark_event_inbox_processing, mark_lark_event_inbox_received
from .outbound import present_lark_conversation_text as _presentation_text
from .private_images import private_message_caption, private_message_images
from .private_progress import ACTIVITY_UPDATE_INTERVAL_SEC, UPDATE_INTERVAL_SEC, project_progress

# Conservative transport budget, not a claim about the provider's maximum.
# Reserve edits for a final/closing notice; continue long Turns on a new draft.
PROGRESS_EDIT_BUDGET = 12


class LarkPrivateConversations:
    def __init__(self, *, controller: Any, runtime_root: Path, runner: Any, cli_bin: str,
                 reaction_feedback: bool = True) -> None:
        self.core = ChatExternalConversations(controller)
        self.bindings = self.core.bindings
        self.runtime_root, self.runner, self.cli_bin = runtime_root, runner, cli_bin
        self.reaction_feedback = reaction_feedback
        self.root = controller.store.root / "lark-private-deliveries"

    def profiles(self) -> dict[str, dict[str, str]]:
        """Discover current App configuration, never authorize a conversation.

        A network/token probe failing is not a durable disconnect. The local
        profile inventory supplies the actual App lease identity; Core still
        freshly verifies App/owner/source/grants at admission and delivery.
        """
        bindings = self.bindings.read()["bindings"]
        if not bindings:
            return {}
        result = call(self.runner, [self.cli_bin, "profile", "list"])
        try:
            configured = json.loads(str(result.get("stdout") or ""))
        except json.JSONDecodeError as exc:
            raise OSError("Lark profile configuration could not be read") from exc
        if result.get("returncode") != 0 or not isinstance(configured, list):
            # An unreadable inventory is unknown, not an empty configuration.
            # The existing stream watcher retains its route on read failures.
            raise OSError("Lark profile configuration could not be read")
        apps = {str(row.get("name") or ""): str(row.get("appId") or "")
                for row in configured if isinstance(row, Mapping)}
        profiles = {}
        for row in bindings:
            app_id = apps.get(row["transport_ref"], "")
            if (row.get("enabled") is not True or not APP_ID_PATTERN.fullmatch(app_id)
                    or identity_ref(app_id) != row["provider_ref"]):
                continue
            profiles[row["transport_ref"]] = {"cli_bin": self.cli_bin, "provider_ref": row["provider_ref"],
                "binding_id": row["binding_id"], "consumer_ref": hashlib.sha256(app_id.encode("utf-8")).hexdigest()[:32]}
        return profiles

    def health(self) -> dict[str, dict[str, int]]:
        health: dict[str, dict[str, int]] = {}
        for path in self.root.glob("*.json"):
            row = _read_json(path)
            entry = health.setdefault(row["binding_id"], {"pending_count": 0, "recovery_count": 0})
            if row["status"] != "delivered":
                entry["pending_count"] += 1
                entry["recovery_count"] += int(any(phase.get("started") and not phase.get("verified")
                                                   for phase in row["deliveries"].values()))
        return health

    def _binding(self, profile: str) -> dict[str, Any]:
        return next(row for row in self.bindings.read()["bindings"] if row["transport_ref"] == profile)

    def _group_source_message(self, profile: str, message_id: str) -> Mapping[str, Any] | None:
        # The display-oriented mget projection omits reply ancestry. Read the
        # original provider envelope for group provenance, including root @.
        result = call(self.runner, lark_args(cli_bin=self.cli_bin, profile=profile,
            tail=["api", "GET", f"/open-apis/im/v1/messages/{message_id}", "--as", "bot"]))
        payload = json_payload(result)
        if result.get("returncode") != 0 or payload.get("code", 0) != 0 or payload.get("ok") is False:
            return None
        return _message(payload, message_id)

    def _source_message(self, record: dict[str, Any], *, selected: dict[str, Any] | None = None) -> Mapping[str, Any] | None:
        """Read the exact source under this App, then recheck the Core audience.

        A p2p source read proves the destination without requiring group-member
        scopes. A caller cannot opt into this path with an HTTP boolean.
        """
        event = record["event"]
        try:
            if selected is None:
                selected = self.bindings.resolve(binding_id=record["binding_id"], **record["source"])
            native_path = self.core.root / f"{record['request_ref']}.json"
            native = _read_json(native_path) if native_path.exists() else {}
            if native.get("agent_target"):
                self.bindings.resolve_agent_target(selected, native["agent_target"])
            result = call(self.runner, lark_args(cli_bin=self.cli_bin, profile=record["profile"],
                tail=["im", "+messages-mget", "--message-ids", event["message_id"],
                      "--as", "bot", "--no-reactions", "--format", "json"]))
            message = _message(json_payload(result), event["message_id"])
            sender = message.get("sender") if isinstance(message, Mapping) else None
            if (result.get("returncode") == 0 and message is not None
                    and message.get("chat_id") == event["chat_id"] and isinstance(sender, Mapping)
                    and sender.get("id") == event["sender_id"] and sender.get("sender_type") == "user"):
                if selected["binding"].get("audience") == "group":
                    canonical = self._group_source_message(record["profile"], event["message_id"])
                    canonical_sender = canonical.get("sender") if isinstance(canonical, Mapping) else None
                    if (canonical is None or canonical.get("chat_id") != event["chat_id"]
                            or not isinstance(canonical_sender, Mapping)
                            or canonical_sender.get("id") != sender.get("id")
                            or canonical_sender.get("sender_type") != "user"
                            or canonical.get("msg_type", canonical.get("message_type"))
                            != message.get("msg_type", message.get("message_type"))):
                        return None
                    # Keep the existing text/media rendering, but only the
                    # lossless envelope supplies identity, ancestry and @.
                    message = {**canonical, "content": message.get("content")}
                    # Provider readback, never event text, fixes the topic root.
                    root = str(message.get("root_id") or message["message_id"])
                    # The compact event projection emits null for absent
                    # ancestry. Readback can enrich the thread id later; that is
                    # not a conflict unless the event supplied another id.
                    # Parent/root ancestry must still agree exactly.
                    parent = str(message.get("parent_id") or "")
                    event_parent = str(event.get("parent_id") or "")
                    event_thread = str(event.get("thread_id") or "")
                    if (root != str(event.get("root_id") or event["message_id"])
                            or (parent and not message.get("root_id"))
                            or parent != event_parent
                            or (event_thread and event_thread != str(message.get("thread_id") or ""))):
                        return None
                    if root == event["message_id"]:
                        original: Mapping[str, Any] | None = message
                    else:
                        original = self._group_source_message(record["profile"], root)
                    from .event_inbox import lark_event_mentions_bot
                    observation = self.bindings.observe(record["profile"])
                    if (not original or original.get("chat_id") != event["chat_id"]
                            or original.get("sender", {}).get("sender_type") != "user"
                            or original.get("parent_id") or original.get("root_id") not in (None, "", root)
                            or not isinstance(original.get("mentions"), list)
                            or not lark_event_mentions_bot(original, bot_display_name=observation["bot_display_name"],
                                                           bot_app_id=observation["bot_app_id"],
                                                           bot_open_id=observation["bot_open_id"],
                                                           allow_text_fallback=False)):
                        return None
                    if message.get("msg_type", message.get("message_type")) == "text":
                        message["_command_input"] = self._group_text_command_input(message, observation)
                return message
        except (KeyError, ValueError, OSError):
            pass
        return None

    def _source_verified(self, record: dict[str, Any]) -> bool:
        return self._source_message(record) is not None

    @staticmethod
    def _group_text_command_input(message: Mapping[str, Any], observation: Mapping[str, Any]) -> str | None:
        # Display readback replaces native mention keys with names. Parse only
        # the lossless provider text, so names and quoted mentions cannot grant
        # a control operation. Model input keeps its original rendered text.
        try:
            text = json.loads(message["body"]["content"])["text"]
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(text, str):
            return None
        text = text.strip()
        mentions = message.get("mentions")
        if not isinstance(mentions, list):
            return text
        leading = [mention for mention in mentions if isinstance(mention, Mapping)
                   and isinstance(mention.get("key"), str) and mention["key"]
                   and text.startswith(mention["key"])
                   and text[len(mention["key"]):len(mention["key"]) + 1].isspace()]
        if len(leading) == 1:
            identities = {observation["bot_app_id"], observation["bot_open_id"]} - {""}
            if lark_provider_mention_identities(leading[0]).intersection(identities):
                return text[len(leading[0]["key"]):].strip()
        return text

    def _inbox(self, record: dict[str, Any]) -> Path:
        observation = self.bindings.observe(record["profile"])
        scope = record["source"]["source_ref"]
        path = self.runtime_root / ".loopx" / "config" / "private-chat" / f"{scope}.json"
        _atomic_write_json(path, {
            "schema_version": "lark_event_inbox_config_v0", "enabled": True,
            "inbox_dir": f".loopx/inbox/private-chat/{scope}", "capture_scope": "configured_chat_all",
            "reply": {"enabled": True, "sender_profile": record["profile"], "sender_identity": "bot",
                "bot_display_name": observation["bot_display_name"], "chat_id": record["event"]["chat_id"],
                "placement_policy": "source_thread" if record["source"].get("group_ref") else "source_context", "received_reaction_emoji": "Get" if self.reaction_feedback else "",
                "received_reaction_policy": "retain", "processing_reaction_emoji": "OnIt" if self.reaction_feedback else ""},
        })
        event = record["event"]
        # Some unsupported attachment events have no rendered content. Keep a
        # source-backed presentation placeholder so the explicit notice can reply.
        content = event.get("content") or "附件（内容尚不支持）"
        captured = ingest_lark_event_inbox(project=self.runtime_root, config_path=path,
            events=[{**event, "content": content, "schema_version": "lark_event_inbox_event_v0"}], execute=True)
        if captured["invalid_count"]:
            raise ValueError("private source could not be captured")
        return path

    def admit(self, profile: str, event: dict[str, Any]) -> dict[str, Any]:
        try:
            binding = self._binding(profile)
            source = lark_private_source(provider_ref=binding["provider_ref"], event=event)
            selected = self.bindings.resolve(binding_id=binding["binding_id"], **source)
        except (KeyError, StopIteration, ValueError):
            return {"status": "audience_rejected"}
        request = identity_ref(binding["provider_ref"], event["message_id"])
        path = self.root / f"{request}.json"
        with exclusive_file_lock(path, operation="capture_private_chat_request"):
            if path.exists():
                record = _read_json(path)
                # event_id can change on redelivery; canonical message content
                # and audience cannot change underneath the stable message id.
                if any(record["event"].get(key) != event.get(key) for key in
                       ["message_id", "sender_id", "chat_id", "content", "message_type", "chat_type", "sender_type", "root_id", "parent_id", "thread_id"]):
                    return {"status": "source_conflict"}
            else:
                record = {"schema_version": "lark_private_chat_delivery_v0", "request_ref": request,
                    "profile": profile, "binding_id": binding["binding_id"], "source": source,
                    "event": event, "deliveries": {}, "status": "captured"}
                if binding.get("audience") != "group":
                    _atomic_write_json(path, record)
            # Reuse this admission preflight only; Core rechecks under its
            # source fence, and outbound writes always perform a fresh check.
            source_message = self._source_message(record, selected=selected)
            if source_message is None:
                return {"status": "source_verification_failed"}
            if binding.get("audience") == "group":
                _atomic_write_json(path, record)
            message_type = str(event.get("message_type") or "")
            text = ""
            attachments: list[dict[str, Any]] = []
            if message_type == "text":
                # lark-cli renders event and mget content as plain text. Read
                # the full canonical message; do not decode a rendered event.
                canonical_type = source_message.get("msg_type", source_message.get("message_type"))
                if canonical_type != "text":
                    return {"status": "source_conflict"}
                content = source_message.get("content")
                if isinstance(content, str):
                    text = content
                else:
                    # Raw provider envelopes are supported by the existing
                    # readback contract too; keep parsing isolated to body.
                    try:
                        raw = json.loads(source_message["body"]["content"])
                        text = raw["text"]
                    except (ValueError, KeyError, TypeError):
                        return {"status": "invalid_text"}
                    if not isinstance(text, str):
                        return {"status": "invalid_text"}
                if not text.strip():
                    return {"status": "empty_text"}
            elif message_type in {"image", "post"}:
                if source_message.get("msg_type", source_message.get("message_type")) != message_type:
                    return {"status": "source_conflict"}
                content = source_message.get("content")
                if not isinstance(content, str):
                    return {"status": "source_verification_failed"}
                if record.get("source_content", content) != content:
                    return {"status": "source_conflict"}
                record["source_content"] = content
                if "attachments" not in record and not record.get("attachment_notice"):
                    try:
                        text, attachments = private_message_images(content=content, message_type=message_type,
                            message_id=event["message_id"], profile=profile, cli_bin=self.cli_bin, runner=self.runner)
                        record.update(message=text, attachments=attachments)
                    except ValueError as exc:
                        record["attachment_notice"] = str(exc)
                    _atomic_write_json(path, record)
                text, attachments = record.get("message", ""), record.get("attachments", [])
            command_input = private_message_caption(record["source_content"]) if attachments else text.strip()
            if binding.get("audience") == "group" and message_type == "text":
                command_input = source_message.get("_command_input") or command_input
            command = {"/status": "status", "/help": "help", "/new": "new", "/stop": "stop"}.get(command_input)
            if command_input == "/agents":
                command = "agents"
            elif command_input == "/project":
                command = "select_project"
            elif command_input == "/agent" or command_input.startswith("/agent "):
                command = "select_agent"
            if binding["context_kind"] == "steward":
                for prefix, selected_command in [("/delegate", "commission"), ("/委托", "commission"),
                                                  ("/confirm", "confirm_commission"), ("/cancel", "cancel_commission"),
                                                  ("/stop-commission", "stop_commission"), ("/resume-commission", "resume_commission")]:
                    if command_input == prefix or command_input.startswith(prefix + " "):
                        command = selected_command
                        break
            if message_type not in {"text", "image", "post"} or record.get("attachment_notice"):
                command = "unsupported"
            try:
                admitted = self.core.admit(binding_id=binding["binding_id"], source=source,
                    request_ref=request, message=text, command=command, attachments=attachments)
            except ValueError:
                record.update(status="rejected", response=("群助手仅处理当前项目和话题，不能选择个人 Agent 会话；/status 查看状态，/help 查看用法。" if binding.get("audience") == "group" else "操作或原授权不可用；Agent 请先用 /agents 查看确切命令，/project 返回项目对话。新委托请使用 /delegate --tokens N 具体目标，确认或取消请使用原预览中的完整命令。"))
                _atomic_write_json(path, record)
                return {"status": "command_rejected"}
            except RuntimeError as exc:
                if str(exc) != "session_queue_full":
                    raise
                record.update(status="rejected", response="队列已满，本条没有被受理；请稍后重试。")
                _atomic_write_json(path, record)
                return {"status": "queue_rejected"}
            record.update(status=admitted["status"], session_id=admitted["session_id"],
                          turn_id=admitted["turn_id"], response=admitted.get("response"), response_code=admitted.get("response_code"))
            _atomic_write_json(path, record)
            status = "durably_accepted" if admitted["status"] == "accepted" else (
                "queue_rejected" if admitted["status"] == "rejected" else "command_recorded")
            return {"status": status}

    def _reply_runner(self, args: Sequence[str]) -> Any:
        return self.runner([self.cli_bin, *args[1:]], None, 30)

    def return_inbox(self, *, route: dict[str, Any], session: dict[str, Any],
                     turn: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
        """Resolve an original worker return through its admitted private source.

        Core revalidates the saved steward context. The two correlation records
        must still name the exact canonical Session/Turn; neither the current
        conversation nor a worker-supplied destination can replace that source.
        Manager-context retains ownership of the return attempt and recovery.
        """
        saved = session["steward_context"]
        selected = self.bindings.session_context(saved)
        client = str(turn.get("client_turn_id") or "")
        request = client.removeprefix("external-")
        if client != f"external-{request}" or not re.fullmatch(r"[a-f0-9]{24}", request):
            raise ValueError("original private request unavailable")
        record = _read_json(self.root / f"{request}.json")
        native = self.core.read_request(request)
        message_id = str(record.get("event", {}).get("message_id") or "")
        if (route["source_id"] not in (request, f"lark:{message_id}")
                or identity_ref(selected["binding"]["provider_ref"], message_id) != request
                or session.get("goal_id") != "loopx-manager"
                or session.get("channel_id") != selected["channel_id"]
                or route["goal_id"] not in selected["context"]["goal_ids"]
                or route["session_id"] != session["session_id"]
                or turn.get("session_id") != session["session_id"]
                or record.get("profile") != selected["binding"]["transport_ref"]
                or record.get("request_ref") != request
                or record.get("source") != lark_private_source(
                    provider_ref=selected["binding"]["provider_ref"], event=record["event"])
                or record["source"]["source_ref"] != saved["source_ref"]
                or any(row.get("binding_id") != saved["binding_id"]
                       or row.get("source") != record["source"]
                       or row.get("session_id") != session["session_id"]
                       or row.get("turn_id") != turn["turn_id"]
                       for row in (record, native))):
            raise ValueError("original private return source changed")
        return self._inbox(record), record

    def _feedback(self, path: Path, record: dict[str, Any], *, inbox: Callable[[], Path], processing: bool = False) -> None:
        """Render observed Core admission/execution through the shared Inbox owner.

        Presentation failures never reject a persisted Turn. Existing reaction
        journals prevent uncertain provider writes from being repeated on replay.
        """
        if not self.reaction_feedback:
            return
        phase = "processing" if processing else "received"
        feedback = record.setdefault("feedback", {})
        if feedback.get(phase, {}).get("ok"):
            return
        config = inbox()
        if processing:
            result = mark_lark_event_inbox_processing(project=self.runtime_root, config_path=config,
                message_id=record["event"]["message_id"], execute=True, runner=self._reply_runner)
        else:
            result = mark_lark_event_inbox_received(project=self.runtime_root, config_path=config,
                event=record["event"], runner=self._reply_runner)
        feedback[phase] = result
        _atomic_write_json(path, record)

    def _deliver(self, path: Path, record: dict[str, Any], phase: str, text: str, *, inbox: Callable[[], Path]) -> bool:
        if not text:
            return False
        phase_state = record["deliveries"].get(phase, {})
        if phase_state.get("verified"):
            return True
        # Repair presentation before recording the exact provider intent. Core's
        # answer stays unchanged. An older frozen intent may contain raw CRLF;
        # preserve it when it still matches Core, letting Inbox verify its wire.
        if phase_state.get("text") != text:
            text = _presentation_text(text)
        if phase_state.get("text") not in (None, text):
            raise ValueError("delivery content changed after an attempt")
        config = inbox()
        kwargs = dict(project=self.runtime_root, config_path=config,
            message_id=record["event"]["message_id"], text=text, runner=self._reply_runner,
            finalize_reactions=phase not in {"admission", "progress"} and not (phase == "terminal" and record.get("commission_resources")),
            source_membership_verifier=lambda: self._source_verified(record))
        progress = record["deliveries"].get("progress") or {}
        stream = record.get("stream") or {}
        edit = phase_state.get("edit_from")
        if phase in {"terminal", "commission_result"} and progress.get("attempt") and not stream.get("closed") and not stream.get("edit_limit_reached") and not phase_state.get("started"):
            # Freeze the known message proof before the first final edit. Later
            # recovery can replace the same ID, never append another answer.
            edit = {"text": _presentation_text(stream.get("confirmed_text") or progress["text"]),
                    "attempt": stream.get("confirmed_attempt") or progress["attempt"]}
            phase_state["edit_from"] = edit
        if edit:
            def before_edit(_intent: str) -> dict[str, bool]:
                phase_state.update(text=text, started=True)
                record["deliveries"][phase] = phase_state
                _atomic_write_json(path, record)
                return {"continue_delivery": True}

            def edit_attempt(value: Any) -> None:
                phase_state["attempt"] = dict(value)
                record["deliveries"][phase] = phase_state
                _atomic_write_json(path, record)

            result = update_lark_inbox_reply(**kwargs, previous_text=edit["text"], attempt=edit["attempt"],
                before_send=before_edit, delivery_attempt_recorder=edit_attempt)
            if result.get("blocker") == "provider_update_edit_limit":
                # A confirmed edit rejection plus fresh non-matching readback
                # proves this final did not reach the draft. Preserve its proof
                # and use the existing source-bound, idempotent final sender.
                # Recovery of older, already-started final edits takes this
                # same path; an ambiguous failure must never take it.
                record.setdefault("stream", {})["edit_limit_reached"] = True
                phase_state["rejected_update"] = {"blocker": result["blocker"],
                    "edit_from": edit, "attempt": phase_state.get("attempt")}
                for key in ("edit_from", "attempt", "started"):
                    phase_state.pop(key, None)
                record["deliveries"][phase] = phase_state
                _atomic_write_json(path, record)
                return self._deliver(path, record, phase, text, inbox=inbox)
            if result.get("blocker") in {"provider_update_too_large", "provider_update_format_unsupported"}:
                # Do not truncate long/mention-bearing final answers to fit a
                # draft. Close it, then use the established full final sender.
                if not self._close_progress(path, record, inbox=inbox) and not record.get("stream", {}).get("edit_limit_reached"):
                    return False
                phase_state.pop("edit_from", None)
                record["deliveries"][phase] = phase_state
                _atomic_write_json(path, record)
                return self._deliver(path, record, phase, text, inbox=inbox)
        elif phase_state.get("attempt"):
            result = verify_lark_inbox_reply(**kwargs, attempt=phase_state["attempt"])
        elif phase_state.get("started"):
            # A crash between provider write and its receipt is ambiguous. Keep
            # the original source recoverable; never resend blindly.
            return False
        else:
            def before_send(_intent: str) -> dict[str, bool]:
                phase_state.update(text=text, started=True)
                record["deliveries"][phase] = phase_state
                _atomic_write_json(path, record)
                return {"continue_delivery": True}

            def attempt(value: Any) -> None:
                phase_state["attempt"] = dict(value)
                _atomic_write_json(path, record)

            result = reply_lark_event_inbox(**kwargs, execute=True, before_send=before_send,
                                           delivery_attempt_recorder=attempt, short_message_limit=None,
                                           content_format="markdown")
        # A verified intermediate reply is not completion. A final reply with
        # cleanup owed recovers its original attempt; it must never be resent.
        phase_state["verified"] = result.get("reply_verified") is True and result.get("ok") is True
        phase_state["blocker"] = result.get("blocker")
        record["deliveries"][phase] = phase_state
        _atomic_write_json(path, record)
        return bool(phase_state["verified"])

    def _edit_progress(self, path: Path, record: dict[str, Any], text: str, *, inbox: Callable[[], Path], finalizing: bool = False) -> bool:
        text = _presentation_text(text)
        progress = record["deliveries"].get("progress") or {}
        if not progress.get("verified"):
            return self._deliver(path, record, "progress", text, inbox=inbox)
        stream = record.setdefault("stream", {})
        if stream.get("edit_limit_reached") or (not finalizing and int(stream.get("edit_attempts") or 0) >= PROGRESS_EDIT_BUDGET):
            return False
        confirmed: dict[str, Any] = {}

        def before_send(_intent: str) -> dict[str, bool]:
            # Persist before the provider write: an uncertain acknowledgement
            # may have consumed an edit too. Restarts retain the same budget.
            stream["edit_attempts"] = int(stream.get("edit_attempts") or 0) + 1
            _atomic_write_json(path, record)
            return {"continue_delivery": True}

        result = update_lark_inbox_reply(project=self.runtime_root, config_path=inbox(),
            message_id=record["event"]["message_id"], text=text,
            previous_text=_presentation_text(stream.get("confirmed_text") or progress["text"]),
            attempt=stream.get("confirmed_attempt") or progress["attempt"],
            runner=self._reply_runner, source_membership_verifier=lambda: self._source_verified(record),
            before_send=before_send, delivery_attempt_recorder=lambda value: confirmed.update(value))
        stream["blocker"] = result.get("blocker")
        if result.get("blocker") == "provider_update_edit_limit":
            stream["edit_limit_reached"] = True
        if result.get("reply_verified") is True and result.get("ok") is True:
            stream.update(confirmed_text=text, confirmed_attempt=confirmed)
        _atomic_write_json(path, record)
        return result.get("ok") is True

    def _close_progress(self, path: Path, record: dict[str, Any], *, inbox: Callable[[], Path]) -> bool:
        text = "回答已生成，完整结果将单独发送。"
        if not self._edit_progress(path, record, text, inbox=inbox, finalizing=True):
            return False
        record["stream"]["closed"] = True
        _atomic_write_json(path, record)
        return True

    def _continue_progress(self, path: Path, record: dict[str, Any], *, inbox: Callable[[], Path]) -> bool:
        """Retire a verified draft before creating the next source-bound one.

        Only a verified closing edit or a definite provider edit-limit rejection
        permits rollover. An uncertain close/create keeps its frozen intent on
        replay; continuation never submits or restarts the Core Turn.
        """
        progress = record["deliveries"].get("progress") or {}
        if not progress.get("verified"):
            return False
        stream = record["stream"]
        if not stream.get("edit_limit_reached"):
            closed = self._edit_progress(path, record, "仍在处理，后续进展见下一条消息。",
                                         inbox=inbox, finalizing=True)
            if not closed and not stream.get("edit_limit_reached"):
                return False
        segment = int(stream.get("segment") or 0)
        record["deliveries"][f"progress.{segment}"] = {
            **progress, "text": stream.get("confirmed_text") or progress["text"],
            "attempt": stream.get("confirmed_attempt") or progress["attempt"],
            "edit_attempts": int(stream.get("edit_attempts") or 0),
            "edit_limit_reached": bool(stream.get("edit_limit_reached")),
        }
        record["deliveries"].pop("progress")
        for key in ("confirmed_text", "confirmed_attempt", "edit_attempts", "edit_limit_reached", "blocker", "closed"):
            stream.pop(key, None)
        stream["segment"] = segment + 1
        _atomic_write_json(path, record)
        return True

    def _progress(self, path: Path, record: dict[str, Any], *, inbox: Callable[[], Path],
                  session_id: str | None = None, turn_id: str | None = None) -> None:
        stream = record.setdefault("stream", {})
        events = self.core.controller.store.events_after(session_id or record["session_id"],
            turn_id or record["turn_id"], stream.get("cursor"))
        text = project_progress(stream, events)
        if text:
            # Journal the exact presentation the sender freezes, not the raw
            # Core text. The same conversion also recovers older draft proofs.
            text = _presentation_text(text)
        view = text
        if text and stream.get("segment"):
            text = f"**后续进展 {int(stream['segment']) + 1}**\n\n" + text
        _atomic_write_json(path, record)
        if not text or text == stream.get("confirmed_text"):
            return
        now = time.time()
        answer_digest = hashlib.sha256(str(stream.get("answer") or "").encode("utf-8")).hexdigest()
        interval = (ACTIVITY_UPDATE_INTERVAL_SEC if answer_digest == stream.get("confirmed_answer_digest")
                    else UPDATE_INTERVAL_SEC)
        if now - float(stream.get("last_attempt_at") or 0) < interval:
            return
        stream["last_attempt_at"] = now
        _atomic_write_json(path, record)
        # A failed creation must resume its frozen intent rather than trying a
        # new body on an uncertain message. New chunks remain in the view.
        progress = record["deliveries"].get("progress") or {}
        if progress.get("verified") and (stream.get("edit_limit_reached")
                or int(stream.get("edit_attempts") or 0) >= PROGRESS_EDIT_BUDGET):
            if not self._continue_progress(path, record, inbox=inbox):
                return
            text = f"**后续进展 {int(stream['segment']) + 1}**\n\n{view}"
            progress = {}
        if not progress.get("verified"):
            initial = str(progress.get("text") or text)
            if self._deliver(path, record, "progress", initial, inbox=inbox):
                stream.update(confirmed_text=initial, confirmed_attempt=progress.get("attempt") or record["deliveries"]["progress"]["attempt"])
                _atomic_write_json(path, record)
        else:
            self._edit_progress(path, record, text, inbox=inbox)
        if stream.get("confirmed_text") == text:
            stream["confirmed_answer_digest"] = answer_digest
            _atomic_write_json(path, record)

    def pending_delivery_paths(self) -> list[Path]:
        """The durable transport store is the queue; no in-memory admission."""
        return [path for path in sorted(self.root.glob("*.json"))
                if _read_json(path)["status"] != "delivered"]

    def reconcile(self) -> int:
        return sum(self.reconcile_request(path) for path in self.pending_delivery_paths())

    def reconcile_request(self, path: Path) -> int:
        # Repair only this persisted source. Core still owns admission, recovery
        # and the shared Session queue/fence; transport workers never run models.
        row = _read_json(path)
        if row["status"] == "captured":
            self.admit(row["profile"], row["event"])
        native_path = self.core.root / f"{row['request_ref']}.json"
        if native_path.exists():
            self.core.recover(request_ref=row["request_ref"])
        with exclusive_file_lock(path, operation="deliver_private_chat_request"):
            record = _read_json(path)
            if record["status"] in {"captured", "delivered"}:
                return 0
            config: Path | None = None

            def inbox() -> Path:
                nonlocal config
                if config is None:
                    config = self._inbox(record)
                return config

            try:
                self.bindings.resolve(binding_id=record["binding_id"], **record["source"])
                if record["status"] in {"command_queued", "command_completed", "commission_running"}:
                    native = self.core.read_request(record["request_ref"])
                    if native["status"] == "command_queued":
                        self._feedback(path, record, inbox=inbox)
                        self._deliver(path, record, "admission", native["response"], inbox=inbox)
                        return 0
                    record.update(status=native["status"], response=native.get("response"),
                                  commission_resources=native.get("commission_resources"),
                                  status_snapshot=native.get("status_snapshot"))
                if record["status"] == "accepted":
                    native = self.core.read_request(record["request_ref"])
                    if native.get("agent_target"):
                        self.bindings.resolve_agent_target(self.bindings.resolve(binding_id=record["binding_id"], **record["source"]), native["agent_target"])
                    self._feedback(path, record, inbox=inbox)
                    # Receipt follows persistent Core admission and is
                    # independent of terminal execution and reply delivery.
                    turn = self.core.controller.store.load_turn(record["session_id"], record["turn_id"])
                    # Get already acknowledges admission. Reserve a text
                    # notification for a real wait or unavailable feedback;
                    # resume any old attempt with its original exact text.
                    admission = (record["deliveries"].get("admission") or {}).get("text")
                    if not admission:
                        if native.get("agent_target"):
                            admission = "已收到，等待原 Agent 宿主处理。/status 查看状态，/project 返回项目对话。"
                        elif turn and turn["status"] == "queued":
                            admission = "正在处理前一条，这条已排队。"
                        elif (record.get("feedback", {}).get("received") or {}).get("ok") is not True:
                            admission = "已收到，正在处理。"
                    if admission:
                        self._deliver(path, record, "admission", admission, inbox=inbox)
                    if turn and turn["status"] in {"starting", "running"}:
                        self._feedback(path, record, inbox=inbox, processing=True)
                        self._progress(path, record, inbox=inbox)
                    if not turn or turn["status"] not in TERMINAL_TURN_STATES:
                        return 0
                    if turn["status"] == "failed" and turn.get("error_code") == "server_overloaded":
                        response = manager_failure_reply(turn)[1] + " 原会话已保留。"
                    else:
                        response = str((turn.get("response") or {}).get("message") or "") if turn["status"] == "completed" else (
                            "本次执行已停止。" if turn["status"] == "interrupted" else
                            "本次执行超时，原会话已保留；请发送 /status 查看状态后再决定是否重试。" if turn["status"] == "timed_out" else
                            "本次执行失败或已过期，原会话已保留；请发送 /status 后再决定是否重试。")
                else:
                    if record["status"] != "rejected":
                        self._feedback(path, record, inbox=inbox)
                    response = (str(record.get("attachment_notice") or "")
                        or _command_text(str(record.get("response_code") or "")) or str(record.get("response") or ""))
                    if record.get("status_snapshot"):
                        response = _status_text(record["status_snapshot"], help_requested=native.get("command") == "help")
                if self._deliver(path, record, "terminal", response, inbox=inbox):
                    resources = record.get("commission_resources") or {}
                    if resources.get("session_id") and resources.get("turn_id"):
                        first_turn = self.core.controller.store.load_turn(resources["session_id"], resources["turn_id"])
                        if first_turn and first_turn["status"] in {"starting", "running"}:
                            self._feedback(path, record, inbox=inbox, processing=True)
                            self._progress(path, record, inbox=inbox,
                                session_id=resources["session_id"], turn_id=resources["turn_id"])
                        if not first_turn or first_turn["status"] not in TERMINAL_TURN_STATES:
                            record["status"] = "commission_running"
                            _atomic_write_json(path, record)
                            return 0
                        result_text = str((first_turn.get("response") or {}).get("message") or "")
                        result_text = ("委托执行结果：\n" + result_text if first_turn["status"] == "completed" else
                            "委托首轮执行超时；原 Goal 和回执已保留，请查看状态后决定恢复。" if first_turn["status"] == "timed_out" else
                            "委托首轮执行未完成；原 Goal 和回执已保留，请查看状态后决定恢复。")
                        proposal_id = native.get("proposal_id")
                        if proposal_id:
                            result_text += f"\n如需恢复暂停或额度受限的原执行：/resume-commission {proposal_id} --tokens N（N 为包含历史用量的总上限，须大于已用量；不会重开线程）。"
                        if not self._deliver(path, record, "commission_result", result_text, inbox=inbox):
                            return 0
                    config = inbox()
                    acknowledge_lark_event_inbox(project=self.runtime_root, config_path=config,
                        message_ids=[record["event"]["message_id"]], execute=True)
                    self.core.record_delivery(record["request_ref"], session_id=record.get("session_id"), turn_id=record.get("turn_id"))
                    record["status"] = "delivered"
                    _atomic_write_json(path, record)
                    return 1
            except (KeyError, ValueError, OSError):
                # Keep pending receipts available for actionable recovery.
                return 0
        return 0


def _command_text(code: str) -> str:
    return {"group_recipient_unavailable": "群助手仅处理当前项目和话题，不能选择个人 Agent 会话；/status 查看状态，/help 查看用法。",
            "unsupported_attachment": "这条消息未提交执行。普通项目或管家对话支持文字与图片；文件、音视频及所选 Agent 的原宿主暂不支持图片。请将文字与图片单独发送，或用 /project 返回项目对话。",
        "no_session": "尚无会话；发送文字即可开始。", "active_session": "正在执行；后续文字会进入同一会话队列。",
        "ready_session": "会话已就绪，可继续发送文字。", "new_session": "已关闭此前会话；下一条文字将开启新会话。",
        "attached_control_unavailable": "原 Agent 宿主尚不支持此处的实时停止或新建会话；原执行没有被停止或替换。请在原宿主处理，/project 返回普通项目对话。",
        "stop_requested": "已请求停止这条消息对应的执行。", "no_active_turn": "当前没有正在执行的消息。"}.get(code, "")


def _status_text(snapshot: dict[str, Any], *, help_requested: bool) -> str:
    """Render Core facts with provider-specific commands and help."""
    steward = snapshot["context_kind"] == "steward"
    attached = bool(snapshot.get("recipient_agent_id"))
    text = render_conversation_status(snapshot)
    if snapshot.get("audience") == "group":
        text += "\n\n/status 当前话题状态 · /stop 停止本话题执行 · /new 本话题新会话 · /help 用法"
        if help_requested:
            text += "\n@此 Bot 开始新话题，后续消息在原话题继续；文字与图片沿用同一会话。仅授权当前项目工作区，不继承个人管家或 Agent 会话。"
        return text
    text += ("\n\n/agents 授权 Agent · /project 返回项目 · /help 用法" if attached else
             "\n\n/stop 停止当前聊天 · /new 新会话 · /help 用法")
    if help_requested:
        if not steward and not attached and snapshot.get("grant") == "workspace_write":
            text += "\n按项目规则和 skills 执行当前指令；读写授权不会创建 Goal 或提高原宿主权限。"
        text += ("\n\n/status 查看当前状态；/help 查看用法。"
                 "\n/agents 查看本 App 已授权的 Agent；使用列表中的完整 /agent 命令选择，/project 返回项目对话。")
        if not attached:
            text += "\n/stop 停止当前聊天执行；/new 关闭当前聊天，下条消息开启新会话。"
        text += (f"\n\n工作区：{markdown_scalar(snapshot['workspace_path'])}"
                 f"\n执行器：{markdown_scalar(snapshot['executor_endpoint_id'])}"
                 f"\n观察时间：{markdown_scalar(snapshot['observed_at'])}"
                 "\n工作区、执行器与解绑：本机 Chat → 设置 → Lark。变更或解绑会重新核验授权；已受理工作不会迁移到新会话。"
                 "\n可直接发送图片或图文消息（PNG/JPEG/GIF/WebP，最多 4 张，单张 5 MB、合计 12 MB）。文件与音视频暂不支持；选择原宿主 Agent 后仅支持文字。")
        if steward:
            text += "\n新委托：/delegate --tokens N 具体目标；读完预览后从原私聊发送完整 /confirm。/cancel 取消预览；/stop-commission 和 /resume-commission 使用原回执中的完整命令。"
    return text
