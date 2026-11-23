"""Return a worker's audience-ready conclusion through the original Lark inbox."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
import re
from typing import TYPE_CHECKING, Protocol, cast

from ...capabilities.manager_context import authority
from ...capabilities.manager_context.roundtrip import ReturnResolutionBlocked, ReturnTransportUnavailable
from ...chat_store import _atomic_write_json
from ...file_lock import exclusive_file_lock
from .conversation_identity import identity_ref
from .event_inbox import _load_processed, load_lark_event_inbox_config
from .goal_channel_contracts import bindings_for_goal
from .goal_channel_targets import goal_channel_target_for_name
from .goal_channel_transport import json_payload
from .goal_topic_runtime import _inbox_config
from .identity_shapes import LARK_MESSAGE_ID_PATTERN
from .inbox_reply import (
    CommandRunner, _bot_identity_verified, _call, _default_runner, _message, _verified_reply_result,
    reply_lark_event_inbox, verify_lark_inbox_reply,
)
from .manager_routing import authorized_manager_goal_ids
from .outbound import present_lark_conversation_text
from .post_delivery import attachment_reply
from .return_files import uploaded_result_files, verify_result_files

if TYPE_CHECKING:
    from ...capabilities.manager_context.roundtrip import ReturnService
    from ...chat_store import ChatSessionStore

Payload = dict[str, object]
PayloadView = Mapping[str, object]
AttemptRecord = Mapping[str, str | None]
AttemptRecorder = Callable[[AttemptRecord], None]
SnapshotProvider = Callable[[], PayloadView]

if TYPE_CHECKING:
    class _ControllerProtocol(Protocol):
        store: ChatSessionStore

    class _CoreProtocol(Protocol):
        controller: _ControllerProtocol

    class _PrivateTransport(Protocol):
        root: Path
        runtime_root: Path
        core: _CoreProtocol
        def _reply_runner(self, args: Sequence[str]) -> Mapping[str, object]: ...
        def _read_web_projection_journal(self, path: Path) -> Payload: ...
        def _web_projection_messages(self, row: Payload) -> list[Payload]: ...
        def _web_projection_delivery_id(self, row: Payload, message: Payload) -> str: ...
        def _inbox(self, record: Payload) -> Path: ...
        def _source_authority(self, row: Payload, profile: str, chat: str, ref: str) -> tuple[str | None, bool]: ...
        def return_inbox(self, *, route: Payload, session: Payload, turn: Payload) -> tuple[Path, Payload]: ...
        def _source_verified(self, record: Payload) -> bool: ...

    class _SnapshotRuntime(Protocol):
        snapshot_provider: SnapshotProvider

    class _ReturnServer(Protocol):
        registry_path: Path
        lark_goal_topic_runtime: _SnapshotRuntime

    class _ReturnServiceServer(_ReturnServer, Protocol):
        chat_store: object

    class _CompositionTransport(Protocol):
        default_return: object
        cancelled: Callable[[], bool]


def _mapping(value: object) -> PayloadView:
    return value if isinstance(value, Mapping) else {}


def _str_field(mapping: PayloadView, key: str) -> str:
    value = mapping[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _blocked(reason: str, message: str) -> ReturnResolutionBlocked:
    return cast(Callable[[str, str], ReturnResolutionBlocked], ReturnResolutionBlocked)(reason, message)


def _grant_authorized(root: Path, registry: Path, route: PayloadView, session: PayloadView, turn: PayloadView) -> bool:
    grant = authority(root, registry, dict(session), dict(turn))
    targets = grant["targets"]
    return (isinstance(targets, list) and {k: route[k] for k in ("goal_id", "agent_id")} in targets
            and grant.get("source_id") == route["source_id"])


def _resolve_return(
    root: Path, registry: Path, snapshot_provider: SnapshotProvider,
    route: PayloadView, session: PayloadView, turn: PayloadView, cancelled: Callable[[], bool],
) -> tuple[PayloadView, Payload, Payload]:
    if cancelled():
        raise ValueError("manager return service stopped")
    snapshot = snapshot_provider()
    # Validate live binding/session independently of the worker's target Goal.
    # Delegation grants permit only this request's audience-ready reply.
    if not authorized_manager_goal_ids(snapshot, session, runtime_root=root, registry_path=registry):
        raise _blocked("return_authorization_unavailable", "manager connection no longer authorized")
    if not _grant_authorized(root, registry, route, session, turn):
        raise _blocked("return_authorization_unavailable", "context return authority revoked")
    matches = [
        binding for gid, payload in _mapping(snapshot.get("binding_payloads")).items()
        if isinstance(payload, Mapping)
        for binding in bindings_for_goal(payload, gid)
        if binding.get("enabled") is True and binding.get("session_id") == route["session_id"]
        and _mapping(binding.get("routing")).get("conversation_kind") == "manager"
    ]
    if len(matches) != 1:
        raise _blocked("original_route_unavailable", "manager return binding ambiguous")
    binding = matches[0]
    target_ref = _str_field(binding, "target_ref")
    target_config = goal_channel_target_for_name(_mapping(snapshot["target_payload"]), target_ref)
    if not target_config or target_config.get("enabled") is not True:
        raise _blocked("original_route_unavailable", "manager return target disabled")
    routing: Payload = {
        "target_ref": target_ref, "conversation_kind": "manager",
        "app_ref": _mapping(target_config.get("identity")).get("sender_profile") or "default",
        "topic_root_message_id": (_mapping(binding.get("topic")).get("root_message_id")
                                  or _mapping(binding.get("channel")).get("pinned_message_id") or ""),
    }
    return snapshot, routing, target_config


def _private_return_authority(
    root: Path, registry: Path, route: PayloadView, session: PayloadView, turn: PayloadView,
    cancelled: Callable[[], bool], private_transport: _PrivateTransport | None,
) -> _PrivateTransport:
    if cancelled():
        raise ValueError("manager return service stopped")
    if not _grant_authorized(root, registry, route, session, turn) or private_transport is None:
        raise _blocked("return_authorization_unavailable", "private return authority unavailable")
    return private_transport


def _private_message_link(link: object, chat_id: str) -> bool:
    return bool(chat_id) and isinstance(link, str) and re.fullmatch(
        r"https://applink\.(?:feishu\.cn|larksuite\.com)/client/chat/open"
        r"\?openChatId=" + re.escape(chat_id) + r"&position=-?[0-9]+", link,
    ) is not None


def _app_mirror_text(message: PayloadView, phase: PayloadView, chat_id: str) -> str:
    user_input = message["role"] == "user"
    speaker = "你" if user_input else "LoopX"
    text = _str_field(message, "text")
    link = phase.get("question_link")
    if link is not None:
        if user_input or not _private_message_link(link, chat_id):
            raise ValueError("App mirror question link changed")
        text = f"[查看对应提问]({link})\n\n{text}"
    return f"**{speaker}（App）**\n\n{text}"


def prepare_app_mirror_text(
    transport: _PrivateTransport, path: Path, row: Payload, messages: Sequence[PayloadView], message: PayloadView,
) -> str:
    """Attach a provider-supplied link before the first answer attempt."""
    deliveries = cast(dict[str, Payload], row["deliveries"])
    key = "web." + _str_field(message, "message_id")
    phase = deliveries.get(key, {})
    chat = _str_field(_mapping(row["event"]), "chat_id")
    if message["role"] != "user" and not {"question_link", "text", "started", "attempt"}.intersection(phase):
        inputs = [item for item in messages if item["role"] == "user" and item["turn_id"] == message["turn_id"]]
        if len(inputs) == 1:
            original = deliveries.get("web." + _str_field(inputs[0], "message_id"), {})
            link = _app_input_link(transport, row, original, chat)
            if link is not None:
                # Retain the chosen link before provider preflight. Recovery
                # reproduces the same text even if the link later disappears.
                phase["question_link"] = link
                deliveries[key] = phase
                _atomic_write_json(path, row)
    return _app_mirror_text(message, phase, chat)


def _app_input_link(transport: _PrivateTransport, row: Payload, original: PayloadView, chat: str) -> str | None:
    if original.get("verified") is not True or not isinstance(original.get("attempt"), Mapping):
        return None
    try:
        _, attempt = cast(Callable[..., tuple[str, object]], attachment_reply)(
            project=transport.runtime_root, config_path=transport._inbox(row),
            text=_str_field(original, "text"), attempt=original["attempt"])
        if not isinstance(attempt, Mapping):
            return None
        ref = _str_field(attempt, "message_ref")
    except (LookupError, OSError, TypeError, ValueError):
        return None
    if not LARK_MESSAGE_ID_PATTERN.fullmatch(ref):
        return None
    result = _call(transport._reply_runner, ["lark-cli", "--profile", _str_field(row, "profile"),
        "im", "+messages-mget", "--message-ids", ref, "--as", "bot", "--format", "json", "--no-reactions"])
    payload = json_payload(result)
    observed = _message(payload, ref) if result.get("returncode") == 0 and payload.get("ok") is True else None
    if (observed is None or observed.get("chat_id") != chat or observed.get("deleted") is True
            or _mapping(observed.get("sender")).get("sender_type") != "app"):
        return None
    link = observed.get("message_app_link")
    return link if isinstance(link, str) and _private_message_link(link, chat) else None


def _verify_web_mirror(
    route: PayloadView, session: PayloadView, turn: PayloadView, text: str,
    private_transport: _PrivateTransport, attempt: AttemptRecord | None, record_attempt: AttemptRecorder | None,
) -> Payload:
    """Read an existing App mirror; its original worker alone owns sends.

    Manager-context supplies its typed canonical result id and owns result
    settlement. Neither a matching body nor a historical verified flag is
    sufficient: revalidate the source and read the exact provider attempt.
    """
    saved = session["steward_context"]
    if not isinstance(saved, Mapping):
        raise ValueError("original App return identity or media unavailable")
    session_id, turn_id = _str_field(session, "session_id"), _str_field(turn, "turn_id")
    path = private_transport.root / "web-projections" / f"{identity_ref(_str_field(saved, 'binding_id'), session_id)}.json"
    if not path.is_file():
        raise _blocked("initial_delivery_receipt_unavailable", "App mirror not yet recorded")
    with exclusive_file_lock(path, operation="verify_web_conversation_return"):
        row = private_transport._read_web_projection_journal(path)
        canonical = private_transport.core.controller.store.load_turn(session_id, turn_id)
        if (route.get("session_id") != session_id or row.get("session_id") != session_id
                or not canonical or canonical.get("origin") != "web"
                or canonical.get("client_turn_id") != turn.get("client_turn_id") or route.get("result_attachments")):
            raise ValueError("original App return identity or media unavailable")
        message = next((item for item in private_transport._web_projection_messages(row)
                        if item["message_id"] == route.get("return_message_id")), None)
        if (not message or message.get("origin") != "manager_followup" or message.get("role") != "agent"
                or message.get("turn_id") != turn_id
                or present_lark_conversation_text(_str_field(message, "text")) != text or message.get("attachments")):
            raise ValueError("canonical App result changed")
        phase = _mapping(_mapping(row["deliveries"]).get("web." + _str_field(message, "message_id")))
        mirrored = phase.get("attempt")
        if phase.get("verified") is not True or not isinstance(mirrored, dict):
            raise _blocked("initial_delivery_receipt_unavailable", "App mirror remains unverified")
        expected = _app_mirror_text(message, phase, _str_field(_mapping(row["event"]), "chat_id"))
        phase_text = phase.get("text")
        if (not isinstance(phase_text, str) or phase_text not in (expected, present_lark_conversation_text(expected))
                or (attempt is not None and dict(attempt) != mirrored)):
            raise ValueError("App mirror intent changed")
        if attempt is None and record_attempt is not None:
            # Persist the original locator before IO, so a crash or outage
            # resumes verification rather than starting another delivery.
            record_attempt(dict(mirrored))
        result = verify_lark_inbox_reply(
            project=private_transport.runtime_root, config_path=private_transport._inbox(row),
            message_id=_str_field(_mapping(row["event"]), "message_id"), text=phase_text, attempt=mirrored,
            reply_placement="chat_root", delivery_id=private_transport._web_projection_delivery_id(row, message),
            runner=private_transport._reply_runner, finalize_reactions=False, resume_parts=False,
            source_authority_verifier=lambda profile, chat, ref: private_transport._source_authority(row, profile, chat, ref),
        )
        if message not in private_transport._web_projection_messages(row):
            raise ValueError("canonical App result changed during readback")
        return {**result, "idempotency_key": mirrored["provider_receipt"], "external_write_performed": False}


def _web_return(
    root: Path, registry: Path, route: PayloadView, session: PayloadView, turn: PayloadView,
    text: str, cancelled: Callable[[], bool], private_transport: _PrivateTransport | None,
    attempt: AttemptRecord | None = None, record_attempt: AttemptRecorder | None = None,
) -> Payload:
    transport = _private_return_authority(root, registry, route, session, turn, cancelled, private_transport)
    try:
        result = _verify_web_mirror(route, session, turn, text, transport, attempt, record_attempt)
    except ReturnResolutionBlocked:
        raise
    except (KeyError, ValueError, OSError) as exc:
        raise _blocked("original_route_unavailable", "original App return source unavailable") from exc
    # Provider readback can outlast a grant or a stop. It grants no publication
    # authority and must not mark a revoked request as delivered.
    _private_return_authority(root, registry, route, session, turn, cancelled, private_transport)
    return result


def _return_inbox(
    root: Path, registry: Path, snapshot_provider: SnapshotProvider, route: PayloadView,
    session: PayloadView, turn: PayloadView, cancelled: Callable[[], bool],
    private_transport: _PrivateTransport | None,
) -> tuple[Path, Path, Callable[[], bool] | None, object, str]:
    project, source_verifier, destination = root, cast("Callable[[], bool] | None", None), cast(object, None)
    if isinstance(session.get("steward_context"), dict):
        transport = _private_return_authority(root, registry, route, session, turn, cancelled, private_transport)
        try:
            config_path, record = transport.return_inbox(route=dict(route), session=dict(session), turn=dict(turn))
            # The original Inbox belongs to the private Chat transport. Its
            # relative paths must not be resolved against the coordination
            # root, which continues to own grants and returned-result state.
            project = transport.runtime_root
        except (KeyError, ValueError, OSError) as exc:
            raise _blocked("original_route_unavailable", "original private source unavailable") from exc

        def verify_source() -> bool:
            return bool(transport._source_verified(record))

        source_verifier = verify_source
        destination = {key: record[key] for key in ("profile", "binding_id", "source", "event")}
        message_id = _str_field(_mapping(record["event"]), "message_id")
    else:
        snapshot, routing, target_config = _resolve_return(
            root, registry, snapshot_provider, route, session, turn, cancelled
        )
        config_path, _ = _inbox_config(runtime_root=root, route=routing, target_payload=_mapping(snapshot["target_payload"]))
        destination, message_id = (routing, target_config), _str_field(route, "source_id").removeprefix("lark:")
    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    if message_id not in _load_processed(cast(Path, config["inbox_path"]) / "processed.json"):
        raise _blocked("initial_delivery_receipt_unavailable", "initial reply has not been acknowledged")
    return project, config_path, source_verifier, destination, message_id


def _run_inbox_return(
    root: Path, registry: Path, snapshot_provider: SnapshotProvider, route: PayloadView,
    session: PayloadView, turn: PayloadView, text: str, runner: CommandRunner | None,
    cancelled: Callable[[], bool], private_transport: _PrivateTransport | None,
    attempt: AttemptRecord | None = None, delivery_attempt_recorder: AttemptRecorder | None = None,
) -> Payload:
    verify_only = attempt is not None
    text, steward_context = present_lark_conversation_text(text), session.get("steward_context")
    if not verify_only and isinstance(steward_context, dict) and turn.get("origin") == "native_loopx":
        _private_return_authority(root, registry, route, session, turn, cancelled, private_transport)
        raise ReturnTransportUnavailable("scheduled Steward result has no private reply transport")
    if isinstance(steward_context, dict) and turn.get("origin") == "web":
        result = _web_return(
            root, registry, route, session, turn, text, cancelled, private_transport,
            attempt=attempt, record_attempt=None if verify_only else delivery_attempt_recorder,
        )
        return result if verify_only else {**result, "verification": result, "external_write_performed": False}

    def resolve() -> tuple[Path, Path, Callable[[], bool] | None, object, str]:
        return _return_inbox(root, registry, snapshot_provider, route, session, turn, cancelled, private_transport)

    project, config_path, source_verifier, destination, message_id = resolve()
    if runner is None and isinstance(steward_context, dict) and private_transport is not None:
        runner = private_transport._reply_runner

    def before_send(_intent: str) -> dict[str, bool]:
        current = resolve()
        return {"continue_delivery": current[:2] == (project, config_path) and current[3:] == (destination, message_id)}

    raw_attachments = route.get("result_attachments")
    attachments = [item for item in raw_attachments if isinstance(item, Mapping)] if isinstance(raw_attachments, (list, tuple)) else []
    keys: tuple[str, ...] = ()
    sender_profile = ""
    if attachments:
        conflict: Payload = {"reply_verified": False, "verification_performed": True, "blocker": "provider_delivery_intent_conflict"}
        if not isinstance(steward_context, dict) or private_transport is None:
            if verify_only:
                return conflict
            raise ValueError("result files require the bound-owner App transport")
        reply = _mapping(load_lark_event_inbox_config(project=project, config_path=config_path)["reply"])
        sender_profile, provider_ref = _str_field(reply, "sender_profile"), _str_field(steward_context, "provider_ref")
        if not verify_only and (
            source_verifier is None or not _bot_identity_verified(
                runner=runner or _default_runner, base=["lark-cli", "--profile", sender_profile],
                expected_name=_str_field(reply, "bot_display_name"),
            ) or not source_verifier()
        ):
            raise ValueError("result file sender or source identity unavailable")

        def before_upload() -> bool:
            return verify_only or (source_verifier is not None and before_send("")["continue_delivery"] and source_verifier())

        try:
            keys = cast(Callable[..., tuple[str, ...]], uploaded_result_files)(
                root=root, profile=sender_profile, provider_ref=provider_ref, attachments=attachments,
                runner=runner, private_transport=private_transport, before_upload=before_upload, verify_only=verify_only,
            )
        except (ValueError, OSError):
            if verify_only:
                return conflict
            raise

    observed_attempt: dict[str, str | None] = {}

    def record_attempt(value: AttemptRecord) -> None:
        observed_attempt.update(value)
        if delivery_attempt_recorder is not None:
            delivery_attempt_recorder(value)

    attachment_names = tuple(_str_field(attachment, "name") for attachment in attachments)
    if attempt is not None:
        result = verify_lark_inbox_reply(
            project=project, config_path=config_path, message_id=message_id, text=text, attempt=attempt,
            runner=runner or _default_runner, source_membership_verifier=source_verifier,
            attachment_keys=keys, attachment_names=attachment_names,
            finalize_reactions=not attachments, resume_parts=True, before_send=before_send,
        )
    else:
        result = reply_lark_event_inbox(
            project=project, config_path=config_path, message_id=message_id, text=text,
            content_format="markdown", execute=True, runner=runner or _default_runner, before_send=before_send,
            delivery_attempt_recorder=record_attempt if attachments else delivery_attempt_recorder,
            source_membership_verifier=source_verifier,
            recover_ambiguous_send=True,
            short_message_limit=None, attachment_keys=keys, attachment_names=attachment_names,
            finalize_reactions=not attachments,
        )
    if attachments and result.get("reply_verified") is True:
        # The recorder persists the exact message locator before this read.
        # Failure retains that attempt; recovery only reads it, never resends.
        file_text, file_attempt = cast(Callable[..., tuple[str, dict[str, str | None]]], attachment_reply)(
            project=project, config_path=config_path,
            attempt=attempt if attempt is not None else observed_attempt, text=text,
        )
        result.update(cast(Callable[..., Payload], verify_result_files)(
            profile=sender_profile, message_id=file_attempt["message_ref"], text=file_text,
            attachments=attachments, keys=keys, runner=runner, private_transport=private_transport,
        ))
        if result.get("reply_verified") is True:
            result.update(_verified_reply_result(
                project=project, config_path=config_path, message_id=message_id,
                runner=runner or _default_runner, finalize_reactions=True,
            ))
    return result


def send_return(
    *, root: Path, registry: Path, snapshot_provider: SnapshotProvider, route: PayloadView,
    session: PayloadView, turn: PayloadView, text: str, runner: CommandRunner | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    delivery_attempt_recorder: AttemptRecorder | None = None, private_transport: _PrivateTransport | None = None,
) -> Payload:
    return _run_inbox_return(
        root, registry, snapshot_provider, route, session, turn, text, runner, cancelled,
        private_transport, delivery_attempt_recorder=delivery_attempt_recorder,
    )


def verify_return(
    *, root: Path, registry: Path, snapshot_provider: SnapshotProvider, route: PayloadView,
    session: PayloadView, turn: PayloadView, text: str, attempt: AttemptRecord,
    runner: CommandRunner | None = None, cancelled: Callable[[], bool] = lambda: False,
    private_transport: _PrivateTransport | None = None,
) -> Payload:
    return _run_inbox_return(
        root, registry, snapshot_provider, route, session, turn, text, runner, cancelled,
        private_transport, attempt=attempt,
    )


class LarkManagerReturnTransport:
    """Send or read back one saved result through the existing Lark adapter."""

    supports_result_files = True

    def __init__(self, server: _ReturnServer, root: Path) -> None:
        self.server, self.root, self.cancelled = server, root, lambda: False

    def _dispatch(
        self, fn: Callable[..., Payload], route: PayloadView, session: PayloadView,
        turn: PayloadView, text: str, **extra: object,
    ) -> Payload:
        return fn(
            root=self.root, registry=self.server.registry_path,
            snapshot_provider=self.server.lark_goal_topic_runtime.snapshot_provider,
            route=route, session=session, turn=turn, text=text, cancelled=self.cancelled,
            private_transport=cast("_PrivateTransport | None", getattr(self.server, "lark_private_conversations", None)),
            **extra,
        )

    def __call__(self, route: PayloadView, session: PayloadView, turn: PayloadView, text: str) -> Payload:
        return self._dispatch(send_return, route, session, turn, text)

    def send_with_attempt(
        self, route: PayloadView, session: PayloadView, turn: PayloadView, text: str, record_attempt: AttemptRecorder
    ) -> Payload:
        return self._dispatch(send_return, route, session, turn, text, delivery_attempt_recorder=record_attempt)

    def verify(
        self, route: PayloadView, session: PayloadView, turn: PayloadView, text: str, attempt: AttemptRecord
    ) -> Payload:
        return self._dispatch(verify_return, route, session, turn, text, attempt=attempt)


def start_return_service(
    server: _ReturnServiceServer, runtime_root: Path, *, start_service: bool = True
) -> ReturnService:
    """Compose the return pump at the existing Chat/Lark service boundary."""
    from ...capabilities.manager_context.roundtrip import ReturnService

    local_transport = LarkManagerReturnTransport(server, runtime_root)
    composition = cast("_CompositionTransport | None", getattr(server, "conversation_transports", None))
    active_transport: LarkManagerReturnTransport | _CompositionTransport = local_transport
    if composition is not None:
        composition.default_return = local_transport
        active_transport = composition
    service = cast(Callable[..., ReturnService], ReturnService)(
        runtime_root, server.registry_path, server.chat_store, active_transport
    )
    active_transport.cancelled = service.stop.is_set
    if start_service:
        service.start()
    return service
