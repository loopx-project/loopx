"""Return a worker's audience-ready conclusion through the original Lark inbox."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .goal_channel_contracts import bindings_for_goal
from .goal_channel_targets import goal_channel_target_for_name
from .goal_topic_runtime import _inbox_config
from .manager_routing import authorized_manager_goal_ids
from .event_inbox import load_lark_event_inbox_config, _load_processed
from .inbox_reply import CommandRunner, _bot_identity_verified, _default_runner, _verified_reply_result, reply_lark_event_inbox, verify_lark_inbox_reply
from .return_files import uploaded_result_files, verify_result_files
from ...capabilities.manager_context import authority
from ...capabilities.manager_context.roundtrip import ReturnResolutionBlocked


def _resolve_return(
    *, root: Path, registry: Path, snapshot_provider: Callable[[], dict[str, Any]],
    route: dict[str, Any], session: dict[str, Any], turn: dict[str, Any], cancelled: Callable[[], bool],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if cancelled():
        raise ValueError("manager return service stopped")
    snapshot = snapshot_provider()
    # Validate live binding/session independently of the worker's target Goal.
    # Delegation grants permit only this request's audience-ready reply.
    if not authorized_manager_goal_ids(snapshot, session, runtime_root=root):
        raise ReturnResolutionBlocked(
            "return_authorization_unavailable",
            "manager connection no longer authorized",
        )
    target = {k: route[k] for k in ("goal_id", "agent_id")}
    grant = authority(root, registry, session, turn)
    if (
        target not in grant["targets"]
        or grant.get("source_id") != route["source_id"]
    ):
        raise ReturnResolutionBlocked(
            "return_authorization_unavailable", "context return authority revoked"
        )
    matches = []
    for gid, payload in snapshot.get("binding_payloads", {}).items():
        for binding in bindings_for_goal(payload, gid):
            if (
                binding.get("enabled") is True
                and binding.get("session_id") == route["session_id"]
                and (binding.get("routing") or {}).get("conversation_kind")
                == "manager"
            ):
                matches.append(binding)
    if len(matches) != 1:
        raise ReturnResolutionBlocked(
            "original_route_unavailable", "manager return binding ambiguous"
        )
    binding = matches[0]
    target_config = goal_channel_target_for_name(
        snapshot["target_payload"], binding["target_ref"]
    )
    if not target_config or target_config.get("enabled") is not True:
        raise ReturnResolutionBlocked(
            "original_route_unavailable", "manager return target disabled"
        )
    routing = {
        "target_ref": binding["target_ref"],
        "conversation_kind": "manager",
        "app_ref": (target_config.get("identity") or {}).get("sender_profile")
        or "default",
        "topic_root_message_id": (binding.get("topic") or {}).get("root_message_id")
        or (binding.get("channel") or {}).get("pinned_message_id")
        or "",
    }
    return snapshot, routing, target_config


def _return_inbox(*, root: Path, registry: Path, snapshot_provider: Callable[[], dict[str, Any]],
                  route: dict[str, Any], session: dict[str, Any], turn: dict[str, Any],
                  cancelled: Callable[[], bool], private_transport: Any) -> tuple[Path, Path, Callable[[], bool] | None, object, str]:
    project = root
    source_verifier: Callable[[], bool] | None
    destination: object
    if isinstance(session.get("steward_context"), dict):
        if cancelled():
            raise ValueError("manager return service stopped")
        grant = authority(root, registry, session, turn)
        target = {k: route[k] for k in ("goal_id", "agent_id")}
        if (target not in grant["targets"] or grant.get("source_id") != route["source_id"]
                or private_transport is None):
            raise ReturnResolutionBlocked(
                "return_authorization_unavailable", "private return authority unavailable"
            )
        try:
            config_path, record = private_transport.return_inbox(route=route, session=session, turn=turn)
            # The original Inbox belongs to the private Chat transport. Its
            # relative paths must not be resolved against the coordination
            # root, which continues to own grants and returned-result state.
            project = private_transport.runtime_root
        except (KeyError, ValueError, OSError) as exc:
            raise ReturnResolutionBlocked(
                "original_route_unavailable", "original private source unavailable"
            ) from exc
        def verify_source() -> bool:
            return bool(private_transport._source_verified(record))
        source_verifier = verify_source
        destination = {key: record[key] for key in ("profile", "binding_id", "source", "event")}
        message_id = record["event"]["message_id"]
    else:
        snapshot, routing, target_config = _resolve_return(
            root=root, registry=registry, snapshot_provider=snapshot_provider,
            route=route, session=session, turn=turn, cancelled=cancelled,
        )
        config_path, _ = _inbox_config(
            runtime_root=root, route=routing, target_payload=snapshot["target_payload"]
        )
        source_verifier = None
        destination = (routing, target_config)
        message_id = route["source_id"].removeprefix("lark:")
    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    if message_id not in _load_processed(config["inbox_path"] / "processed.json"):
        raise ReturnResolutionBlocked(
            "initial_delivery_receipt_unavailable", "initial reply has not been acknowledged"
        )
    return project, config_path, source_verifier, destination, message_id


def send_return(
    *,
    root: Path,
    registry: Path,
    snapshot_provider: Callable[[], dict[str, Any]],
    route: dict[str, Any],
    session: dict[str, Any],
    turn: dict[str, Any],
    text: str,
    runner: CommandRunner | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    delivery_attempt_recorder: Callable[[Mapping[str, str | None]], None] | None = None,
    private_transport: Any = None,
) -> dict[str, Any]:
    def resolve() -> tuple[Path, Path, Callable[[], bool] | None, object, str]:
        return _return_inbox(
            root=root,
            registry=registry,
            snapshot_provider=snapshot_provider,
            route=route,
            session=session,
            turn=turn,
            cancelled=cancelled,
            private_transport=private_transport,
        )

    project, config_path, source_verifier, destination, message_id = resolve()
    if runner is None and isinstance(session.get("steward_context"), dict):
        runner = private_transport._reply_runner
    def before_send(_intent: str) -> dict[str, bool]:
        current = resolve()
        return {"continue_delivery": current[:2] == (project, config_path) and current[3:] == (destination, message_id)}

    attachments = route.get("result_attachments") or []
    keys = ()
    if attachments:
        if not isinstance(session.get("steward_context"), dict) or private_transport is None:
            raise ValueError("result files require the bound-owner App transport")
        reply = load_lark_event_inbox_config(project=project, config_path=config_path)["reply"]
        if not _bot_identity_verified(runner=runner or _default_runner,
                                     base=["lark-cli", "--profile", reply["sender_profile"]],
                                     expected_name=reply["bot_display_name"]) or not source_verifier():
            raise ValueError("result file sender or source identity unavailable")
        keys = uploaded_result_files(root=root, profile=reply["sender_profile"],
            provider_ref=session["steward_context"]["provider_ref"], attachments=attachments,
            runner=runner, private_transport=private_transport,
            before_upload=lambda: before_send("")["continue_delivery"] and source_verifier())

    runner_kwargs: dict[str, Any] = {"runner": runner} if runner else {}
    observed_attempt = {}
    def record_attempt(value):
        observed_attempt.update(value)
        if delivery_attempt_recorder is not None:
            delivery_attempt_recorder(value)
    result: dict[str, Any] = reply_lark_event_inbox(
        project=project,
        config_path=config_path,
        message_id=message_id,
        text=text,
        content_format="markdown",
        execute=True,
        before_send=before_send,
        delivery_attempt_recorder=record_attempt if attachments else delivery_attempt_recorder,
        source_membership_verifier=source_verifier,
        # A returned manager answer keeps the provider bound, not the compact
        # notification length.
        short_message_limit=None,
        attachment_keys=keys,
        attachment_names=tuple(attachment["name"] for attachment in attachments),
        finalize_reactions=not attachments,
        **runner_kwargs,
    )
    if attachments and result.get("reply_verified") is True:
        # The recorder persists the exact message locator before this read.
        # Failure retains that attempt; recovery only reads it, never resends.
        verified = verify_result_files(profile=reply["sender_profile"],
            message_id=observed_attempt["message_ref"], text=text, attachments=attachments, keys=keys,
            runner=runner, private_transport=private_transport)
        result.update(verified)
        if verified.get("reply_verified") is True:
            result.update(_verified_reply_result(project=project, config_path=config_path,
                message_id=message_id, runner=runner or _default_runner, finalize_reactions=True))
    return result


def verify_return(
    *,
    root: Path,
    registry: Path,
    snapshot_provider: Callable[[], dict[str, Any]],
    route: dict[str, Any],
    session: dict[str, Any],
    turn: dict[str, Any],
    text: str,
    attempt: Mapping[str, str | None],
    runner: CommandRunner | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    private_transport: Any = None,
) -> dict[str, Any]:
    project, config_path, source_verifier, _destination, message_id = _return_inbox(
        root=root,
        registry=registry,
        snapshot_provider=snapshot_provider,
        route=route,
        session=session,
        turn=turn,
        cancelled=cancelled,
        private_transport=private_transport,
    )
    if runner is None and isinstance(session.get("steward_context"), dict):
        runner = private_transport._reply_runner
    runner_kwargs: dict[str, Any] = {"runner": runner} if runner else {}
    attachments = route.get("result_attachments") or []
    keys = ()
    if attachments:
        if not isinstance(session.get("steward_context"), dict) or private_transport is None:
            return {"reply_verified": False, "verification_performed": True, "blocker": "provider_delivery_intent_conflict"}
        reply = load_lark_event_inbox_config(project=project, config_path=config_path)["reply"]
        try:
            keys = uploaded_result_files(root=root, profile=reply["sender_profile"],
                provider_ref=session["steward_context"]["provider_ref"], attachments=attachments,
                runner=runner, private_transport=private_transport, verify_only=True)
        except (ValueError, OSError):
            return {"reply_verified": False, "verification_performed": True, "blocker": "provider_delivery_intent_conflict"}
    result: dict[str, Any] = verify_lark_inbox_reply(
        project=project,
        config_path=config_path,
        message_id=message_id,
        text=text,
        attempt=attempt,
        source_membership_verifier=source_verifier,
        attachment_keys=keys,
        attachment_names=tuple(attachment["name"] for attachment in attachments),
        finalize_reactions=not attachments,
        **runner_kwargs,
    )
    if attachments and result.get("reply_verified") is True:
        result.update(verify_result_files(profile=reply["sender_profile"],
            message_id=attempt["message_ref"], text=text, attachments=attachments, keys=keys,
            runner=runner, private_transport=private_transport))
        if result.get("reply_verified") is True:
            result.update(_verified_reply_result(project=project, config_path=config_path,
                message_id=message_id, runner=runner or _default_runner, finalize_reactions=True))
    return result


class LarkManagerReturnTransport:
    """Send or read back one saved result through the existing Lark adapter."""

    supports_result_files = True

    def __init__(self, server: Any, root: Path) -> None:
        self.server = server
        self.root = root
        self.cancelled = lambda: False

    def _arguments(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "registry": self.server.registry_path,
            "snapshot_provider": self.server.lark_goal_topic_runtime.snapshot_provider,
            "cancelled": self.cancelled,
            "private_transport": getattr(self.server, "lark_private_conversations", None),
        }

    def __call__(self, route: dict[str, Any], session: dict[str, Any], turn: dict[str, Any], text: str) -> dict[str, Any]:
        return send_return(
            **self._arguments(), route=route, session=session, turn=turn, text=text
        )

    def send_with_attempt(self, route: dict[str, Any], session: dict[str, Any], turn: dict[str, Any],
                          text: str, record_attempt: Callable[[Mapping[str, str | None]], None]) -> dict[str, Any]:
        return send_return(
            **self._arguments(),
            route=route,
            session=session,
            turn=turn,
            text=text,
            delivery_attempt_recorder=record_attempt,
        )

    def verify(self, route: dict[str, Any], session: dict[str, Any], turn: dict[str, Any],
               text: str, attempt: Mapping[str, str | None]) -> dict[str, Any]:
        return verify_return(
            **self._arguments(),
            route=route,
            session=session,
            turn=turn,
            text=text,
            attempt=attempt,
        )


def start_return_service(server: Any, runtime_root: Path) -> Any:
    """Compose the return pump at the existing Chat/Lark service boundary."""
    from ...capabilities.manager_context.roundtrip import ReturnService

    transport = LarkManagerReturnTransport(server, runtime_root)
    service = ReturnService(
        runtime_root, server.registry_path, server.chat_store, transport
    )
    transport.cancelled = service.stop.is_set
    service.start()
    return service
