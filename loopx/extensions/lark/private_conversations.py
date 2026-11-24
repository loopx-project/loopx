"""Private-message transport for native, Core-bound project Chat.

Only provider provenance, Inbox presentation and delivery receipts live here.
Core owns audience grants, canonical Sessions, durable Turns and stop/recovery.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import cached_property, partial
import hashlib
from itertools import tee
import json
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Protocol, cast
import zlib

from ...capabilities.native_chat.external_conversations import (
    ChatExternalConversations, QUOTA_BACKLOG_BATCH_SIZE, STEWARD_BINDING_ADOPTION_PENDING,
)
from ...capabilities.native_chat.file_discovery import FileChatDiscovery
from ...chat_store import TERMINAL_TURN_STATES, ChatSessionStore, _atomic_write_json, _read_json
from ...control_plane.effect_runtime import effect_runtime_request_scope
from ...file_lock import exclusive_file_lock
from . import private_mention_topics, private_steward_triage as triage
from .cli_transport import CommandRunner
from .conversation_identity import identity_ref, lark_private_source
from .event_inbox import acknowledge_lark_event_inbox, ingest_lark_event_inbox
from .goal_channel_transport import record_event_timing
from .identity_shapes import LARK_APP_ID_PATTERN
from .inbox_reactions import mark_lark_event_inbox_processing, mark_lark_event_inbox_received
from .inbox_reply import (
    SourceAuthorityVerifier, _message, reply_lark_event_inbox, update_lark_inbox_reply, verify_lark_inbox_reply,
)
from .manager_context import manager_failure_reply
from .outbound import present_lark_conversation_text as _presentation_text
from .post_delivery import attachment_reply
from .private_answer_images import (
    ANSWER_IMAGE_RETRY_SECONDS as ANSWER_IMAGE_RETRY_SECONDS,
    ANSWER_IMAGE_SOURCE_TIMEOUT as ANSWER_IMAGE_SOURCE_TIMEOUT,
    PrivateAnswerImageMixin,
)
from .private_delivery_discovery import (
    PrivateDeliveryDiscovery, answer_image_retry_waits, answer_image_source_matches, answer_media_dispatch,
    context_recovery_backoff_valid, context_recovery_fingerprints, failure_notice_receipt_unknown,
    is_answer_image_followup, prepared_steward_binding_waits, record_context_recovery_noop_backoff,
    resumable_answer_image_session as resumable_answer_image_session,
)
from .private_images import private_message_caption, private_message_images
from .private_ingress import (
    PrivateSourceUnavailable, private_source_text, project_private_reply_history as project_private_reply_history,
    steward_source_preflight,
)
from .private_progress import (
    PROGRESS_EDIT_BUDGET as PROGRESS_EDIT_BUDGET, PrivateProgressMixin, presentation_phase, presentation_stream,
)
from .private_source_verification import (
    CHAT_CONTROL_COMMANDS as CHAT_CONTROL_COMMANDS, Payload, PayloadView, PrivateSourceVerificationMixin,
    _ensure_dict, _lark_payload as _lark_payload, _mapping, _msg_type as _msg_type,
    _req_dict, _str_field, _valid_user_sender as _valid_user_sender,
)
from .private_topic_lanes import expected_private_source, finalize_private_steward_lane, resolve_private_steward_lane
from .private_web_projection import (
    PrivateUnsupportedMediaHistory as PrivateUnsupportedMediaHistory, PrivateWebProjectionMixin,
    _command_text, _same_fields, _status_text as _status_text,
)
from .return_files import uploaded_chat_images, verify_chat_images

if TYPE_CHECKING:
    from .team_plan_confirmation import TeamPlanReviewCallbackStream

    class _ControllerProtocol(Protocol):
        @property
        def store(self) -> ChatSessionStore: ...
        @property
        def project_contexts(self) -> object: ...

_UNSET = object()
APP_ID_PATTERN = LARK_APP_ID_PATTERN
_STEWARD_PREFIX_COMMANDS = (
    ("/delegate", "commission"), ("/委托", "commission"), ("/confirm", "confirm_commission"),
    ("/cancel", "cancel_commission"), ("/stop-commission", "stop_commission"),
    ("/resume-commission", "resume_commission"),
)


class _ControlReadFallback(Exception):
    """A valid source needs ordinary rendering, before any Core write."""


class LarkPrivateConversations(
    PrivateProgressMixin, PrivateWebProjectionMixin, PrivateAnswerImageMixin, PrivateSourceVerificationMixin,
):
    _web_projection_scan_rows: list[tuple[Path, Payload, bool]] | None = None

    def __init__(
        self, *, controller: _ControllerProtocol, runtime_root: Path, runner: CommandRunner,
        cli_bin: str, reaction_feedback: bool = True,
    ) -> None:
        self.core = ChatExternalConversations(controller)
        self.bindings = self.core.bindings
        self.runtime_root, self.runner, self.cli_bin = runtime_root, runner, cli_bin
        self.reaction_feedback = reaction_feedback
        self.steward_callback_streams: dict[tuple[str, str], TeamPlanReviewCallbackStream] = {}
        self.root = controller.store.root / "lark-private-deliveries"
        self._context_recovery_probe_at: dict[Path, float] = {}
        self._context_recovery_noop_backoff: dict[Path, tuple[tuple[object, ...], int]] = {}
        self._historical_image_probe_at: dict[Path, float] = {}
        self._planned_repairs_stamp: tuple[bool, int | None] | None = None
        self._planned_repairs_names: frozenset[str] = frozenset()
        self._delivery_discovery = PrivateDeliveryDiscovery(self.root)
        self._body_discovery = PrivateDeliveryDiscovery(self.root)
        self._web_projection_scan_at = 0.0
        self._web_projection_snapshots: dict[str, tuple[int, ...]] = {}
        from .owner_attention import LarkOwnerAttention
        self.owner_attention = LarkOwnerAttention(self)
        from .private_ingress import PrivateIngressObserver
        self.ingress = PrivateIngressObserver(self)
        from ...capabilities.native_chat.steward_managed_mode import StewardManagedMode
        self.managed_steward = StewardManagedMode(controller, self.bindings)
        from .steward_reports import LarkStewardReports
        self.steward_reports = LarkStewardReports(self)
        setattr(controller, "steward_report_provider", self.steward_reports)

    def profiles(self) -> dict[str, dict[str, str]]:
        """Discover current App configuration, never authorize a conversation."""
        bindings = self.bindings.read()["bindings"]
        if not bindings:
            return {}
        apps = self.configured_profile_apps()
        profiles: dict[str, dict[str, str]] = {}
        for row in bindings:
            transport_ref = str(row["transport_ref"])
            app_id = apps.get(transport_ref, "")
            if row.get("enabled") is not True or not app_id or identity_ref(app_id) != row["provider_ref"]:
                continue
            profiles[transport_ref] = {
                "cli_bin": self.cli_bin, "provider_ref": str(row["provider_ref"]),
                "bot_app_id": app_id, "audience": str(row.get("audience", "owner")),
                "context_kind": str(row["context_kind"]), "binding_id": str(row["binding_id"]),
                "consumer_ref": hashlib.sha256(app_id.encode("utf-8")).hexdigest()[:32],
            }
        return profiles

    def configured_profile_apps(self, *, cli_bin: str | None = None) -> dict[str, str]:
        """Read local App identities for listener leases, without granting access."""
        from .goal_channel_transport import call

        result = call(self.runner, [cli_bin or self.cli_bin, "profile", "list"])
        try:
            configured = json.loads(str(result.get("stdout") or ""))
        except json.JSONDecodeError as exc:
            raise OSError("Lark profile configuration could not be read") from exc
        if result.get("returncode") != 0 or not isinstance(configured, list):
            raise OSError("Lark profile configuration could not be read")
        apps = {str(row.get("name") or ""): str(row.get("appId") or "")
                for row in configured if isinstance(row, Mapping)}
        # lark-cli also accepts an App ID as --profile; explicit names win.
        for app_id in list(apps.values()):
            apps.setdefault(app_id, app_id)
        return {profile: app_id for profile, app_id in apps.items() if APP_ID_PATTERN.fullmatch(app_id)}

    @cached_property
    def _health_delivery_discovery(self) -> FileChatDiscovery:
        return FileChatDiscovery()

    @effect_runtime_request_scope()
    def health(self) -> dict[str, dict[str, int]]:
        health: dict[str, dict[str, int]] = {}
        observed_fps: dict[Path, tuple[int, ...]] = {}
        quota_pending = self.core.quota_backlog_observer(use_cache=True, observed_fps=observed_fps)
        completed: list[tuple[Payload, Payload]] = []

        def count(row: PayloadView) -> None:
            entry = health[_str_field(row, "binding_id")]
            entry["pending_count"] += 1
            deliveries = _mapping(row.get("deliveries"))
            entry["recovery_count"] += any(
                _mapping(phase).get("started") and not _mapping(phase).get("verified")
                for phase in deliveries.values()
            )

        def observe_completed() -> None:
            flags = cast("Callable[[list[Payload]], list[bool]]", quota_pending)([native for _, native in completed])
            for (row, _), pending in zip(completed, flags, strict=True):
                if pending:
                    count(row)
            completed.clear()

        def health_fields(_path: Path, row: Payload) -> Payload | None:
            if row["status"] != "delivered" or _mapping(row.get("historical_answer_images")).get("status") == "pending":
                return None
            deliveries = row.get("deliveries")
            if not isinstance(deliveries, dict) or not all(isinstance(phase, dict) for phase in deliveries.values()):
                return None
            fields = ("binding_id", "status", "request_ref", "source", "session_id", "turn_id")
            return {key: row[key] for key in fields if key in row} | {
                "deliveries": {
                    str(key): {"started": bool(phase.get("started")), "verified": bool(phase.get("verified"))}
                    for key, phase in deliveries.items()
                },
            }

        def delivered_requests() -> Iterator[Payload]:
            paths = [*self.root.glob("*.json"), *self.root.glob("web-projections/*.json")]
            for path, row in self._health_delivery_discovery.rows(self.root, paths, read=_read_json, project=health_fields):
                health.setdefault(_str_field(row, "binding_id"), {"pending_count": 0, "recovery_count": 0})
                if row["status"] != "delivered":
                    if triage.pending(row):
                        count(row)
                elif _mapping(row.get("historical_answer_images")).get("status") == "pending":
                    count(row)
                elif path.parent == self.root and row.get("session_id") and row.get("turn_id"):
                    yield row

        receipts, refs = tee(delivered_requests())
        natives = self.core.read_backlog_requests(
            (_str_field(row, "request_ref") for row in refs), defer_recovery=True, observed_fps=observed_fps,
        )
        for row, native in zip(receipts, natives, strict=True):
            if (native.get("status") == "accepted" and native.get("command") is None
                    and _same_fields(native, row, "binding_id", "source", "session_id", "turn_id")):
                completed.append((row, native))
                if len(completed) >= QUOTA_BACKLOG_BATCH_SIZE:
                    observe_completed()
        observe_completed()
        return health

    def _binding_row(self, binding_id: str) -> Payload | None:
        return next((row for row in self.bindings.read()["bindings"] if row.get("binding_id") == binding_id), None)

    def _binding(self, profile: str) -> Payload:
        return next(row for row in self.bindings.read()["bindings"] if row["transport_ref"] == profile)

    def _resolve_binding(
        self, binding_id: str, source: PayloadView, *, include_observation: bool = False,
        session: PayloadView | None = None, session_context: PayloadView | None = None,
    ) -> Payload:
        raw_private = source["private_human_message"]
        if not isinstance(raw_private, bool):
            raise ValueError("source private_human_message must be a bool")
        group_ref, topic_ref, origin = source.get("group_ref"), source.get("topic_ref"), source.get("origin")
        return self.bindings.resolve(
            binding_id=binding_id, source_ref=_str_field(source, "source_ref"),
            sender_ref=_str_field(source, "sender_ref"), private_human_message=raw_private,
            group_ref=group_ref if isinstance(group_ref, str) else None,
            topic_ref=topic_ref if isinstance(topic_ref, str) else None,
            group_human_message=source.get("group_human_message") is True,
            origin=origin if isinstance(origin, str) else None,
            session=dict(session) if session is not None else None,
            session_context=dict(session_context) if session_context is not None else None,
            include_observation=include_observation,
        )

    def _admit_group_control(
        self, path: Path, record: Payload, binding: PayloadView, root: str, *, defer_background_stop: bool,
    ) -> dict[str, str] | None:
        event = _mapping(record["event"])
        command_input = self._group_control_hint(event)
        if command_input is None:
            return None
        checked = source_verified = False
        profile, message_id = _str_field(record, "profile"), _str_field(event, "message_id")
        try:
            record_event_timing(profile, message_id, "control_source_read_started")
            envelopes = self._group_source_pair(profile, message_id, root)
            record_event_timing(profile, message_id, "control_source_read_returned")
        except (KeyError, ValueError, OSError):
            return {"status": "source_verification_failed"}
        if any(message is None for message in envelopes):
            return {"status": "source_verification_failed"}

        def preflight(selected: Payload) -> None:
            nonlocal checked, source_verified
            record_event_timing(profile, message_id, "control_preflight_started")
            checked = True
            if selected["binding"] != binding:
                raise ValueError("conversation binding changed during source read")
            message = self._source_message(record, selected=selected, render_content=False, source_pair=envelopes)
            if message is None:
                raise ValueError("canonical control source verification failed")
            if message.get("_command_input") != command_input:
                raise _ControlReadFallback
            source_verified = True
            _atomic_write_json(path, record)
            record_event_timing(profile, message_id, "control_preflight_returned")

        try:
            record_event_timing(profile, message_id, "core_admission_started")
            admitted = self.core.admit(
                binding_id=_str_field(binding, "binding_id"), source=_req_dict(record, "source"),
                request_ref=_str_field(record, "request_ref"), message=_str_field(event, "content"),
                command=CHAT_CONTROL_COMMANDS[command_input],
                defer_background_stop=defer_background_stop, source_preflight=preflight,
            )
            record_event_timing(profile, message_id, "core_admission_returned")
        except _ControlReadFallback:
            return None
        except ValueError:
            if not checked:
                return {"status": "audience_rejected"}
            if not source_verified:
                return {"status": "source_verification_failed"}
            return self._reject_command(path, record, binding)
        return self._record_admission(path, record, admitted)

    @staticmethod
    def _reject_command(path: Path, record: Payload, binding: PayloadView) -> dict[str, str]:
        record.update(
            status="rejected",
            response=(
                "群助手仅处理当前项目和话题，不能选择个人 Agent 会话；/status 查看状态，/help 查看用法。"
                if binding.get("audience") == "group" else
                "操作或原授权不可用；Agent 请先用 /agents 查看确切命令，/project 返回项目对话。新委托请使用 /delegate --tokens N 具体目标，确认或取消请使用原预览中的完整命令。"
            ),
        )
        _atomic_write_json(path, record)
        return {"status": "command_rejected"}

    @staticmethod
    def _record_admission(path: Path, record: Payload, admitted: PayloadView) -> dict[str, str]:
        record.update(
            status="captured" if admitted["status"] == "prepared" else admitted["status"],
            session_id=admitted["session_id"], turn_id=admitted["turn_id"],
            response=admitted.get("response"), response_code=admitted.get("response_code"),
        )
        if recovery := admitted.get("context_recovery") or private_mention_topics.replay_presentation(admitted):
            record["context_recovery"] = recovery
        if admitted.get("mention_delivery"):
            record["mention_delivery"] = admitted["mention_delivery"]
        _atomic_write_json(path, record)
        if admitted["status"] == "prepared" and (
                admitted.get("response_code") == STEWARD_BINDING_ADOPTION_PENDING
                or _mapping(admitted.get("mention_delivery")).get("action") == "unresolved"):
            return {"status": "admission_deferred"}
        status = (
            "durably_accepted" if admitted["status"] == "accepted"
            else "queue_rejected" if admitted["status"] == "rejected" else "command_recorded"
        )
        return {"status": status}

    def _inbox(
        self, record: PayloadView, *, observation: PayloadView | None = None, reuse_display_name: bool = False,
    ) -> Path:
        source, event = _mapping(record["source"]), _mapping(record["event"])
        scope = source["source_ref"]
        if not isinstance(scope, str) or not re.fullmatch(r"[a-f0-9]{24}", scope):
            raise ValueError("invalid private source scope")
        profile, chat_id = _str_field(record, "profile"), _str_field(event, "chat_id")
        path = self.runtime_root / ".loopx" / "config" / "private-chat" / f"{scope}.json"
        if observation is None and reuse_display_name and path.exists():
            try:
                previous = _read_json(path)
                reply = _mapping(previous.get("reply"))
                name = reply.get("bot_display_name")
                if (previous.get("schema_version") == "lark_event_inbox_config_v0"
                        and reply.get("sender_profile") == profile and reply.get("chat_id") == chat_id
                        and isinstance(name, str) and name):
                    observation = {"bot_display_name": name}
            except (OSError, ValueError, AttributeError):
                pass
        if observation is None:
            observation = self.bindings.observe(profile)
        _atomic_write_json(path, {
            "schema_version": "lark_event_inbox_config_v0", "enabled": True,
            "inbox_dir": f".loopx/inbox/private-chat/{scope}", "capture_scope": "configured_chat_all",
            "reply": {
                "enabled": True, "sender_profile": profile, "sender_identity": "bot",
                "bot_display_name": observation["bot_display_name"], "chat_id": chat_id,
                "placement_policy": "source_thread" if source.get("group_ref") else "source_context",
                "received_reaction_emoji": "Get" if self.reaction_feedback else "",
                "received_reaction_policy": "retain",
                "processing_reaction_emoji": "OnIt" if self.reaction_feedback else "",
            },
        })
        content = event.get("content") or "附件（内容尚不支持）"
        captured = ingest_lark_event_inbox(
            project=self.runtime_root, config_path=path,
            events=[{**event, "content": content, "schema_version": "lark_event_inbox_event_v0"}], execute=True,
        )
        if captured["invalid_count"]:
            raise ValueError("private source could not be captured")
        return path

    def _extract_admit_input(
        self, path: Path, record: Payload, selected: Payload, source_message: PayloadView,
        message_type: str, *, history_review: bool,
    ) -> tuple[str, list[Payload], dict[str, str] | None]:
        if message_type == "text":
            if _msg_type(source_message) != "text":
                return "", [], {"status": "source_conflict"}
            text = private_source_text(source_message)
            if not text or not text.strip():
                return "", [], {"status": "invalid_text" if text is None else "empty_text"}
            captured = triage.capture_message(self, path, record, selected, text, history_review=history_review)
            return text, [], dict(captured) if captured is not None else None
        if message_type in {"image", "post"}:
            if _msg_type(source_message) != message_type:
                return "", [], {"status": "source_conflict"}
            content = source_message.get("content")
            if not isinstance(content, str):
                return "", [], {"status": "source_verification_failed"}
            if record.get("source_content", content) != content:
                return "", [], {"status": "source_conflict"}
            record["source_content"] = content
            if "attachments" not in record and not record.get("attachment_notice"):
                try:
                    text, attachments = private_message_images(
                        content=content, message_type=message_type,
                        message_id=_str_field(_mapping(record["event"]), "message_id"),
                        profile=_str_field(record, "profile"), cli_bin=self.cli_bin, runner=self.runner,
                    )
                    record.update(message=text, attachments=attachments)
                except ValueError as exc:
                    record["attachment_notice"] = str(exc)
                _atomic_write_json(path, record)
            raw_attachments = record.get("attachments")
            attachments = [dict(item) for item in raw_attachments if isinstance(item, Mapping)] if isinstance(raw_attachments, list) else []
            return str(record.get("message") or ""), attachments, None
        return "", [], None

    def _admit_history_answer(
        self, path: Path, record: Payload, binding: PayloadView, source: Payload,
        request: str, selected: PayloadView, source_message: PayloadView, text: str,
    ) -> dict[str, str]:
        event = _mapping(record["event"])
        profile = _str_field(record, "profile")
        message_id, chat_id = _str_field(event, "message_id"), _str_field(event, "chat_id")
        canonical = self._group_source_message(profile, message_id)

        def raw_source(value: PayloadView | None) -> Payload | None:
            body = value.get("body") if value else None
            if (value is None
                    or not _valid_user_sender(value, chat_id, event["sender_id"],
                                               msg_type="text", not_deleted=True, message_id=message_id)
                    or not str(value.get("create_time", "")).isdigit()
                    or not isinstance(body, Mapping) or not isinstance(body.get("content"), str)):
                return None
            return {key: value.get(key) for key in
                    ("message_id", "chat_id", "sender", "create_time", "body", "parent_id", "root_id", "thread_id")}

        frozen = raw_source(canonical)
        if frozen is None or record.get("history_source", frozen) != frozen:
            return {"status": "history_answer_held"}
        record["history_source"] = frozen
        _atomic_write_json(path, record)

        def verify_history(fresh: Payload) -> None:
            def bounded_runner(argv: list[str], cwd: Path | None, _t: float | None) -> dict[str, object]:
                return self.runner(argv, cwd, 5)

            def read(tail: list[str]) -> PayloadView | None:
                payload = _lark_payload(cast(CommandRunner, bounded_runner), self.cli_bin, profile, tail)
                return _message(payload, message_id) if payload is not None and payload.get("identity") in (None, "bot") else None

            with ThreadPoolExecutor(max_workers=2) as reads:
                rendered = reads.submit(read, [
                    "im", "+messages-mget", "--message-ids", message_id,
                    "--as", "bot", "--no-reactions", "--format", "json",
                ])
                raw = reads.submit(read, ["api", "GET", f"/open-apis/im/v1/messages/{message_id}", "--as", "bot"])
                reread, raw_read = rendered.result(), raw.result()
            if (reread is None or reread.get("content") != source_message.get("content")
                    or raw_source(raw_read) != frozen or fresh["binding"] != selected["binding"]):
                raise ValueError("historical source changed")

        created = datetime.fromtimestamp(int(str(frozen["create_time"])) / 1000, timezone.utc).isoformat()
        admitted = self.core.admit_history_answer(
            binding_id=_str_field(binding, "binding_id"), source=source,
            request_ref=request, message=text, source_created_at=created, source_preflight=verify_history,
        )
        if admitted["status"] == "prepared":
            record["history_answer_hold"] = admitted.get("history_answer_hold", "original_reservation_unavailable")
            _atomic_write_json(path, record)
            return {"status": "history_answer_held"}
        return self._record_admission(path, record, admitted)

    @effect_runtime_request_scope()
    def admit(
        self, profile: str, event: Payload, *, defer_background_stop: bool = False,
        history_review: bool = False, expected_binding_id: str | None = None,
    ) -> dict[str, str]:
        message_id = str(event.get("message_id") or "")
        record_event_timing(profile, message_id, "admission_started")
        try:
            binding = self._binding(profile)
            binding_id = _str_field(binding, "binding_id")
            if expected_binding_id is not None and binding_id != expected_binding_id:
                return {"status": "audience_rejected"}
            provider_ref = _str_field(binding, "provider_ref")
            source: Payload = lark_private_source(provider_ref=provider_ref, event=event)
            if history_review and (binding.get("context_kind") != "steward"
                    or binding.get("audience", "owner") != "owner" or event.get("chat_type") != "p2p"):
                return {"status": "history_answer_held"}
            request = identity_ref(provider_ref, _str_field(event, "message_id"))
            control_root = self._admitted_control_root({"request_ref": request, "source": source, "event": event}, binding)
        except (KeyError, StopIteration, ValueError):
            return {"status": "audience_rejected"}
        path, base_source = self.root / f"{request}.json", source
        record_event_timing(profile, message_id, "binding_hint_ready")

        def new_capture(resolved_source: Payload, auto_lane: bool) -> Payload:
            return {
                "schema_version": "lark_private_chat_delivery_v0", "request_ref": request, "profile": profile,
                "binding_id": binding_id, "source": resolved_source, "event": event, "deliveries": {}, "status": "captured",
                **({"auto_topic_lane": True} if auto_lane else {}),
                **({"capture_origin": "provider_history"} if history_review else {}),
            }

        with exclusive_file_lock(path, operation="capture_private_chat_request"):
            record_event_timing(profile, message_id, "capture_lock_acquired")
            fresh_capture = not path.exists()
            source, auto_topic_lane = resolve_private_steward_lane(
                controller=self.core.controller, binding=binding, base_source=base_source,
                event=event, existing_record=None if fresh_capture else _read_json(path),
                history_review=history_review, allow_auto_fork=False,
            )
            if (fresh_capture and binding.get("enabled") is True and binding.get("audience", "owner") == "owner"
                    and source.get("private_human_message") is True and source["sender_ref"] == binding["operator_ref"]):
                _atomic_write_json(path, new_capture(source, auto_topic_lane))
            mention_capture = self.core.capture_native_mention(binding, source) if fresh_capture else None
            if fresh_capture and path.exists():
                captured_row = _read_json(path)
                captured_row["mention_capture"] = mention_capture
                _atomic_write_json(path, captured_row)
            try:
                selected = None if control_root else self._resolve_binding(binding_id, source, include_observation=True)
            except (KeyError, StopIteration, ValueError):
                return {"status": "audience_rejected"}
            if path.exists():
                record: Payload = _read_json(path)
                if history_review and record.get("capture_origin") != "provider_history":
                    return {"status": "history_answer_held"}
                saved_event = _mapping(record["event"])
                omitted_private_thread = (
                    event["chat_type"] == "p2p" and not event.get("thread_id") and "reply_context_message" in record
                )
                if any(saved_event.get(key) != event.get(key) for key in (
                        "message_id", "sender_id", "chat_id", "content", "message_type",
                        "chat_type", "sender_type", "root_id", "parent_id")) or (
                        not omitted_private_thread and saved_event.get("thread_id") != event.get("thread_id")):
                    return {"status": "source_conflict"}
            else:
                record = new_capture(source, auto_topic_lane)
                if binding.get("audience") != "group":
                    _atomic_write_json(path, record)
            if record["status"] == "delivered" and not {"session_id", "turn_id"}.intersection(record):
                return {"status": "triage_delivered" if "triage" in record else "command_rejected"}
            if control_root:
                result = self._admit_group_control(path, record, binding, control_root, defer_background_stop=defer_background_stop)
                if result is not None:
                    return result
                try:
                    selected = self._resolve_binding(binding_id, source, include_observation=True)
                except ValueError:
                    return {"status": "audience_rejected"}
            assert selected is not None
            if fresh_capture:
                record["mention_capture"] = mention_capture
                if binding.get("audience") != "group":
                    _atomic_write_json(path, record)
            source_message = self._source_message(record, selected=selected)
            if source_message is None:
                return {"status": "source_verification_failed"}
            if record.get("capture_origin") == "provider_history" and source_message.get("content") != event.get("content"):
                return {"status": "history_answer_held"}
            if binding.get("audience") == "group":
                _atomic_write_json(path, record)
            message_type = str(event.get("message_type") or "")
            text, attachments, early = self._extract_admit_input(
                path, record, selected, source_message, message_type, history_review=history_review,
            )
            if early is not None:
                return early
            command_input = private_message_caption(_str_field(record, "source_content")) if attachments else text.strip()
            if binding.get("audience") == "group" and message_type == "text":
                command_input = _str_field(source_message, "_command_input")
            prefixes = (("/agent", "select_agent"), *(_STEWARD_PREFIX_COMMANDS if binding["context_kind"] == "steward" else ()))
            command = (
                CHAT_CONTROL_COMMANDS.get(command_input)
                or {"/agents": "agents", "/project": "select_project"}.get(command_input)
                or next((cmd for prefix, cmd in prefixes if command_input == prefix or command_input.startswith(prefix + " ")), None)
            )
            if message_type not in {"text", "image", "post"} or record.get("attachment_notice"):
                command = "unsupported"
            if command is None:
                try:
                    text = self._reply_context_message(path, record, selected=selected, source_message=source_message, text=text)
                except (KeyError, ValueError, OSError):
                    return {"status": "source_verification_failed"}
            try:
                record_event_timing(profile, message_id, "core_admission_started")
                if record.get("capture_origin") == "provider_history":
                    if message_type != "text" or command is not None or attachments:
                        return {"status": "history_answer_held"}
                    history_result = self._admit_history_answer(
                        path, record, binding, source, request, selected, source_message, text,
                    )
                    record_event_timing(profile, message_id, "core_admission_returned")
                    return history_result
                preflight = (
                    steward_source_preflight(_mapping(selected["binding"]), source_message,
                        lambda fresh: self._source_message(record, selected=fresh))
                    if command is None else None
                )
                native_mention = command is None and source_message.get("_native_mention") is True
                source, selected = finalize_private_steward_lane(
                    transport=self, path=path, record=record, binding=binding, base_source=base_source,
                    current_source=source, selected=selected, event=event, request_ref=request,
                    fresh_capture=fresh_capture, history_review=history_review,
                    is_control_or_mention=(command is not None or native_mention),
                )
                if native_mention:
                    previous_preflight = preflight

                    def verify_mention(fresh: Payload) -> None:
                        if previous_preflight is not None:
                            previous_preflight(fresh)
                        current = self._source_message(record, selected=fresh)
                        if current is None:
                            raise PrivateSourceUnavailable("native mention source is unavailable")
                        if (current.get("_native_mention") is not True
                                or any(current.get(key) != source_message.get(key) for key in
                                       ("content", "parent_id", "root_id", "thread_id"))):
                            raise ValueError("native mention source changed")

                    preflight = verify_mention
                    native_mention = private_mention_topics.steer_in_topic(self, path, record, binding)
                raw_capture = record.get("mention_capture")
                mention_arg = dict(raw_capture) if isinstance(raw_capture, Mapping) else None
                admitted = self.core.admit(
                    binding_id=binding_id, source=source, request_ref=request, message=text,
                    command=command, attachments=attachments, defer_background_stop=defer_background_stop,
                    source_preflight=preflight, native_mention=native_mention,
                    native_mention_capture=mention_arg,
                )
                record_event_timing(profile, message_id, "core_admission_returned")
            except PrivateSourceUnavailable:
                return {"status": "source_verification_failed"}
            except ValueError:
                if record.get("capture_origin") == "provider_history":
                    return {"status": "history_answer_held"}
                return self._reject_command(path, record, binding)
            except RuntimeError as exc:
                if str(exc) != "session_queue_full":
                    raise
                record.update(status="rejected", response="队列已满，本条没有被受理；请稍后重试。")
                _atomic_write_json(path, record)
                return {"status": "queue_rejected"}
            return self._record_admission(path, record, admitted)

    def _reply_runner(self, args: Sequence[str]) -> PayloadView:
        return self.runner([self.cli_bin, *args[1:]], None, 30)

    def return_inbox(self, *, route: PayloadView, session: PayloadView, turn: PayloadView) -> tuple[Path, Payload]:
        """Resolve an original worker return through its admitted private source."""
        saved = _mapping(session["steward_context"])
        selected = self.bindings.session_context(dict(saved))
        client = str(turn.get("client_turn_id") or "")
        match = re.fullmatch(r"external-([a-f0-9]{24})(?:-.+)?", client)
        if match is None:
            raise ValueError("original private request unavailable")
        request = match[1]
        record: Payload = _read_json(self.root / f"{request}.json")
        native = self.core.read_request(request)
        if client != f"external-{request}":
            recovery = self.core.recovery_context(dict(session), dict(turn))
            if (recovery is None or recovery.get("request_ref") != request
                    or record.get("context_recovery") != native.get("context_recovery")):
                raise ValueError("original private recovery unavailable")
        event = _mapping(record.get("event"))
        message_id = str(event.get("message_id") or "")
        sel_binding = _mapping(selected["binding"])
        provider_ref = _str_field(sel_binding, "provider_ref")
        goal_ids = _mapping(selected["context"]).get("goal_ids")
        if (route["source_id"] not in (request, f"lark:{message_id}")
                or identity_ref(provider_ref, message_id) != request
                or session.get("goal_id") != "loopx-manager"
                or session.get("channel_id") != selected["channel_id"]
                or not isinstance(goal_ids, (list, tuple, set)) or route["goal_id"] not in goal_ids
                or route["session_id"] != session["session_id"]
                or turn.get("session_id") != session["session_id"]
                or record.get("profile") != sel_binding["transport_ref"]
                or record.get("request_ref") != request
                or record.get("source") != expected_private_source(provider_ref=provider_ref, event=event, saved_context=saved)
                or _mapping(record["source"])["source_ref"] != saved["source_ref"]
                or any(row.get("binding_id") != saved["binding_id"] or row.get("source") != record["source"]
                       or row.get("session_id") != session["session_id"] or row.get("turn_id") != turn["turn_id"]
                       for row in (record, native))):
            raise ValueError("original private return source changed")
        return self._inbox(record), record

    def _feedback(self, path: Path, record: Payload, *, inbox: Callable[[], Path], processing: bool = False) -> None:
        """Render observed Core admission/execution through the shared Inbox owner."""
        if not self.reaction_feedback or record.get("context_recovery"):
            return
        phase = "processing" if processing else "received"
        feedback = _ensure_dict(record, "feedback")
        if _mapping(feedback.get(phase)).get("ok"):
            return
        config, event = inbox(), _mapping(record["event"])
        result = (
            mark_lark_event_inbox_processing(
                project=self.runtime_root, config_path=config,
                message_id=_str_field(event, "message_id"), execute=True, runner=self._reply_runner,
            ) if processing else mark_lark_event_inbox_received(
                project=self.runtime_root, config_path=config, event=event, runner=self._reply_runner,
            )
        )
        feedback[phase] = result
        _atomic_write_json(path, record)

    def _deliver(
        self, path: Path, record: Payload, phase: str, text: str, *, inbox: Callable[[], Path],
        edit_only: bool = False, authorization_check: Callable[[], bool] | None = None,
        source_authority_verifier: SourceAuthorityVerifier | None = None,
        reply_placement: str | None = None, delivery_id: str | None = None,
        finalize_source: bool = True, image_attachments: Sequence[Payload] | None = None,
        historical_source_check: Callable[[], bool] | None = None,
    ) -> bool:
        if not text:
            return False
        event, source = _mapping(record["event"]), _mapping(record["source"])
        if reply_placement is not None and (event.get("chat_type") != "p2p" or source.get("group_ref")):
            raise ValueError("a new private result requires its captured private source")
        if authorization_check is not None and not authorization_check():
            return False
        kind = phase.split(".", 1)[0]
        if historical_source_check is not None and phase != "answer_images.historical":
            raise ValueError("historical source verification requires a media repair")
        phase = presentation_phase(record, phase)
        expected_text = _str_field(event, "content") if kind == "answer_images" else None
        source_authority: SourceAuthorityVerifier = source_authority_verifier or partial(
            self._source_authority, record, expected_text=expected_text,
        )
        deliveries = _ensure_dict(record, "deliveries")
        raw_phase = deliveries.get(phase)
        phase_state: Payload = raw_phase if isinstance(raw_phase, dict) else {}

        def save_phase(**updates: object) -> None:
            phase_state.update(updates)
            deliveries[phase] = phase_state
            _atomic_write_json(path, record)

        if phase_state.get("verified"):
            return True
        if phase_state.get("text") != text:
            text = _presentation_text(text)
        if phase_state.get("text") not in (None, text):
            raise ValueError("delivery content changed after an attempt")
        if image_attachments and (edit_only or not (
                (kind == "web" and reply_placement == "chat_root"
                 and (authorization_check is not None or source_authority_verifier is not None))
                or (kind == "answer_images" and event.get("chat_type") == "p2p"
                    and source.get("private_human_message") is True and authorization_check is not None))):
            raise ValueError("Image projection requires its original private outbox")
        profile, message_id = _str_field(record, "profile"), _str_field(event, "message_id")
        chat_id = _str_field(event, "chat_id")
        record_event_timing(profile, message_id, "delivery_started")
        config = inbox()
        record_event_timing(profile, message_id, "delivery_inbox_ready")

        def provider_images_current() -> bool:
            if historical_source_check is not None:
                return historical_source_check()
            payload = _lark_payload(
                self._read_answer_image_source, self.cli_bin, profile,
                ["api", "GET", f"/open-apis/im/v1/messages/{message_id}", "--as", "bot"],
            )
            return payload is not None and answer_image_source_matches(_message(payload, message_id), event)

        def verified_source() -> bool:
            if historical_source_check is not None:
                return historical_source_check()
            name, verified = source_authority(profile, chat_id, message_id)
            return bool(name) and verified

        def image_authorized() -> bool:
            if kind == "answer_images":
                return provider_images_current() and authorization_check is not None and authorization_check()
            return (authorization_check is None or authorization_check()) and verified_source()

        image_keys: tuple[str, ...] = ()
        if image_attachments:
            binding_id = _str_field(record, "binding_id")
            resource_scope = f"{binding_id}:{_str_field(record, 'request_ref')}:{phase}" if kind == "answer_images" else binding_id
            image_keys = cast(Callable[..., tuple[str, ...]], uploaded_chat_images)(
                root=self.runtime_root, profile=profile, provider_ref=resource_scope,
                attachments=list(image_attachments), private_transport=self, before_upload=image_authorized,
                dispatch_source_check=provider_images_current,
                verify_only=bool(phase_state.get("started") or phase_state.get("attempt")),
                dispatch_guard=partial(answer_media_dispatch, self.core, record) if kind == "answer_images" else None,
            )
            if phase_state.get("image_keys", list(image_keys)) != list(image_keys):
                raise ValueError("App image delivery resources changed after an attempt")
            save_phase(image_keys=list(image_keys))

        finalize_reactions = (
            finalize_source and not edit_only
            and kind not in {"admission", "progress", "periodic_report", "steward_monitor_report"}
            and not (kind == "terminal" and record.get("commission_resources"))
        )

        def scoped_authority(req_profile: str, req_chat: str, req_message: str) -> tuple[str | None, bool]:
            if authorization_check is None or authorization_check():
                return source_authority(req_profile, req_chat, req_message)
            return None, False

        progress = _mapping(deliveries.get(presentation_phase(record, "progress")))
        stream = presentation_stream(record)
        raw_edit = phase_state.get("edit_from")
        edit: PayloadView | None = raw_edit if isinstance(raw_edit, Mapping) else None
        if (not edit_only and kind in {"terminal", "commission_result"} and progress.get("attempt")
                and not stream.get("closed") and not stream.get("edit_limit_reached")
                and not phase_state.get("started")):
            prev = stream.get("confirmed_text") if stream.get("confirmed_text") else progress["text"]
            edit_map: Payload = {
                "text": _presentation_text(str(prev)),
                "attempt": stream.get("confirmed_attempt") or progress["attempt"],
            }
            phase_state["edit_from"] = edit_map
            edit = edit_map
        if edit_only and not edit:
            return False
        if edit and reply_placement is not None:
            raise ValueError("a new private message cannot replace an existing source reply")

        def before_send(_intent: str) -> dict[str, bool]:
            if (authorization_check is not None and not authorization_check()) or not verified_source() or (
                    authorization_check is not None and not authorization_check()):
                return {"continue_delivery": False}
            save_phase(text=text, started=True)
            return {"continue_delivery": True}

        effective_placement = "source_thread" if reply_placement is None and record.get("auto_topic_lane") is True else reply_placement
        raw_attempt = phase_state.get("attempt")
        if edit is not None:
            result = update_lark_inbox_reply(
                project=self.runtime_root, config_path=config, message_id=message_id, text=text,
                previous_text=_str_field(edit, "text"), attempt=_mapping(edit["attempt"]),
                reply_placement=effective_placement, runner=self._reply_runner,
                finalize_reactions=finalize_reactions, source_authority_verifier=scoped_authority,
                before_send=before_send, delivery_attempt_recorder=lambda value: save_phase(attempt=dict(value)),
                require_previous_text_match=edit_only,
            )
            if not edit_only and result.get("blocker") == "provider_update_edit_limit":
                stream["edit_limit_reached"] = True
                rejected = {"blocker": result["blocker"], "edit_from": edit, "attempt": phase_state.get("attempt")}
                for key in ("edit_from", "attempt", "started"):
                    phase_state.pop(key, None)
                save_phase(rejected_update=rejected)
                return self._deliver(path, record, phase, text, inbox=inbox, finalize_source=finalize_source)
            if not edit_only and result.get("blocker") in {"provider_update_too_large", "provider_update_format_unsupported"}:
                if not self._close_progress(path, record, inbox=inbox) and not stream.get("edit_limit_reached"):
                    return False
                phase_state.pop("edit_from", None)
                save_phase()
                return self._deliver(path, record, phase, text, inbox=inbox, finalize_source=finalize_source)
        elif isinstance(raw_attempt, Mapping):
            result = verify_lark_inbox_reply(
                project=self.runtime_root, config_path=config, message_id=message_id, text=text,
                attempt=raw_attempt, reply_placement=effective_placement, delivery_id=delivery_id,
                image_keys=image_keys, runner=self._reply_runner, finalize_reactions=finalize_reactions,
                source_authority_verifier=scoped_authority, resume_parts=True, before_send=before_send,
            )
        elif phase_state.get("started"):
            if phase_state.get("blocker") != "provider_delivery_receipt_unknown":
                save_phase(blocker="provider_delivery_receipt_unknown")
            return False
        else:
            def attempt(value: Mapping[str, str | None]) -> None:
                phase_state["attempt"] = dict(value)
                _atomic_write_json(path, record)

            result = reply_lark_event_inbox(
                project=self.runtime_root, config_path=config, message_id=message_id, text=text,
                reply_placement=effective_placement, delivery_id=delivery_id, image_keys=image_keys,
                runner=self._reply_runner, finalize_reactions=finalize_reactions,
                source_authority_verifier=scoped_authority, execute=True, before_send=before_send,
                delivery_attempt_recorder=attempt, short_message_limit=None, content_format="markdown",
                overlap_source_preflight=bool(
                    kind == "terminal" and self._completed_stop_root(record, self._binding_row(_str_field(record, "binding_id")) or {})
                ),
            )
        if image_attachments and result.get("reply_verified") is True and result.get("ok") is True:
            image_text, image_attempt = cast(Callable[..., tuple[str, PayloadView]], attachment_reply)(
                project=self.runtime_root, config_path=config, attempt=phase_state["attempt"], text=text,
            )
            verified = cast(Callable[..., Payload], verify_chat_images)(
                profile=profile, message_id=_str_field(image_attempt, "message_ref"),
                text=image_text, attachments=list(image_attachments), keys=image_keys,
                private_transport=self, before_read=image_authorized,
            )
            result.update(verified)
            phase_state["images_verified"] = verified.get("reply_verified") is True
        done = result.get("reply_verified") is True and result.get("ok") is True
        if not edit_only and record.get("context_recovery") and kind == "terminal" and done:
            stream["closed"] = True
        save_phase(verified=done, blocker=result.get("blocker"))
        record_event_timing(profile, message_id, "delivery_returned")
        return bool(phase_state["verified"])

    def pending_body_paths(self) -> list[Path]:
        """Find unsent bodies without waiting for historical recovery probes."""
        paths = [
            path for path, row, _ in self._body_discovery.rows()
            if row["status"] != "delivered" and triage.pending(row)
            and not is_answer_image_followup(row) and not failure_notice_receipt_unknown(row)
        ]
        return [*paths, *self._pending_web_projection_paths()]

    def _noop_backoff_cache(self) -> dict[Path, tuple[tuple[object, ...], int]]:
        cache = getattr(self, "_context_recovery_noop_backoff", None)
        if not isinstance(cache, dict):
            cache = {}
            self._context_recovery_noop_backoff = cache
        return cache

    def _session_token_cache(self) -> dict[Path, tuple[tuple[int, ...], tuple[str, bool, str]]]:
        cache = getattr(self, "_session_backoff_token_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._session_backoff_token_cache = cache
        return cache

    def _record_context_recovery_noop_backoff(
        self, path: Path, row: PayloadView, *, stagger_first: bool = False,
        fingerprints: tuple[object, ...] | None | object = _UNSET,
    ) -> None:
        current = (
            context_recovery_fingerprints(
                self.core, path, _str_field(row, "request_ref"), _str_field(row, "session_id"),
                _str_field(row, "turn_id"), self._session_token_cache(),
            ) if fingerprints is _UNSET else fingerprints
        )
        record_context_recovery_noop_backoff(
            self._noop_backoff_cache(), self._context_recovery_probe_at, path,
            None if current is _UNSET else cast("tuple[object, ...] | None", current),
            now_monotonic=time.monotonic(), stagger_first=stagger_first,
        )

    @effect_runtime_request_scope()
    def pending_delivery_paths(self) -> list[Path]:
        """The durable transport store is the queue; no in-memory admission."""
        paths: list[Path] = []
        recovery_candidate = self.core.context_recovery_observer()
        now = datetime.now(timezone.utc).timestamp()
        planned_repairs = self._planned_historical_repairs()
        noop_backoff, token_cache = self._noop_backoff_cache(), self._session_token_cache()
        session_tokens: dict[Path, tuple[str, bool, str] | None] = {}
        scanned_rows: list[tuple[Path, Payload, bool]] = []
        rows_iter = getattr(self._delivery_discovery, "rows_with_fingerprints", None)
        entries: Iterator[tuple[Path, Payload, bool, tuple[int, ...] | None]] = (
            rows_iter() if callable(rows_iter)
            else ((path, row, repairs, None) for path, row, repairs in self._delivery_discovery.rows())
        )
        for path, row, repairs, path_fp in entries:
            scanned_rows.append((path, row, repairs))
            receipt_unknown = failure_notice_receipt_unknown(row)
            due_at = row.get("historical_images_due_at")
            if row["status"] != "delivered" and not receipt_unknown:
                if triage.pending(row) and not answer_image_retry_waits(row, now):
                    paths.append(path)
            elif isinstance(due_at, (int, float)) and due_at <= now:
                paths.append(path)
            elif (not row.get("historical_images_admitted") and path.name in planned_repairs
                    and time.monotonic() >= self._historical_image_probe_at.get(path, 0)):
                paths.append(path)
            elif row.get("session_id") and row.get("turn_id"):
                request_ref = _str_field(row, "request_ref")
                session_id, turn_id = _str_field(row, "session_id"), _str_field(row, "turn_id")
                current_fp: tuple[object, ...] | None | object = _UNSET
                saved_backoff = noop_backoff.get(path)
                if saved_backoff is not None:
                    current_fp = context_recovery_fingerprints(
                        self.core, path, request_ref, session_id, turn_id, token_cache,
                        path_fp=path_fp, session_tokens=session_tokens,
                    )
                    if not context_recovery_backoff_valid(saved_backoff[0], current_fp):
                        noop_backoff.pop(path, None)
                        self._context_recovery_probe_at.pop(path, None)
                if time.monotonic() >= self._context_recovery_probe_at.get(path, 0):
                    now_mono = time.monotonic()
                    stagger = zlib.crc32(path.name.encode("utf-8")) % 60
                    self._context_recovery_probe_at[path] = now_mono + 60 + stagger
                    candidate = (
                        recovery_candidate(request_ref, failure_notice_receipt_unknown=True)
                        if receipt_unknown else recovery_candidate(request_ref)
                    )
                    if repairs or candidate:
                        self._context_recovery_probe_at[path] = now_mono + 60
                        paths.append(path)
                    else:
                        if current_fp is _UNSET:
                            current_fp = context_recovery_fingerprints(
                                self.core, path, request_ref, session_id, turn_id, token_cache,
                                path_fp=path_fp, session_tokens=session_tokens,
                            )
                        self._record_context_recovery_noop_backoff(
                            path, row, stagger_first=True, fingerprints=current_fp,
                        )
        self._web_projection_scan_rows = scanned_rows
        try:
            web_paths = self._web_projection_paths()
        finally:
            self._web_projection_scan_rows = None
        return [*paths, *private_mention_topics.pending_replays(self, set(paths)), *web_paths]

    def is_recovery_path(self, path: Path) -> bool:
        """Schedule recovery of persisted attempts after fresh replies."""
        if path.parent == self.root / "web-projections":
            return False
        row = _read_json(path)
        card = _mapping(row.get("triage"))
        return (
            row.get("status") == "delivered" or is_answer_image_followup(row)
            or failure_notice_receipt_unknown(row)
            or bool(card.get("send_started_at") or card.get("message_id"))
        )

    def observe_ingress(self, *, cancelled: Callable[[], bool] = lambda: False) -> list[str]:
        """The original service observes gaps, then asks Core for one read-only answer."""
        reconnect: list[str] = self.ingress.observe(cancelled=cancelled)
        for profile, source in self.ingress.answer_candidates(cancelled=cancelled):
            if cancelled():
                break
            try:
                self.admit(profile, source, history_review=True)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                continue
        return reconnect

    def reconcile(self) -> int:
        delivered = sum(self.reconcile_request(path) for path in self.pending_delivery_paths())
        self.reconcile_app_feedback()
        return delivered

    def _received_during_recovery(self, path: Path) -> None:
        """Acknowledge durable ordinary admission independently of recovery."""
        if not self.reaction_feedback:
            return
        with exclusive_file_lock(path, operation="deliver_private_chat_request"):
            record = _read_json(path)
            if record["status"] != "accepted" or _mapping(_mapping(record.get("feedback")).get("received")).get("ok"):
                return
            request_ref = _str_field(record, "request_ref")
            native = self.core.read_request(request_ref)
            if (native["status"] != "accepted" or native.get("agent_target") or native.get("command")
                    or not _same_fields(native, record, "request_ref", "binding_id", "source", "session_id", "turn_id")):
                return
            store = self.core.controller.store
            sid, tid = _str_field(record, "session_id"), _str_field(record, "turn_id")
            session, turn = store.load_session(sid), store.load_turn(sid, tid)
            raw_saved = (session or {}).get("project_context") or (session or {}).get("steward_context")
            saved, event = _mapping(raw_saved), _mapping(record["event"])
            expected_client = _mapping(record.get("context_recovery")).get("client_turn_id") or f"external-{request_ref}"
            if (not session or not turn or not raw_saved or session.get("status") == "closed"
                    or turn.get("session_id") != session["session_id"] or turn.get("client_turn_id") != expected_client
                    or identity_ref(_str_field(saved, "provider_ref"), _str_field(event, "message_id")) != request_ref
                    or record["source"] != expected_private_source(
                        provider_ref=_str_field(saved, "provider_ref"), event=event, saved_context=saved)):
                return
            selected = self._resolve_binding(
                _str_field(record, "binding_id"), _mapping(record["source"]),
                session_context=saved, include_observation=True,
            )
            sel_binding = _mapping(selected["binding"])
            if (record["profile"] != sel_binding["transport_ref"]
                    or session.get("goal_id") != ("loopx-manager" if sel_binding["context_kind"] == "steward" else None)
                    or session.get("channel_id") != selected["channel_id"]):
                return
            observation = _mapping(selected["observation"])
            self._feedback(path, record, inbox=lambda: self._inbox(record, observation=observation))

    def _reconcile_native_result(self, path: Path, native: PayloadView) -> int:
        with exclusive_file_lock(path, operation="deliver_private_chat_request"):
            record = _read_json(path)
            if not _same_fields(record, native, "request_ref", "binding_id", "source", "session_id", "turn_id"):
                return 0
            request_ref = _str_field(record, "request_ref")
            text = self.core.native_result_reply(request_ref)
            if text is None:
                return 0
            recovered = _mapping(native["native_result_recovery"])
            recovered_message_id = _str_field(recovered, "message_id")

            def authorized() -> bool:
                return self.core.native_result_reply(request_ref) == text

            delivery_id = hashlib.sha256(recovered_message_id.encode()).hexdigest()
            if not self._deliver(
                path, record, f"native_result.{_str_field(record, 'turn_id')}", text,
                inbox=lambda: self._inbox(record), authorization_check=authorized,
                reply_placement="chat_root", delivery_id=delivery_id, finalize_source=False,
            ):
                return 0
            self.core.record_native_result_delivery(request_ref, message_id=recovered_message_id)
            return 1

    def _recover_core(self, row: PayloadView) -> None:
        profile, message_id = _str_field(row, "profile"), _str_field(_mapping(row["event"]), "message_id")
        record_event_timing(profile, message_id, "core_recovery_started")
        self.core.recover(request_ref=_str_field(row, "request_ref"))
        record_event_timing(profile, message_id, "core_recovery_returned")

    def _reconcile_context_recovery(self, path: Path, row: Payload) -> tuple[Payload, int | None]:
        receipt_unknown = failure_notice_receipt_unknown(row)
        request_ref = _str_field(row, "request_ref")
        eligible = (
            row.get("session_id") and row.get("turn_id")
            and (row["status"] == "delivered" or (row["status"] == "accepted" and (
                self.core.context_recovery_candidate(request_ref, failure_notice_receipt_unknown=True)
                if receipt_unknown else self.core.context_recovery_candidate(request_ref))))
        )
        if not eligible:
            if receipt_unknown:
                self._record_context_recovery_noop_backoff(path, row)
                return row, 0
            return row, None
        def obs(selected: Payload) -> Payload | None:
            return self._recovery_source_observation(row, selected)
        recovered = (
            self.core.recover_failed_request(request_ref, source_observation=obs, failure_notice_receipt_unknown=True)
            if receipt_unknown else self.core.recover_failed_request(request_ref, source_observation=obs)
        )
        native_rec = _mapping(recovered.get("native_result_recovery"))
        if row["status"] == "delivered" and native_rec and native_rec.get("turn_id") == recovered.get("turn_id"):
            delivered_native = self._reconcile_native_result(path, recovered)
            if delivered_native:
                self._noop_backoff_cache().pop(path, None)
            else:
                self._record_context_recovery_noop_backoff(path, row)
            return row, delivered_native
        if recovered.get("context_recovery") and not recovered.get("delivery_verified") and recovered.get("turn_id") != row["turn_id"]:
            self._noop_backoff_cache().pop(path, None)
            with exclusive_file_lock(path, operation="deliver_private_chat_request"):
                row = _read_json(path)
                if not _same_fields(row, recovered, "request_ref", "binding_id", "source", "session_id"):
                    raise ValueError("original recovery source changed")
                row.update(status="accepted", turn_id=recovered["turn_id"], context_recovery=recovered["context_recovery"])
                _atomic_write_json(path, row)
            return row, None
        if receipt_unknown:
            self._record_context_recovery_noop_backoff(path, row)
            return row, 0
        if row["status"] == "delivered":
            self._record_context_recovery_noop_backoff(path, row)
        return row, None

    def _reconcile_captured_request(self, path: Path, row: Payload) -> tuple[Payload, int | None]:
        with exclusive_file_lock(path, operation="capture_private_chat_request"):
            row = _read_json(path)
            request_ref = _str_field(row, "request_ref")
            native_path = self.core.root / f"{request_ref}.json"
            if row["status"] == "captured" and native_path.exists():
                native = self.core.read_request(request_ref)
                if (row.get("response_code") == STEWARD_BINDING_ADOPTION_PENDING
                        and native.get("status") == "prepared"
                        and native.get("response_code") == STEWARD_BINDING_ADOPTION_PENDING
                        and _same_fields(native, row, "request_ref", "binding_id", "source", "session_id", "turn_id")
                        and prepared_steward_binding_waits(native, self.bindings.read()["bindings"], self.core.controller.store)):
                    return row, 0
                if (native.get("command") == "stop" and native.get("target_recorded") is True
                        and _same_fields(native, row, "request_ref", "binding_id", "source")):
                    self._recover_core(row)
                    native = self.core.read_request(request_ref)
                    if native["status"] == "prepared":
                        return row, 0
                    if native["status"] == "command_completed":
                        row.update(**{key: native.get(key) for key in ("status", "session_id", "turn_id", "response", "response_code")})
                        _atomic_write_json(path, row)
        if row["status"] == "captured":
            self.admit(_str_field(row, "profile"), _req_dict(row, "event"))
            row = _read_json(path)
            if row["status"] == "captured" and row.get("response_code") == STEWARD_BINDING_ADOPTION_PENDING:
                return row, 0
            if row["status"] == "captured" and _mapping(row.get("mention_delivery")).get("action") == "unresolved":
                with exclusive_file_lock(path, operation="deliver_private_chat_request"):
                    row = _read_json(path)
                    self._deliver(
                        path, row, "admission", _str_field(row, "response"),
                        inbox=lambda: self._inbox(row), finalize_source=False,
                    )
                return row, 0
        return row, None

    def _recover_request_with_feedback(self, path: Path, row: Payload) -> None:
        if (self.reaction_feedback and not row.get("context_recovery") and row["status"] == "accepted"
                and not _mapping(_mapping(row.get("feedback")).get("received")).get("ok")):
            with ThreadPoolExecutor(max_workers=1) as feedback:
                received = feedback.submit(self._received_during_recovery, path)
                self._recover_core(row)
                try:
                    received.result()
                except (KeyError, ValueError, OSError):
                    pass
        else:
            self._recover_core(row)

    def _resolve_accepted_response(
        self, path: Path, record: Payload, selected: PayloadView, native: PayloadView, inbox: Callable[[], Path],
    ) -> tuple[str, bool, Sequence[str | PayloadView | None] | None] | None:
        store = self.core.controller.store
        sid, tid = _str_field(record, "session_id"), _str_field(record, "turn_id")
        if native.get("agent_target"):
            self.bindings.resolve_agent_target(
                self._resolve_binding(_str_field(record, "binding_id"), _mapping(record["source"])),
                _req_dict(native, "agent_target"),
            )
        self._feedback(path, record, inbox=inbox)
        turn = store.load_turn(sid, tid)
        admission = _mapping(_mapping(record["deliveries"]).get(presentation_phase(record, "admission"))).get("text")
        if not admission:
            if native.get("agent_target"):
                admission = "已收到，等待原 Agent 宿主处理。/status 查看状态，/project 返回项目对话。"
            elif turn and turn["status"] == "queued":
                admission = "已收到，这条已排队。"
            elif _mapping(_mapping(record.get("feedback")).get("received")).get("ok") is not True:
                admission = "已收到，正在处理。"
        if isinstance(admission, str) and admission:
            self._deliver(path, record, "admission", admission, inbox=inbox)
        if turn and turn["status"] in {"starting", "running"}:
            self._feedback(path, record, inbox=inbox, processing=True)
            turn = store.load_turn(sid, tid)
            if turn and turn["status"] in {"starting", "running"}:
                self._progress(path, record, inbox=inbox)
        if turn and turn["status"] == "completing":
            self.core.controller.recover_exited_turn(sid, tid)
            turn = store.load_turn(sid, tid)
        if not turn or turn["status"] not in TERMINAL_TURN_STATES:
            return None
        if turn["status"] == "failed":
            events = store.events_after(sid, tid, None) if turn.get("error_code") in {"host_gate", "response_timeout"} else []
            preparation_recovery = (
                _mapping(record["source"]).get("private_human_message") is True
                and _mapping(selected["binding"]).get("context_kind") in {"project", "steward"}
                and not native.get("agent_target")
            )
            return manager_failure_reply(turn, phase_events=events, preparation_recovery=preparation_recovery)[1] + " 原会话已保留。", False, None
        if turn["status"] == "completed":
            turn_response = _mapping(turn.get("response"))
            response = self.core.todo_confirmation_reply(_str_field(record, "request_ref"), dict(turn_response))
            raw_plan = turn_response.get("answer_image_plan")
            plan = cast("Sequence[str | PayloadView | None] | None", raw_plan if isinstance(raw_plan, list) else None)
            return response, True, plan
        response = (
            "本次执行已停止。" if turn["status"] == "interrupted" else
            "本次执行超时，原会话已保留；请发送 /status 查看状态后再决定是否重试。" if turn["status"] == "timed_out" else
            "本次未能完成，原会话已保留。"
        )
        return response, False, None

    def _deliver_commission_followup(
        self, path: Path, record: Payload, native: PayloadView, inbox: Callable[[], Path],
    ) -> bool:
        resources = _mapping(record.get("commission_resources"))
        if not (resources.get("session_id") and resources.get("turn_id")):
            return True
        rsid, rtid = _str_field(resources, "session_id"), _str_field(resources, "turn_id")
        first_turn = self.core.controller.store.load_turn(rsid, rtid)
        if first_turn and first_turn["status"] in {"starting", "running"}:
            self._feedback(path, record, inbox=inbox, processing=True)
            self._progress(path, record, inbox=inbox, session_id=rsid, turn_id=rtid)
        if not first_turn or first_turn["status"] not in TERMINAL_TURN_STATES:
            record["status"] = "commission_running"
            _atomic_write_json(path, record)
            return False
        result_text = str(_mapping(first_turn.get("response")).get("message") or "")
        result_text = (
            "委托执行结果：\n" + result_text if first_turn["status"] == "completed" else
            "委托首轮执行超时；原 Goal 和回执已保留，请查看状态后决定恢复。" if first_turn["status"] == "timed_out" else
            "委托首轮执行未完成；原 Goal 和回执已保留，请查看状态后决定恢复。"
        )
        if proposal_id := native.get("proposal_id"):
            result_text += f"\n如需恢复暂停或额度受限的原执行：/resume-commission {proposal_id} --tokens N（N 为包含历史用量的总上限，须大于已用量；不会重开线程）。"
        return self._deliver(path, record, "commission_result", result_text, inbox=inbox)

    def reconcile_request(self, path: Path) -> int:
        if path.parent == self.root / "web-projections":
            return self._reconcile_web_projection(path)
        row = _read_json(path)
        if "triage" in row:
            return triage.reconcile(self, path)
        if private_mention_topics.reconcile_rejection(self, path, row):
            return 0
        historical_images = _mapping(row.get("historical_answer_images"))
        if (row.get("status") == "delivered" and (
                historical_images.get("status") == "pending"
                or (row.get("historical_answer_images") is None and (self.root / "historical-answer-images" / path.name).is_file()))):
            if row.get("historical_answer_images") is None:
                self._historical_image_probe_at[path] = time.monotonic() + 60
            return self._reconcile_historical_answer_images(path)
        if answer_image_retry_waits(row, datetime.now(timezone.utc).timestamp()):
            return 0
        record_event_timing(_str_field(row, "profile"), _str_field(_mapping(row["event"]), "message_id"), "reconciliation_started")
        if row["status"] == "delivered" and row.get("session_id") and row.get("turn_id"):
            self._context_recovery_probe_at[path] = time.monotonic() + 60
        if row["status"] == "delivered" and row.get("context_recovery"):
            self._repair_recovery_presentation(path)
            if not self.core.context_recovery_candidate(_str_field(row, "request_ref")):
                return 0
        row, early = self._reconcile_context_recovery(path, row)
        if early is not None:
            return early
        if row["status"] == "captured":
            row, early = self._reconcile_captured_request(path, row)
            if early is not None:
                return early
        native_path = self.core.root / f"{_str_field(row, 'request_ref')}.json"
        if native_path.exists():
            self._recover_request_with_feedback(path, row)
        with exclusive_file_lock(path, operation="deliver_private_chat_request"):
            record = _read_json(path)
            if record["status"] in {"captured", "delivered"}:
                return 0
            config: Path | None = None
            selected: Payload | None = None
            control_receipt_first = False
            native: PayloadView = {}

            def inbox() -> Path:
                nonlocal config
                if config is None:
                    obs = _mapping(selected["observation"]) if selected else None
                    config = self._inbox(record, observation=obs, reuse_display_name=control_receipt_first and selected is None)
                return config

            try:
                request_ref, binding_id = _str_field(record, "request_ref"), _str_field(record, "binding_id")
                source = _mapping(record["source"])
                if record["status"] in {"command_queued", "command_completed", "commission_running"}:
                    native = self.core.read_request(request_ref)
                    if native["status"] == "command_queued":
                        selected = self._resolve_binding(binding_id, source, include_observation=True)
                        self._feedback(path, record, inbox=inbox)
                        self._deliver(path, record, "admission", _str_field(native, "response"), inbox=inbox)
                        return 0
                    record.update(
                        status=native["status"], response=native.get("response"),
                        commission_resources=native.get("commission_resources"), status_snapshot=native.get("status_snapshot"),
                    )
                    control_receipt_first = (
                        native["status"] == "command_completed" and native.get("command") in CHAT_CONTROL_COMMANDS.values()
                    )
                if not (control_receipt_first and native.get("command") == "stop"
                        and native.get("target_recorded") is True and source.get("group_human_message") is True
                        and not _mapping(_mapping(record["deliveries"]).get("terminal")).get("verified")
                        and _same_fields(native, record, "request_ref", "binding_id", "source")):
                    selected = self._resolve_binding(binding_id, source, include_observation=True)
                completed_answer = False
                answer_image_plan: Sequence[str | PayloadView | None] | None = None
                if record["status"] == "accepted":
                    native = self.core.read_request(request_ref)
                    assert selected is not None
                    resolved = self._resolve_accepted_response(path, record, selected, native, inbox)
                    if resolved is None:
                        return 0
                    response, completed_answer, answer_image_plan = resolved
                else:
                    if record["status"] != "rejected" and not control_receipt_first:
                        self._feedback(path, record, inbox=inbox)
                    response = (
                        str(record.get("attachment_notice") or "")
                        or _command_text(str(record.get("response_code") or ""))
                        or str(record.get("response") or "")
                    )
                    if record.get("status_snapshot"):
                        response = _status_text(_req_dict(record, "status_snapshot"), help_requested=native.get("command") == "help")
                delivered = (
                    self._deliver_completed_answer(path, record, response, inbox=inbox, answer_image_plan=answer_image_plan)
                    if completed_answer else self._deliver(path, record, "terminal", response, inbox=inbox)
                )
                if delivered:
                    if control_receipt_first:
                        self._feedback(path, record, inbox=inbox)
                    if not self._deliver_commission_followup(path, record, native, inbox):
                        return 0
                    config = inbox()
                    acknowledge_lark_event_inbox(
                        project=self.runtime_root, config_path=config,
                        message_ids=[_str_field(_mapping(record["event"]), "message_id")], execute=True,
                    )
                    if native_path.exists():
                        sid, tid = record.get("session_id"), record.get("turn_id")
                        self.core.record_delivery(
                            request_ref,
                            session_id=sid if isinstance(sid, str) else None,
                            turn_id=tid if isinstance(tid, str) else None,
                            message_text=response,
                        )
                    elif record["status"] != "rejected":
                        raise ValueError("delivery request journal unavailable")
                    record["status"] = "delivered"
                    _atomic_write_json(path, record)
                    return 1
                if (control_receipt_first and selected is None
                        and _mapping(_mapping(record["deliveries"]).get("terminal")).get("blocker")
                        == "lark_inbox_reply_sender_identity_mismatch"):
                    self._inbox(record)
            except (KeyError, ValueError, OSError):
                return 0
        return 0
