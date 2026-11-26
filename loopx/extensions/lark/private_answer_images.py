"""Answer-image follow-up and historical image repair delivery for private Lark chat."""
from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from datetime import timezone
import hashlib
import http.client
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ...capabilities.native_chat.file_discovery import reply_image_workspace
from ...chat.attachments import CHAT_IMAGE_MAX_COUNT
from ...file_lock import exclusive_file_lock
from .cli_transport import CommandRunner
from .goal_channel_transport import call, json_payload, lark_args
from .inbox_reactions import _reconcile_outbound_input_reactions
from .inbox_reply import SourceAuthorityVerifier, _message
from .outbound import normalize_lark_outbound_text
from .private_delivery_discovery import (
    answer_image_retry_delay,
    answer_image_retry_waits,
    answer_image_source_matches,
    historical_image_reply_matches,
    historical_image_source,
    load_historical_image_plan,
    private_answer_images,
    remote_answer_image_urls_plausible,
)
from .private_source_verification import Payload, PayloadView, _ensure_dict, _mapping, _pc, _str_field
from .private_web_projection import _same_fields
from .return_files import read_answer_images

if TYPE_CHECKING:
    from ...capabilities.native_chat.conversation_bindings import ChatConversationBindings
    from ...capabilities.native_chat.external_conversations import ChatExternalConversations

ANSWER_IMAGE_RETRY_SECONDS = 300
ANSWER_IMAGE_SOURCE_TIMEOUT = 5


def _bump_image_retry(path: Path, record: Payload, state: Payload, now: float) -> None:
    raw_count = state.get("retry_count")
    retry_count = (int(raw_count) if isinstance(raw_count, int) and not isinstance(raw_count, bool) else 0) + 1
    state.update(
        status="pending",
        retry_count=retry_count,
        next_check_at=now + answer_image_retry_delay(retry_count, base_seconds=ANSWER_IMAGE_RETRY_SECONDS),
    )
    _pc()._atomic_write_json(path, record)


def _mark_image_delivery_complete(path: Path, record: Payload, state: Payload) -> None:
    state.update(status="delivered")
    state.pop("attachments", None)
    state.pop("retry_count", None)
    _pc()._atomic_write_json(path, record)


