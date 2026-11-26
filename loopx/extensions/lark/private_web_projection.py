"""App mirror outbox, unsupported media history, and status text for private Lark chat."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from functools import partial
import hashlib
import json
from pathlib import Path
import re
from threading import Lock
from typing import TYPE_CHECKING, Any

from ...capabilities.native_chat.conversation_bindings import ChatConversationBindings
from ...capabilities.native_chat.file_discovery import FileChatDiscovery, file_change_fingerprint
from ...chat_store import CHAT_MESSAGE_SCHEMA_VERSION, ChatSessionStore, TERMINAL_TURN_STATES, _atomic_write_json
from ...file_lock import exclusive_file_lock, try_exclusive_file_lock
from ...presentation.markdown import markdown_scalar
from ...presentation.renderers.conversation_status_markdown import render_conversation_status
from .conversation_identity import identity_ref, lark_private_source
from .inbox_reactions import _reconcile_outbound_input_reactions
from .inbox_reply import verify_lark_inbox_reply
from .manager_context import manager_failure_reply
from .outbound import present_lark_conversation_text as _presentation_text

if TYPE_CHECKING:
    from .private_conversations import LarkPrivateConversations

_UNSUPPORTED_MEDIA_LABELS = {"audio": "音频", "file": "文件", "media": "视频"}
_PROJECTION_SOURCE_KEYS = ("request_ref", "binding_id", "profile", "source", "event", "session_id")


def _pc() -> Any:
    from . import private_conversations
    return private_conversations


def _same_fields(left: Mapping[str, Any], right: Mapping[str, Any], *keys: str) -> bool:
    return all(left.get(key) == right.get(key) for key in keys)


def _projection_source_eligible(row: Mapping[str, Any], enabled: set[str], sid: str | None = None) -> bool:
    session_match = bool(row.get("session_id")) if sid is None else row.get("session_id") == sid
    return (row.get("binding_id") in enabled and session_match
            and row.get("event", {}).get("chat_type") == "p2p"
            and not row.get("agent_target") and row.get("status") in {"accepted", "delivered"})


def _unsupported_media_label(row: Mapping[str, Any]) -> str | None:
    event = row.get("event")
    kind = event.get("message_type") if isinstance(event, Mapping) else None
    if (isinstance(kind, str) and row.get("schema_version") == "lark_private_chat_delivery_v0"
            and row.get("status") in ("command_completed", "delivered")
            and row.get("response_code") == "unsupported_attachment" and "turn_id" in row and row["turn_id"] is None):
        return _UNSUPPORTED_MEDIA_LABELS.get(kind)
    return None


def _history_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing history time")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("unqualified history time")
    return stamp.astimezone(timezone.utc)


class PrivateUnsupportedMediaHistory:
    """Read Lark presentation history without constructing a transport worker."""

    def __init__(self) -> None:
        self._discovery = FileChatDiscovery()
        self._location_lock = Lock()
        self._location_cache: tuple[Path, tuple[int, ...], dict[str, list[Path]]] | None = None

    def _locations(self, root: Path, sid: str) -> list[Path]:
        complete = True

        def read_receipt(path: Path) -> dict[str, Any]:
            try:
                return _pc()._read_json(path)
            except UnicodeDecodeError:
                return {}

        def locate(_path: Path, row: dict[str, Any]) -> dict[str, Any] | None:
            nonlocal complete
            # Keep valid locators, but retry unreadable receipts on the next scan.
            if row.get("schema_version") != "lark_private_chat_delivery_v0":
                complete = False
                return None
            return {"media_session": row.get("session_id") if _unsupported_media_label(row) else None}

        with self._location_lock:
            try:
                stamp = file_change_fingerprint(root)
            except OSError:
                return []
            cached = self._location_cache
            if stamp is not None and cached is not None and cached[:2] == (root, stamp):
                return cached[2].get(sid, [])
            paths = (path for path in root.glob("*.json") if re.fullmatch(r"[a-f0-9]{24}", path.stem))
            locations: dict[str, list[Path]] = {}
            for path, view in self._discovery.rows(root, paths, read=read_receipt, project=locate):
                session_id = view.get("media_session")
                if isinstance(session_id, str):
                    locations.setdefault(session_id, []).append(path)
            # All production journal publications are atomic replacements.
            # Cache only the locator list, never a selected receipt or grant.
            try:
                if complete and stamp is not None and file_change_fingerprint(root) == stamp:
                    self._location_cache = (root, stamp, locations)
            except OSError:
                pass
            return locations.get(sid, [])

    def read(self, store: ChatSessionStore, bindings: ChatConversationBindings | None,
             session: dict[str, Any]) -> list[dict[str, Any]]:
        """Show original, non-executed Lark media without replay or provider IO."""
        saved = session.get("steward_context") or session.get("project_context") or {}
        if (bindings is None or saved.get("audience") != "bound_owner"
                or session.get("session_mode") != "managed_runtime"):
            return []
        sid = session["session_id"]
        try:
            state = bindings.read()
            matches = [row for row in state["bindings"] if row["binding_id"] == saved.get("binding_id")]
        except (KeyError, TypeError, ValueError, OSError):
            return []
        if len(matches) != 1:
            return []
        binding, = matches
        prefix = "manager.external.native" if binding.get("context_kind") == "steward" else "project.external"
        if (binding.get("enabled") is not True or binding.get("audience", "owner") != "owner"
                or not _same_fields(saved, binding, "provider_ref", "operator_ref", "project_ref", "grant")
                or any(not saved.get(k) for k in ("provider_ref", "operator_ref", "project_ref", "grant", "source_ref"))
                or session.get("channel_id") != f"{prefix}.{binding['binding_id']}.{saved['source_ref']}"):
            return []

        markers = []
        read_json = _pc()._read_json
        for path in self._locations(store.root / "lark-private-deliveries", sid):
            try:
                # The locator is only a hint. Join the two exact receipts and
                # recompute the private principal; don't trust a cached grant.
                record = read_json(path)
                label = _unsupported_media_label(record)
                event = record["event"]
                source = lark_private_source(provider_ref=saved["provider_ref"], event=event)
                native = read_json(store.root / "external-requests" / path.name)
                expected = {"request_ref": path.stem, "binding_id": binding["binding_id"],
                            "session_id": sid, "source": source, "response_code": "unsupported_attachment"}
                if (not label or record.get("profile") != binding["transport_ref"]
                        or source["private_human_message"] is not True or source["source_ref"] != saved["source_ref"]
                        or source["sender_ref"] != saved["operator_ref"]
                        or identity_ref(saved["provider_ref"], event["message_id"]) != path.stem
                        or any(row.get(k) != v for row in (record, native) for k, v in expected.items())
                        or native.get("schema_version") != "loopx_chat_external_request_v0"
                        or native.get("origin") not in (None, "lark")
                        or native.get("status") != "command_completed" or native.get("command") != "unsupported"
                        or "turn_id" not in native or native["turn_id"] is not None):
                    continue
                epoch = str(event.get("create_time") or "")
                stamp = (datetime.fromtimestamp(int(epoch) / 1000, timezone.utc)
                         if re.fullmatch(r"[0-9]{13}", epoch) else _history_timestamp(native.get("created_at")))
                markers.append({"schema_version": CHAT_MESSAGE_SCHEMA_VERSION,
                    "message_id": f"lark.unsupported.{path.stem}", "turn_id": None, "role": "error", "origin": "lark",
                    "text": f"飞书{label} · 未提交执行。请在原飞书会话查看。",
                    "created_at": stamp.isoformat(timespec="microseconds")})
            except (KeyError, ValueError, TypeError, OSError):
                continue

        try:
            if not markers or bindings.read() != state:
                return []
        except (ValueError, OSError):
            return []
        return sorted(markers, key=lambda row: (row["created_at"], row["message_id"]))

    @staticmethod
    def summary(summary: dict[str, Any], markers: list[dict[str, Any]]) -> dict[str, Any]:
        """Invalidate an App's quiet history cache even when no Turn was created."""
        revision = summary.get("transcript_revision")
        if not revision or not markers:
            return summary
        digest = hashlib.sha256(json.dumps(markers, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        return {**summary, "transcript_revision": f"{revision}.lark.{digest}"}

    @staticmethod
    def insert(messages: list[dict[str, Any]], markers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not markers:
            return messages
        ids = {row.get("message_id") for row in messages}
        pending = [(_history_timestamp(row["created_at"]), row) for row in reversed(markers) if row["message_id"] not in ids]
        projected = []
        for message in messages:
            try:
                stamp = _history_timestamp(message.get("created_at"))
            except ValueError:
                stamp = None
            while pending and stamp is not None and pending[-1][0] < stamp:
                projected.append(pending.pop()[1])
            projected.append(message)
        merged = projected + [row[1] for row in reversed(pending)]
        journal_sha = getattr(messages, "journal_sha256", None)
        if not isinstance(journal_sha, bytes):
            return merged
        from ...chat.journal import CachedJournalRows
        raw_markers = json.dumps(markers, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return CachedJournalRows(merged, journal_sha256=journal_sha, markers_sha256=hashlib.sha256(raw_markers).digest())


class PrivateWebProjectionMixin:
    """App/Web mirror discovery, delivery, and input-reaction feedback for private Lark chat."""

    def _pending_web_projection_paths(self: LarkPrivateConversations) -> list[Path]:
        paths = []
        for path in sorted((self.root / "web-projections").glob("*.json")):
            try:
                if _pc()._read_json(path).get("status") == "pending":
                    paths.append(path)
            except (OSError, ValueError):
                # An unreadable journal remains unresolved, but must not stop
                # discovery of other original conversations' replies.
                continue
        return paths

    @staticmethod
    def _read_web_projection_journal(path: Path) -> dict[str, Any]:
        row = _pc()._read_json(path)
        if (row.get("schema_version") != "lark_private_web_projection_v0" or row.get("projection_id") != path.stem
                or row.get("status") not in ("pending", "delivered", "partial")
                or any(not isinstance(row.get(k), str) or not row[k] for k in ("request_ref", "binding_id", "profile", "session_id"))
                or any(not isinstance(row.get(k), dict) for k in ("source", "event", "deliveries"))
                or any(not isinstance(phase, dict) for phase in row["deliveries"].values())):
            raise ValueError("Original App mirror journal is unavailable")
        return row

    def _web_projection_paths(self: LarkPrivateConversations) -> list[Path]:
        """Discover committed App messages, using the existing reply workers.

        These files are provider outboxes, not input requests or model work.
        The original Core transcript and grant are re-read before each effect.
        File observations only avoid rereading unchanged local Chat JSONL; they
        never authorize a send, acknowledge input or advance a delivery cursor.
        """
        root, monotonic = self.root / "web-projections", _pc().time.monotonic
        if monotonic() >= self._web_projection_scan_at:
            self._web_projection_scan_at = monotonic() + 5
            enabled = {row["binding_id"] for row in self.bindings.read()["bindings"]
                       if row.get("enabled") is True and row.get("audience", "owner") == "owner"}
            sources: dict[str, Path] = {}
            cached_rows = getattr(self, "_web_projection_scan_rows", None)
            discovery_rows = cached_rows if cached_rows is not None else self._delivery_discovery.rows()
            for path, row, _ in discovery_rows:
                if _projection_source_eligible(row, enabled):
                    sources.setdefault(row["session_id"], path)
            store = self.core.controller.store
            for sid, source_path in sources.items():
                try:
                    snapshot = file_change_fingerprint(store.sessions_root / sid / "messages.jsonl")
                    if snapshot is not None and self._web_projection_snapshots.get(sid) == snapshot:
                        continue
                    source = _pc()._read_json(source_path)
                    if not _projection_source_eligible(source, enabled, sid):
                        continue
                    key = identity_ref(source["binding_id"], sid)
                    path = root / f"{key}.json"
                    selected = self._web_projection_transcript(sid)
                    if selected:
                        with exclusive_file_lock(path, operation="discover_web_conversation_projection"):
                            row = self._read_web_projection_journal(path) if path.exists() else {
                                "schema_version": "lark_private_web_projection_v0", "projection_id": key,
                                **{k: source[k] for k in _PROJECTION_SOURCE_KEYS}, "deliveries": {}, "status": "pending"}
                            if any(not row["deliveries"].get("web." + msg["message_id"], {}).get("verified") for msg in selected):
                                row["status"] = "pending"
                                _atomic_write_json(path, row)
                    if snapshot is not None:
                        self._web_projection_snapshots[sid] = snapshot
                except (OSError, ValueError, KeyError):
                    # An unreadable source is unknown. Keep any prior outbox;
                    # it retains fresh source and canonical-message checks.
                    continue
        return self._pending_web_projection_paths()

    def _web_projection_transcript(self: LarkPrivateConversations, session_id: str, *, inputs_only: bool = False,
                                   verify_turns: bool = False) -> list[dict[str, Any]]:
        store = self.core.controller.store
        messages = store.messages(session_id)
        web_turns = {m["turn_id"] for m in messages if m.get("role") == "user" and m.get("origin") == "web"}
        roles, selected, failed_turns = {"user"} if inputs_only else {"user", "agent", "error"}, [], set()
        for message in messages:
            turn_id, role = message.get("turn_id"), message.get("role")
            if turn_id not in web_turns or role not in roles:
                continue
            turn = store.load_turn(session_id, turn_id) if verify_turns or role == "error" else None
            same_turn = bool(turn and turn.get("origin") == "web" and turn.get("session_id") == session_id and turn.get("turn_id") == turn_id)
            if role == "error":
                # An unconfirmed stop may leave an earlier error row in the
                # transcript. Use only the exact terminal failure, once per Turn.
                if (not same_turn or turn_id in failed_turns or turn["status"] not in {"failed", "timed_out"}
                        or not turn.get("completed_at") or turn.get("error") != message.get("text") or message.get("attachments")):
                    continue
                failed_turns.add(turn_id)
                phase_events = (
                    store.events_after(session_id, turn_id, None)
                    if turn.get("error_code") in {"host_gate", "response_timeout", "resume_failed"} else []
                )
                failure = ("服务在处理此请求时重启，本轮未完成。详情可在 App 的原会话查看。"
                           if turn.get("error_code") == "server_restarted"
                           else manager_failure_reply(turn, phase_events=phase_events)[1])
                message = {**message, "text": f"App 执行记录（{turn['completed_at']}）\n\n{failure}"}
            elif verify_turns and not same_turn:
                raise ValueError("canonical App input identity changed")
            selected.append(message)
        return selected

    def _web_projection_messages(self: LarkPrivateConversations, row: dict[str, Any], *, inputs_only: bool = False) -> list[dict[str, Any]]:
        store = self.core.controller.store
        source, native = _pc()._read_json(self.root / f"{row['request_ref']}.json"), self.core.read_request(row["request_ref"])
        if (not _same_fields(row, source, *_PROJECTION_SOURCE_KEYS) or not _same_fields(native, row, "binding_id", "source", "session_id")
                or source.get("agent_target") or row["event"].get("chat_type") != "p2p"):
            raise ValueError("original App projection source changed")
        session = store.load_session(row["session_id"])
        if session is None:
            raise ValueError("original App projection Session unavailable")
        saved = session.get("steward_context") or session.get("project_context") or {}
        if saved.get("binding_id") != row["binding_id"] or saved.get("audience") != "bound_owner" or saved.get("source_ref") != row["source"]["source_ref"]:
            raise ValueError("original App projection audience changed")
        with self.bindings.input_context(saved, origin="web", session=session, store=store):
            return self._web_projection_transcript(session["session_id"], inputs_only=inputs_only, verify_turns=True)

    @staticmethod
    def _web_projection_delivery_id(row: dict[str, Any], message: dict[str, Any]) -> str:
        """The existing provider identity binds this exact canonical message."""
        return hashlib.sha256(json.dumps([row["binding_id"], row["session_id"], message["message_id"]],
            separators=(",", ":")).encode()).hexdigest()

    def _web_projection_feedback(self: LarkPrivateConversations, row: dict[str, Any], message: dict[str, Any]) -> None:
        if not self.reaction_feedback or message["role"] != "user":
            return
        phase = row["deliveries"].get("web." + message["message_id"], {})
        if not phase.get("verified"):
            return
        store = self.core.controller.store
        turn = store.load_turn(row["session_id"], message["turn_id"]) or {}
        status = turn.get("status")
        if not self._app_feedback_due(phase, status):
            return
        try:
            read_json = _pc()._read_json

            def has_input() -> bool:
                return message in self._web_projection_messages(row, inputs_only=True)

            if not has_input():
                raise ValueError("canonical App input changed")
            expected_text = f"**你（App）**\n\n{message['text']}"
            if phase.get("text") not in (expected_text, _presentation_text(expected_text)):
                raise ValueError("App input no longer matches its frozen mirror")
            source = read_json(self.root / f"{row['request_ref']}.json")
            if (source.get("context_recovery") or turn.get("context_recovery")
                    or status not in {"queued", "starting", "running", *TERMINAL_TURN_STATES}):
                raise ValueError("App feedback source/Turn unavailable")
            config = self._inbox(row)
            from .post_delivery import attachment_reply
            _, target = attachment_reply(project=self.runtime_root, config_path=config, attempt=phase["attempt"], text=phase["text"])

            def authorized() -> bool:
                try:
                    if not has_input():
                        return False
                    verified = verify_lark_inbox_reply(project=self.runtime_root, config_path=config,
                        message_id=row["event"]["message_id"], text=phase["text"], attempt=phase["attempt"],
                        reply_placement="chat_root", delivery_id=self._web_projection_delivery_id(row, message),
                        image_keys=tuple(phase.get("image_keys", [])), runner=self._reply_runner, finalize_reactions=False,
                        source_authority_verifier=partial(self._source_authority, row))
                    # Provider IO can outlast a grant or a Turn. Re-read both
                    # before this exact mirror's reaction, including cleanup.
                    if verified.get("ok") is not True or verified.get("reply_verified") is not True or not has_input():
                        return False
                    current_source = read_json(self.root / f"{row['request_ref']}.json")
                    current = store.load_turn(row["session_id"], message["turn_id"]) or {}
                    return (current.get("status") == status and current.get("origin") == "web"
                            and not current.get("context_recovery") and not current_source.get("context_recovery"))
                except (OSError, ValueError, KeyError):
                    return False

            result = _reconcile_outbound_input_reactions(project=self.runtime_root, config_path=config,
                message_id=target["message_ref"], processing=status in {"starting", "running"},
                complete=status in TERMINAL_TURN_STATES, before_write=authorized, runner=self._reply_runner)
            phase["feedback"] = {"ok": result.get("ok") is True, "turn_status": status, "message_ref": target["message_ref"], "result": result}
        except (OSError, ValueError, KeyError):
            phase["feedback"] = {"ok": False, "turn_status": status, "blocker": "original_web_projection_unavailable"}

    @staticmethod
    def _app_feedback_due(phase: dict[str, Any], status: str | None) -> bool:
        if "feedback" not in phase:
            return status not in TERMINAL_TURN_STATES  # Do not backfill finished mirrors.
        prior = phase["feedback"]
        return not (prior.get("ok") and prior.get("turn_status") == status)

    def _has_app_feedback_work(self: LarkPrivateConversations, row: dict[str, Any]) -> bool:
        # Discovery uses only reference metadata and current Turn state. A
        # due item still follows the complete input/source/grant checks below.
        store = self.core.controller.store
        sid = row["session_id"]
        for message_id, turn_id in self._delivery_discovery.web_input_turn_refs(store, sid):
            phase = row["deliveries"].get("web." + message_id, {})
            if not phase.get("verified"):
                continue
            turn = store.load_turn(sid, turn_id)
            if turn is not None and self._app_feedback_due(phase, turn.get("status")):
                return True
        return False

    def _reconcile_web_projection(self: LarkPrivateConversations, path: Path) -> int:
        # The existing worker pool serializes this outbox with a durable fence.
        # Recovery reads the same provider attempt; it never starts a Core Turn.
        with exclusive_file_lock(path, operation="deliver_web_conversation_projection"):
            try:
                row = self._read_web_projection_journal(path)
            except (OSError, ValueError):
                # Never overwrite the sole evidence of an unknown send with
                # a blocker or a new outbox when its journal cannot be read.
                return 0
            try:
                messages = self._web_projection_messages(row)
                pending = next((message for message in messages
                    if not row["deliveries"].get("web." + message["message_id"], {}).get("verified")), None)
                if pending is None:
                    # Older verified placeholders retain their exact attempts.
                    # They cannot be relabelled as media delivery or resent.
                    row["status"] = "partial" if any(message.get("attachments") and not
                        row["deliveries"].get("web." + message["message_id"], {}).get("images_verified")
                        for message in messages) else "delivered"
                    _atomic_write_json(path, row)
                    return 0
                def authorized_source(profile: str, chat_id: str, message_id: str) -> tuple[str | None, bool]:
                    # Check the original provider source first. The fresh App
                    # grant and canonical body checks then cover any changes
                    # during that read, both before preview and before dispatch.
                    name, verified = self._source_authority(row, profile, chat_id, message_id)
                    if not name or not verified:
                        return None, False
                    try:
                        return name, pending in self._web_projection_messages(row)
                    except (OSError, ValueError, KeyError):
                        return None, False
                from .manager_returns import prepare_app_mirror_text
                text = prepare_app_mirror_text(self, path, row, messages, pending)
                phase_key = "web." + pending["message_id"]
                if self.reaction_feedback and pending["role"] == "user":
                    # Persist feedback intent before a mirror write/recovery;
                    # old completed mirrors without it are not backfilled.
                    row["deliveries"].setdefault(phase_key, {}).setdefault("feedback", {})
                    _atomic_write_json(path, row)
                delivered = self._deliver(path, row, phase_key, text,
                    inbox=lambda: self._inbox(row), source_authority_verifier=authorized_source,
                    reply_placement="chat_root", delivery_id=self._web_projection_delivery_id(row, pending),
                    finalize_source=False, image_attachments=pending.get("attachments"))
                if delivered:
                    row.pop("blocker", None)
                _atomic_write_json(path, row)
                return int(delivered)
            except (OSError, ValueError, KeyError):
                row["blocker"] = "original_web_projection_unavailable"
                _atomic_write_json(path, row)
                return 0

    def reconcile_app_feedback(self: LarkPrivateConversations) -> int:
        """Reconcile optional reactions independently of the body outbox."""
        if not self.reaction_feedback:
            return 0
        for path in sorted((self.root / "web-projections").glob("*.json")):
            with exclusive_file_lock(path.with_suffix(".feedback"), operation="reconcile_web_input_feedback"):
                try:
                    # Leave a busy body outbox for the next feedback pass so
                    # independent Sessions can still acknowledge their inputs.
                    with try_exclusive_file_lock(path, operation="read_web_input_feedback") as lock:
                        if lock is None:
                            continue
                        row = self._read_web_projection_journal(path)
                except (OSError, ValueError):
                    continue
                try:
                    due = self._has_app_feedback_work(row)
                except (OSError, ValueError, KeyError):
                    # An unavailable hint cannot suppress the original reader.
                    due = True
                if not due:
                    continue
                try:
                    messages = self._web_projection_messages(row, inputs_only=True)
                except (OSError, ValueError, KeyError):
                    continue
                for message in messages:
                    phase_key = "web." + message["message_id"]
                    phase = row["deliveries"].get(phase_key, {})
                    # Keep a frozen comparison without the independent
                    # feedback field; concurrent body deliveries stay intact.
                    frozen = {key: value for key, value in phase.items() if key != "feedback"}
                    prior_feedback = phase.get("feedback")
                    self._web_projection_feedback(row, message)
                    if "feedback" not in phase or phase["feedback"] == prior_feedback:
                        continue
                    # The reaction has its own receipt. Defer writeback when
                    # a body worker acquired the journal during provider I/O.
                    with try_exclusive_file_lock(path, operation="record_web_input_feedback") as lock:
                        if lock is None:
                            continue
                        try:
                            current = self._read_web_projection_journal(path)
                        except (OSError, ValueError):
                            continue
                        saved = current["deliveries"].get(phase_key, {})
                        if (_same_fields(current, row, *_PROJECTION_SOURCE_KEYS)
                                and {key: value for key, value in saved.items() if key != "feedback"} == frozen):
                            saved["feedback"] = phase["feedback"]
                            _atomic_write_json(path, current)
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