class PrivateAnswerImageMixin:
    """Deferred answer-image verification and historical repair delivery."""

    if TYPE_CHECKING:
        root: Path
        runtime_root: Path
        cli_bin: str
        runner: CommandRunner
        reaction_feedback: bool
        core: ChatExternalConversations
        bindings: ChatConversationBindings
        _planned_repairs_stamp: tuple[bool, int | None] | None
        _planned_repairs_names: frozenset[str]

        def _inbox(
            self, record: Payload, *, observation: PayloadView | None = None, reuse_display_name: bool = False,
        ) -> Path: ...
        def _reply_runner(self, args: Sequence[str]) -> PayloadView: ...
        def _source_authority(
            self, record: Payload, profile: str, chat_id: str, message_id: str, *, expected_text: str | None = None,
        ) -> tuple[str | None, bool]: ...
        def _original_message_pair(
            self, profile: str, message_id: str, other_id: str, *, runner: CommandRunner | None = None,
        ) -> tuple[PayloadView | None, PayloadView | None]: ...
        def _resolve_binding(
            self, binding_id: str, source: PayloadView, *, include_observation: bool = False,
            session: PayloadView | None = None, session_context: PayloadView | None = None,
        ) -> Payload: ...
        def _deliver(
            self, path: Path, record: Payload, phase: str, text: str, *, inbox: Callable[[], Path],
            edit_only: bool = False, authorization_check: Callable[[], bool] | None = None,
            source_authority_verifier: SourceAuthorityVerifier | None = None,
            reply_placement: str | None = None, delivery_id: str | None = None,
            finalize_source: bool = True, image_attachments: Sequence[Payload] | None = None,
            historical_source_check: Callable[[], bool] | None = None,
        ) -> bool: ...

    def _read_answer_image_source(self, args: list[str], cwd: Path | None, _timeout: float | None) -> Payload:
        return self.runner(args, cwd, ANSWER_IMAGE_SOURCE_TIMEOUT)

    def _ensure_answer_images(
        self, path: Path, record: Payload, phase: str, origin: str,
        targets: Sequence[str | PayloadView | None], state: Payload, *,
        verification_key: str = "source_urls_verified",
        local_scope: tuple[Path, str] | None | Callable[[], tuple[Path, str] | None] = None,
        expected_digests: list[str] | None = None,
    ) -> list[Payload] | None:
        raw_images = state.get("attachments")
        images: list[Payload] | None = (
            [dict(item) for item in raw_images if isinstance(item, Mapping)]
            if isinstance(raw_images, list) else None
        )
        recheck = images is None or state.get(verification_key) is not True
        if recheck:
            scope = local_scope() if callable(local_scope) else local_scope
            raw_targets: list[str | dict[str, object] | None] = [
                dict(target) if isinstance(target, Mapping) else target for target in targets
            ]
            deliveries = _mapping(record.get("deliveries"))
            phase_delivery = _mapping(deliveries.get(phase))
            images = cast(
                list[Payload],
                read_answer_images(origin, raw_targets, images, phase_delivery, local_scope=scope),
            )
        if images is None:
            return None
        if expected_digests is not None and [
            hashlib.sha256(base64.b64decode(_str_field(pic, "data_url").split(",", 1)[1], validate=True)).hexdigest()
            for pic in images
        ] != expected_digests:
            return None
        if recheck:
            state.update(attachments=images, **{verification_key: True})
            _pc()._atomic_write_json(path, record)
        return images

    def _deliver_completed_answer(
        self, path: Path, record: Payload, text: str, *, inbox: Callable[[], Path],
        answer_image_plan: Sequence[str | PayloadView | None] | None = None,
    ) -> bool:
        from .private_progress import presentation_phase

        phase_name = presentation_phase(record, "terminal")
        media_phase = "answer_images." + phase_name
        deliveries = _ensure_dict(record, "deliveries")
        old = _mapping(deliveries.get(phase_name))
        event = _mapping(record.get("event"))
        source = _mapping(record.get("source"))
        if ((old.get("started") and not record.get("answer_images"))
                or event.get("chat_type") != "p2p"
                or source.get("private_human_message") is not True):
            return self._deliver(path, record, "terminal", text, inbox=inbox)
        body, remote_targets = private_answer_images(text)
        if not remote_targets and answer_image_plan is None:
            return self._deliver(path, record, "terminal", text, inbox=inbox)
        targets: Sequence[str | PayloadView | None] = remote_targets if answer_image_plan is None else answer_image_plan
        pc = _pc()

        def local_scope(session: PayloadView | None, turn: PayloadView | None) -> tuple[Path, str] | None:
            if session is None or turn is None:
                return None
            workspace = reply_image_workspace(dict(session), dict(turn))
            if workspace is None:
                return None
            project_ref = _mapping(session.get("project_context")).get("project_ref")
            current = self.core.controller.project_contexts.available()
            if any(row["project_ref"] == project_ref and row["workspace_path"] == str(workspace)
                   and row["grant"] == "workspace_write" for row in current):
                return workspace, _str_field(turn, "turn_id")
            return None

        def authorized() -> bool:
            request_ref = _str_field(record, "request_ref")
            native = self.core.read_request(request_ref)
            same_request = _same_fields(native, record, "binding_id", "source", "session_id", "turn_id")
            if native.get("status") != "accepted" or not same_request:
                return False
            turn = self.core.controller.store.load_turn(_str_field(native, "session_id"), _str_field(native, "turn_id"))
            session = pc.resumable_answer_image_session(self.core, native, turn)
            return session is not None and (answer_image_plan is None or (
                local_scope(session, turn) is not None
                and _mapping(_mapping(turn).get("response")).get("answer_image_plan") == list(answer_image_plan)))

        state = _ensure_dict(record, "answer_images")
        # Hold the source and the exact completed body fixed across retries.
        digest = hashlib.sha256(text.encode()).hexdigest()
        if state.setdefault("answer_sha256", digest) != digest:
            return False
        if answer_image_plan is not None:
            plan_digest = hashlib.sha256(json.dumps(list(answer_image_plan), sort_keys=True).encode()).hexdigest()
            if state.setdefault("image_plan_sha256", plan_digest) != plan_digest:
                return False
        if old.get("verified") is not True:
            state.setdefault("status", "pending")
            pc._atomic_write_json(path, record)
            caption = ("图片将单独核验和发送。" if answer_image_plan is not None
                       else "原图会另发；有链接的图名可打开原始来源。")
            self._deliver(path, record, "terminal", body + "\n\n" + caption, inbox=inbox, finalize_source=False)
            # Release the body worker before the pending image work.
            return False
        if state.get("status") == "delivered" and _mapping(deliveries.get(media_phase)).get("images_verified") is True:
            return True
        now = pc.datetime.now(timezone.utc).timestamp()
        if answer_image_retry_waits(record, now):
            return False
        _bump_image_retry(path, record, state, now)
        try:
            origin = event.get("content")
            if (not isinstance(targets, Sequence) or isinstance(targets, (str, bytes))
                    or not 1 <= len(targets) <= CHAT_IMAGE_MAX_COUNT
                    or event.get("message_type") != "text" or not isinstance(origin, str)):
                raise ValueError("answer image source unavailable or not authorized")
            if answer_image_plan is None and not remote_answer_image_urls_plausible(origin, list(targets)):
                raise ValueError("answer image targets are not verifiable remote URLs")
            profile = _str_field(record, "profile")
            chat_id = _str_field(event, "chat_id")
            message_id = _str_field(event, "message_id")
            if not authorized() or not self._source_authority(record, profile, chat_id, message_id, expected_text=origin)[1]:
                raise ValueError("answer image source unavailable or not authorized")
            if answer_image_plan is not None and (
                    None in targets or [target for target in targets if isinstance(target, str)] != remote_targets):
                raise ValueError("the completed answer contains an unavailable image")

            def plan_scope() -> tuple[Path, str] | None:
                if answer_image_plan is None:
                    return None
                store = self.core.controller.store
                sid, tid = _str_field(record, "session_id"), _str_field(record, "turn_id")
                return local_scope(store.load_session(sid), store.load_turn(sid, tid))

            images = self._ensure_answer_images(path, record, media_phase, origin, targets, state, local_scope=plan_scope)
            if images is None:
                raise ValueError("answer images unavailable")
            if self._deliver(path, record, media_phase, "原图（按正文顺序）", inbox=inbox,
                             authorization_check=authorized, finalize_source=False, image_attachments=images):
                if self.reaction_feedback and not _reconcile_outbound_input_reactions(
                        project=self.runtime_root, config_path=inbox(), message_id=message_id,
                        processing=False, complete=True, before_write=authorized, runner=self._reply_runner).get("ok"):
                    return False
                _mark_image_delivery_complete(path, record, state)
                return True
        except (KeyError, ValueError, OSError, http.client.HTTPException):
            pass
        state["status"] = "unverified"
        pc._atomic_write_json(path, record)
        notice_phase = "answer_image_notice." + phase_name
        if _mapping(deliveries.get(notice_phase)).get("verified") is not True:
            notice = ("正文已送达，图片尚未核验送达。"
                      if answer_image_plan is not None else "正文已送达，图片尚未核验送达。有链接的图名可打开原始来源。")
            self._deliver(path, record, notice_phase, notice,
                          inbox=inbox, authorization_check=authorized, finalize_source=False)
        return False

    def _load_historical_image_plan(self, path: Path, record: Payload) -> Payload | None:
        had_state = record.get("historical_answer_images") is not None
        state = load_historical_image_plan(self.root, self.core, path, record)
        if state is not None and not had_state:
            _pc()._atomic_write_json(path, record)
        return state

    def _reconcile_historical_answer_images(self, path: Path) -> int:
        pc = _pc()
        with exclusive_file_lock(path, operation="deliver_private_chat_request"):
            record = pc._read_json(path)
            try:
                state = self._load_historical_image_plan(path, record)
            except (KeyError, ValueError, OSError):
                return 0
            now = pc.datetime.now(timezone.utc).timestamp()
            next_check = state.get("next_check_at") if state else None
            next_check_ts = float(next_check) if isinstance(next_check, (int, float)) else 0.0
            if not state or state.get("status") != "pending" or next_check_ts > now:
                return 0
            _bump_image_retry(path, record, state, now)
            plan = _mapping(state.get("plan"))
            try:
                session = historical_image_source(self.core, record, plan)
                source = _mapping(record.get("source"))
                raw_ctx = session.get("project_context") or session.get("steward_context")
                session_ctx = raw_ctx if isinstance(raw_ctx, Mapping) else None
                selected = self._resolve_binding(
                    _str_field(plan, "binding_id"), source,
                    include_observation=True, session=session, session_context=session_ctx,
                )
                event = _mapping(record.get("event"))
                observation = _mapping(selected.get("observation"))
                if _mapping(selected.get("binding")).get("transport_ref") != record.get("profile"):
                    return 0
                profile = _str_field(record, "profile")
                message_id = _str_field(event, "message_id")
                chat_id = _str_field(event, "chat_id")
                plan_message_id = _str_field(plan, "message_id")
                terminal_text = _str_field(_mapping(_mapping(record.get("deliveries")).get("terminal")), "text")
                original_text = normalize_lark_outbound_text(terminal_text, limit=None, preserve_format=True)

                def original_pair_matches() -> bool:
                    source_msg, reply_msg = self._original_message_pair(
                        profile, message_id, plan_message_id, runner=self._read_answer_image_source,
                    )
                    return answer_image_source_matches(source_msg, event) and historical_image_reply_matches(
                        reply_msg, chat_id, observation, original_text,
                    )

                if not original_pair_matches():
                    return 0
                phase = "answer_images.historical"
                raw_snapshots = plan.get("captured_images")
                targets: Sequence[str | PayloadView | None]
                if raw_snapshots is None:
                    raw_images = plan.get("images")
                    if not isinstance(raw_images, list):
                        return 0
                    images = [_mapping(item) for item in raw_images]
                    targets = [_str_field(item, "url") for item in images]
                    local_scope = None
                    verification_key = "source_urls_verified"
                else:
                    if not isinstance(raw_snapshots, list):
                        return 0
                    # The operator stages the exact Turn's snapshots in this private host
                    # directory. Paths from the project and model text are never opened here.
                    images = [_mapping(item) for item in raw_snapshots]
                    targets = images
                    local_scope = (self.root / "historical-answer-images", _str_field(plan, "turn_id"))
                    verification_key = "captured_images_verified"
                attachments = self._ensure_answer_images(
                    path, record, phase, _str_field(event, "content"), targets, state,
                    verification_key=verification_key, local_scope=local_scope,
                    expected_digests=[_str_field(item, "sha256") for item in images],
                )
                if attachments is None:
                    return 0

                def authorized() -> bool:
                    try:
                        return (selected["binding"] in self.bindings.read()["bindings"]
                                and bool(historical_image_source(self.core, record, plan)))
                    except (KeyError, OSError, ValueError):
                        return False

                if not self._deliver(path, record, phase, "原图（按原文顺序）",
                                     inbox=lambda: self._inbox(record, observation=observation),
                                     authorization_check=authorized, historical_source_check=original_pair_matches,
                                     finalize_source=False, reply_placement="chat_root", image_attachments=attachments):
                    return 0
                media = _mapping(_mapping(record.get("deliveries")).get(phase))
                sent_ref = _str_field(_mapping(media.get("attempt")), "message_ref")
                output = call(self.runner, lark_args(cli_bin=self.cli_bin, profile=profile, tail=[
                    "im", "+messages-mget", "--message-ids", sent_ref, "--as", "bot", "--no-reactions", "--format", "json",
                ]))
                sent = _message(json_payload(output), sent_ref) if output.get("returncode") == 0 else None
                raw_keys = media.get("image_keys")
                keys = tuple(str(k) for k in raw_keys) if isinstance(raw_keys, list) else ()
                if not historical_image_reply_matches(sent, chat_id, observation, _str_field(media, "text"), keys):
                    return 0
            except (KeyError, ValueError, OSError, http.client.HTTPException):
                return 0
            _mark_image_delivery_complete(path, record, state)
            return 1

    def _planned_historical_repairs(self) -> frozenset[str]:
        repairs_dir = self.root / "historical-answer-images"
        try:
            stamp: tuple[bool, int | None] = (True, repairs_dir.stat().st_mtime_ns)
        except OSError:
            stamp = (False, None)
        if stamp == getattr(self, "_planned_repairs_stamp", None):
            cached = getattr(self, "_planned_repairs_names", None)
            if isinstance(cached, frozenset):
                return cached
        if not stamp[0]:
            names: frozenset[str] = frozenset()
        else:
            names = frozenset(item.name for item in repairs_dir.glob("*.json"))
            try:
                stamp = (True, repairs_dir.stat().st_mtime_ns)
            except OSError:
                stamp = (False, None)
                names = frozenset()
        self._planned_repairs_stamp = stamp
        self._planned_repairs_names = names
        return names
